// Display + touch HAL. Lite: TFT_eSPI drives the ST7789 directly and reads the XPT2046. 3S: TFT_eSPI is only a
// renderer into a full-screen sprite in PSRAM, which flush() pushes to the AXS15231B over QSPI (Arduino_GFX);
// touch is the AXS15231B's capacitive controller over I2C.
#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>

namespace display {
void begin();
TFT_eSPI& gfx();                             // full-screen target (the face)
TFT_eSPI& ui();                              // 320x240 target for the settings/status screens (Lite: same as gfx(); 3S: own sprite composited centred)
void flush();                                // present the frame (no-op on the Lite); call once per loop
bool touch(uint16_t& x, uint16_t& y);        // screen coordinates, SCREEN_ROTATION applied
void uiViewport(bool on);                    // show the UI layer (3S: composited over a cleared frame); no-op on the Lite
void blit(TFT_eSprite& s, int32_t x, int32_t y);   // draw a sprite onto the target (face eyes/mouth)
void selfTest(int mode);                     // diagnostics: 1 panel fill red (bypass framebuffer), 2 framebuffer bars + flush, 3 backlight toggle, 4 rotation 0 push
}
