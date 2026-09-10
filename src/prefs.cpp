#include "prefs.h"
#include <Preferences.h>
#include <WiFi.h>

// Dev convenience: if include/secrets.h exists it seeds NVS once (first boot only); users never need it.
#if __has_include("secrets.h")
#include "secrets.h"
#define HAVE_SECRETS 1
#else
#define HAVE_SECRETS 0
#endif
#ifndef PIXEL_DEFAULT_BRAIN_HOST
#define PIXEL_DEFAULT_BRAIN_HOST "pixel.local"
#define PIXEL_DEFAULT_BRAIN_PORT 8765
#define PIXEL_DEFAULT_BRAIN_TLS 0
#endif

namespace prefs {
char name[24] = "Pixel";
uint32_t eyeRGB = 0xEBE128;
uint16_t autoSleepS = 45;
char moods[160] = "";
char wifiSsid[33] = "", wifiPass[65] = "";
char brainHost[96] = PIXEL_DEFAULT_BRAIN_HOST;
uint16_t brainPort = PIXEL_DEFAULT_BRAIN_PORT;
bool brainTls = PIXEL_DEFAULT_BRAIN_TLS;
char token[64] = "";
static Preferences p;
static char devId[20] = "";

static void getStr(const char* key, char* dst, size_t n) { String v = p.getString(key, dst); strlcpy(dst, v.c_str(), n); }

void load() {
  p.begin("pixel", true);
  getStr("name", name, sizeof name);
  eyeRGB = p.getUInt("eye", eyeRGB);
  autoSleepS = p.getUShort("sleep", autoSleepS);
  getStr("moods", moods, sizeof moods);
  getStr("ssid", wifiSsid, sizeof wifiSsid); getStr("pass", wifiPass, sizeof wifiPass);
  getStr("host", brainHost, sizeof brainHost);
  brainPort = p.getUShort("port", brainPort); brainTls = p.getBool("tls", brainTls);
  getStr("token", token, sizeof token);
  bool seeded = p.getBool("seeded", false);
  uint8_t schema = p.getUChar("schema", 1);
  p.end();
  // schema 2 (2026-09-10): the brain moved from a laptop IP to pixel.priteshbhavsar.com; boards still pointing at a
  // private LAN address are moved to the compiled-in production default once (Wi-Fi and pairing untouched).
  if (schema < 2) {
    if (strncmp(brainHost, "192.168.", 8) == 0 || strncmp(brainHost, "10.", 3) == 0 || strncmp(brainHost, "172.", 4) == 0) {
      strlcpy(brainHost, PIXEL_DEFAULT_BRAIN_HOST, sizeof brainHost); brainPort = PIXEL_DEFAULT_BRAIN_PORT; brainTls = PIXEL_DEFAULT_BRAIN_TLS;
    }
    p.begin("pixel", false); p.putString("host", brainHost); p.putUShort("port", brainPort); p.putBool("tls", brainTls); p.putUChar("schema", 2); p.end();
  }
#if HAVE_SECRETS
  if (!seeded && !wifiSsid[0]) {           // one-time seed from the developer's secrets.h
    strlcpy(wifiSsid, WIFI_SSID, sizeof wifiSsid); strlcpy(wifiPass, WIFI_PASSWORD, sizeof wifiPass);
    strlcpy(brainHost, BACKEND_HOST, sizeof brainHost); brainPort = BACKEND_PORT; brainTls = BACKEND_TLS;
    p.begin("pixel", false); p.putBool("seeded", true); p.end();
    save();
  }
#endif
}

void save() {
  p.begin("pixel", false);
  p.putString("name", name); p.putUInt("eye", eyeRGB); p.putUShort("sleep", autoSleepS); p.putString("moods", moods);
  p.putString("ssid", wifiSsid); p.putString("pass", wifiPass);
  p.putString("host", brainHost); p.putUShort("port", brainPort); p.putBool("tls", brainTls);
  p.putString("token", token);
  p.end();
}

void factoryReset() {
  p.begin("pixel", false); p.clear(); p.putBool("seeded", true); p.end();   // a reset board is "seeded": secrets.h must not refill it
  wifiSsid[0] = wifiPass[0] = token[0] = 0;
}

void forgetWifi() {
  wifiSsid[0] = wifiPass[0] = 0; save();
}

void forgetToken() { token[0] = 0; save(); }

bool hasWifi() { return wifiSsid[0] != 0; }
bool hasToken() { return token[0] != 0; }

const char* deviceId() {
  if (!devId[0]) {
    uint8_t mac[6]; WiFi.macAddress(mac);
    snprintf(devId, sizeof devId, "lite-%02x%02x%02x", mac[3], mac[4], mac[5]);
  }
  return devId;
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
  char buf[160]; strlcpy(buf, moods, sizeof buf);
  for (char* tok = strtok(buf, ","); tok; tok = strtok(nullptr, ",")) {
    char* eq = strchr(tok, '='); if (!eq) continue; *eq = 0;
    Expression e; if (!expressionFromName(tok, e)) continue;
    face.setMoodColor(e, eq[1] == '-' ? 0 : strtoul(eq + 1, nullptr, 16));
  }
}
}
