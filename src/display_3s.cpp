#include "board.h"
#ifdef PIXEL_BOARD_3S
#include "display.h"
#include "log.h"
#include <Wire.h>
#include <Arduino_GFX_Library.h>

namespace display {
static TFT_eSPI dummy_;                       // never initialised: TFT_eSprite needs a parent for fonts/colour helpers
static TFT_eSprite frame_(&dummy_);           // 480x320x16 = 300 KB, lives in PSRAM
static Arduino_DataBus* bus_ = nullptr;
static Arduino_AXS15231B* panel_ = nullptr;
static bool ok_ = false;

// --- TCA9554 I/O expander (0x20): the panel's reset line hangs off EXIO1 ---
static void tcaWrite(uint8_t reg, uint8_t v) { Wire.beginTransmission(0x20); Wire.write(reg); Wire.write(v); Wire.endTransmission(); }
static void panelReset() {
  tcaWrite(0x03, (uint8_t)~(1 << EXIO_LCD_RST));   // config: EXIO1 output, others input
  tcaWrite(0x01, 1 << EXIO_LCD_RST); delay(10);
  tcaWrite(0x01, 0); delay(10);
  tcaWrite(0x01, 1 << EXIO_LCD_RST); delay(200);
}

void begin() {
  panelReset();                               // Wire is begun in setup() (PMIC first, so the camera rails are up before anything shares the bus)
  bus_ = new Arduino_ESP32QSPI(PIN_LCD_CS, PIN_LCD_CLK, PIN_LCD_D0, PIN_LCD_D1, PIN_LCD_D2, PIN_LCD_D3);
  panel_ = new Arduino_AXS15231B(bus_, GFX_NOT_DEFINED /* RST via TCA */, SCREEN_ROTATION, false, 320, 480);
  ok_ = panel_->begin();
  if (!ok_) dbg::log("[disp] AXS15231B begin failed");
  panel_->fillScreen(0);
  pinMode(PIN_LCD_BL, OUTPUT); digitalWrite(PIN_LCD_BL, HIGH);
  frame_.setColorDepth(16);
  if (!frame_.createSprite(SCREEN_W, SCREEN_H)) dbg::log("[disp] framebuffer alloc failed (PSRAM?)");
  frame_.fillSprite(TFT_BLACK);
  frame_.setTextFont(2);
  dbg::log("[disp] %dx%d framebuffer, psram %u free", SCREEN_W, SCREEN_H, ESP.getFreePsram());
}

TFT_eSPI& gfx() { return frame_; }

void flush() {
  if (!ok_) return;
  frame_.resetViewport();   // push the whole frame regardless of the current UI viewport
  panel_->draw16bitRGBBitmap(0, 0, (uint16_t*)frame_.getPointer(), SCREEN_W, SCREEN_H);
}

// --- AXS15231B capacitive touch (0x3B); protocol from the vendor esp_lcd_touch_axs15231b driver ---
bool touch(uint16_t& x, uint16_t& y) {
  static uint32_t last = 0; static bool lastDown = false; static uint16_t lx = 0, ly = 0;
  if (millis() - last < 20) { x = lx; y = ly; return lastDown; }
  last = millis();
  const uint8_t cmd[11] = {0xb5, 0xab, 0xa5, 0x5a, 0x00, 0x00, 0x00, 0x0e, 0x00, 0x00, 0x00};
  uint8_t d[14] = {0};
  Wire.beginTransmission(0x3B); Wire.write(cmd, sizeof cmd);
  if (Wire.endTransmission() != 0) { lastDown = false; return false; }
  if (Wire.requestFrom((uint8_t)0x3B, (uint8_t)14) != 14) { lastDown = false; return false; }
  Wire.readBytes(d, 14);
  uint8_t n = d[1];
  if (d[0] == 0xff || n == 0 || n > 2) { lastDown = false; return false; }
  uint16_t rx = ((d[2] & 0x0F) << 8) | d[3], ry = ((d[4] & 0x0F) << 8) | d[5];   // native portrait 320x480
  if (rx >= 320 || ry >= 480) { lastDown = false; return false; }
  // rotation 1 (landscape 480x320): x along the long side
  lx = ry; ly = 319 - rx;
  x = lx; y = ly; lastDown = true;
  return true;
}

void uiViewport(bool on) {
  if (on) frame_.setViewport(UI_X, UI_Y, UI_W, UI_H);
  else frame_.resetViewport();
}

void blit(TFT_eSprite& s, int32_t x, int32_t y) { s.pushToSprite(&frame_, x, y); }
}
#endif
