// Hosyond 3.2" ESP32-32E pin map (verified against vendor schematic/demos, see docs/vendor/pack).
#pragma once

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

// Screen orientation used by the face: landscape
#define SCREEN_ROTATION 1
#define SCREEN_W 320
#define SCREEN_H 240
