#include "speedtest.h"
#include "prefs.h"
#include "net.h"
#include "log.h"
#include <WiFiClient.h>
#include <HTTPClient.h>
#include <esp_heap_caps.h>

namespace speedtest {
class ZeroStream : public Stream {                 // a supply of zero bytes for the upload body
  size_t left_;
public:
  explicit ZeroStream(size_t n) : left_(n) {}
  int available() override { return left_ > 0x7fff ? 0x7fff : (int)left_; }
  int read() override { if (!left_) return -1; left_--; return 0; }
  int peek() override { return left_ ? 0 : -1; }
  size_t readBytes(char* buf, size_t n) override { size_t k = min(n, left_); memset(buf, 0, k); left_ -= k; return k; }
  size_t write(uint8_t) override { return 0; }
};

static Result res_; static volatile bool busy_ = false; static size_t downBytes_, upBytes_;

static String url(const char* path) {
  // plain HTTP on purpose: a second TLS session does not fit next to the brain link on this heap; Caddy keeps /api/speedtest/* open on :80
  String u = String("http://") + prefs::brainHost;
  if (!prefs::brainTls && prefs::brainPort != 80) u += String(":") + prefs::brainPort;
  return u + path;
}

static uint32_t download(size_t bytes, int& err) {
  HTTPClient http; WiFiClient plain; http.setTimeout(15000);
  String u = url("/api/speedtest/down?bytes=") + bytes;
  if (!http.begin(plain, u)) { err = -100; return 0; }
  int code = http.GET();
  if (code != 200) { err = code; dbg::log("[speed] download %d %s", code, http.errorToString(code).c_str()); http.end(); return 0; }
  WiFiClient* s = http.getStreamPtr();
  uint8_t buf[1024]; size_t got = 0; uint32_t t0 = millis(), last = t0;
  while (got < bytes && millis() - last < 8000) {
    int n = s->read(buf, sizeof buf);
    if (n > 0) { got += n; last = millis(); }
    else { if (!s->connected()) break; delay(1); }
  }
  uint32_t ms = max<uint32_t>(1, millis() - t0);
  http.end();
  dbg::log("[speed] down %u/%u bytes in %lu ms", (unsigned)got, (unsigned)bytes, ms);
  if (got < bytes) { err = -101; return 0; }
  return (uint32_t)((uint64_t)got * 8 / ms);
}

static uint32_t upload(size_t bytes, int& err) {
  HTTPClient http; WiFiClient plain; http.setTimeout(15000); http.setReuse(true);
  String u = url("/api/speedtest/up");
  if (!http.begin(plain, u)) { err = -100; return 0; }
  http.addHeader("Content-Type", "application/octet-stream");
  ZeroStream warm(1);
  int code = http.sendRequest("POST", &warm, 1);                     // connect + one round trip, not timed
  if (code != 200) { err = code; dbg::log("[speed] upload warm-up %d %s", code, http.errorToString(code).c_str()); http.end(); return 0; }
  http.addHeader("Content-Type", "application/octet-stream");
  ZeroStream body(bytes); uint32_t t0 = millis();
  code = http.sendRequest("POST", &body, bytes);
  uint32_t ms = max<uint32_t>(1, millis() - t0);
  http.end();
  dbg::log("[speed] up %u bytes in %lu ms http %d", (unsigned)bytes, ms, code);
  if (code != 200) { err = code; return 0; }
  return (uint32_t)((uint64_t)bytes * 8 / ms);
}

static void task(void*) {
  // The brain link is paused for the few seconds this takes: the ESP32's heap is tight and the measurement is cleaner.
  net::suspend(); delay(200);
  dbg::log("[speed] start (heap %u, largest block %u)", ESP.getFreeHeap(), heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
  res_.downKbps = download(downBytes_, res_.downErr);
  res_.upKbps = upload(upBytes_, res_.upErr);
  net::resume();
  res_.done = true; busy_ = false;
  vTaskDelete(nullptr);
}

bool start(size_t downBytes, size_t upBytes) {
  if (busy_) return false;
  res_ = Result(); downBytes_ = downBytes; upBytes_ = upBytes; busy_ = true;
  if (xTaskCreatePinnedToCore(task, "speed", 8192, nullptr, 1, nullptr, 1) != pdPASS) {   // plain HTTP: no TLS on this stack
    dbg::log("[speed] task alloc failed (heap %u, largest block %u)", ESP.getFreeHeap(), heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    busy_ = false; res_.done = true; res_.downErr = res_.upErr = -102; return false;
  }
  return true;
}
bool busy() { return busy_; }
const Result& result() { return res_; }
}
