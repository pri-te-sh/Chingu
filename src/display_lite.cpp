#include "board.h"
#ifdef PIXEL_BOARD_LITE
#include "display.h"

namespace display {
static TFT_eSPI tft_;

void begin() {
  tft_.init();
  tft_.setRotation(SCREEN_ROTATION);
  uint16_t calData[5] = { 366, 3573, 257, 3590, 3 };   // vendor calibration for rotation 1
  tft_.setTouch(calData);
}
TFT_eSPI& gfx() { return tft_; }
TFT_eSPI& ui() { return tft_; }
void flush() {}
bool touch(uint16_t& x, uint16_t& y) { return tft_.getTouch(&x, &y, 300); }
void uiViewport(bool) {}
void blit(TFT_eSprite& s, int32_t x, int32_t y) { s.pushSprite(x, y); }
void selfTest(int) {}
}
#endif
