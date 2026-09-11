#include "speaker.h"
#include "board.h"
#include "log.h"
#include <driver/i2s.h>

// Design: the WebSocket handler (main loop) drops PCM into a lock-free single-producer/single-consumer ring;
// a dedicated FreeRTOS task on core 0 pulls from it and blocks in i2s_write(). The main loop can stall for
// tens of ms on TFT pushes or TLS without the DAC ever running dry (feeding from the main loop produced
// garbled audio: 16 ms of samples per pass while a pass took longer than that).
namespace speaker {
static const i2s_port_t PORT = I2S_NUM_0;        // built-in DAC lives on I2S0; the mic will use I2S1
static const int RATE = 16000;
static const size_t RING = 24576;                 // 0.75 s: the brain paces frames to real time with a ~0.4 s lead
                                                  // (kept small on purpose: TLS handshakes need ~50 KB of contiguous heap)
static uint8_t ring_[RING]; static volatile size_t head_ = 0, tail_ = 0;
static volatile bool playing_ = false, ending_ = false, flushReq_ = false, endPending_ = false;
static volatile float level_ = 0; static float volume_ = 0.8f;
static volatile uint32_t lastData_ = 0;
static void (*onEnd_)() = nullptr;
static bool droppedThisTurn_ = false;
static volatile uint32_t playedBytes_ = 0, playStart_ = 0;

static size_t avail() { return (head_ + RING - tail_) % RING; }

static void audioTask(void*) {
  static uint16_t out[512];                       // 256 mono samples -> 256 stereo frames (L=R); DAC uses the high byte
  for (;;) {
    if (flushReq_) {
      tail_ = head_; flushReq_ = false; ending_ = false;
      i2s_zero_dma_buffer(PORT); playing_ = false; level_ = 0;
    }
    size_t have = avail();
    if (have >= 4) {
      size_t n = min(have, (size_t)512) & ~1u;
      float sum = 0;
      for (size_t i = 0; i < n; i += 2) {
        size_t t = tail_;
        int16_t s = (int16_t)(ring_[t] | (ring_[(t + 1) % RING] << 8)); tail_ = (t + 2) % RING;
        float v = s * volume_; sum += v * v;
        uint16_t u = (uint16_t)((int32_t)v + 32768);
        out[i] = u; out[i + 1] = u;
      }
      size_t written = 0;
      i2s_write(PORT, out, n * 2, &written, portMAX_DELAY);   // blocks until DMA takes it: natural pacing at 16 kHz
      playedBytes_ += n;
      float rms = sqrtf(sum / (n / 2)) / 32768.0f;
      level_ = level_ * 0.6f + min(1.0f, rms * 4.0f) * 0.4f;
    } else {
      level_ *= 0.7f;
      if (playing_ && (ending_ || millis() - lastData_ > 1500)) {   // drained and the brain is done (or went quiet)
        playing_ = false; level_ = 0; ending_ = false; endPending_ = true;
        uint32_t ms = millis() - playStart_;
        dbg::log("[spk] played %u samples in %u ms (%.0f Hz effective)", (unsigned)(playedBytes_ / 2), (unsigned)ms, ms ? playedBytes_ / 2 * 1000.0f / ms : 0.0f);
      }
      vTaskDelay(pdMS_TO_TICKS(4));
    }
  }
}

static bool apll_ = true; static uint32_t clk_ = RATE;   // APLL is required: the PLL_D2 divider path runs the built-in-DAC clock ~3x too fast on IDF 4.4
static void install() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_DAC_BUILT_IN);
  cfg.sample_rate = clk_; cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;                 // stereo frames (mono TX is unreliable on classic ESP32)
  cfg.communication_format = I2S_COMM_FORMAT_STAND_MSB;
  cfg.intr_alloc_flags = 0; cfg.dma_buf_count = 8; cfg.dma_buf_len = 256; cfg.use_apll = apll_;   // 8 KB DMA = 128 ms
  cfg.tx_desc_auto_clear = true;                                   // underrun -> silence, not a repeated stale buffer
  i2s_driver_install(PORT, &cfg, 0, nullptr);
  i2s_set_dac_mode(I2S_DAC_CHANNEL_LEFT_EN);                       // IDF: LEFT = DAC channel 2 = GPIO26 (RIGHT would be GPIO25)
  i2s_zero_dma_buffer(PORT);
  dbg::log("[spk] DAC ready on GPIO%d @ %u Hz apll=%d (driver clk %.0f Hz)", PIN_AUDIO_DAC, (unsigned)clk_, apll_, i2s_get_clk(PORT));
}

void begin() {
  install();
  xTaskCreatePinnedToCore(audioTask, "audio", 4096, nullptr, 3, nullptr, 0);
}

void reinit(bool apll, uint32_t hz) { apll_ = apll; clk_ = hz; i2s_driver_uninstall(PORT); install(); }

void setClock(uint32_t hz) { i2s_set_sample_rates(PORT, hz); dbg::log("[spk] clock -> %u Hz (driver reports %.0f)", (unsigned)hz, i2s_get_clk(PORT)); }
void setVolume(float v) { volume_ = constrain(v, 0.0f, 1.0f); }
bool playing() { return playing_; }
float level() { return level_; }

void feed(const uint8_t* pcm, size_t len) {
  ending_ = false; lastData_ = millis();
  size_t free = RING - 1 - avail();
  if (len > free) {
    if (!droppedThisTurn_) { droppedThisTurn_ = true; dbg::log("[spk] buffer full, dropping %u bytes", (unsigned)(len - free)); }
    len = free;                                   // keep what fits (brain pacing makes this rare)
  }
  size_t h = head_;
  for (size_t i = 0; i < len; i++) { ring_[h] = pcm[i]; h = (h + 1) % RING; }
  head_ = h;                                       // publish once, after the bytes are in place
  if (!playing_) { playing_ = true; playedBytes_ = 0; playStart_ = millis(); digitalWrite(PIN_AMP_EN, LOW); }
}

void endOfSpeech() { ending_ = true; droppedThisTurn_ = false; }

void flush() { flushReq_ = true; endPending_ = false; droppedThisTurn_ = false; }

void loop() {
  // runs on the main loop: deliver the end-of-playback callback there (it talks to the WebSocket)
  if (endPending_) { endPending_ = false; if (onEnd_) onEnd_(); }
}

void onPlaybackEnd(void (*cb)()) { onEnd_ = cb; }

void tone(float hz, uint16_t ms) {
  // synthesised in 1 KB pieces straight into the ring (no big static buffers - heap is precious for TLS)
  int16_t piece[512]; uint32_t total = RATE * ms / 1000, i = 0;
  while (i < total) {
    uint32_t n = min<uint32_t>(512, total - i);
    for (uint32_t k = 0; k < n; k++, i++) {
      float env = i < 320 ? i / 320.0f : i > total - 320 ? (total - i) / 320.0f : 1.0f;
      piece[k] = (int16_t)(sinf(i * 2 * PI * hz / RATE) * 26000 * env);
    }
    while (RING - 1 - avail() < n * 2) vTaskDelay(pdMS_TO_TICKS(5));   // let the audio task drain before adding more
    feed((const uint8_t*)piece, n * 2);
  }
  endOfSpeech();
}
}
