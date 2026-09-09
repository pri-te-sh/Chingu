// Pixel - Tiny AI Companion firmware. Phase 2: animated face with autonomous behaviour.
// Controls for testing: BOOT button cycles expressions; serial commands (115200):
//   expr <name> [intensity 0-1] [holdMs]   list   sleep   wake   look <x> <y>   say <text>   net   debug   verbose
// Long-press BOOT (>=1.5 s) or hold the screen 2 s: settings/diagnostics mode.
#include <Arduino.h>
#include <TFT_eSPI.h>
#include "board.h"
#include "face.h"
#include "net.h"
#include "log.h"
#include "debug_ui.h"
#include "prefs.h"

TFT_eSPI tft;
Face face(tft);
DebugUI debugUi(tft);

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
  Serial.println("\n[pixel] boot - phase 2 face");

  pinMode(PIN_LED_R, OUTPUT); pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_LED_B, OUTPUT); setLed(0, 0, 0);
  pinMode(PIN_AMP_EN, OUTPUT); digitalWrite(PIN_AMP_EN, HIGH);   // amp off until we have audio
  pinMode(PIN_BOOT_BTN, INPUT_PULLUP);
  pinMode(PIN_TOUCH_IRQ, INPUT);

  tft.init();
  tft.setRotation(SCREEN_ROTATION);
  uint16_t calData[5] = { 366, 3573, 257, 3590, 3 };   // vendor calibration for rotation 1
  tft.setTouch(calData);

  prefs::load();
  face.begin();
  prefs::apply(face, tft);
  net::begin(face, tft);
  Serial.printf("[pixel] free heap after sprites: %u bytes\n", ESP.getFreeHeap());
}

void loop() {
  if (debugUi.active()) debugUi.update(); else face.update();
  net::loop();
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
