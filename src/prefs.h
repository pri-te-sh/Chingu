// Settings pushed from the portal (via the brain) and persisted in NVS so they survive reboots offline.
#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>
#include "face.h"

namespace prefs {
extern char name[24];
extern uint32_t eyeRGB;          // 0xRRGGBB
extern uint16_t autoSleepS;
void load();
void save();
void apply(Face& face, TFT_eSPI& tft);
bool setFromHex(const char* hex);   // "#RRGGBB" -> eyeRGB
bool parseHex(const char* hex, uint32_t& out);
extern char moods[160];             // "love=FF6AD5,annoyed=FF4A4A,..." ("-" = tint off)
}
