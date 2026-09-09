// Everything the device remembers across reboots (NVS): Wi-Fi, brain address, its pairing token, and the
// settings the portal pushes (name, eyes, moods, sleep). Nothing here is baked into the firmware image.
#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>
#include "face.h"

namespace prefs {
// pushed settings
extern char name[24];
extern uint32_t eyeRGB;          // 0xRRGGBB
extern uint16_t autoSleepS;
extern char moods[160];          // "love=FF6AD5,annoyed=FF4A4A,..." ("-" = tint off)
// provisioning
extern char wifiSsid[33], wifiPass[65];
extern char brainHost[96];
extern uint16_t brainPort;
extern bool brainTls;
extern char token[64];           // per-device token issued when paired ("" = unpaired)

void load();
void save();
void factoryReset();             // wipes Wi-Fi, brain and token (keeps nothing)
bool hasWifi();
bool hasToken();
void apply(Face& face, TFT_eSPI& tft);
bool setFromHex(const char* hex);
bool parseHex(const char* hex, uint32_t& out);
const char* deviceId();          // stable "lite-xxxxxx" from the Wi-Fi MAC
}
