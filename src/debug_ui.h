// On-device diagnostics: network status, internet/backend latency tests, pipeline presets, event log, system info.
// Enter with a long press on BOOT (or a 2 s touch hold); the face is paused while this is active.
#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>

class DebugUI {
public:
  struct Rect { int16_t x, y, w, h; bool has(int16_t px, int16_t py) const { return px >= x && px < x + w && py >= y && py < y + h; } };
  explicit DebugUI(TFT_eSPI& tft) : tft_(tft) {}
  void enter();
  void exit();
  bool active() const { return active_; }
  void touch(int16_t x, int16_t y);
  void update();                       // periodic refresh + async test steps
  void setFps(uint16_t fps) { fps_ = fps; }

private:
  enum Screen { MENU, NETWORK, INTERNET, PIPELINE, LOG, SYSTEM, PORTAL };

  // widget kit
  void header(const char* title, bool back);
  void statusChips();
  void card(const Rect& r, uint16_t fill);
  void pill(const Rect& r, const char* label, uint16_t fill, uint16_t text, int font = 2);
  void kv(int x, int y, int w, const char* key, const char* val, uint16_t valColor);
  void hbar(int x, int y, int w, int h, float frac, uint16_t color);
  void gauge(int cx, int cy, int r, float frac, uint16_t color, const char* big, const char* small);
  void wrap(int x, int y, int w, const char* text, int font, int maxLines, uint16_t color);
  void iconWifi(int cx, int cy, int rssi, uint16_t off);
  void iconGlobe(int cx, int cy, uint16_t c);
  void iconChat(int cx, int cy, uint16_t c);
  void iconLog(int cx, int cy, uint16_t c);
  void iconChip(int cx, int cy, uint16_t c);
  void iconFace(int cx, int cy, uint16_t c);

  void show(Screen s);
  void drawMenu();
  void drawNetwork();
  void drawInternet();
  void stepInternet();
  void drawPipeline();
  void drawLog();
  void drawSystem();
  void drawPortal();
  void iconQr(int cx, int cy, uint16_t c);

  TFT_eSPI& tft_;
  bool active_ = false;
  Screen screen_ = MENU;
  uint32_t lastRefresh_ = 0, lastChips_ = 0;
  uint16_t fps_ = 0;

  struct { int step = 0; uint32_t dns = 0, http = 0; int httpCode = 0; uint32_t rtt[5] = {0}; int pingIdx = 0; uint32_t pingT0 = 0; bool running = false; } inet_;
  int lastPreset_ = -1;
  int lastLogCount_ = -1;
  bool lastTurnDone_ = true;
};
