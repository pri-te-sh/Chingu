#include "net.h"
#include "board.h"
#include "log.h"
#include "prefs.h"
#include "ota.h"
#include "battery.h"
#include "certs.h"
#include "speaker.h"
#include <time.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>

namespace net {

static Turn turn_;
const Turn& lastTurn() { return turn_; }

static WebSocketsClient ws;
static Face* face_ = nullptr;
static TFT_eSPI* tft_ = nullptr;
static State state_ = CONNECTING_WIFI;
static bool ready_ = false, wsBegun_ = false, suspended_ = false;
static uint32_t pingSent_ = 0, pongRtt_ = 0, holdOffUntil_ = 0, lastStatus_ = 0;
static char pairingCode_[8] = "";
static char apName_[24] = "";
static WebServer* http_ = nullptr;
static DNSServer* dns_ = nullptr;

State state() { return state_; }
const char* pairingCode() { return pairingCode_; }
const char* apName() { return apName_; }
bool wifiUp() { return WiFi.status() == WL_CONNECTED; }
bool connected() { return ready_; }
const char* backendHost() { return prefs::brainHost; }
uint16_t backendPort() { return prefs::brainPort; }
bool tls() { return prefs::brainTls; }

static void setLedBlue(bool on) { digitalWrite(PIN_LED_B, on ? LOW : HIGH); }
static uint32_t since() { return millis() - turn_.t0; }
static void sendStatus();
static void wsConnect();

// ------------------------------------------------------------------ provisioning (SoftAP captive portal)
static const char SETUP_HTML[] PROGMEM = R"HTML(<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><title>Set up Pixel</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;background:#0e1020;color:#eef;margin:0;padding:24px}h1{color:#f5b301;font-size:22px}
label{display:block;margin:14px 0 6px;font-size:13px;color:#9aa}input,select{width:100%%;padding:12px;border:2px solid #2f3559;background:#181c33;color:#fff;border-radius:8px;font-size:16px}
button{margin-top:20px;width:100%%;padding:14px;background:#f5b301;border:0;border-radius:8px;font-weight:700;font-size:16px}small{color:#9aa}</style></head><body>
<h1>Hi, I'm %NAME%</h1><p>Tell me which Wi-Fi to join. I'll then show a pairing code on my face - add me in the Pixel portal with it.</p>
<form method=post action=/save><label>Wi-Fi network</label><select name=ssid id=ssid>%NETS%</select><input name=ssid2 placeholder="or type a network name" style="margin-top:8px">
<label>Password</label><input name=pass type=password><label>Brain address <small>(leave as is unless told otherwise; a dev brain is http://host:8765)</small></label><input name=host value="%HOST%">
<input type=hidden name=port value="%PORT%"><input type=hidden name=tls value="%TLS%"><button>Save & connect</button></form></body></html>)HTML";

static String scanOptions() {
  String o; int n = WiFi.scanNetworks();
  for (int i = 0; i < n && i < 15; i++) o += "<option>" + WiFi.SSID(i) + "</option>";
  return o.length() ? o : "<option>(no networks found - type one below)</option>";
}

static void handleRoot() {
  String page = FPSTR(SETUP_HTML);
  page.replace("%NAME%", prefs::name); page.replace("%NETS%", scanOptions());
  page.replace("%HOST%", prefs::brainHost); page.replace("%PORT%", String(prefs::brainPort)); page.replace("%TLS%", prefs::brainTls ? "1" : "0");
  http_->send(200, "text/html", page);
}

static void handleSave() {
  String ssid = http_->arg("ssid2").length() ? http_->arg("ssid2") : http_->arg("ssid");
  strlcpy(prefs::wifiSsid, ssid.c_str(), sizeof prefs::wifiSsid);
  strlcpy(prefs::wifiPass, http_->arg("pass").c_str(), sizeof prefs::wifiPass);
  String host = http_->arg("host"); host.trim();
  if (host.length()) {                      // accept "host", "host:port", "https://host"
    bool explicitScheme = host.startsWith("https://") || host.startsWith("http://");
    if (explicitScheme) prefs::brainTls = host.startsWith("https://");           // a bare host keeps the current/default TLS setting
    else prefs::brainTls = http_->arg("tls") == "1";
    host.replace("https://", ""); host.replace("http://", "");
    int c = host.indexOf(':');
    if (c > 0) { prefs::brainPort = host.substring(c + 1).toInt(); host = host.substring(0, c); }
    else if (explicitScheme) prefs::brainPort = prefs::brainTls ? 443 : 80;   // scheme given, no port: that scheme's default (dev brains: type host:8765)
    else { int prt = http_->arg("port").toInt(); prefs::brainPort = prt > 0 ? prt : (prefs::brainTls ? 443 : 8765); }
    strlcpy(prefs::brainHost, host.c_str(), sizeof prefs::brainHost);
  }
  prefs::save();
  http_->send(200, "text/html", "<html><body style='font-family:sans-serif;background:#0e1020;color:#eef;padding:24px'><h1 style='color:#f5b301'>Got it!</h1><p>Connecting to <b>" + ssid + "</b>. Watch my face for the pairing code.</p></body></html>");
  dbg::log("[net] provisioned ssid=%s brain=%s:%u tls=%d", prefs::wifiSsid, prefs::brainHost, prefs::brainPort, prefs::brainTls);
  delay(600);
  ESP.restart();
}

void startProvisioning() {
  state_ = PROVISIONING;
  WiFi.disconnect(true);
  WiFi.mode(WIFI_AP_STA);
  uint8_t mac[6]; WiFi.macAddress(mac);
  snprintf(apName_, sizeof apName_, "Pixel-%02X%02X", mac[4], mac[5]);
  WiFi.softAP(apName_);
  delay(100);
  dns_ = new DNSServer(); dns_->start(53, "*", WiFi.softAPIP());          // captive: every name -> us
  http_ = new WebServer(80);
  http_->on("/", handleRoot); http_->on("/save", HTTP_POST, handleSave);
  http_->onNotFound([]() { http_->sendHeader("Location", "http://" + WiFi.softAPIP().toString() + "/", true); http_->send(302, "text/plain", ""); });
  http_->begin();
  dbg::log("[net] setup mode: join Wi-Fi '%s' and open http://%s", apName_, WiFi.softAPIP().toString().c_str());
}

// ------------------------------------------------------------------ brain link
static void onEvent(WStype_t type, uint8_t* payload, size_t len) {
  switch (type) {
    case WStype_CONNECTED: {
      dbg::log("[net] ws connected");
      state_ = CONNECTING_BRAIN;
      JsonDocument d;
      d["type"] = "hello"; d["device"] = prefs::deviceId(); d["device_type"] = "lite"; d["fw"] = ota::version(); d["build"] = __DATE__ " " __TIME__;
      if (prefs::hasToken()) d["token"] = prefs::token;
      d["device_key"] = prefs::deviceKey;
      JsonObject caps = d["capabilities"].to<JsonObject>();
      caps["speaker"] = true; caps["mic"] = false; caps["camera"] = false; caps["touch"] = true; caps["display"] = "320x240";
      String s; serializeJson(d, s); ws.sendTXT(s);
      break;
    }
    case WStype_DISCONNECTED:
      if (ready_) dbg::log("[net] ws disconnected");
      ready_ = false;
      if (state_ != PAIRING) state_ = wifiUp() ? CONNECTING_BRAIN : CONNECTING_WIFI;
      if (holdOffUntil_ > millis()) ws.disconnect();
      break;
    case WStype_TEXT: {
      JsonDocument d;
      if (deserializeJson(d, payload, len)) break;
      const char* t = d["type"] | "";
      if (!strcmp(t, "ready")) { ready_ = true; state_ = READY; pairingCode_[0] = 0; dbg::log("[net] brain ready"); ota::markHealthy(); sendStatus(); }
      else if (!strcmp(t, "ota")) { dbg::log("[net] update requested by the portal"); ota::requestInstall(); }
      else if (!strcmp(t, "ota_manifest")) ota::onManifest(d.as<JsonVariantConst>());
      else if (!strcmp(t, "brain")) {                                   // move to another brain (cutover): host/port/tls, then reconnect
        const char* host = d["host"] | "";
        if (host[0]) {
          strlcpy(prefs::brainHost, host, sizeof prefs::brainHost); prefs::brainTls = d["tls"] | false; prefs::brainPort = d["port"] | (prefs::brainTls ? 443 : 8765);
          prefs::save(); dbg::log("[net] brain moved to %s:%u tls=%d - restarting", prefs::brainHost, prefs::brainPort, prefs::brainTls);
          delay(300); ESP.restart();
        }
      }
      else if (!strcmp(t, "pairing")) {
        strlcpy(pairingCode_, d["code"] | "", sizeof pairingCode_); state_ = PAIRING;
        dbg::log("[net] waiting to be paired - code %s", pairingCode_);
      }
      else if (!strcmp(t, "paired")) {
        strlcpy(prefs::token, d["token"] | "", sizeof prefs::token);
        if (d["name"].is<const char*>() && strlen(d["name"] | "")) strlcpy(prefs::name, d["name"], sizeof prefs::name);
        prefs::save(); pairingCode_[0] = 0;
        dbg::log("[net] paired as '%s' - token stored", prefs::name);
        ws.disconnect();                      // reconnect with the token
      }
      else if (!strcmp(t, "unpaired")) {
        prefs::token[0] = 0; prefs::save(); dbg::log("[net] unpaired by owner"); ws.disconnect();
      }
      else if (!strcmp(t, "config")) {
        strlcpy(prefs::name, d["name"] | prefs::name, sizeof prefs::name);
        prefs::setFromHex(d["eye_color"] | "");
        prefs::autoSleepS = d["auto_sleep_s"] | prefs::autoSleepS;
        if (d["mood_colors"].is<JsonObject>()) {
          String m;
          for (JsonPair kv : d["mood_colors"].as<JsonObject>()) {
            const char* v = kv.value().as<const char*>(); if (!v) continue;
            if (m.length()) m += ",";
            m += kv.key().c_str(); m += "="; m += (v[0] == '#') ? v + 1 : "-";
          }
          strlcpy(prefs::moods, m.c_str(), sizeof prefs::moods);
        }
        prefs::save(); prefs::apply(*face_, *tft_);
        dbg::log("[net] config: %s eyes #%06X sleep %us", prefs::name, prefs::eyeRGB, prefs::autoSleepS);
      }
      else if (!strcmp(t, "redeploy")) {
        uint32_t wait = d["wait_s"] | 60; holdOffUntil_ = millis() + wait * 1000;
        dbg::log("[net] brain redeploying - reconnecting in %us", wait); ws.disconnect();
      }
      else if (!strcmp(t, "pong")) { pongRtt_ = millis() - pingSent_; pingSent_ = 0; }
      else if (!strcmp(t, "transcript")) {
        // a turn starts the moment the brain tells us what was heard/typed (portal-initiated turns included)
        if (!turn_.active) { turn_ = Turn(); turn_.t0 = millis(); turn_.active = true; }
        turn_.tTranscript = since(); strlcpy(turn_.transcript, d["text"] | "", sizeof turn_.transcript);
        dbg::log("[net] heard: %s", turn_.transcript);
      }
      else if (!strcmp(t, "expression")) {
        Expression e; const char* name = d["name"] | "neutral"; float inten = d["intensity"] | 0.8f;
        if (expressionFromName(name, e)) face_->setExpression(e, inten, 8000);
        if (turn_.active && strcmp(name, "thinking") && !turn_.tExpr) { turn_.tExpr = since(); turn_.intensity = inten; strlcpy(turn_.expr, name, sizeof turn_.expr); }
        if (dbg::verbose) dbg::log("[net] expr %s %.1f @%ums", name, inten, since());
      }
      else if (!strcmp(t, "step")) { if (dbg::verbose) dbg::log("[net] step: %s", d["text"] | ""); }
      else if (!strcmp(t, "tool")) { if (dbg::verbose) dbg::log("[net] tool %s", d["name"] | ""); }
      else if (!strcmp(t, "reply")) {
        turn_.tReply = since(); turn_.done = true; turn_.active = false;
        strlcpy(turn_.reply, d["text"] | "", sizeof turn_.reply);
        dbg::log("[net] pixel: %s", turn_.reply);
        dbg::log("[net] turn: expr %ums, audio %ums, done %ums", turn_.tExpr, turn_.tFirstAudio, turn_.tReply);
      }
      else if (!strcmp(t, "speech_start")) turn_.audioBytes = 0;
      else if (!strcmp(t, "speech_end")) { turn_.tSpeechEnd = since(); speaker::endOfSpeech(); face_->setExpression(face_->expression(), 1.0f, 1500); }
      else if (!strcmp(t, "speech_cancel")) { turn_.active = false; speaker::flush(); }
      else if (!strcmp(t, "vad")) { if (d["speaking"] | false) face_->setExpression(EXPR_LISTENING, 1.0f, 20000); }
      else if (!strcmp(t, "error")) dbg::log("[net] backend error: %s", d["message"] | "");
      break;
    }
    case WStype_BIN:
      if (turn_.audioBytes == 0) turn_.tFirstAudio = since();
      turn_.audioBytes += len;
      speaker::feed(payload, len);
      break;
    default: break;
  }
}

static void wsConnect() {
  if (prefs::brainTls) ws.beginSslWithCA(prefs::brainHost, prefs::brainPort, "/ws", PIXEL_CA_BUNDLE);   // verified: only the real brain
  else ws.begin(prefs::brainHost, prefs::brainPort, "/ws");
  wsBegun_ = true;
}

static void sendStatus() {
  if (!ready_) return;
  JsonDocument d;
  d["type"] = "status"; d["rssi"] = WiFi.RSSI(); d["heap"] = ESP.getFreeHeap(); d["uptime_s"] = millis() / 1000;
  d["ip"] = WiFi.localIP().toString(); d["fw"] = ota::version(); d["build"] = __DATE__ " " __TIME__; d["ota"] = ota::state(); d["expr"] = expressionName(face_->expression());
  d["name"] = prefs::name; d["reset_reason"] = (int)esp_reset_reason(); d["min_heap"] = ESP.getMinFreeHeap();
  d["battery_mv"] = battery::millivolts(); d["battery_v"] = battery::millivolts() / 1000.0f; d["battery_pct"] = battery::percent();
  d["battery_state"] = battery::stateName(); d["battery_talk"] = prefs::batteryTalk;
  String s; serializeJson(d, s); ws.sendTXT(s);
}

void sendRaw(const char* json) { if (ready_) ws.sendTXT(json); }
void sendStatusNow() { if (ready_) { lastStatus_ = millis(); sendStatus(); } }

void suspend() { suspended_ = true; ready_ = false; ws.disconnect(); dbg::log("[net] brain link suspended"); }
void resume() { suspended_ = false; wsBegun_ = false; if (wifiUp()) wsConnect(); dbg::log("[net] brain link resumed"); }

static void playbackEnded() { if (ready_) ws.sendTXT("{\"type\":\"playback_end\"}"); }

void begin(Face& face, TFT_eSPI& tft) {
  face_ = &face; tft_ = &tft;
  speaker::begin(); speaker::onPlaybackEnd(playbackEnded);
  ws.onEvent(onEvent);
  ws.setReconnectInterval(3000);
  ws.enableHeartbeat(15000, 3000, 2);
  if (!prefs::hasWifi()) { startProvisioning(); return; }
  WiFi.mode(WIFI_STA); WiFi.setSleep(false);
  WiFi.setHostname(prefs::deviceId());
  WiFi.begin(prefs::wifiSsid, prefs::wifiPass);
  state_ = CONNECTING_WIFI;
  dbg::log("[net] %s connecting to %s, brain %s:%u", prefs::deviceId(), prefs::wifiSsid, prefs::brainHost, prefs::brainPort);
}

void loop() {
  static bool wasUp = false; static uint32_t lastBlink = 0, wifiStart = millis();
  if (state_ == PROVISIONING) {
    dns_->processNextRequest(); http_->handleClient();
    if (millis() - lastBlink > 250) { lastBlink = millis(); setLedBlue((millis() / 250) & 1); }   // fast blink = setup mode
    return;
  }
  bool up = wifiUp();
  if (up != wasUp) {
    wasUp = up;
    if (up) {
      dbg::log("[net] wifi up, ip %s rssi %d", WiFi.localIP().toString().c_str(), WiFi.RSSI()); setLedBlue(false); state_ = CONNECTING_BRAIN;
      configTzTime("UTC0", "pool.ntp.org", "time.google.com", "time.cloudflare.com");   // TLS certificate checks need a real clock
      if (!prefs::brainTls && !wsBegun_) wsConnect();
    }
    else { dbg::log("[net] wifi down"); state_ = CONNECTING_WIFI; wifiStart = millis(); }
  }
  if (!up) {
    if (millis() - lastBlink > 600) { lastBlink = millis(); setLedBlue((millis() / 600) & 1); }
    if (millis() - wifiStart > 90000) { dbg::log("[net] wifi failed for 90 s - opening setup"); startProvisioning(); }   // wrong password etc.
    return;
  }
  if (up && prefs::brainTls && !wsBegun_) {                       // wait (max ~20 s) for SNTP before the first TLS handshake
    static uint32_t ntpStart = 0; if (!ntpStart) ntpStart = millis();
    if (time(nullptr) > 1700000000 || millis() - ntpStart > 20000) { dbg::log("[net] clock %s, connecting over TLS", time(nullptr) > 1700000000 ? "synced" : "NOT synced"); wsConnect(); }
  }
  if (holdOffUntil_ && millis() > holdOffUntil_) { holdOffUntil_ = 0; wsConnect(); dbg::log("[net] reconnecting to brain"); }
  if (!holdOffUntil_ && !suspended_) ws.loop();
  if (ready_ && millis() - lastStatus_ > 30000) { lastStatus_ = millis(); sendStatus(); }
}

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
bool sendPing() { if (!ready_) return false; pongRtt_ = 0; pingSent_ = millis(); ws.sendTXT("{\"type\":\"ping\"}"); return true; }
uint32_t pongRtt() { return pongRtt_; }

}  // namespace net
