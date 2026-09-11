#include "speedtest.h"
#include "prefs.h"
#include "certs.h"
#include "log.h"
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

namespace speedtest {
class ZeroStream : public Stream {                 // an endless supply of zero bytes for the upload body
  size_t left_;
public:
  explicit ZeroStream(size_t n) : left_(n) {}
  int available() override { return left_ > 0x7fff ? 0x7fff : (int)left_; }
  int read() override { if (!left_) return -1; left_--; return 0; }
  int peek() override { return left_ ? 0 : -1; }
  size_t readBytes(char* buf, size_t n) override { size_t k = min(n, left_); memset(buf, 0, k); left_ -= k; return k; }
  size_t write(uint8_t) override { return 0; }
};

static bool open(HTTPClient& http, WiFiClientSecure& tls, WiFiClient& plain, const char* path) {
  char url[192];
  snprintf(url, sizeof url, "%s://%s:%u%s", prefs::brainTls ? "https" : "http", prefs::brainHost, prefs::brainPort, path);
  http.setConnectTimeout(8000); http.setTimeout(15000); http.setReuse(true);
  if (prefs::brainTls) { tls.setCACert(PIXEL_CA_BUNDLE); tls.setHandshakeTimeout(10); return http.begin(tls, url); }
  return http.begin(plain, url);
}

uint32_t download(size_t bytes) {
  WiFiClientSecure tls; WiFiClient plain; HTTPClient http;
  char path[64]; snprintf(path, sizeof path, "/api/speedtest/down?bytes=%u", (unsigned)bytes);
  if (!open(http, tls, plain, path)) return 0;
  int code = http.GET();
  if (code != 200) { dbg::log("[speed] download http %d", code); http.end(); return 0; }
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
  return got < bytes ? 0 : (uint32_t)((uint64_t)got * 8 / ms);
}

uint32_t upload(size_t bytes) {
  WiFiClientSecure tls; WiFiClient plain; HTTPClient http;
  if (!open(http, tls, plain, "/api/speedtest/up")) return 0;
  http.addHeader("Content-Type", "application/octet-stream");
  ZeroStream warm(1); uint32_t t0 = millis();
  int code = http.sendRequest("POST", &warm, 1);                     // handshake + one round trip
  uint32_t overhead = millis() - t0;
  if (code != 200) { dbg::log("[speed] upload warm-up http %d", code); http.end(); return 0; }
  http.addHeader("Content-Type", "application/octet-stream");
  ZeroStream body(bytes); t0 = millis();
  code = http.sendRequest("POST", &body, bytes);
  uint32_t ms = millis() - t0;
  http.end();
  dbg::log("[speed] up %u bytes in %lu ms (warm-up %lu ms) http %d", (unsigned)bytes, ms, overhead, code);
  if (code != 200) return 0;
  return (uint32_t)((uint64_t)bytes * 8 / max<uint32_t>(1, ms));    // connection was already warm: this is transfer + one round trip
}
}
