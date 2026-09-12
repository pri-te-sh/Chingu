// Display + touch HAL. Lite: TFT_eSPI drives the ST7789 directly and reads the XPT2046. 3S: TFT_eSPI is only a
// renderer into a full-screen sprite in PSRAM, which flush() pushes to the AXS15231B over QSPI (Arduino_GFX);
// touch is the AXS15231B's capacitive controller over I2C.
#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>

namespace display {
void begin();
TFT_eSPI& gfx();                             // the drawing target every screen uses
void flush();                                // present the frame (no-op on the Lite); call once per loop
bool touch(uint16_t& x, uint16_t& y);        // screen coordinates, SCREEN_ROTATION applied
void uiViewport(bool on);                    // 320x240 centred viewport for the settings/status screens (no-op on the Lite)
void blit(TFT_eSprite& s, int32_t x, int32_t y);   // draw a sprite onto the target (face eyes/mouth)
}
