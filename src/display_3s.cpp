#include "board.h"
#ifdef PIXEL_BOARD_3S
#include "display.h"
#include "log.h"
#include <Wire.h>
#include <Arduino_GFX_Library.h>

namespace display {
static TFT_eSPI dummy_;                       // never initialised: TFT_eSprite needs a parent for fonts/colour helpers
static TFT_eSprite frame_(&dummy_);           // 480x320x16 = 300 KB, lives in PSRAM
static TFT_eSprite uiSpr_(&dummy_);           // 320x240 settings/status layer (TFT_eSPI's large fonts misrender inside sprite viewports, so no viewport)
static Arduino_DataBus* bus_ = nullptr;
static Arduino_AXS15231B* panel_ = nullptr;
static bool ok_ = false;
static uint16_t* portrait_ = nullptr;         // 320x480 rotated copy for the panel (PSRAM)
static bool flip_ = false;                    // false: USB-side down when held landscape one way; 'flip' serial command swaps 180°
static uint32_t flushMs_ = 0;
static bool uiOn_ = false;

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
  panel_ = new Arduino_AXS15231B(bus_, GFX_NOT_DEFINED /* RST via TCA */, 0 /* the AXS15231B ignores MADCTL MV: we rotate in software */, false, 320, 480);
  ok_ = panel_->begin();
  if (!ok_) dbg::log("[disp] AXS15231B begin failed");
  panel_->fillScreen(0);
  pinMode(PIN_LCD_BL, OUTPUT); digitalWrite(PIN_LCD_BL, HIGH);
  frame_.setColorDepth(16);
  if (!frame_.createSprite(SCREEN_W, SCREEN_H)) dbg::log("[disp] framebuffer alloc failed (PSRAM?)");
  frame_.fillSprite(TFT_BLACK);
  frame_.setTextFont(2);
  uiSpr_.setColorDepth(16);
  if (!uiSpr_.createSprite(UI_W, UI_H)) dbg::log("[disp] ui sprite alloc failed");
  uiSpr_.fillSprite(TFT_BLACK); uiSpr_.setTextFont(2);
  portrait_ = (uint16_t*)heap_caps_malloc(320 * 480 * 2, MALLOC_CAP_SPIRAM);
  if (!portrait_) dbg::log("[disp] portrait buffer alloc failed");
  dbg::log("[disp] %dx%d framebuffer, psram %u free", SCREEN_W, SCREEN_H, ESP.getFreePsram());
}

TFT_eSPI& gfx() { return frame_; }
TFT_eSPI& ui() { return uiSpr_; }

void flush() {
  if (!ok_ || !portrait_) return;
  uint32_t t0 = millis();
  if (uiOn_) uiSpr_.pushToSprite(&frame_, UI_X, UI_Y);
  const uint16_t* src = (const uint16_t*)frame_.getPointer();
  // landscape (x: 0..479, y: 0..319) -> portrait panel (px: 0..319, py: 0..479). 8-row blocks keep PSRAM reads sequential.
  for (int y0 = 0; y0 < SCREEN_H; y0 += 8) {
    for (int x = 0; x < SCREEN_W; x++) {
      if (!flip_) { uint16_t* d = portrait_ + x * 320 + (319 - y0 - 7); for (int k = 7; k >= 0; k--) *d++ = src[(y0 + k) * SCREEN_W + x]; }
      else        { uint16_t* d = portrait_ + (479 - x) * 320 + y0;     for (int k = 0; k < 8; k++)  *d++ = src[(y0 + k) * SCREEN_W + x]; }
    }
  }
  panel_->draw16bitBeRGBBitmap(0, 0, portrait_, 320, 480);   // TFT_eSPI sprites hold big-endian RGB565 (ready for SPI); don't swap again
  flushMs_ = millis() - t0;
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
  // landscape mapping, same transform as flush()
  if (!flip_) { lx = ry; ly = 319 - rx; } else { lx = 479 - ry; ly = rx; }
  x = lx; y = ly; lastDown = true;
  return true;
}

void uiViewport(bool on) {
  if (on && !uiOn_) frame_.fillSprite(TFT_BLACK);   // black margins around the UI layer
  uiOn_ = on;
}

void blit(TFT_eSprite& s, int32_t x, int32_t y) { s.pushToSprite(&frame_, x, y); }

void selfTest(int mode) {
  static bool bl = true;
  switch (mode) {
    case 1: panel_->fillScreen(0xF800); dbg::log("[disp] test1: panel red (ok=%d)", ok_); break;
    case 2: frame_.resetViewport(); for (int i = 0; i < 4; i++) frame_.fillRect(i * SCREEN_W / 4, 0, SCREEN_W / 4, SCREEN_H, i == 0 ? TFT_RED : i == 1 ? TFT_GREEN : i == 2 ? TFT_BLUE : TFT_WHITE);
            frame_.setTextColor(TFT_BLACK, TFT_WHITE); frame_.setTextDatum(MC_DATUM); frame_.drawString("PIXEL 3S", SCREEN_W * 7 / 8, SCREEN_H / 2, 4); flush(); dbg::log("[disp] test2: framebuffer bars flushed"); break;
    case 3: bl = !bl; digitalWrite(PIN_LCD_BL, bl ? HIGH : LOW); dbg::log("[disp] test3: backlight %d", bl); break;
    case 4: panel_->setRotation(0); panel_->fillScreen(0x07E0); panel_->fillRect(20, 20, 100, 200, 0x001F); dbg::log("[disp] test4: rotation 0, green + blue block"); break;
    case 5: flip_ = !flip_; dbg::log("[disp] flipped %d", flip_); break;
    case 7: dbg::log("[disp] last flush %lu ms, ui=%d", flushMs_, uiOn_); break;
    case 8: { uiSpr_.setTextDatum(MC_DATUM); uiSpr_.setTextColor(TFT_WHITE, TFT_BLACK); uiSpr_.fillRect(0, 0, UI_W, 30, TFT_BLACK); uiSpr_.drawString("Let's get me online", UI_W / 2, 14, 4); dbg::log("[disp] test8: headline on the ui layer"); break; }
    case 6: { uint16_t* row = (uint16_t*)malloc(480 * 2); for (int i = 0; i < 480; i++) row[i] = 0xFFE0; panel_->setRotation(1); for (int y = 0; y < 320; y += 2) panel_->draw16bitRGBBitmap(0, y, row, 480, 1); free(row); dbg::log("[disp] test6: yellow stripes via bitmap"); break; }
  }
}
}
#endif
