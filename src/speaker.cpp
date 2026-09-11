#include "speaker.h"
#include "board.h"
#include "log.h"
#include <driver/i2s.h>

namespace speaker {
static const i2s_port_t PORT = I2S_NUM_0;        // built-in DAC lives on I2S0; the mic will use I2S1
static const int RATE = 16000;
static const size_t RING = 24576;                 // 0.75 s: the brain paces frames to real time with a ~0.4 s lead, so this absorbs Wi-Fi jitter
                                                  // (kept small on purpose: TLS handshakes need ~50 KB of contiguous heap)
static uint8_t ring_[RING]; static volatile size_t head_ = 0, tail_ = 0;
static bool playing_ = false, ending_ = false, endedSent_ = false;
static float level_ = 0, volume_ = 0.8f;
static uint32_t lastData_ = 0;
static void (*onEnd_)() = nullptr;

static size_t avail() { return (head_ + RING - tail_) % RING; }

void begin() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_DAC_BUILT_IN);
  cfg.sample_rate = RATE; cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;                 // stereo frames (mono is unreliable on classic ESP32 TX); we duplicate L=R
  cfg.communication_format = I2S_COMM_FORMAT_STAND_MSB;
  cfg.intr_alloc_flags = 0; cfg.dma_buf_count = 6; cfg.dma_buf_len = 256; cfg.use_apll = false;
  i2s_driver_install(PORT, &cfg, 0, nullptr);
  i2s_set_dac_mode(I2S_DAC_CHANNEL_LEFT_EN);                       // IDF: LEFT = DAC channel 2 = GPIO26 (RIGHT would be GPIO25)
  i2s_zero_dma_buffer(PORT);
  dbg::log("[spk] DAC ready on GPIO%d @ %d Hz", PIN_AUDIO_DAC, RATE);
}

void setVolume(float v) { volume_ = constrain(v, 0.0f, 1.0f); }
bool playing() { return playing_; }
float level() { return level_; }

void feed(const uint8_t* pcm, size_t len) {
  ending_ = false; endedSent_ = false; lastData_ = millis();
  for (size_t i = 0; i < len; i++) {
    size_t next = (head_ + 1) % RING;
    if (next == tail_) { dbg::log("[spk] buffer full, dropping"); return; }   // brain is faster than realtime; keep latest
    ring_[head_] = pcm[i]; head_ = next;
  }
  if (!playing_) { playing_ = true; digitalWrite(PIN_AMP_EN, LOW); }
}

void endOfSpeech() { ending_ = true; }

void flush() {
  head_ = tail_ = 0; ending_ = false; endedSent_ = true;
  i2s_zero_dma_buffer(PORT);
  playing_ = false; level_ = 0;
}

void loop() {
  static uint16_t out[512];                       // stereo frames: DAC wants unsigned samples in the high byte; volume applied here
  size_t have = avail();
  if (have >= 4) {
    size_t n = min(have, (size_t)512) & ~1u;      // bytes of mono PCM16 -> n/2 samples -> n uint16 (L,R duplicated)
    float sum = 0;
    for (size_t i = 0; i < n; i += 2) {
      int16_t s = (int16_t)(ring_[tail_] | (ring_[(tail_ + 1) % RING] << 8)); tail_ = (tail_ + 2) % RING;
      float v = s * volume_; sum += v * v;
      uint16_t u = (uint16_t)((int32_t)v + 32768);
      out[i] = u; out[i + 1] = u;
    }
    size_t written = 0;
    i2s_write(PORT, out, n * 2, &written, 20 / portTICK_PERIOD_MS);
    if (written < n * 2) tail_ = (tail_ + RING - (n - written / 2)) % RING;    // DMA full: rewind the unwritten tail, retry next loop
    float rms = sqrtf(sum / (n / 2)) / 32768.0f;
    level_ = level_ * 0.6f + min(1.0f, rms * 4.0f) * 0.4f;
  } else {
    level_ *= 0.7f;
    if (playing_ && (ending_ || millis() - lastData_ > 1500)) {      // drained and the brain is done (or went quiet)
      playing_ = false; level_ = 0;
      if (!endedSent_) { endedSent_ = true; if (onEnd_) onEnd_(); }
    }
  }
}

void onPlaybackEnd(void (*cb)()) { onEnd_ = cb; }

void tone(float hz, uint16_t ms) {
  // synthesised in 1 KB pieces straight into the ring (no big static buffers - heap is precious for TLS)
  int16_t piece[512]; uint32_t total = RATE * ms / 1000, i = 0;
  while (i < total) {
    uint32_t n = min<uint32_t>(512, total - i);
    for (uint32_t k = 0; k < n; k++, i++) {
      float env = i < 320 ? i / 320.0f : i > total - 320 ? (total - i) / 320.0f : 1.0f;
      piece[k] = (int16_t)(sinf(i * 2 * PI * hz / RATE) * 11000 * env);
    }
    feed((const uint8_t*)piece, n * 2);
    if (avail() > RING - 2048) loop();          // ring nearly full: drain some to the DAC before continuing
  }
  endOfSpeech();
}
}
