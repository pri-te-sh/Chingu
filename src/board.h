// Board pin maps. One firmware, two boards: PIXEL_BOARD_LITE (Hosyond 3.2" ESP32-32E) and PIXEL_BOARD_3S
// (Waveshare ESP32-S3-Touch-LCD-3.5B). Everything display/touch/audio/battery-specific sits behind the HAL
// namespaces (display, speaker, battery); the rest of the firmware only sees SCREEN_W/H and pxRGB().
#pragma once
#include <Arduino.h>

#if defined(PIXEL_BOARD_3S)
// ---- Waveshare ESP32-S3-Touch-LCD-3.5B (pins verified against the vendor demos + schematic, docs/vendor/3s) ----
#define PIN_I2C_SDA   8            // shared: touch AXS15231B (0x3B), AXP2101 PMIC (0x34), TCA9554 (0x20), ES8311 (0x18), QMI8658, PCF85063, camera SCCB
#define PIN_I2C_SCL   7
#define PIN_LCD_CS    12           // AXS15231B over QSPI
#define PIN_LCD_CLK   5
#define PIN_LCD_D0    1
#define PIN_LCD_D1    2
#define PIN_LCD_D2    3
#define PIN_LCD_D3    4
#define PIN_LCD_BL    6
#define EXIO_LCD_RST  1            // TCA9554 pin driving the panel/touch reset
#define PIN_I2S_MCLK  44           // ES8311 codec
#define PIN_I2S_BCLK  13
#define PIN_I2S_LRCK  15
#define PIN_I2S_DOUT  16           // ESP -> codec DAC (speaker)
#define PIN_I2S_DIN   14           // codec ADC (mic) -> ESP
#define PIN_BOOT_BTN  0
#define PIN_LED_R -1
#define PIN_LED_G -1
#define PIN_LED_B -1
// This panel shows colours correctly: plain RGB565.
static inline uint16_t pxRGB(uint8_t r, uint8_t g, uint8_t b) { return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)); }
#define SCREEN_ROTATION 1
#define SCREEN_W 480
#define SCREEN_H 320

#else
// ---- Hosyond 3.2" ESP32-32E (verified against vendor schematic/demos, see docs/vendor/pack) ----
#define PIXEL_BOARD_LITE 1
// LCD (ST7789P3) + touch (XPT2046) share the SPI bus; configured via TFT_eSPI build flags in platformio.ini.
#define PIN_TOUCH_IRQ  36
// RGB LED, common anode (LOW = on)
#define PIN_LED_R 22
#define PIN_LED_G 16
#define PIN_LED_B 17
// Audio: onboard amp enable (LOW = on) and DAC output
#define PIN_AMP_EN   4
#define PIN_AUDIO_DAC 26
// Buttons
#define PIN_BOOT_BTN 0
// This ST7789P3 panel ignores the MADCTL colour-order bit and shows R and B swapped, so every colour we
// compose goes through pxRGB(), which pre-swaps. (Greys and TFT_BLACK/TFT_WHITE are unaffected.)
static inline uint16_t pxRGB(uint8_t r, uint8_t g, uint8_t b) { return (uint16_t)(((b & 0xF8) << 8) | ((g & 0xFC) << 3) | (r >> 3)); }
// Screen orientation used by the face: landscape
#define SCREEN_ROTATION 1
#define SCREEN_W 320
#define SCREEN_H 240
#endif

// The settings/status screens were laid out for 320x240; on bigger panels they are drawn centred in a viewport.
#define UI_W 320
#define UI_H 240
#define UI_X ((SCREEN_W - UI_W) / 2)
#define UI_Y ((SCREEN_H - UI_H) / 2)
