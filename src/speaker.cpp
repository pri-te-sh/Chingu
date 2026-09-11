#include "speaker.h"
#include "board.h"
#include "log.h"
#include <driver/i2s.h>

namespace speaker {
static const i2s_port_t PORT = I2S_NUM_0;        // built-in DAC lives on I2S0; the mic will use I2S1
static const int RATE = 16000;
static const size_t RING = 32768;                 // ~1 s of audio: absorbs Wi-Fi jitter without adding much latency
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
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT;                 // DAC2 = GPIO26 = right channel
  cfg.communication_format = I2S_COMM_FORMAT_STAND_MSB;
  cfg.intr_alloc_flags = 0; cfg.dma_buf_count = 6; cfg.dma_buf_len = 256; cfg.use_apll = false;
  i2s_driver_install(PORT, &cfg, 0, nullptr);
  i2s_set_dac_mode(I2S_DAC_CHANNEL_RIGHT_EN);
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
  static uint16_t out[256];                       // DAC wants unsigned samples in the high byte; we also apply volume
  size_t have = avail();
  if (have >= 4) {
    size_t n = min(have, sizeof(out)) & ~1u;      // whole samples
    float sum = 0;
    for (size_t i = 0; i < n; i += 2) {
      int16_t s = (int16_t)(ring_[tail_] | (ring_[(tail_ + 1) % RING] << 8)); tail_ = (tail_ + 2) % RING;
      float v = s * volume_; sum += v * v;
      out[i / 2] = (uint16_t)((int32_t)v + 32768);
    }
    size_t written = 0;
    i2s_write(PORT, out, n, &written, 20 / portTICK_PERIOD_MS);
    if (written < n) tail_ = (tail_ + RING - (n - written)) % RING;    // DMA full: rewind the unwritten tail, retry next loop
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
}
