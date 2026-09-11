// Pixel - Tiny AI Companion firmware. Phase 2: animated face with autonomous behaviour.
// Controls for testing: BOOT button cycles expressions; serial commands (115200):
//   expr <name> [intensity 0-1] [holdMs]   list   sleep   wake   look <x> <y>   say <text>   net   debug   verbose   update   id
// Long-press BOOT (>=1.5 s) or hold the screen 2 s: settings/diagnostics mode. Hold BOOT 10 s: factory reset (Wi-Fi + pairing).
#include <Arduino.h>
#include <TFT_eSPI.h>
#include "board.h"
#include "face.h"
#include "net.h"
#include "log.h"
#include "debug_ui.h"
#include "prefs.h"
#include "ota.h"
#include "speaker.h"

TFT_eSPI tft;
Face face(tft);
DebugUI debugUi(tft);

// While the device is not paired & online, the face is paused and a status screen takes over.
static void drawStateScreen() {
  static net::State last = net::READY; static char lastCode[8] = ""; static char lastSsid[33] = "";
  net::State st = net::state();
  bool changed = st != last || strcmp(lastCode, net::pairingCode()) != 0 || strcmp(lastSsid, prefs::wifiSsid) != 0;
  if (!changed) return;
  last = st; strlcpy(lastCode, net::pairingCode(), sizeof lastCode); strlcpy(lastSsid, prefs::wifiSsid, sizeof lastSsid);
  if (st == net::READY) { face.begin(); prefs::apply(face, tft); return; }     // back to the face, fresh canvas
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  // small closed eyes at the top so it still reads as Pixel
  uint16_t eye = pxRGB(prefs::eyeRGB >> 16, (prefs::eyeRGB >> 8) & 0xFF, prefs::eyeRGB & 0xFF);
  tft.fillSmoothRoundRect(96, 44, 46, 10, 5, eye, TFT_BLACK); tft.fillSmoothRoundRect(178, 44, 46, 10, 5, eye, TFT_BLACK);
  if (st == net::PROVISIONING) {
    tft.setTextColor(pxRGB(245, 179, 1), TFT_BLACK); tft.drawString("Let's get me online", SCREEN_W / 2, 96, 4);
    tft.setTextColor(pxRGB(236, 238, 245), TFT_BLACK); tft.drawString("On your phone, join the Wi-Fi", SCREEN_W / 2, 136, 2);
    tft.setTextColor(pxRGB(245, 179, 1), TFT_BLACK); tft.drawString(net::apName(), SCREEN_W / 2, 164, 4);
    tft.setTextColor(pxRGB(140, 150, 180), TFT_BLACK); tft.drawString("a setup page opens (or visit 192.168.4.1)", SCREEN_W / 2, 200, 2);
  } else if (st == net::PAIRING) {
    tft.setTextColor(pxRGB(236, 238, 245), TFT_BLACK); tft.drawString("Add me in the Pixel portal", SCREEN_W / 2, 92, 2);
    tft.drawString("with this code", SCREEN_W / 2, 112, 2);
    tft.setTextColor(pxRGB(245, 179, 1), TFT_BLACK); tft.setTextSize(2); tft.drawString(net::pairingCode(), SCREEN_W / 2, 158, 4); tft.setTextSize(1);
    tft.setTextColor(pxRGB(140, 150, 180), TFT_BLACK); tft.drawString("Profile > Add a Pixel", SCREEN_W / 2, 204, 2);
  } else if (st == net::CONNECTING_WIFI) {
    tft.setTextColor(pxRGB(140, 150, 180), TFT_BLACK); { String j = String("joining ") + prefs::wifiSsid + " ..."; while (tft.textWidth(j, 2) > SCREEN_W - 16 && j.length() > 12) j = j.substring(0, j.length() - 5) + "...";  tft.drawString(j, SCREEN_W / 2, 140, 2); }
    tft.drawString("hold BOOT 10 s to start over", SCREEN_W / 2, 204, 1);
  } else if (st == net::CONNECTING_BRAIN) {
    tft.setTextColor(pxRGB(140, 150, 180), TFT_BLACK); tft.drawString("reaching my brain ...", SCREEN_W / 2, 140, 2);
    tft.drawString(String(prefs::brainHost) + ":" + prefs::brainPort, SCREEN_W / 2, 164, 2);
  }
}

// Full-screen progress while a firmware update downloads (the face is paused; nothing else runs).
static void drawUpdateProgress(uint8_t pct, const char* stage) {
  static bool drawn = false; static uint8_t lastPct = 255;
  if (!drawn) {
    drawn = true; tft.fillScreen(TFT_BLACK); tft.setTextDatum(MC_DATUM);
    uint16_t eye = pxRGB(prefs::eyeRGB >> 16, (prefs::eyeRGB >> 8) & 0xFF, prefs::eyeRGB & 0xFF);
    tft.fillSmoothRoundRect(96, 44, 46, 10, 5, eye, TFT_BLACK); tft.fillSmoothRoundRect(178, 44, 46, 10, 5, eye, TFT_BLACK);
    tft.setTextColor(pxRGB(245, 179, 1), TFT_BLACK); tft.drawString("Updating myself", SCREEN_W / 2, 96, 4);
    tft.setTextColor(pxRGB(140, 150, 180), TFT_BLACK); tft.drawString("keep me plugged in - about a minute", SCREEN_W / 2, 200, 2);
    tft.fillSmoothRoundRect(40, 140, 240, 14, 7, pxRGB(30, 36, 54), TFT_BLACK);
  }
  if (pct != lastPct) {
    lastPct = pct;
    int w = 240 * pct / 100;
    if (w > 14) tft.fillSmoothRoundRect(40, 140, w, 14, 7, pxRGB(70, 220, 130), pxRGB(30, 36, 54));
    char b[40]; snprintf(b, sizeof b, "%s  %u%%   ", stage, pct);
    tft.setTextDatum(MC_DATUM); tft.setTextColor(pxRGB(236, 238, 245), TFT_BLACK); tft.fillRect(60, 160, 200, 20, TFT_BLACK); tft.drawString(b, SCREEN_W / 2, 170, 2);
  }
  if (!strcmp(stage, "rebooting")) drawn = false;
}

// Portal push or the on-device Update screen asked for an install: check, download, reboot (or report failure).
static void runInstall() {
  ota::Manifest m;
  if (!ota::check(m) || !ota::available()) { dbg::log("[ota] nothing to install"); return; }
  if (debugUi.active()) debugUi.exit();
  drawUpdateProgress(0, "starting");
  if (!ota::update(m)) {                       // only returns on failure
    tft.setTextDatum(MC_DATUM); tft.setTextColor(pxRGB(255, 90, 90), TFT_BLACK); tft.fillRect(0, 160, SCREEN_W, 24, TFT_BLACK);
    tft.drawString("update failed - I'll keep running this version", SCREEN_W / 2, 170, 2);
    delay(2500); face.begin(); prefs::apply(face, tft);
  }
}

static void toggleDebug() { if (debugUi.active()) { debugUi.exit(); face.begin(); prefs::apply(face, tft); } else debugUi.enter(); }

static void setLed(bool r, bool g, bool b) {
  digitalWrite(PIN_LED_R, r ? LOW : HIGH);
  digitalWrite(PIN_LED_G, g ? LOW : HIGH);
  digitalWrite(PIN_LED_B, b ? LOW : HIGH);
}

// LED gives a glanceable hint of mood (mostly useful for debugging from across the room)
static void ledForExpression(Expression e) {
  switch (e) {
    case EXPR_HAPPY: case EXPR_EXCITED: case EXPR_LOVE: setLed(0, 1, 0); break;
    case EXPR_ANNOYED: case EXPR_SUSPICIOUS:            setLed(1, 0, 0); break;
    case EXPR_THINKING: case EXPR_LISTENING:            setLed(0, 0, 1); break;
    case EXPR_SAD:                                      setLed(1, 0, 1); break;
    default:                                            setLed(0, 0, 0); break;
  }
}

static void handleSerial() {
  static char line[64]; static uint8_t len = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      line[len] = 0; len = 0;
      if (!line[0]) continue;
      if (!strncmp(line, "say ", 4)) { net::sendText(line + 4); continue; }
      char* cmd = strtok(line, " ");
      char* a1 = strtok(nullptr, " "); char* a2 = strtok(nullptr, " "); char* a3 = strtok(nullptr, " ");
      Expression e;
      if (!strcmp(cmd, "expr") && a1 && expressionFromName(a1, e)) {
        face.setExpression(e, a2 ? atof(a2) : 1.0f, a3 ? atol(a3) : 0);
        Serial.printf("ok expr=%s\n", expressionName(e));
      } else if (!strcmp(cmd, "list")) {
        for (uint8_t i = 0; i < EXPR_COUNT; i++) Serial.printf("%s%s", i ? " " : "", expressionName((Expression)i));
        Serial.println();
      } else if (!strcmp(cmd, "sleep")) { face.setExpression(EXPR_ASLEEP); Serial.println("ok sleep"); }
      else if (!strcmp(cmd, "wake")) { face.wake(); Serial.println("ok wake"); }
      else if (!strcmp(cmd, "debug")) toggleDebug();
      else if (!strcmp(cmd, "setup")) { prefs::forgetWifi(); ESP.restart(); }
      else if (!strcmp(cmd, "unpair")) { prefs::forgetToken(); ESP.restart(); }
      else if (!strcmp(cmd, "factory")) { prefs::factoryReset(); ESP.restart(); }
      else if (!strcmp(cmd, "vol") && a1) { speaker::setVolume(atof(a1)); Serial.printf("ok volume %s\n", a1); }
      else if (!strcmp(cmd, "beep")) { speaker::tone(440, 400); Serial.println("ok beep"); }
      else if (!strcmp(cmd, "update")) { ota::Manifest m; bool a = ota::check(m); Serial.printf("fw %s latest %s %s\n", ota::version(), m.version[0] ? m.version : "(none)", a ? "- installing" : "- up to date"); if (a) ota::requestInstall(); }
      else if (!strcmp(cmd, "id")) Serial.printf("device %s brain %s:%u tls %d paired %d\n", prefs::deviceId(), prefs::brainHost, prefs::brainPort, prefs::brainTls, prefs::hasToken());
      else if (!strcmp(cmd, "verbose")) { dbg::verbose = !dbg::verbose; Serial.printf("verbose %s\n", dbg::verbose ? "on" : "off"); }
      else if (!strcmp(cmd, "net")) Serial.printf("net: %s\n", net::connected() ? "connected" : "not connected");
      else if (!strcmp(cmd, "look") && a1 && a2) { face.lookAt(atof(a1), atof(a2), 1500); Serial.println("ok look"); }
      else Serial.printf("? unknown: %s\n", cmd);
    } else if (len < sizeof(line) - 1) line[len++] = c;
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\n[pixel] boot - firmware %s\n", PIXEL_FW_VERSION);

  pinMode(PIN_LED_R, OUTPUT); pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_LED_B, OUTPUT); setLed(0, 0, 0);
  pinMode(PIN_AMP_EN, OUTPUT); digitalWrite(PIN_AMP_EN, LOW);    // amp enable (speaker.cpp drives it)
  pinMode(PIN_BOOT_BTN, INPUT_PULLUP);
  pinMode(PIN_TOUCH_IRQ, INPUT);

  tft.init();
  tft.setRotation(SCREEN_ROTATION);
  uint16_t calData[5] = { 366, 3573, 257, 3590, 3 };   // vendor calibration for rotation 1
  tft.setTouch(calData);

  prefs::load();
  ota::begin(); ota::onProgress(drawUpdateProgress);
  speaker::setVolume(prefs::volume / 100.0f);
  face.begin();
  prefs::apply(face, tft);
  net::begin(face, tft);
  Serial.printf("[pixel] free heap after sprites: %u bytes\n", ESP.getFreeHeap());
}

void loop() {
  if (debugUi.active()) debugUi.update();
  else if (net::state() == net::READY) { drawStateScreen(); face.update(); }
  else drawStateScreen();
  net::loop();
  speaker::loop(); face.setTalk(speaker::level());
  ota::loop();
  if (ota::takeInstallRequest()) runInstall();
  handleSerial();

  // touch: act on press edge; re-aim gaze while held; a 2 s hold toggles settings mode
  static bool wasDown = false; static uint32_t lastTouchPoll = 0, lastDrag = 0, downSince = 0; static bool longFired = false;
  if (millis() - lastTouchPoll > 25) {
    lastTouchPoll = millis();
    uint16_t x, y;
    bool down = tft.getTouch(&x, &y, 300);
    if (down && !wasDown) {
      downSince = millis(); longFired = false;
      if (debugUi.active()) debugUi.touch(x, y); else { face.touch(x, y); if (dbg::verbose) Serial.printf("touch (%u,%u)\n", x, y); }
    } else if (down && !longFired && millis() - downSince > 2000) {
      longFired = true; toggleDebug();
    } else if (down && !debugUi.active() && millis() - lastDrag > 150) {
      lastDrag = millis(); face.lookAt((x - SCREEN_W / 2) / 160.0f, (y - 100) / 120.0f, 600);
    }
    wasDown = down;
  }

  // BOOT button: short press cycles expressions, long press (>=1.5 s) toggles settings mode
  static bool btnWas = true; static uint32_t btnDown = 0; static bool btnLong = false;
  bool btn = digitalRead(PIN_BOOT_BTN);
  if (!btn && btnWas) { btnDown = millis(); btnLong = false; }
  if (!btn && !btnLong && millis() - btnDown > 1500) { btnLong = true; toggleDebug(); }
  static bool resetDone = false;
  if (!btn && millis() - btnDown > 10000 && !resetDone) {          // held 10 s: forget Wi-Fi + pairing, back to setup
    resetDone = true; dbg::log("[pixel] factory reset requested"); prefs::factoryReset(); delay(300); ESP.restart();
  }
  if (btn) resetDone = false;
  if (btn && !btnWas && !btnLong && millis() - btnDown > 30 && !debugUi.active()) {
    Expression next = (Expression)((face.expression() + 1) % EXPR_COUNT);
    face.setExpression(next);
    Serial.printf("button -> %s\n", expressionName(next));
  }
  btnWas = btn;

  static Expression ledShown = EXPR_COUNT;
  if (face.expression() != ledShown) { ledShown = face.expression(); ledForExpression(ledShown); }

  static uint32_t frames = 0, lastReport = 0;   // frame-rate report for tuning
  frames++;
  if (millis() - lastReport > 5000) {
    debugUi.setFps(30);
    if (dbg::verbose) Serial.printf("[pixel] loop=%u/s maxframe=%ums expr=%s heap=%u\n", frames / 5, face.takeMaxFrameUs() / 1000, expressionName(face.expression()), ESP.getFreeHeap());
    frames = 0; lastReport = millis();
  }
}
