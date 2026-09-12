#include "board.h"
#ifdef PIXEL_BOARD_3S
#include "mic.h"
#include "speaker.h"
#include "net.h"
#include "log.h"

namespace mic {
static const int FRAME = 320;                       // 20 ms @ 16 kHz
static const int BATCH = 2;                         // 40 ms per WebSocket message (25 msg/s)
static QueueHandle_t q_ = nullptr;
static volatile float level_ = 0; static bool muted_ = false; static volatile bool streaming_ = false;
static uint32_t lastPlay_ = 0;

// G.711 mu-law encoder (ITU-T), 14-bit input
static inline uint8_t ulaw(int16_t pcm) {
  const int BIAS = 0x84, CLIP = 32635;
  int sign = (pcm >> 8) & 0x80;
  if (sign) pcm = -pcm;
  if (pcm > CLIP) pcm = CLIP;
  pcm += BIAS;
  int exponent = 7; for (int mask = 0x4000; (pcm & mask) == 0 && exponent > 0; exponent--, mask >>= 1) {}
  int mantissa = (pcm >> (exponent + 3)) & 0x0F;
  return (uint8_t)~(sign | (exponent << 4) | mantissa);
}

static void micTask(void*) {
  static int16_t pcm[FRAME]; static uint8_t enc[FRAME * BATCH]; int n = 0;
  for (;;) {
    size_t got = speaker::readMic(pcm, FRAME);
    if (got == 0) { vTaskDelay(1); continue; }
    float sum = 0; for (size_t i = 0; i < got; i++) { float v = pcm[i]; sum += v * v; }
    float rms = sqrtf(sum / got) / 32768.0f;
    level_ = level_ * 0.7f + min(1.0f, rms * 6.0f) * 0.3f;
    bool gate = muted_ || speaker::playing() || millis() - lastPlay_ < 400 || !net::connected();
    if (speaker::playing()) lastPlay_ = millis();
    if (gate) { n = 0; streaming_ = false; continue; }
    for (size_t i = 0; i < got; i++) enc[n * FRAME + i] = ulaw(pcm[i]);
    if (++n >= BATCH) { n = 0; uint8_t* copy = (uint8_t*)malloc(FRAME * BATCH); if (copy) { memcpy(copy, enc, FRAME * BATCH); if (xQueueSend(q_, &copy, 0) != pdTRUE) free(copy); } }
    streaming_ = true;
  }
}

void begin() {
  q_ = xQueueCreate(8, sizeof(uint8_t*));
  xTaskCreatePinnedToCore(micTask, "mic", 4096, nullptr, 2, nullptr, 0);
  dbg::log("[mic] ES8311 ADC -> mu-law, %d ms frames", FRAME * BATCH / 16);
}

void loop() {
  uint8_t* f;
  while (q_ && xQueueReceive(q_, &f, 0) == pdTRUE) { net::sendAudio(f, FRAME * BATCH); free(f); }
}

float level() { return level_; }
bool streaming() { return streaming_; }
void setMuted(bool m) { muted_ = m; }
bool muted() { return muted_; }
}
#endif
