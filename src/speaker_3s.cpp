#include "board.h"
#ifdef PIXEL_BOARD_3S
// Pixel-3S audio out: PCM16 mono 16 kHz from the brain -> ES8311 codec over I2S (MCLK 4.096 MHz) -> onboard amp -> MX1.25 speaker.
#include "speaker.h"
#include "log.h"
#include <Wire.h>
#include <driver/i2s.h>
#include <es8311.h>

namespace speaker {
static const i2s_port_t PORT = I2S_NUM_0;
static const int RATE = 16000;
static const size_t RING = 32768;                 // 1 s; heap is plentiful on the S3
static uint8_t ring_[RING]; static volatile size_t head_ = 0, tail_ = 0;
static volatile bool playing_ = false, ending_ = false, flushReq_ = false, endPending_ = false;
static volatile float level_ = 0; static float volume_ = 0.8f;
static volatile uint32_t lastData_ = 0;
static void (*onEnd_)() = nullptr;
static bool droppedThisTurn_ = false;
static es8311_handle_t codec_ = nullptr;
static volatile uint32_t playedBytes_ = 0, playStart_ = 0;

static size_t avail() { return (head_ + RING - tail_) % RING; }

static void audioTask(void*) {
  static int16_t out[512];                        // 256 mono samples -> 256 stereo frames
  for (;;) {
    if (flushReq_) { tail_ = head_; flushReq_ = false; ending_ = false; i2s_zero_dma_buffer(PORT); playing_ = false; level_ = 0; }
    size_t have = avail();
    if (have >= 4) {
      size_t n = min(have, (size_t)512) & ~1u;
      float sum = 0;
      for (size_t i = 0; i < n; i += 2) {
        size_t t = tail_;
        int16_t s = (int16_t)(ring_[t] | (ring_[(t + 1) % RING] << 8)); tail_ = (t + 2) % RING;
        float v = s * volume_; sum += v * v;
        int16_t o = (int16_t)constrain((int)v, -32768, 32767);
        out[i] = o; out[i + 1] = o;
      }
      size_t written = 0;
      i2s_write(PORT, out, n * 2, &written, portMAX_DELAY);
      playedBytes_ += n;
      float rms = sqrtf(sum / (n / 2)) / 32768.0f;
      level_ = level_ * 0.6f + min(1.0f, rms * 4.0f) * 0.4f;
    } else {
      level_ *= 0.7f;
      if (playing_ && (ending_ || millis() - lastData_ > 1500)) {
        playing_ = false; level_ = 0; ending_ = false; endPending_ = true;
        uint32_t ms = millis() - playStart_;
        dbg::log("[spk] played %u samples in %u ms (%.0f Hz effective)", (unsigned)(playedBytes_ / 2), (unsigned)ms, ms ? playedBytes_ / 2 * 1000.0f / ms : 0.0f);
      }
      vTaskDelay(pdMS_TO_TICKS(4));
    }
  }
}

void begin() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX);
  cfg.sample_rate = RATE; cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = 0; cfg.dma_buf_count = 8; cfg.dma_buf_len = 256; cfg.use_apll = true;
  cfg.tx_desc_auto_clear = true; cfg.fixed_mclk = RATE * 256;
  if (i2s_driver_install(PORT, &cfg, 0, nullptr) != ESP_OK) { dbg::log("[spk] i2s install failed"); return; }
  i2s_pin_config_t pins = {}; pins.mck_io_num = PIN_I2S_MCLK; pins.bck_io_num = PIN_I2S_BCLK; pins.ws_io_num = PIN_I2S_LRCK;
  pins.data_out_num = PIN_I2S_DOUT; pins.data_in_num = PIN_I2S_DIN;
  i2s_set_pin(PORT, &pins);
  i2s_zero_dma_buffer(PORT);
  codec_ = es8311_create(I2C_NUM_0, ES8311_ADDRRES_0);          // the vendor driver talks over Wire (already begun by display)
  es8311_clock_config_t clk = {}; clk.mclk_inverted = false; clk.sclk_inverted = false; clk.mclk_from_mclk_pin = true;
  clk.mclk_frequency = RATE * 256; clk.sample_frequency = RATE;
  if (!codec_ || es8311_init(codec_, &clk, ES8311_RESOLUTION_16, ES8311_RESOLUTION_16) != ESP_OK) { dbg::log("[spk] ES8311 init failed"); }
  else {
    es8311_voice_volume_set(codec_, 80, nullptr);
    es8311_microphone_config(codec_, false);                    // analog onboard mic
    es8311_microphone_gain_set(codec_, ES8311_MIC_GAIN_24DB);
    dbg::log("[spk] ES8311 ready, I2S %d Hz, MCLK %d", RATE, RATE * 256);
  }
  xTaskCreatePinnedToCore(audioTask, "audio", 4096, nullptr, 3, nullptr, 0);
}

void setVolume(float v) { volume_ = constrain(v, 0.0f, 1.0f); }
bool playing() { return playing_; }
float level() { return level_; }

void feed(const uint8_t* pcm, size_t len) {
  ending_ = false; lastData_ = millis();
  size_t free = RING - 1 - avail();
  if (len > free) {
    if (!droppedThisTurn_) { droppedThisTurn_ = true; dbg::log("[spk] buffer full, dropping %u bytes", (unsigned)(len - free)); }
    len = free;
  }
  size_t h = head_;
  for (size_t i = 0; i < len; i++) { ring_[h] = pcm[i]; h = (h + 1) % RING; }
  head_ = h;
  if (!playing_) { playing_ = true; playedBytes_ = 0; playStart_ = millis(); }
}
void endOfSpeech() { ending_ = true; droppedThisTurn_ = false; }
void flush() { flushReq_ = true; endPending_ = false; droppedThisTurn_ = false; }
void loop() { if (endPending_) { endPending_ = false; if (onEnd_) onEnd_(); } }
void onPlaybackEnd(void (*cb)()) { onEnd_ = cb; }
void tone(float hz, uint16_t ms) {
  int16_t piece[512]; uint32_t total = RATE * ms / 1000, i = 0;
  while (i < total) {
    uint32_t n = min<uint32_t>(512, total - i);
    for (uint32_t k = 0; k < n; k++, i++) {
      float env = i < 320 ? i / 320.0f : i > total - 320 ? (total - i) / 320.0f : 1.0f;
      piece[k] = (int16_t)(sinf(i * 2 * PI * hz / RATE) * 20000 * env);
    }
    while (RING - 1 - avail() < n * 2) vTaskDelay(pdMS_TO_TICKS(5));
    feed((const uint8_t*)piece, n * 2);
  }
  endOfSpeech();
}
void setClock(uint32_t hz) { i2s_set_sample_rates(PORT, hz); }
void reinit(bool, uint32_t hz) { setClock(hz); }
// mic capture (used by mic.cpp): blocking read of 16-bit stereo frames -> mono
size_t readMic(int16_t* mono, size_t samples) {
  static int16_t st[1024]; size_t got = 0, want = min(samples, (size_t)512);
  i2s_read(PORT, st, want * 4, &got, portMAX_DELAY);
  size_t frames = got / 4;
  for (size_t i = 0; i < frames; i++) mono[i] = st[i * 2];     // left slot carries the ES8311 ADC
  return frames;
}
}
#endif
