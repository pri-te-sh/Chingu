#include "net.h"
#include "board.h"
#include "log.h"

#if __has_include("secrets.h")
#include "secrets.h"
#define NET_ENABLED 1
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#else
#define NET_ENABLED 0
#endif

namespace net {

static Turn turn_;
const Turn& lastTurn() { return turn_; }

#if NET_ENABLED
static WebSocketsClient ws;
static Face* face_ = nullptr;
static bool ready_ = false;
static uint32_t pingSent_ = 0, pongRtt_ = 0;

static void setLedBlue(bool on) { digitalWrite(PIN_LED_B, on ? LOW : HIGH); }
static uint32_t since() { return millis() - turn_.t0; }

static void onEvent(WStype_t type, uint8_t* payload, size_t len) {
  switch (type) {
    case WStype_CONNECTED: {
      dbg::log("[net] ws connected");
      JsonDocument d;
      d["type"] = "hello"; d["token"] = BACKEND_TOKEN; d["device"] = "pixel";
      String s; serializeJson(d, s); ws.sendTXT(s);
      break;
    }
    case WStype_DISCONNECTED:
      if (ready_) dbg::log("[net] ws disconnected");
      ready_ = false;
      break;
    case WStype_TEXT: {
      JsonDocument d;
      if (deserializeJson(d, payload, len)) break;
      const char* t = d["type"] | "";
      if (!strcmp(t, "ready")) { ready_ = true; dbg::log("[net] brain ready"); }
      else if (!strcmp(t, "pong")) { pongRtt_ = millis() - pingSent_; pingSent_ = 0; if (dbg::verbose) dbg::log("[net] pong %ums", pongRtt_); }
      else if (!strcmp(t, "expression")) {
        Expression e;
        const char* name = d["name"] | "neutral";
        float inten = d["intensity"] | 0.8f;
        if (expressionFromName(name, e)) face_->setExpression(e, inten, 8000);
        if (turn_.active && strcmp(name, "thinking")) {   // the real reaction, not the interim "thinking"
          turn_.tExpr = since(); turn_.intensity = inten; strlcpy(turn_.expr, name, sizeof turn_.expr);
        }
        if (dbg::verbose || turn_.active) dbg::log("[net] expr %s %.1f @%ums", name, inten, since());
      }
      else if (!strcmp(t, "transcript")) {
        turn_.tTranscript = since(); strlcpy(turn_.transcript, d["text"] | "", sizeof turn_.transcript);
        dbg::log("[net] heard: %s", turn_.transcript);
      }
      else if (!strcmp(t, "reply")) {
        turn_.tReply = since(); turn_.done = true; turn_.active = false;
        strlcpy(turn_.reply, d["text"] | "", sizeof turn_.reply);
        dbg::log("[net] pixel: %s", turn_.reply);
        dbg::log("[net] turn: expr %ums, audio %ums, done %ums", turn_.tExpr, turn_.tFirstAudio, turn_.tReply);
      }
      else if (!strcmp(t, "speech_start")) { turn_.audioBytes = 0; }
      else if (!strcmp(t, "speech_end")) {
        turn_.tSpeechEnd = since();
        dbg::log("[net] speech %.1fs audio, %u bytes", turn_.audioBytes / 32000.0f, turn_.audioBytes);
        face_->setExpression(face_->expression(), 1.0f, 1500);
      }
      else if (!strcmp(t, "vad")) {
        if (d["speaking"] | false) face_->setExpression(EXPR_LISTENING, 1.0f, 20000);
        if (dbg::verbose) dbg::log("[net] vad %s", (d["speaking"] | false) ? "speaking" : "silent");
      }
      else if (!strcmp(t, "error")) dbg::log("[net] backend error: %s", d["message"] | "");
      break;
    }
    case WStype_BIN:
      if (turn_.audioBytes == 0) turn_.tFirstAudio = since();
      turn_.audioBytes += len;
      if (dbg::verbose && (turn_.audioBytes / len) % 16 == 1) dbg::log("[net] audio frame %u bytes", len);
      break;
    default: break;
  }
}

void begin(Face& face) {
  face_ = &face;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);         // lower latency at the cost of some power; revisit on battery
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  dbg::log("[net] connecting to %s", WIFI_SSID);
  ws.onEvent(onEvent);
  ws.setReconnectInterval(3000);
  ws.enableHeartbeat(15000, 3000, 2);
#if BACKEND_TLS
  ws.beginSSL(BACKEND_HOST, BACKEND_PORT, "/ws");
#else
  ws.begin(BACKEND_HOST, BACKEND_PORT, "/ws");
#endif
}

void loop() {
  static bool wasUp = false; static uint32_t lastBlink = 0;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up != wasUp) {
    wasUp = up;
    if (up) { dbg::log("[net] wifi up, ip %s rssi %d", WiFi.localIP().toString().c_str(), WiFi.RSSI()); setLedBlue(false); }
    else dbg::log("[net] wifi down");
  }
  if (!up && millis() - lastBlink > 400) { lastBlink = millis(); setLedBlue((millis() / 400) & 1); }
  if (up) ws.loop();
}

bool wifiUp() { return WiFi.status() == WL_CONNECTED; }
bool connected() { return ready_; }
const char* backendHost() { return BACKEND_HOST; }
uint16_t backendPort() { return BACKEND_PORT; }

static void startTurn() { turn_ = Turn(); turn_.t0 = millis(); turn_.active = true; }

void sendText(const char* text) {
  if (!ready_) { dbg::log("[net] not connected"); return; }
  startTurn();
  JsonDocument d; d["type"] = "text"; d["text"] = text;
  String s; serializeJson(d, s); ws.sendTXT(s);
  dbg::log("[net] say: %s", text);
}
void sendAudio(const uint8_t* pcm16, size_t len) { if (ready_) ws.sendBIN(pcm16, len); }
void sendEnd() { if (ready_) { startTurn(); ws.sendTXT("{\"type\":\"end\"}"); } }

bool sendPing() {
  if (!ready_) return false;
  pongRtt_ = 0; pingSent_ = millis();
  ws.sendTXT("{\"type\":\"ping\"}");
  return true;
}
uint32_t pongRtt() { return pongRtt_; }

#else   // ---- no secrets.h: networking compiled out ----
void begin(Face&) { dbg::log("[net] include/secrets.h missing - networking disabled"); }
void loop() {}
bool wifiUp() { return false; }
bool connected() { return false; }
const char* backendHost() { return "(none)"; }
uint16_t backendPort() { return 0; }
void sendText(const char*) { dbg::log("[net] networking disabled"); }
void sendAudio(const uint8_t*, size_t) {}
void sendEnd() {}
bool sendPing() { return false; }
uint32_t pongRtt() { return 0; }
#endif

}  // namespace net
