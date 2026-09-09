#include "prefs.h"
#include <Preferences.h>

namespace prefs {
char name[24] = "Pixel";
uint32_t eyeRGB = 0xEBE128;
uint16_t autoSleepS = 45;
char moods[160] = "";
static Preferences p;

void load() {
  p.begin("pixel", true);
  String n = p.getString("name", name);
  strlcpy(name, n.c_str(), sizeof name);
  eyeRGB = p.getUInt("eye", eyeRGB);
  autoSleepS = p.getUShort("sleep", autoSleepS);
  String m = p.getString("moods", moods); strlcpy(moods, m.c_str(), sizeof moods);
  p.end();
}

void save() {
  p.begin("pixel", false);
  p.putString("name", name); p.putUInt("eye", eyeRGB); p.putUShort("sleep", autoSleepS); p.putString("moods", moods);
  p.end();
}

bool parseHex(const char* hex, uint32_t& out) {
  if (!hex || hex[0] != '#' || strlen(hex) != 7) return false;
  out = strtoul(hex + 1, nullptr, 16);
  return true;
}
bool setFromHex(const char* hex) { return parseHex(hex, eyeRGB); }

void apply(Face& face, TFT_eSPI&) {
  face.setEyeColor(eyeRGB);
  face.setAutoSleep((uint32_t)autoSleepS * 1000);
  // moods: "name=RRGGBB,name=-,..."
  char buf[160]; strlcpy(buf, moods, sizeof buf);
  for (char* tok = strtok(buf, ","); tok; tok = strtok(nullptr, ",")) {
    char* eq = strchr(tok, '='); if (!eq) continue; *eq = 0;
    Expression e; if (!expressionFromName(tok, e)) continue;
    face.setMoodColor(e, eq[1] == '-' ? 0 : strtoul(eq + 1, nullptr, 16));
  }
}
}
