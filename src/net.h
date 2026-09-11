// Wi-Fi + WebSocket link to Pixel's brain, device provisioning (SoftAP captive portal) and pairing.
#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>
#include "face.h"

namespace net {
struct Turn {
  uint32_t t0 = 0;
  uint32_t tTranscript = 0, tExpr = 0, tFirstAudio = 0, tSpeechEnd = 0, tReply = 0;
  uint32_t audioBytes = 0;
  char expr[16] = "";
  float intensity = 0;
  char transcript[96] = "";
  char reply[160] = "";
  bool active = false, done = false;
};

enum State : uint8_t { PROVISIONING, CONNECTING_WIFI, CONNECTING_BRAIN, PAIRING, READY };

void begin(Face& face, TFT_eSPI& tft);
void loop();
State state();
bool wifiUp();
bool connected();                       // paired + brain said "ready"
const char* backendHost();
uint16_t backendPort();
bool tls();
const char* pairingCode();              // "" unless the brain is waiting for the owner to claim us
const char* apName();                   // SoftAP name while provisioning
void startProvisioning();               // drop Wi-Fi creds and open the setup portal
void suspend();                         // close the brain link (before an OTA download); resume() reconnects
void sendText(const char* text);
void sendAudio(const uint8_t* pcm16, size_t len);
void sendEnd();
const Turn& lastTurn();
void resume();
bool sendPing();
void sendStatusNow();                   // push a status message right away (battery state changed)
uint32_t pongRtt();
}
