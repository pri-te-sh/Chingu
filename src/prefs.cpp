#include "prefs.h"
#include <Preferences.h>

namespace prefs {
char name[24] = "Pixel";
uint32_t eyeRGB = 0xEBE128;
uint16_t autoSleepS = 45;
static Preferences p;

void load() {
  p.begin("pixel", true);
  String n = p.getString("name", name);
  strlcpy(name, n.c_str(), sizeof name);
  eyeRGB = p.getUInt("eye", eyeRGB);
  autoSleepS = p.getUShort("sleep", autoSleepS);
  p.end();
}

void save() {
  p.begin("pixel", false);
  p.putString("name", name); p.putUInt("eye", eyeRGB); p.putUShort("sleep", autoSleepS);
  p.end();
}

bool setFromHex(const char* hex) {
  if (!hex || hex[0] != '#' || strlen(hex) != 7) return false;
  eyeRGB = strtoul(hex + 1, nullptr, 16);
  return true;
}

void apply(Face& face, TFT_eSPI& tft) {
  face.setEyeColor(tft.color565(eyeRGB >> 16, (eyeRGB >> 8) & 0xFF, eyeRGB & 0xFF));
  face.setAutoSleep((uint32_t)autoSleepS * 1000);
}
}
