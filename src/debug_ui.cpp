#include "debug_ui.h"
#include "board.h"
#include "log.h"
#include "net.h"
#include "prefs.h"
#include "ota.h"
#include "speaker.h"
#include "battery.h"
#include "speedtest.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <qrcode.h>

// ---- palette (true RGB now that TFT_RGB_ORDER is set) ----
#define RGB(r, g, b) pxRGB((r), (g), (b))
static const uint16_t BG = RGB(8, 10, 16), SURF = RGB(20, 24, 36), CARD = RGB(30, 36, 54), EDGE = RGB(52, 60, 84);
static const uint16_t AMBER = RGB(255, 196, 0), CYAN = RGB(0, 205, 255), GREEN = RGB(70, 220, 130), RED = RGB(255, 90, 90);
static const uint16_t TXT = RGB(236, 238, 245), MUTED = RGB(128, 138, 160), INK = RGB(20, 20, 24);
static const int HDR = 34;

static const char* PRESETS[5] = {
  "Hey Pixel, I'm leaving for work now.",
  "I skipped the gym again today.",
  "Tell me a fun fact in one sentence.",
  "What should I cook for dinner tonight?",
  "How are you feeling today, Pixel?",
};

// ---- menu tree ----
static const DebugUI::Tile MAIN[6] = {
  {"Connectivity", DebugUI::IC_WIFI, DebugUI::CONNECTIVITY}, {"Sound", DebugUI::IC_SPEAKER, DebugUI::SOUND}, {"Power", DebugUI::IC_BATTERY, DebugUI::POWER},
  {"Pixel", DebugUI::IC_CHIP, DebugUI::PIXEL}, {"Diagnostics", DebugUI::IC_CHAT, DebugUI::DIAGNOSTICS}, {"Face", DebugUI::IC_FACE, DebugUI::MENU}};
static const DebugUI::Tile CONN[2] = {{"Wi-Fi", DebugUI::IC_WIFI, DebugUI::WIFI}, {"Network", DebugUI::IC_GLOBE, DebugUI::NETWORK}};
static const DebugUI::Tile PIX[4] = {{"Portal", DebugUI::IC_QR, DebugUI::PORTAL}, {"Update", DebugUI::IC_DOWNLOAD, DebugUI::UPDATE},
                                     {"System", DebugUI::IC_GEAR, DebugUI::SYSTEM}, {"Reset", DebugUI::IC_RESET, DebugUI::RESET}};
static const DebugUI::Tile DIAG[2] = {{"Pipeline", DebugUI::IC_CHAT, DebugUI::PIPELINE}, {"Event log", DebugUI::IC_LOG, DebugUI::LOG}};

static DebugUI::Screen parentOf(DebugUI::Screen s) {
  switch (s) {
    case DebugUI::WIFI: case DebugUI::NETWORK: return DebugUI::CONNECTIVITY;
    case DebugUI::PORTAL: case DebugUI::UPDATE: case DebugUI::SYSTEM: case DebugUI::RESET: return DebugUI::PIXEL;
    case DebugUI::PIPELINE: case DebugUI::LOG: return DebugUI::DIAGNOSTICS;
    default: return DebugUI::MENU;
  }
}
static const char* titleOf(DebugUI::Screen s) {
  switch (s) {
    case DebugUI::CONNECTIVITY: return "Connectivity"; case DebugUI::PIXEL: return "Pixel"; case DebugUI::DIAGNOSTICS: return "Diagnostics";
    case DebugUI::WIFI: return "Wi-Fi"; case DebugUI::NETWORK: return "Network"; case DebugUI::SOUND: return "Sound"; case DebugUI::POWER: return "Power";
    case DebugUI::PORTAL: return "Portal"; case DebugUI::UPDATE: return "Update"; case DebugUI::SYSTEM: return "System"; case DebugUI::RESET: return "Reset";
    case DebugUI::PIPELINE: return "Pipeline test"; case DebugUI::LOG: return "Event log"; default: return prefs::name;
  }
}

static const DebugUI::Rect BACK{6, 5, 44, HDR - 10};
static uint16_t latColor(uint32_t ms) { return ms < 60 ? GREEN : ms < 200 ? AMBER : RED; }
static const char* rssiLabel(int r) { return r > -55 ? "excellent" : r > -65 ? "good" : r > -75 ? "fair" : "weak"; }

// =========================================================== widget kit
void DebugUI::header(Screen s) {
  for (int y = 0; y < HDR; y++) {                       // subtle vertical gradient
    uint8_t v = 22 + (HDR - y) / 3;
    tft_.drawFastHLine(0, y, SCREEN_W, RGB(v, v + 4, v + 14));
  }
  tft_.drawFastHLine(0, HDR, SCREEN_W, EDGE);
  uint16_t hbg = RGB(27, 31, 41);
  int x = 12;
  if (s != MENU) { pill(BACK, "<", CARD, AMBER, 4); x = BACK.x + BACK.w + 10; }
  tft_.setTextDatum(ML_DATUM);
  Screen p = parentOf(s);
  if (p != MENU) {                                      // breadcrumb: "Connectivity > Wi-Fi"
    tft_.setTextColor(MUTED, hbg); tft_.drawString(titleOf(p), x, HDR / 2, 2); x += tft_.textWidth(titleOf(p), 2) + 6;
    tft_.setTextColor(EDGE, hbg); tft_.drawString(">", x, HDR / 2, 2); x += 12;
    tft_.setTextColor(TXT, hbg); tft_.drawString(titleOf(s), x, HDR / 2, 2);
  } else { tft_.setTextColor(TXT, hbg); tft_.drawString(titleOf(s), x, HDR / 2, 4); }
  lastChips_ = 0; statusChips();
}

void DebugUI::statusChips() {                           // top-right: wifi bars + brain dot, refreshed live
  if (millis() - lastChips_ < 1000) return;
  lastChips_ = millis();
  int x = SCREEN_W - 12, y = HDR / 2;
  uint16_t bg = RGB(24, 28, 38);
  tft_.fillRect(SCREEN_W - 90, 2, 88, HDR - 4, bg);
  bool brain = net::connected();
  tft_.fillSmoothCircle(x - 6, y, 5, brain ? GREEN : RED, bg);
  int rssi = net::wifiUp() ? WiFi.RSSI() : -100;
  int bars = rssi > -55 ? 4 : rssi > -65 ? 3 : rssi > -75 ? 2 : rssi > -90 ? 1 : 0;
  for (int i = 0; i < 4; i++) {
    int h = 4 + i * 3, bx = x - 40 + i * 6;
    tft_.fillRect(bx, y + 6 - h, 4, h, i < bars ? (bars >= 3 ? GREEN : bars == 2 ? AMBER : RED) : EDGE);
  }
  if (battery::millivolts() >= 2500) {                   // tiny battery glyph
    uint8_t pct = battery::percent(); battery::State st = battery::state();
    uint16_t c = st == battery::ST_CRITICAL ? RED : st == battery::ST_LOW ? AMBER : st == battery::ST_CHARGING ? CYAN : GREEN;
    int bx = x - 78, by = y - 5;
    tft_.drawRect(bx, by, 14, 10, c); tft_.fillRect(bx + 14, by + 3, 2, 4, c);
    int w = pct * 10 / 100; if (w > 0) tft_.fillRect(bx + 2, by + 2, w, 6, c);
  }
}

void DebugUI::card(const Rect& r, uint16_t fill) {
  tft_.fillSmoothRoundRect(r.x, r.y, r.w, r.h, 8, fill, BG);
  tft_.drawSmoothRoundRect(r.x, r.y, 8, 7, r.w, r.h, EDGE, BG);
}

void DebugUI::pill(const Rect& r, const char* label, uint16_t fill, uint16_t text, int font) {
  tft_.fillSmoothRoundRect(r.x, r.y, r.w, r.h, r.h / 2, fill, BG);
  tft_.setTextDatum(MC_DATUM); tft_.setTextColor(text, fill);
  tft_.drawString(label, r.x + r.w / 2, r.y + r.h / 2, font);
}

const char* DebugUI::fit(const char* text, int w, int font) {
  static char buf[96];
  strlcpy(buf, text, sizeof buf);
  if (tft_.textWidth(buf, font) <= w) return buf;
  size_t n = strlen(buf);
  while (n > 1) { buf[--n] = 0; strcpy(buf + n, "..."); if (tft_.textWidth(buf, font) <= w) break; buf[n] = 0; }
  return buf;
}

void DebugUI::kv(int x, int y, int w, const char* key, const char* val, uint16_t valColor) {
  tft_.fillRect(x, y, w, 18, BG);
  tft_.setTextDatum(TL_DATUM);
  tft_.setTextColor(MUTED, BG); tft_.drawString(key, x, y + 1, 2);
  tft_.setTextColor(valColor, BG); tft_.drawString(fit(val, w - 76, 2), x + 76, y + 1, 2);
}

void DebugUI::hbar(int x, int y, int w, int h, float frac, uint16_t color) {
  frac = constrain(frac, 0.0f, 1.0f);
  tft_.fillSmoothRoundRect(x, y, w, h, h / 2, CARD, BG);
  int fw = (int)(w * frac);
  if (fw > h) tft_.fillSmoothRoundRect(x, y, fw, h, h / 2, color, CARD);
  else if (fw > 0) tft_.fillSmoothCircle(x + h / 2, y + h / 2, h / 2, color, CARD);
}

void DebugUI::gauge(int cx, int cy, int r, float frac, uint16_t color, const char* big, const char* small) {
  frac = constrain(frac, 0.0f, 1.0f);
  tft_.drawSmoothArc(cx, cy, r, r - 7, 45, 315, CARD, BG, true);
  int end = 45 + (int)(270 * frac);
  if (end > 46) tft_.drawSmoothArc(cx, cy, r, r - 7, 45, end, color, BG, true);
  tft_.setTextDatum(MC_DATUM); tft_.setTextColor(TXT, BG); tft_.drawString(big, cx, cy - 4, 4);
  tft_.setTextColor(MUTED, BG); tft_.drawString(small, cx, cy + r - 4, 1);
}

void DebugUI::wrap(int x, int y, int w, const char* text, int font, int maxLines, uint16_t color) {
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(color, tft_.readPixel(x, y));
  char lineBuf[80]; int lines = 0; const char* p = text;
  while (*p && lines < maxLines) {
    const char* q = p; char trial[80]; size_t len = 0; lineBuf[0] = 0;
    while (*q) {
      const char* e = q; while (*e && *e != ' ') e++;
      size_t wl = e - q;
      if (len + wl + (len ? 1 : 0) >= sizeof trial - 1) break;
      snprintf(trial, sizeof trial, "%s%s%.*s", lineBuf, len ? " " : "", (int)wl, q);
      if (tft_.textWidth(trial, font) > w && len) break;
      strcpy(lineBuf, trial); len = strlen(lineBuf);
      q = e; while (*q == ' ') q++;
      if (tft_.textWidth(lineBuf, font) > w) break;
    }
    if (!len) break;
    tft_.drawString(lineBuf, x, y + lines * 17, font);
    lines++; p = q;
  }
}

// icons are ~40 px, drawn from primitives
void DebugUI::iconWifi(int cx, int cy, int rssi, uint16_t off) {
  int bars = rssi > -55 ? 3 : rssi > -65 ? 2 : rssi > -75 ? 1 : 0;
  for (int i = 0; i < 3; i++) {
    int r = 8 + i * 7;
    tft_.drawSmoothArc(cx, cy + 8, r, r - 3, 135, 225, i < bars ? GREEN : off, CARD, true);
  }
  tft_.fillSmoothCircle(cx, cy + 8, 3, bars ? GREEN : off, CARD);
}
void DebugUI::iconGlobe(int cx, int cy, uint16_t c) {
  tft_.drawSmoothCircle(cx, cy, 16, c, CARD);
  tft_.drawEllipse(cx, cy, 7, 16, c);
  tft_.drawFastHLine(cx - 16, cy, 33, c);
  tft_.drawFastHLine(cx - 13, cy - 8, 27, c); tft_.drawFastHLine(cx - 13, cy + 8, 27, c);
}
void DebugUI::iconChat(int cx, int cy, uint16_t c) {
  tft_.fillSmoothRoundRect(cx - 18, cy - 14, 36, 24, 7, c, CARD);
  tft_.fillTriangle(cx - 8, cy + 9, cx - 2, cy + 9, cx - 10, cy + 16, c);
  for (int i = 0; i < 3; i++) tft_.fillSmoothCircle(cx - 8 + i * 8, cy - 2, 2, CARD, c);
}
void DebugUI::iconLog(int cx, int cy, uint16_t c) {
  const int w[4] = {30, 22, 30, 16};
  for (int i = 0; i < 4; i++) tft_.fillSmoothRoundRect(cx - 15, cy - 14 + i * 8, w[i], 4, 2, i == 3 ? AMBER : c, CARD);
}
void DebugUI::iconChip(int cx, int cy, uint16_t c) {
  tft_.fillSmoothRoundRect(cx - 12, cy - 12, 24, 24, 4, c, CARD);
  tft_.fillRect(cx - 6, cy - 6, 12, 12, CARD);
  for (int i = -1; i <= 1; i++) {
    tft_.fillRect(cx - 17, cy + i * 7 - 1, 5, 3, c); tft_.fillRect(cx + 12, cy + i * 7 - 1, 5, 3, c);
    tft_.fillRect(cx + i * 7 - 1, cy - 17, 3, 5, c); tft_.fillRect(cx + i * 7 - 1, cy + 12, 3, 5, c);
  }
}
void DebugUI::iconQr(int cx, int cy, uint16_t c) {
  auto finder = [&](int x, int y) { tft_.fillRect(x, y, 12, 12, c); tft_.fillRect(x + 2, y + 2, 8, 8, CARD); tft_.fillRect(x + 4, y + 4, 4, 4, c); };
  finder(cx - 16, cy - 16); finder(cx + 4, cy - 16); finder(cx - 16, cy + 4);
  const uint8_t m[4][4] = {{1,0,1,1},{0,1,1,0},{1,1,0,1},{1,0,1,0}};
  for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) if (m[y][x]) tft_.fillRect(cx + 4 + x * 3, cy + 4 + y * 3, 3, 3, c);
}
void DebugUI::iconGear(int cx, int cy, uint16_t c) {
  tft_.drawSmoothArc(cx, cy, 15, 9, 0, 360, c, CARD, false);
  for (int i = 0; i < 8; i++) { float a = i * PI / 4; tft_.fillSmoothCircle(cx + cosf(a) * 15, cy + sinf(a) * 15, 3, c, CARD); }
}
void DebugUI::iconDownload(int cx, int cy, uint16_t c) {
  tft_.fillRect(cx - 3, cy - 16, 6, 16, c);
  tft_.fillTriangle(cx - 11, cy - 2, cx + 11, cy - 2, cx, cy + 9, c);
  tft_.fillSmoothRoundRect(cx - 16, cy + 12, 32, 4, 2, c, CARD);
}
void DebugUI::iconSpeaker(int cx, int cy, uint16_t c) {
  tft_.fillRect(cx - 16, cy - 6, 8, 12, c);
  tft_.fillTriangle(cx - 8, cy - 6, cx - 8, cy + 6, cx + 2, cy + 14, c); tft_.fillTriangle(cx - 8, cy - 6, cx + 2, cy - 14, cx + 2, cy + 14, c);
  tft_.drawSmoothArc(cx + 2, cy, 12, 10, 60, 120, c, CARD, true); tft_.drawSmoothArc(cx + 2, cy, 18, 16, 60, 120, c, CARD, true);
}
void DebugUI::iconFace(int cx, int cy, uint16_t c) {
  tft_.fillSmoothRoundRect(cx - 18, cy - 12, 12, 16, 4, c, CARD);
  tft_.fillSmoothRoundRect(cx + 6, cy - 12, 12, 16, 4, c, CARD);
  tft_.drawSmoothArc(cx, cy + 4, 12, 9, 60, 120, c, CARD, true);
}
void DebugUI::iconBattery(int cx, int cy, uint16_t c, float frac) {
  tft_.drawSmoothRoundRect(cx - 16, cy - 9, 3, 2, 30, 18, c, CARD);
  tft_.fillRect(cx + 15, cy - 4, 3, 8, c);
  int w = (int)(24 * constrain(frac, 0.0f, 1.0f));
  if (w > 0) tft_.fillRect(cx - 13, cy - 6, w, 12, c);
}
void DebugUI::iconLink(int cx, int cy, uint16_t c) {
  tft_.drawSmoothRoundRect(cx - 17, cy - 6, 6, 4, 20, 12, c, CARD);
  tft_.drawSmoothRoundRect(cx - 3, cy - 6, 6, 4, 20, 12, c, CARD);
  tft_.fillRect(cx - 6, cy - 1, 12, 3, c);
}
void DebugUI::iconReset(int cx, int cy, uint16_t c) {
  tft_.drawSmoothArc(cx, cy, 15, 11, 60, 330, c, CARD, true);
  tft_.fillTriangle(cx + 6, cy - 16, cx + 16, cy - 8, cx + 4, cy - 4, c);
}

void DebugUI::icon(Icon ic, int cx, int cy) {
  switch (ic) {
    case IC_WIFI: iconWifi(cx, cy - 6, net::wifiUp() ? WiFi.RSSI() : -100, EDGE); break;
    case IC_GLOBE: iconGlobe(cx, cy, CYAN); break;
    case IC_CHAT: iconChat(cx, cy, net::connected() ? GREEN : MUTED); break;
    case IC_LOG: iconLog(cx, cy, TXT); break;
    case IC_CHIP: iconChip(cx, cy, AMBER); break;
    case IC_QR: iconQr(cx, cy, TXT); break;
    case IC_GEAR: iconGear(cx, cy, MUTED); break;
    case IC_DOWNLOAD: iconDownload(cx, cy, ota::available() ? GREEN : CYAN); break;
    case IC_SPEAKER: iconSpeaker(cx, cy, AMBER); break;
    case IC_FACE: iconFace(cx, cy, AMBER); break;
    case IC_LINK: iconLink(cx, cy, CYAN); break;
    case IC_RESET: iconReset(cx, cy, RED); break;
    case IC_BATTERY: {
      battery::State st = battery::state();
      iconBattery(cx, cy, st == battery::ST_CRITICAL ? RED : st == battery::ST_LOW ? AMBER : st == battery::ST_CHARGING ? CYAN : GREEN, battery::percent() / 100.0f);
      break;
    }
  }
}

// =========================================================== navigation
void DebugUI::enter() { active_ = true; show(MENU); dbg::log("[dbg] settings mode"); }
void DebugUI::exit() { active_ = false; tft_.fillScreen(TFT_BLACK); dbg::log("[dbg] back to face"); }

void DebugUI::show(Screen s) {
  screen_ = s; confirm_ = 0;
  tft_.fillScreen(BG);
  header(s);
  switch (s) {
    case MENU:         drawTiles(MAIN, 6); break;
    case CONNECTIVITY: drawTiles(CONN, 2); break;
    case PIXEL:        drawTiles(PIX, 4); break;
    case DIAGNOSTICS:  drawTiles(DIAG, 2); break;
    case WIFI:         drawWifi(); break;
    case NETWORK:      drawNetwork(); break;
    case SOUND:        drawSound(); break;
    case POWER:        drawPower(); break;
    case PORTAL:       drawPortal(); break;
    case UPDATE:       checked_ = false; drawUpdate(); break;
    case SYSTEM:       drawSystem(); break;
    case RESET:        drawReset(); break;
    case PIPELINE:     drawPipeline(); break;
    case LOG:          lastLogCount_ = -1; drawLog(); break;
  }
}

// 3 columns of compact tiles (icon left, label right); menus with fewer tiles fill from the top-left
static DebugUI::Rect tileRect(int i) { return {(int16_t)(8 + (i % 3) * 104), (int16_t)(HDR + 8 + (i / 3) * 64), 96, 56}; }
static const DebugUI::Tile* tilesFor(DebugUI::Screen s, int& n) {
  switch (s) { case DebugUI::CONNECTIVITY: n = 2; return CONN; case DebugUI::PIXEL: n = 4; return PIX; case DebugUI::DIAGNOSTICS: n = 2; return DIAG;
               case DebugUI::MENU: n = 6; return MAIN; default: n = 0; return nullptr; }
}

void DebugUI::drawTiles(const Tile* tiles, int n) {
  for (int i = 0; i < n; i++) {
    Rect r = tileRect(i);
    card(r, CARD);
    icon(tiles[i].icon, r.x + 26, r.y + r.h / 2);
    tft_.setTextDatum(ML_DATUM); tft_.setTextColor(TXT, CARD);
    tft_.drawString(fit(tiles[i].label, r.w - 50, 2), r.x + 50, r.y + r.h / 2, 2);
    // badges: update available, weak/no link, low battery
    uint16_t badge = 0;
    if (tiles[i].target == UPDATE || tiles[i].target == PIXEL) { if (ota::available()) badge = GREEN; }
    if (tiles[i].target == CONNECTIVITY || tiles[i].target == WIFI) { if (!net::connected() || (net::wifiUp() && WiFi.RSSI() < -75)) badge = AMBER; }
    if (tiles[i].target == POWER && (battery::state() == battery::ST_LOW || battery::state() == battery::ST_CRITICAL)) badge = RED;
    if (badge) tft_.fillSmoothCircle(r.x + r.w - 10, r.y + 10, 4, badge, CARD);
  }
  if (screen_ == MENU) {
    tft_.setTextDatum(TC_DATUM); tft_.setTextColor(MUTED, BG);
    char b[64]; snprintf(b, sizeof b, "%s  -  fw %s", prefs::deviceId(), ota::version());
    tft_.drawString(b, SCREEN_W / 2, SCREEN_H - 14, 1);
  }
}

// =========================================================== Connectivity > Wi-Fi
static const DebugUI::Rect BTN_WIFI{16, SCREEN_H - 50, 288, 38};

void DebugUI::drawWifi() {
  bool up = net::wifiUp();
  int y = HDR + 10; char b[80];
  int rssi = up ? WiFi.RSSI() : -100;
  kv(12, y, 300, "SSID", up ? WiFi.SSID().c_str() : (prefs::wifiSsid[0] ? prefs::wifiSsid : "not configured"), up ? TXT : RED); y += 19;
  snprintf(b, sizeof b, "%s   ch %d", up ? "connected" : "not connected", up ? WiFi.channel() : 0);
  kv(12, y, 300, "Status", b, up ? GREEN : AMBER); y += 19;
  kv(12, y, 300, "BSSID", up ? WiFi.BSSIDstr().c_str() : "-", MUTED); y += 26;
  Rect sc{12, (int16_t)y, 296, 62}; card(sc, CARD);
  iconWifi(sc.x + 30, sc.y + 24, rssi, EDGE);
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(TXT, CARD);
  snprintf(b, sizeof b, "%d dBm", rssi); tft_.drawString(up ? b : "--", sc.x + 66, sc.y + 8, 4);
  tft_.setTextColor(MUTED, CARD); tft_.drawString(up ? rssiLabel(rssi) : "no Wi-Fi", sc.x + 66, sc.y + 36, 2);
  float q = constrain((rssi + 90) / 50.0f, 0.0f, 1.0f);
  tft_.fillSmoothRoundRect(sc.x + 190, sc.y + 24, 96, 12, 6, SURF, CARD);
  if (q > 0.05f) tft_.fillSmoothRoundRect(sc.x + 190, sc.y + 24, (int)(96 * q), 12, 6, q > 0.5f ? GREEN : q > 0.3f ? AMBER : RED, SURF);
  pill(BTN_WIFI, confirm_ == 1 ? "TAP AGAIN: CHANGE WI-FI" : "Change Wi-Fi", confirm_ == 1 ? AMBER : CARD, confirm_ == 1 ? INK : TXT);
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(MUTED, BG); tft_.fillRect(12, BTN_WIFI.y - 20, 296, 14, BG);
  tft_.drawString(confirm_ == 1 ? "Reboots into Wi-Fi setup. Pairing is kept." : "Change Wi-Fi opens the setup hotspot on the next boot.", 16, BTN_WIFI.y - 18, 1);
}

// =========================================================== Connectivity > Network
static const DebugUI::Rect BTN_SPEED{212, HDR + 8, 98, 30};

void DebugUI::drawNetwork() {
  bool up = net::wifiUp();
  int y = HDR + 10; char b[80];
  kv(12, y, 200, "IP", up ? WiFi.localIP().toString().c_str() : "-", TXT); y += 19;
  kv(12, y, 200, "Gateway", up ? WiFi.gatewayIP().toString().c_str() : "-", TXT); y += 19;
  kv(12, y, 200, "DNS", up ? WiFi.dnsIP().toString().c_str() : "-", TXT); y += 19;
  kv(12, y, 300, "MAC", WiFi.macAddress().c_str(), MUTED); y += 19;
  snprintf(b, sizeof b, "%s:%u  %s", net::backendHost(), net::backendPort(), net::connected() ? "(linked)" : "(offline)");
  kv(12, y, 300, "Brain", b, net::connected() ? GREEN : AMBER);
  drawSpeed();
}

void DebugUI::drawSpeed() {
  pill(BTN_SPEED, speed_.running ? "running" : speed_.step ? "Again" : "Speed test", speed_.running ? SURF : AMBER, speed_.running ? MUTED : INK);
  const int top = HDR + 110;
  tft_.fillRect(0, top, SCREEN_W, SCREEN_H - top, BG);
  tft_.setTextDatum(TL_DATUM);
  if (!speed_.step) {
    tft_.setTextColor(MUTED, BG);
    wrap(12, top + 4, 296, "Speed test: DNS lookup, HTTP fetch, 5 brain pings, then a 256 KB download and 128 KB upload to the brain over TLS. Takes about 10 s; the brain link pauses meanwhile.", 1, 4, MUTED);
    return;
  }
  int done = 0, lost = 0; uint32_t sum = 0;
  for (int i = 0; i < speed_.pingIdx; i++) { if (speed_.rtt[i] == 0xFFFFFFFF) lost++; else { sum += speed_.rtt[i]; done++; } }
  struct Row { const char* name; bool have; bool fail; bool isRate; uint32_t v; float frac; };
  char pingTxt[32]; snprintf(pingTxt, sizeof pingTxt, "%d/%d", done, speed_.pingIdx);
  Row rows[5] = {
    {"DNS", speed_.step > 1, speed_.step > 1 && speed_.dns == 0, false, speed_.dns, speed_.dns / 300.0f},
    {"HTTP", speed_.step > 2, speed_.step > 2 && speed_.httpCode != 204, false, speed_.http, speed_.http / 600.0f},
    {"Brain ping", speed_.pingIdx > 0, speed_.pingIdx >= 5 && done == 0, false, done ? sum / done : 0, (done ? sum / done : 0) / 300.0f},
    {"Download", speed_.step > 5, speed_.step > 5 && speed_.downFail, true, speed_.downKbps, speed_.downKbps / 8000.0f},
    {"Upload", speed_.step > 5, speed_.step > 5 && speed_.upFail, true, speed_.upKbps, speed_.upKbps / 4000.0f}};
  int y = top + 2; char b[40];
  for (int i = 0; i < 5; i++) {
    const Row& r = rows[i];
    tft_.setTextColor(MUTED, BG); tft_.drawString(r.name, 12, y, 2);
    int running = speed_.running && ((i == 0 && speed_.step == 1) || (i == 1 && speed_.step == 2) || (i == 2 && speed_.step == 3) || (i == 3 && (speed_.step == 4 || speed_.step == 5)) || (i == 4 && speed_.step == 5));
    if (!r.have) { tft_.setTextColor(running ? AMBER : EDGE, BG); tft_.drawString(running ? "..." : "-", 100, y, 2); hbar(200, y + 5, 108, 6, 0, CARD); }
    else if (r.fail) { tft_.setTextColor(RED, BG); tft_.drawString("FAILED", 100, y, 2); hbar(200, y + 5, 108, 6, 1, RED); }
    else {
      uint16_t c = r.isRate ? (r.v > 2000 ? GREEN : r.v > 800 ? AMBER : RED) : latColor(r.v);
      if (r.isRate) snprintf(b, sizeof b, "%.1f Mbit/s", r.v / 1000.0f);
      else if (i == 2) snprintf(b, sizeof b, "%lu ms  %s", r.v, pingTxt);
      else snprintf(b, sizeof b, "%lu ms", r.v);
      tft_.setTextColor(c, BG); tft_.drawString(b, 100, y, 2);
      hbar(200, y + 5, 108, 6, r.frac, c);
    }
    y += 19;
  }
  if (speed_.step >= 6) { tft_.setTextColor(MUTED, BG); tft_.drawString("Speech needs ~0.3 Mbit/s each way.", 12, y + 4, 1); }
}

void DebugUI::stepSpeed() {
  if (!speed_.running) return;
  switch (speed_.step) {
    case 1: {
      IPAddress ip; uint32_t t = millis();
      bool ok = net::wifiUp() && WiFi.hostByName(net::backendHost(), ip);
      speed_.dns = ok ? max<uint32_t>(1, millis() - t) : 0;
      dbg::log("[dbg] dns %s %lums", ok ? ip.toString().c_str() : "fail", millis() - t);
      speed_.step = 2; drawSpeed(); return;
    }
    case 2: {
      HTTPClient http; uint32_t t = millis();
      http.setConnectTimeout(3000); http.setTimeout(3000);
      http.begin("http://connectivitycheck.gstatic.com/generate_204");
      speed_.httpCode = http.GET(); speed_.http = millis() - t; http.end();
      dbg::log("[dbg] http %d %lums", speed_.httpCode, speed_.http);
      speed_.step = 3; speed_.pingIdx = 0; speed_.pingT0 = 0; drawSpeed(); return;
    }
    case 3:
      if (speed_.pingIdx >= 5 || !net::connected()) { if (!net::connected() && speed_.pingIdx == 0) speed_.pingIdx = 5; speed_.step = 4; drawSpeed(); return; }
      if (speed_.pingT0 == 0) {
        if (!net::sendPing()) { speed_.rtt[speed_.pingIdx++] = 0xFFFFFFFF; drawSpeed(); return; }
        speed_.pingT0 = millis();
      } else if (net::pongRtt()) { speed_.rtt[speed_.pingIdx++] = net::pongRtt(); speed_.pingT0 = 0; drawSpeed(); }
      else if (millis() - speed_.pingT0 > 1500) { speed_.rtt[speed_.pingIdx++] = 0xFFFFFFFF; speed_.pingT0 = 0; drawSpeed(); }
      return;
    case 4:
      speedtest::start(262144, 131072);                 // the worker suspends/resumes the brain link itself
      speed_.step = 5; drawSpeed(); return;
    case 5:
      if (speedtest::busy()) return;
      { const speedtest::Result& r = speedtest::result();
        speed_.downKbps = r.downKbps; speed_.downFail = r.downKbps == 0; speed_.upKbps = r.upKbps; speed_.upFail = r.upKbps == 0; }
      speed_.step = 6; speed_.running = false; drawSpeed(); return;
    default: speed_.running = false; return;
  }
}

// =========================================================== Sound
static const DebugUI::Rect BTN_VDOWN{16, HDR + 58, 60, 44};
static const DebugUI::Rect BTN_VUP{244, HDR + 58, 60, 44};
static const DebugUI::Rect BTN_TONE{16, SCREEN_H - 52, 136, 40};
static const DebugUI::Rect BTN_HELLO{168, SCREEN_H - 52, 136, 40};

void DebugUI::drawSound() {
  tft_.fillRect(0, HDR + 2, SCREEN_W, SCREEN_H - HDR - 2, BG);
  char b[32]; snprintf(b, sizeof b, "Volume %u%%", prefs::volume);
  tft_.setTextDatum(TC_DATUM); tft_.setTextColor(TXT, BG); tft_.drawString(b, SCREEN_W / 2, HDR + 14, 4);
  pill(BTN_VDOWN, "-", CARD, TXT, 4); pill(BTN_VUP, "+", CARD, TXT, 4);
  hbar(86, HDR + 74, 148, 12, prefs::volume / 100.0f, AMBER);
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(MUTED, BG);
  tft_.drawString(fit(speaker::playing() ? "playing..." : "Speaker on the JP1 connector, amp on IO4, DAC on IO26.", 288, 2), 16, HDR + 116, 2);
  tft_.drawString(fit(net::connected() ? "Say hello asks the brain for a short spoken reply." : "Say hello needs the brain connection.", 288, 2), 16, HDR + 138, 2);
  pill(BTN_TONE, "TEST TONE", AMBER, INK); pill(BTN_HELLO, "SAY HELLO", net::connected() ? CYAN : CARD, net::connected() ? INK : MUTED);
}

// =========================================================== Power
static const DebugUI::Rect BTN_BTALK{130, HDR + 98, 174, 30};

void DebugUI::drawPower() {
  tft_.fillRect(0, HDR + 2, SCREEN_W, SCREEN_H - HDR - 2, BG);
  char b[48]; uint8_t pct = battery::percent(); battery::State st = battery::state(); uint16_t mv = battery::millivolts();
  bool noBat = mv < 2500;
  uint16_t c = st == battery::ST_CRITICAL ? RED : st == battery::ST_LOW ? AMBER : st == battery::ST_CHARGING ? CYAN : GREEN;
  snprintf(b, sizeof b, "%u%%", pct);
  gauge(66, HDR + 62, 46, noBat ? 0 : pct / 100.0f, c, noBat ? "--" : b, noBat ? "no battery" : st == battery::ST_UNKNOWN ? "measuring" : battery::stateName());
  int x = 130, y = HDR + 14;
  snprintf(b, sizeof b, "%.2f V", mv / 1000.0f); kv(x, y, 178, "Voltage", noBat ? "-" : b, TXT); y += 19;
  kv(x, y, 178, "State", noBat ? "JP2 empty" : st == battery::ST_UNKNOWN ? "measuring..." : battery::stateName(), noBat ? MUTED : c); y += 19;
  int tr = battery::trendMvPerMin(); snprintf(b, sizeof b, "%+d mV/min", tr);
  kv(x, y, 178, "Trend", noBat || st == battery::ST_UNKNOWN ? "-" : b, TXT); y += 19;
  kv(x, y, 178, "Cell", "3000 mAh LiPo", MUTED);
  pill(BTN_BTALK, prefs::batteryTalk ? "Remarks: ON" : "Remarks: OFF", prefs::batteryTalk ? AMBER : CARD, prefs::batteryTalk ? INK : TXT);
  wrap(16, HDR + 140, 288, "USB charges the cell at about 300 mA and stops on its own when full. With remarks on, Pixel says when it gets plugged in, is full, or is hungry.", 1, 3, MUTED);
}

// =========================================================== Pixel > Portal
static const DebugUI::Rect BTN_UNPAIR{176, SCREEN_H - 44, 132, 32};

void DebugUI::drawPortal() {
  char url[160];
  snprintf(url, sizeof url, "%s://%s%s/portal", net::tls() ? "https" : "http", net::backendHost(),
           (net::backendPort() == 443 || net::backendPort() == 80) ? "" : (String(":") + net::backendPort()).c_str());
  QRCode qr; uint8_t data[qrcode_getBufferSize(5)];
  bool ok = qrcode_initText(&qr, data, 5, ECC_LOW, url) == 0;
  const int scale = 4, size = qr.size * scale, x0 = 14, y0 = HDR + (SCREEN_H - HDR - size) / 2;
  tft_.fillRect(x0 - 6, y0 - 6, size + 12, size + 12, TFT_WHITE);
  if (ok) for (uint8_t y = 0; y < qr.size; y++) for (uint8_t x = 0; x < qr.size; x++)
    if (qrcode_getModule(&qr, x, y)) tft_.fillRect(x0 + x * scale, y0 + y * scale, scale, scale, TFT_BLACK);
  int tx = x0 + size + 18, tw = SCREEN_W - tx - 8, y = HDR + 10;
  tft_.fillRect(tx, HDR + 2, tw + 8, SCREEN_H - HDR - 2, BG);
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(TXT, BG);
  wrap(tx, y, tw, "Scan to open the portal", 2, 2, TXT); y += 38;
  tft_.setTextColor(MUTED, BG); tft_.drawString("Device", tx, y, 1); y += 12;
  tft_.setTextColor(CYAN, BG); tft_.drawString(fit(prefs::deviceId(), tw, 2), tx, y, 2); y += 22;
  tft_.setTextColor(MUTED, BG); tft_.drawString("Pairing", tx, y, 1); y += 12;
  if (net::pairingCode()[0]) { tft_.setTextColor(AMBER, BG); tft_.drawString(net::pairingCode(), tx, y, 4); y += 30; wrap(tx, y, tw, "PIXELS > Add a Pixel, enter this code", 1, 2, MUTED); }
  else {
    tft_.setTextColor(net::connected() ? GREEN : AMBER, BG); tft_.drawString(net::connected() ? "paired" : "connecting...", tx, y, 2); y += 22;
    pill(BTN_UNPAIR, confirm_ == 2 ? "TAP AGAIN" : "Unpair", confirm_ == 2 ? AMBER : CARD, confirm_ == 2 ? INK : TXT);
    tft_.setTextColor(MUTED, BG); tft_.drawString(confirm_ == 2 ? "Forgets the pairing; a new code shows." : "Unpair to move it to another account.", tx, BTN_UNPAIR.y - 14, 1);
  }
}

// =========================================================== Pixel > Update
static const DebugUI::Rect BTN_CHECK{16, SCREEN_H - 50, 136, 38};
static const DebugUI::Rect BTN_INSTALL{168, SCREEN_H - 50, 136, 38};

void DebugUI::drawUpdate() {
  tft_.fillRect(0, HDR + 2, SCREEN_W, SCREEN_H - HDR - 2, BG);
  char b[96]; int y = HDR + 12;
  kv(12, y, 296, "Installed", ota::version(), TXT); y += 19;
  kv(12, y, 296, "Built", __DATE__ " " __TIME__, MUTED); y += 19;
  const ota::Manifest& m = ota::latest();
  const char* st = ota::state();
  if (!strcmp(st, "checking")) kv(12, y, 296, "Latest", "checking...", AMBER);
  else if (!strcmp(st, "failed")) kv(12, y, 296, "Latest", "last update failed - try again", RED);
  else if (!m.version[0]) kv(12, y, 296, "Latest", checked_ ? "no release published yet" : "not checked yet", MUTED);
  else { snprintf(b, sizeof b, "%s%s", m.version, ota::available() ? "  - update available" : "  - you're up to date"); kv(12, y, 296, "Latest", b, ota::available() ? GREEN : TXT); }
  y += 26;
  if (m.notes[0]) { Rect nc{12, (int16_t)y, 296, 62}; card(nc, CARD); wrap(nc.x + 10, nc.y + 8, nc.w - 20, m.notes, 2, 3, TXT); }
  else { tft_.setTextDatum(TL_DATUM); tft_.setTextColor(MUTED, BG); wrap(12, y, 296, "Pixel also checks for updates on its own once a day, and the portal can push one to it.", 2, 3, MUTED); }
  bool busy = !strcmp(st, "checking") || !strcmp(st, "downloading");
  pill(BTN_CHECK, busy ? "..." : "Check now", busy ? CARD : AMBER, busy ? MUTED : INK);
  pill(BTN_INSTALL, "Install", ota::available() && !busy ? GREEN : CARD, ota::available() && !busy ? INK : MUTED);
}

// =========================================================== Pixel > System
static const DebugUI::Rect VERBOSE{212, HDR + 6, 98, 26};
static const DebugUI::Rect REBOOT{212, HDR + 38, 98, 26};
static const DebugUI::Rect LOGBTN{212, HDR + 70, 98, 26};

void DebugUI::drawSystem() {
  pill(VERBOSE, dbg::verbose ? "Verbose ON" : "Verbose off", dbg::verbose ? AMBER : CARD, dbg::verbose ? INK : TXT);
  pill(REBOOT, "Reboot", CARD, RED);
  pill(LOGBTN, "Event log", CARD, CYAN);
  uint32_t total = ESP.getHeapSize(), freeH = ESP.getFreeHeap();
  char b[64];
  snprintf(b, sizeof b, "%uK", (total - freeH) >> 10);
  gauge(58, HDR + 52, 40, (float)(total - freeH) / total, AMBER, b, "heap used");
  snprintf(b, sizeof b, "%u", fps_);
  gauge(150, HDR + 52, 40, fps_ / 30.0f, GREEN, b, "face fps");
  int y = HDR + 104;
  snprintf(b, sizeof b, "%s rev%d  %dMHz", ESP.getChipModel(), ESP.getChipRevision(), ESP.getCpuFreqMHz());
  kv(12, y, 300, "Chip", b, TXT); y += 19;
  snprintf(b, sizeof b, "%uMB flash, sketch %uKB", ESP.getFlashChipSize() >> 20, ESP.getSketchSize() >> 10);
  kv(12, y, 300, "Storage", b, TXT); y += 19;
  snprintf(b, sizeof b, "%u free, %u min", freeH, ESP.getMinFreeHeap());
  kv(12, y, 300, "Heap", b, TXT); y += 19;
  snprintf(b, sizeof b, "%lus", millis() / 1000);
  kv(12, y, 300, "Uptime", b, TXT); y += 19;
  snprintf(b, sizeof b, "%s  (%s)", ota::version(), __DATE__);
  kv(12, y, 300, "Firmware", b, TXT); y += 19;
  kv(12, y, 300, "Device", prefs::deviceId(), MUTED);
}

// =========================================================== Pixel > Reset
static const DebugUI::Rect BTN_FACTORY{16, HDR + 96, 288, 44};

void DebugUI::drawReset() {
  tft_.fillRect(0, HDR + 2, SCREEN_W, SCREEN_H - HDR - 2, BG);
  tft_.setTextDatum(TL_DATUM);
  wrap(16, HDR + 14, 288, "Factory reset forgets Wi-Fi, the brain address, the pairing and local settings. The portal keeps this Pixel's history; pair it again with a new code. The device identity is kept.", 2, 4, TXT);
  pill(BTN_FACTORY, confirm_ == 3 ? "TAP AGAIN: FACTORY RESET" : "Factory reset", confirm_ == 3 ? RED : CARD, confirm_ == 3 ? TFT_WHITE : RED);
  tft_.setTextColor(MUTED, BG);
  tft_.drawString(confirm_ == 3 ? "Second tap wipes and reboots." : "Change Wi-Fi is under Connectivity, Unpair under Portal.", 16, HDR + 150, 1);
}

// =========================================================== Diagnostics > Pipeline
static DebugUI::Rect presetRect(int i) { return {(int16_t)(10 + i * 61), HDR + 8, 54, 28}; }

void DebugUI::drawPipeline() {
  for (int i = 0; i < 5; i++) { char l[4]; snprintf(l, 4, "P%d", i + 1); pill(presetRect(i), l, i == lastPreset_ ? AMBER : CARD, i == lastPreset_ ? INK : TXT); }
  const net::Turn& t = net::lastTurn();
  int y = HDR + 46;
  tft_.fillRect(0, y, SCREEN_W, SCREEN_H - y, BG);
  if (lastPreset_ < 0) {
    tft_.setTextDatum(TL_DATUM); tft_.setTextColor(net::connected() ? MUTED : RED, BG);
    tft_.drawString(net::connected() ? "Tap a preset to run the full brain pipeline:" : "Brain not connected - see Connectivity", 12, y, 2);
    for (int i = 0; i < 5; i++) { char s[64]; snprintf(s, sizeof s, "P%d  %s", i + 1, PRESETS[i]); tft_.setTextColor(TXT, BG); tft_.drawString(fit(s, 296, 2), 12, y + 26 + i * 18, 2); }
    return;
  }
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(MUTED, BG);
  tft_.drawString(fit(PRESETS[lastPreset_], 296, 2), 12, y, 2); y += 22;
  uint32_t total = t.done ? max<uint32_t>(t.tReply, 1) : max<uint32_t>(millis() - t.t0, 1);
  uint32_t scale = max<uint32_t>(total, 1500);
  int bx = 12, bw = 296;
  tft_.fillSmoothRoundRect(bx, y, bw, 10, 5, CARD, BG);
  auto seg = [&](uint32_t from, uint32_t to, uint16_t c) {
    if (to <= from) return;
    int x0 = bx + (int)((uint64_t)bw * from / scale), x1 = bx + (int)((uint64_t)bw * to / scale);
    tft_.fillRect(x0, y + 2, max(1, x1 - x0), 6, c);
  };
  if (t.done) { seg(0, t.tExpr, AMBER); seg(t.tExpr, t.tFirstAudio, CYAN); seg(t.tFirstAudio, t.tReply, GREEN); }
  else seg(0, total, AMBER);
  y += 16;
  char b[64];
  tft_.setTextColor(AMBER, BG); snprintf(b, sizeof b, "face %lums", t.tExpr); tft_.drawString(t.done ? b : "waiting...", bx, y, 1);
  if (t.done) {
    tft_.setTextColor(CYAN, BG); snprintf(b, sizeof b, "voice %lums", t.tFirstAudio); tft_.drawString(b, bx + 100, y, 1);
    tft_.setTextColor(GREEN, BG); snprintf(b, sizeof b, "done %lums", t.tReply); tft_.drawString(b, bx + 200, y, 1);
  }
  y += 16;
  if (t.expr[0]) {
    snprintf(b, sizeof b, "%s %.1f", t.expr, t.intensity);
    Rect ep{12, (int16_t)y, (int16_t)(tft_.textWidth(b, 2) + 18), 20}; pill(ep, b, CARD, AMBER);
    snprintf(b, sizeof b, "speech %.1fs", t.audioBytes / 32000.0f);
    tft_.setTextDatum(ML_DATUM); tft_.setTextColor(MUTED, BG); tft_.drawString(b, ep.x + ep.w + 10, y + 10, 2);
    y += 28;
  }
  if (t.reply[0]) {
    int lines = min(5, (int)(tft_.textWidth(t.reply, 2) / 270) + 1);
    Rect bub{12, (int16_t)y, 296, (int16_t)(lines * 17 + 14)};
    if (bub.y + bub.h > SCREEN_H - 2) bub.h = SCREEN_H - 2 - bub.y;
    tft_.fillSmoothRoundRect(bub.x, bub.y, bub.w, bub.h, 10, CARD, BG);
    tft_.fillTriangle(bub.x + 14, bub.y, bub.x + 26, bub.y, bub.x + 20, bub.y - 6, CARD);
    wrap(bub.x + 10, bub.y + 7, bub.w - 20, t.reply, 2, lines, TXT);
  }
}

// =========================================================== Diagnostics > Event log
void DebugUI::drawLog() {
  if (dbg::count() == lastLogCount_) return;
  lastLogCount_ = dbg::count();
  int rows = (SCREEN_H - HDR - 6) / 13;
  tft_.fillRect(0, HDR + 1, SCREEN_W, SCREEN_H - HDR - 1, BG);
  tft_.setTextDatum(TL_DATUM);
  for (int i = 0; i < rows; i++) {
    const char* l = dbg::line(rows - 1 - i);
    if (!l) continue;
    int y = HDR + 5 + i * 13;
    tft_.setTextColor(MUTED, BG); tft_.drawString(l, 4, y, 1);
    const char* tag = strchr(l, '[');
    if (!tag) continue;
    bool err = strstr(l, "error") || strstr(l, "disconnect") || strstr(l, "down") || strstr(l, "fail");
    uint16_t c = err ? RED : strncmp(tag, "[net]", 5) == 0 ? CYAN : strncmp(tag, "[dbg]", 5) == 0 ? AMBER : TXT;
    int x = 4 + tft_.textWidth(l, 1) - tft_.textWidth(tag, 1);
    tft_.setTextColor(c, BG); tft_.drawString(tag, x, y, 1);
  }
}

// =========================================================== input / refresh
void DebugUI::touch(int16_t x, int16_t y) {
  if (screen_ != MENU && BACK.has(x, y)) { if (speed_.running) return; show(parentOf(screen_)); return; }
  int n; const Tile* tiles = tilesFor(screen_, n);
  if (tiles) {
    for (int i = 0; i < n; i++)
      if (tileRect(i).has(x, y)) { if (tiles[i].target == MENU) exit(); else show(tiles[i].target); return; }
    return;
  }
  auto restart = [&](const char* what) {
    dbg::log("[dbg] %s", what);
    tft_.fillScreen(BG); tft_.setTextDatum(MC_DATUM); tft_.setTextColor(AMBER, BG); tft_.drawString("restarting...", SCREEN_W / 2, SCREEN_H / 2, 4);
    delay(400); ESP.restart();
  };
  switch (screen_) {
    case WIFI:
      if (BTN_WIFI.has(x, y)) { if (confirm_ == 1) { prefs::forgetWifi(); restart("change wifi"); } confirm_ = 1; drawWifi(); }
      else if (confirm_) { confirm_ = 0; drawWifi(); }
      break;
    case NETWORK:
      if (BTN_SPEED.has(x, y) && !speed_.running) { speed_ = {}; speed_.running = true; speed_.step = 1; drawSpeed(); }
      break;
    case PORTAL:
      if (!net::pairingCode()[0] && BTN_UNPAIR.has(x, y)) { if (confirm_ == 2) { prefs::forgetToken(); restart("unpair"); } confirm_ = 2; drawPortal(); }
      else if (confirm_) { confirm_ = 0; drawPortal(); }
      break;
    case RESET:
      if (BTN_FACTORY.has(x, y)) { if (confirm_ == 3) { prefs::factoryReset(); restart("factory reset"); } confirm_ = 3; drawReset(); }
      else if (confirm_) { confirm_ = 0; drawReset(); }
      break;
    case PIPELINE:
      for (int i = 0; i < 5; i++)
        if (presetRect(i).has(x, y)) { lastPreset_ = i; lastTurnDone_ = false; net::sendText(PRESETS[i]); drawPipeline(); return; }
      break;
    case SOUND:
      if (BTN_VDOWN.has(x, y) || BTN_VUP.has(x, y)) {
        int v = (int)prefs::volume + (BTN_VUP.has(x, y) ? 10 : -10); prefs::volume = (uint8_t)constrain(v, 0, 100);
        speaker::setVolume(prefs::volume / 100.0f); prefs::save(); drawSound();
      } else if (BTN_TONE.has(x, y)) { speaker::tone(523.25f, 500); dbg::log("[dbg] test tone"); drawSound(); }
      else if (BTN_HELLO.has(x, y) && net::connected()) { net::sendText("Say hello in one short cheerful sentence so I can hear your voice."); dbg::log("[dbg] say hello"); }
      break;
    case POWER:
      if (BTN_BTALK.has(x, y)) { prefs::batteryTalk = !prefs::batteryTalk; prefs::save(); net::sendStatusNow(); drawPower(); }
      break;
    case UPDATE:
      if (BTN_CHECK.has(x, y) && strcmp(ota::state(), "downloading")) { ota::requestCheck(); checked_ = true; drawUpdate(); } else if (BTN_INSTALL.has(x, y) && ota::available()) { ota::requestInstall(); exit(); }   // main loop draws the progress screen
      break;
    case SYSTEM:
      if (VERBOSE.has(x, y)) { dbg::verbose = !dbg::verbose; dbg::log("[dbg] verbose %s", dbg::verbose ? "on" : "off"); drawSystem(); }
      else if (REBOOT.has(x, y)) { dbg::log("[dbg] reboot"); delay(200); ESP.restart(); }
      else if (LOGBTN.has(x, y)) show(LOG);
      break;
    default: break;
  }
}

void DebugUI::update() {
  if (!active_) return;
  if (screen_ == NETWORK) stepSpeed();
  statusChips();
  if (millis() - lastRefresh_ < 500) return;
  lastRefresh_ = millis();
  static uint32_t slow = 0; bool tick2s = millis() - slow > 2000; if (tick2s) slow = millis();
  switch (screen_) {
    case WIFI: if (tick2s && !confirm_) drawWifi(); break;
    case NETWORK: if (tick2s && !speed_.running) drawNetwork(); break;
    case LOG: drawLog(); break;
    case MENU: case CONNECTIVITY: case PIXEL: case DIAGNOSTICS: { if (tick2s) { int n; const Tile* t = tilesFor(screen_, n); drawTiles(t, n); } break; }
    case SYSTEM: if (tick2s) drawSystem(); break;
    case UPDATE: { static bool was = false; bool now = ota::checking(); if (now != was) { was = now; drawUpdate(); } break; }
    case POWER: if (tick2s) drawPower(); break;
    case SOUND: { static bool was = false; bool now = speaker::playing(); if (now != was) { was = now; drawSound(); } break; }
    case PIPELINE:
      if (lastPreset_ >= 0) { bool d = net::lastTurn().done; if (!d || !lastTurnDone_) drawPipeline(); lastTurnDone_ = d; }
      break;
    default: break;
  }
}
