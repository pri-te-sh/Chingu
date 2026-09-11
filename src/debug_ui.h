// On-device Settings: a small hierarchical menu (Connectivity / Sound / Power / Pixel / Diagnostics / Face) with
// live status chips. Enter with a long press on BOOT (or a 2 s touch hold); the face is paused while this is active.
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

  enum Screen : uint8_t { MENU, CONNECTIVITY, PIXEL, DIAGNOSTICS,          // menus
                          WIFI, NETWORK, SOUND, POWER, PORTAL, UPDATE, SYSTEM, RESET, PIPELINE, LOG };   // leaves
  enum Icon : uint8_t { IC_WIFI, IC_GLOBE, IC_CHAT, IC_LOG, IC_CHIP, IC_QR, IC_GEAR, IC_DOWNLOAD, IC_SPEAKER, IC_FACE, IC_BATTERY, IC_LINK, IC_RESET };
  struct Tile { const char* label; Icon icon; Screen target; };

private:
  // widget kit
  void header(Screen s);
  void statusChips();
  void card(const Rect& r, uint16_t fill);
  void pill(const Rect& r, const char* label, uint16_t fill, uint16_t text, int font = 2);
  void kv(int x, int y, int w, const char* key, const char* val, uint16_t valColor);
  const char* fit(const char* text, int w, int font);   // truncates with "..." so it never runs off the screen
  void hbar(int x, int y, int w, int h, float frac, uint16_t color);
  void gauge(int cx, int cy, int r, float frac, uint16_t color, const char* big, const char* small);
  void wrap(int x, int y, int w, const char* text, int font, int maxLines, uint16_t color, uint16_t bg = 0xFFFF);   // bg 0xFFFF = screen BG
  void icon(Icon ic, int cx, int cy);
  void iconWifi(int cx, int cy, int rssi, uint16_t off);
  void iconGlobe(int cx, int cy, uint16_t c);
  void iconChat(int cx, int cy, uint16_t c);
  void iconLog(int cx, int cy, uint16_t c);
  void iconChip(int cx, int cy, uint16_t c);
  void iconFace(int cx, int cy, uint16_t c);
  void iconQr(int cx, int cy, uint16_t c);
  void iconGear(int cx, int cy, uint16_t c);
  void iconDownload(int cx, int cy, uint16_t c);
  void iconSpeaker(int cx, int cy, uint16_t c);
  void iconBattery(int cx, int cy, uint16_t c, float frac);
  void iconLink(int cx, int cy, uint16_t c);
  void iconReset(int cx, int cy, uint16_t c);

  void show(Screen s);
  void drawTiles(const Tile* tiles, int n);
  void drawWifi();
  void drawNetwork();
  void drawSpeed();          // results block on the Network screen
  void stepSpeed();          // one async step of the speed test per update()
  void drawSound();
  void drawPower();
  void drawPortal();
  void drawUpdate();
  void drawSystem();
  void drawReset();
  void drawPipeline();
  void drawLog();

  TFT_eSPI& tft_;
  bool active_ = false;
  Screen screen_ = MENU;
  uint32_t lastRefresh_ = 0, lastChips_ = 0;
  uint16_t fps_ = 0;
  bool checked_ = false;     // UPDATE: a manifest check ran since the screen opened
  uint8_t confirm_ = 0;      // destructive actions: which one is awaiting its second tap (1 wifi, 2 unpair, 3 factory)

  struct { int step = 0; uint32_t dns = 0, http = 0; int httpCode = 0; uint32_t rtt[5] = {0}; int pingIdx = 0; uint32_t pingT0 = 0;
           uint32_t downKbps = 0, upKbps = 0; bool downFail = false, upFail = false; bool running = false; } speed_;
  int lastPreset_ = -1;
  int lastLogCount_ = -1;
  bool lastTurnDone_ = true;
};
