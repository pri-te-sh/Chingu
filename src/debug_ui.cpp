#include "debug_ui.h"
#include "board.h"
#include "log.h"
#include "net.h"
#include "prefs.h"
#include <WiFi.h>
#include <HTTPClient.h>

// ---- palette (true RGB now that TFT_RGB_ORDER is set) ----
#define RGB(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))
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

static const DebugUI::Rect BACK{6, 5, 44, HDR - 10};
static const DebugUI::Rect RUN{212, HDR + 8, 98, 30};
static const DebugUI::Rect VERBOSE{212, HDR + 8, 98, 30};
static const DebugUI::Rect REBOOT{212, HDR + 46, 98, 30};
static uint16_t latColor(uint32_t ms) { return ms < 60 ? GREEN : ms < 200 ? AMBER : RED; }

// =========================================================== widget kit
void DebugUI::header(const char* title, bool back) {
  for (int y = 0; y < HDR; y++) {                       // subtle vertical gradient
    uint8_t v = 22 + (HDR - y) / 3;
    tft_.drawFastHLine(0, y, SCREEN_W, RGB(v, v + 4, v + 14));
  }
  tft_.drawFastHLine(0, HDR, SCREEN_W, EDGE);
  int x = 12;
  if (back) { pill(BACK, "<", CARD, AMBER, 4); x = BACK.x + BACK.w + 10; }
  tft_.setTextDatum(ML_DATUM); tft_.setTextColor(TXT, RGB(22 + 5, 26 + 5, 36 + 5));
  tft_.drawString(title, x, HDR / 2, 4);
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

void DebugUI::kv(int x, int y, int w, const char* key, const char* val, uint16_t valColor) {
  tft_.fillRect(x, y, w, 18, BG);
  tft_.setTextDatum(TL_DATUM);
  tft_.setTextColor(MUTED, BG); tft_.drawString(key, x, y + 1, 2);
  tft_.setTextColor(valColor, BG); tft_.drawString(val, x + 76, y + 1, 2);
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
void DebugUI::iconFace(int cx, int cy, uint16_t c) {
  tft_.fillSmoothRoundRect(cx - 18, cy - 12, 12, 16, 4, c, CARD);
  tft_.fillSmoothRoundRect(cx + 6, cy - 12, 12, 16, 4, c, CARD);
  tft_.drawSmoothArc(cx, cy + 4, 12, 9, 60, 120, c, CARD, true);
}

// =========================================================== screens
void DebugUI::enter() { active_ = true; show(MENU); dbg::log("[dbg] settings mode"); }
void DebugUI::exit() { active_ = false; tft_.fillScreen(TFT_BLACK); dbg::log("[dbg] back to face"); }

void DebugUI::show(Screen s) {
  screen_ = s;
  tft_.fillScreen(BG);
  switch (s) {
    case MENU:     header(prefs::name, false); drawMenu(); break;
    case NETWORK:  header("Network", true); drawNetwork(); break;
    case INTERNET: header("Internet test", true); inet_ = {}; drawInternet(); break;
    case PIPELINE: header("Pipeline test", true); drawPipeline(); break;
    case LOG:      header("Event log", true); lastLogCount_ = -1; drawLog(); break;
    case SYSTEM:   header("System", true); drawSystem(); break;
  }
}

static const struct { const char* label; uint8_t id; } TILES[6] = {
  {"Network", 1}, {"Internet", 2}, {"Pipeline", 3}, {"Event log", 4}, {"System", 5}, {"Face", 6}};
static DebugUI::Rect tileRect(int i) { return {(int16_t)(8 + (i % 3) * 104), (int16_t)(HDR + 10 + (i / 3) * 98), 96, 90}; }

void DebugUI::drawMenu() {
  for (int i = 0; i < 6; i++) {
    Rect r = tileRect(i);
    card(r, CARD);
    int cx = r.x + r.w / 2, cy = r.y + 32;
    switch (i) {
      case 0: iconWifi(cx, cy, net::wifiUp() ? WiFi.RSSI() : -100, EDGE); break;
      case 1: iconGlobe(cx, cy, CYAN); break;
      case 2: iconChat(cx, cy, net::connected() ? GREEN : MUTED); break;
      case 3: iconLog(cx, cy, TXT); break;
      case 4: iconChip(cx, cy, AMBER); break;
      case 5: iconFace(cx, cy, AMBER); break;
    }
    tft_.setTextDatum(BC_DATUM); tft_.setTextColor(TXT, CARD);
    tft_.drawString(TILES[i].label, cx, r.y + r.h - 8, 2);
  }
  tft_.setTextDatum(BC_DATUM); tft_.setTextColor(MUTED, BG);
  tft_.drawString("hold BOOT or the screen to leave", SCREEN_W / 2, SCREEN_H - 3, 1);
}

static const char* rssiLabel(int r) { return r > -55 ? "excellent" : r > -65 ? "good" : r > -75 ? "fair" : "weak"; }

void DebugUI::drawNetwork() {
  bool up = net::wifiUp();
  int y = HDR + 10; char b[64];
  kv(12, y, 300, "SSID", up ? WiFi.SSID().c_str() : "not connected", up ? TXT : RED); y += 19;
  kv(12, y, 300, "IP", up ? WiFi.localIP().toString().c_str() : "-", TXT); y += 19;
  snprintf(b, sizeof b, "%s  /  %s", up ? WiFi.gatewayIP().toString().c_str() : "-", up ? WiFi.dnsIP().toString().c_str() : "-");
  kv(12, y, 300, "GW / DNS", b, TXT); y += 19;
  kv(12, y, 300, "MAC", WiFi.macAddress().c_str(), MUTED); y += 19;
  snprintf(b, sizeof b, "%s:%u", net::backendHost(), net::backendPort());
  kv(12, y, 300, "Backend", b, TXT); y += 19;
  kv(12, y, 300, "Brain", net::connected() ? "connected, ready" : up ? "connecting..." : "offline", net::connected() ? GREEN : AMBER); y += 26;

  // signal card
  Rect sc{12, (int16_t)y, 296, 62}; card(sc, CARD);
  int rssi = up ? WiFi.RSSI() : -100;
  iconWifi(sc.x + 30, sc.y + 24, rssi, EDGE);
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(TXT, CARD);
  snprintf(b, sizeof b, "%d dBm", rssi); tft_.drawString(up ? b : "--", sc.x + 66, sc.y + 8, 4);
  tft_.setTextColor(MUTED, CARD);
  snprintf(b, sizeof b, "%s   ch %d", up ? rssiLabel(rssi) : "", up ? WiFi.channel() : 0); tft_.drawString(b, sc.x + 66, sc.y + 36, 2);
  float q = constrain((rssi + 90) / 50.0f, 0.0f, 1.0f);
  tft_.fillSmoothRoundRect(sc.x + 190, sc.y + 24, 96, 12, 6, SURF, CARD);
  if (q > 0.05f) tft_.fillSmoothRoundRect(sc.x + 190, sc.y + 24, (int)(96 * q), 12, 6, q > 0.5f ? GREEN : q > 0.3f ? AMBER : RED, SURF);
}

void DebugUI::drawInternet() {
  pill(RUN, inet_.running ? "running" : "Run test", inet_.running ? CARD : AMBER, inet_.running ? MUTED : INK);
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(MUTED, BG);
  tft_.fillRect(12, HDR + 8, 190, 32, BG);
  tft_.drawString("Reachability + brain latency", 12, HDR + 14, 2);

  struct { const char* name; uint32_t ms; bool have; bool fail; } rows[3] = {
    {"DNS", inet_.dns, inet_.step > 1, inet_.step > 1 && inet_.dns == 0},
    {"HTTP", inet_.http, inet_.step > 2, inet_.step > 2 && inet_.httpCode != 204},
    {"Brain", 0, false, false}};
  int done = 0, lost = 0; uint32_t sum = 0, mn = 99999, mx = 0;
  for (int i = 0; i < inet_.pingIdx; i++) {
    if (inet_.rtt[i] == 0xFFFFFFFF) lost++; else { sum += inet_.rtt[i]; mn = min(mn, inet_.rtt[i]); mx = max(mx, inet_.rtt[i]); done++; }
  }
  rows[2].have = inet_.pingIdx > 0; rows[2].ms = done ? sum / done : 0; rows[2].fail = inet_.pingIdx > 0 && done == 0;

  int y = HDR + 50; char b[48];
  for (auto& r : rows) {
    tft_.fillRect(12, y, 296, 30, BG);
    tft_.setTextDatum(TL_DATUM); tft_.setTextColor(TXT, BG); tft_.drawString(r.name, 12, y + 2, 2);
    if (!r.have) { tft_.setTextColor(MUTED, BG); tft_.drawString("-", 70, y + 2, 2); hbar(70, y + 20, 238, 6, 0, CARD); }
    else if (r.fail) { tft_.setTextColor(RED, BG); tft_.drawString("FAILED", 70, y + 2, 2); hbar(70, y + 20, 238, 6, 1, RED); }
    else {
      snprintf(b, sizeof b, "%lu ms", r.ms); tft_.setTextColor(latColor(r.ms), BG); tft_.drawString(b, 70, y + 2, 2);
      hbar(70, y + 20, 238, 6, r.ms / 300.0f, latColor(r.ms));
    }
    y += 34;
  }
  // ping sparkline
  tft_.fillRect(12, y, 296, 40, BG);
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(MUTED, BG); tft_.drawString("WS ping x5", 12, y, 1);
  if (done) { snprintf(b, sizeof b, "min %lu  avg %lu  max %lu  loss %d/5", mn, sum / done, mx, lost); tft_.setTextColor(TXT, BG); tft_.drawString(b, 90, y - 1, 2); }
  for (int i = 0; i < 5; i++) {
    int cx = 24 + i * 24, base = y + 34;
    if (i >= inet_.pingIdx) { tft_.fillSmoothCircle(cx, base - 4, 3, CARD, BG); continue; }
    if (inet_.rtt[i] == 0xFFFFFFFF) { tft_.drawWideLine(cx - 4, base - 8, cx + 4, base, 2, RED, BG); tft_.drawWideLine(cx - 4, base, cx + 4, base - 8, 2, RED, BG); continue; }
    int h = constrain((int)(inet_.rtt[i] / 5), 3, 20);
    tft_.fillSmoothRoundRect(cx - 4, base - h, 8, h, 3, latColor(inet_.rtt[i]), BG);
  }
}

void DebugUI::stepInternet() {
  if (!inet_.running) return;
  if (inet_.step == 1) {
    IPAddress ip; uint32_t t = millis();
    bool ok = net::wifiUp() && WiFi.hostByName("connectivitycheck.gstatic.com", ip);
    inet_.dns = ok ? max<uint32_t>(1, millis() - t) : 0;
    dbg::log("[dbg] dns %s %lums", ok ? ip.toString().c_str() : "fail", millis() - t);
    inet_.step = 2; drawInternet(); return;
  }
  if (inet_.step == 2) {
    HTTPClient http; uint32_t t = millis();
    http.setConnectTimeout(3000); http.setTimeout(3000);
    http.begin("http://connectivitycheck.gstatic.com/generate_204");
    inet_.httpCode = http.GET(); inet_.http = millis() - t; http.end();
    dbg::log("[dbg] http %d %lums", inet_.httpCode, inet_.http);
    inet_.step = 3; inet_.pingIdx = 0; inet_.pingT0 = 0; drawInternet(); return;
  }
  if (inet_.step == 3) {
    if (inet_.pingIdx >= 5) { inet_.running = false; inet_.step = 4; drawInternet(); return; }
    if (inet_.pingT0 == 0) {
      if (!net::sendPing()) { inet_.rtt[inet_.pingIdx++] = 0xFFFFFFFF; drawInternet(); return; }
      inet_.pingT0 = millis();
    } else if (net::pongRtt()) { inet_.rtt[inet_.pingIdx++] = net::pongRtt(); inet_.pingT0 = 0; drawInternet(); }
    else if (millis() - inet_.pingT0 > 1500) { inet_.rtt[inet_.pingIdx++] = 0xFFFFFFFF; inet_.pingT0 = 0; drawInternet(); }
  }
}

static DebugUI::Rect presetRect(int i) { return {(int16_t)(10 + i * 61), HDR + 8, 54, 28}; }

void DebugUI::drawPipeline() {
  for (int i = 0; i < 5; i++) { char l[4]; snprintf(l, 4, "P%d", i + 1); pill(presetRect(i), l, i == lastPreset_ ? AMBER : CARD, i == lastPreset_ ? INK : TXT); }
  const net::Turn& t = net::lastTurn();
  int y = HDR + 46;
  tft_.fillRect(0, y, SCREEN_W, SCREEN_H - y, BG);
  if (lastPreset_ < 0) {
    tft_.setTextDatum(TL_DATUM); tft_.setTextColor(net::connected() ? MUTED : RED, BG);
    tft_.drawString(net::connected() ? "Tap a preset to run the full brain pipeline:" : "Brain not connected - see Network", 12, y, 2);
    for (int i = 0; i < 5; i++) { char s[64]; snprintf(s, sizeof s, "P%d  %s", i + 1, PRESETS[i]); tft_.setTextColor(TXT, BG); tft_.drawString(s, 12, y + 26 + i * 18, 2); }
    return;
  }
  // what we sent
  tft_.setTextDatum(TL_DATUM); tft_.setTextColor(MUTED, BG);
  tft_.drawString(PRESETS[lastPreset_], 12, y, 2); y += 22;

  // turn timeline: 0 -> done, markers for expression / first audio / done
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
  if (t.reply[0]) {                                     // chat bubble
    int lines = min(5, (int)(tft_.textWidth(t.reply, 2) / 270) + 1);
    Rect bub{12, (int16_t)y, 296, (int16_t)(lines * 17 + 14)};
    if (bub.y + bub.h > SCREEN_H - 2) bub.h = SCREEN_H - 2 - bub.y;
    tft_.fillSmoothRoundRect(bub.x, bub.y, bub.w, bub.h, 10, CARD, BG);
    tft_.fillTriangle(bub.x + 14, bub.y, bub.x + 26, bub.y, bub.x + 20, bub.y - 6, CARD);
    wrap(bub.x + 10, bub.y + 7, bub.w - 20, t.reply, 2, lines, TXT);
  }
}

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
    tft_.setTextColor(MUTED, BG); tft_.drawString(l, 4, y, 1);   // timestamp (first 6 chars) stays muted
    const char* tag = strchr(l, '[');
    if (!tag) continue;
    bool err = strstr(l, "error") || strstr(l, "disconnect") || strstr(l, "down") || strstr(l, "fail");
    uint16_t c = err ? RED : strncmp(tag, "[net]", 5) == 0 ? CYAN : strncmp(tag, "[dbg]", 5) == 0 ? AMBER : TXT;
    int x = 4 + tft_.textWidth(l, 1) - tft_.textWidth(tag, 1);
    tft_.setTextColor(c, BG); tft_.drawString(tag, x, y, 1);
  }
}

void DebugUI::drawSystem() {
  pill(VERBOSE, dbg::verbose ? "Verbose ON" : "Verbose off", dbg::verbose ? AMBER : CARD, dbg::verbose ? INK : TXT);
  pill(REBOOT, "Reboot", CARD, RED);
  uint32_t total = ESP.getHeapSize(), freeH = ESP.getFreeHeap();
  char b[32];
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
  kv(12, y, 300, "Build", __DATE__ " " __TIME__, MUTED); y += 19;
  kv(12, y, 300, "Serial", "expr say look net debug verbose", MUTED);
}

// =========================================================== input / refresh
void DebugUI::touch(int16_t x, int16_t y) {
  if (screen_ != MENU && BACK.has(x, y)) { show(MENU); return; }
  switch (screen_) {
    case MENU:
      for (int i = 0; i < 6; i++)
        if (tileRect(i).has(x, y)) { if (TILES[i].id == 6) exit(); else show((Screen)TILES[i].id); return; }
      break;
    case INTERNET:
      if (RUN.has(x, y) && !inet_.running) { inet_ = {}; inet_.running = true; inet_.step = 1; drawInternet(); }
      break;
    case PIPELINE:
      for (int i = 0; i < 5; i++)
        if (presetRect(i).has(x, y)) { lastPreset_ = i; lastTurnDone_ = false; net::sendText(PRESETS[i]); drawPipeline(); return; }
      break;
    case SYSTEM:
      if (VERBOSE.has(x, y)) { dbg::verbose = !dbg::verbose; dbg::log("[dbg] verbose %s", dbg::verbose ? "on" : "off"); drawSystem(); }
      else if (REBOOT.has(x, y)) { dbg::log("[dbg] reboot"); delay(200); ESP.restart(); }
      break;
    default: break;
  }
}

void DebugUI::update() {
  if (!active_) return;
  if (screen_ == INTERNET) stepInternet();
  statusChips();
  if (millis() - lastRefresh_ < 500) return;
  lastRefresh_ = millis();
  switch (screen_) {
    case NETWORK: drawNetwork(); break;
    case LOG: drawLog(); break;
    case MENU: { static uint32_t last = 0; if (millis() - last > 3000) { last = millis(); drawMenu(); } break; }
    case SYSTEM: { static uint32_t last = 0; if (millis() - last > 2000) { last = millis(); drawSystem(); } break; }
    case PIPELINE:
      if (lastPreset_ >= 0) { bool d = net::lastTurn().done; if (!d || !lastTurnDone_) drawPipeline(); lastTurnDone_ = d; }
      break;
    default: break;
  }
}
