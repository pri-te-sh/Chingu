// Wi-Fi + WebSocket link to Pixel's brain. Compiles to a no-op if include/secrets.h is absent.
#pragma once
#include <Arduino.h>
#include "face.h"

namespace net {
// Timeline of the most recent conversational turn (ms since sendText / end of utterance).
struct Turn {
  uint32_t t0 = 0;                      // millis() when we sent
  uint32_t tTranscript = 0, tExpr = 0, tFirstAudio = 0, tSpeechEnd = 0, tReply = 0;
  uint32_t audioBytes = 0;
  char expr[16] = "";
  float intensity = 0;
  char transcript[96] = "";
  char reply[160] = "";
  bool active = false, done = false;
};

void begin(Face& face);
void loop();
bool wifiUp();
bool connected();                       // WebSocket session up (brain said "ready")
const char* backendHost();
uint16_t backendPort();
void sendText(const char* text);        // typed input -> backend (testing path until the mic exists)
void sendAudio(const uint8_t* pcm16, size_t len);
void sendEnd();
const Turn& lastTurn();

// latency probe: sendPing() then poll pongRtt() (>0 when answered, 0 pending)
bool sendPing();
uint32_t pongRtt();
}
