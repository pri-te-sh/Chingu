#include "ota.h"
#include "log.h"
#include "net.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <Update.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include "mbedtls/sha256.h"
#include "certs.h"

#ifndef PIXEL_FW_VERSION
#define PIXEL_FW_VERSION "0.0.0"
#endif
#ifndef PIXEL_DEVICE_TYPE
#define PIXEL_DEVICE_TYPE "lite"
#endif

namespace ota {
static Manifest latest_;
static const char* state_ = "idle";
static uint8_t progress_ = 0;
static bool available_ = false, pendingValidation_ = false;
static uint32_t bootMs_ = 0, lastCheck_ = 0;
static bool installReq_ = false;
static uint32_t checkSent_ = 0;                                       // millis() when an ota_check went out, 0 = none in flight
static void (*progressCb_)(uint8_t, const char*) = nullptr;
static void report(const char* stage) { if (progressCb_) progressCb_(progress_, stage); }

const char* version() { return PIXEL_FW_VERSION; }
const char* state() { return state_; }
uint8_t progress() { return progress_; }
bool available() { return available_; }
const Manifest& latest() { return latest_; }
void requestInstall() { installReq_ = true; if (!available_) requestCheck(); }
bool takeInstallRequest() { if (!installReq_ || checkSent_) return false; installReq_ = false; return available_ && latest_.valid; }
void onProgress(void (*cb)(uint8_t, const char*)) { progressCb_ = cb; }
uint32_t lastCheckAge() { return lastCheck_ ? millis() - lastCheck_ : 0; }

// "1.2.10" > "1.2.9"
static int cmpVersion(const char* a, const char* b) {
  int ai[3] = {0, 0, 0}, bi[3] = {0, 0, 0};
  sscanf(a, "%d.%d.%d", &ai[0], &ai[1], &ai[2]); sscanf(b, "%d.%d.%d", &bi[0], &bi[1], &bi[2]);
  for (int i = 0; i < 3; i++) if (ai[i] != bi[i]) return ai[i] < bi[i] ? -1 : 1;
  return 0;
}

void begin() {
  bootMs_ = millis();
  Preferences p; p.begin("pixel", false);
  pendingValidation_ = p.getBool("ota_pending", false);
  p.end();
  if (pendingValidation_) dbg::log("[ota] booted new firmware %s - waiting for the brain to confirm it works", PIXEL_FW_VERSION);
}

void markHealthy() {
  if (!pendingValidation_) return;
  pendingValidation_ = false;
  Preferences p; p.begin("pixel", false); p.putBool("ota_pending", false); p.end();
  dbg::log("[ota] firmware %s confirmed healthy", PIXEL_FW_VERSION);
}

bool checking() { return checkSent_ != 0; }

void requestCheck() {
  if (!net::connected()) return;
  state_ = "checking"; checkSent_ = millis();
  net::sendRaw("{\"type\":\"ota_check\"}");
}

void onManifest(JsonVariantConst d) {
  checkSent_ = 0; lastCheck_ = millis(); state_ = "idle";
  if (!d["version"].is<const char*>()) { available_ = false; latest_ = Manifest(); dbg::log("[ota] no release published"); return; }
  Manifest out;
  strlcpy(out.version, d["version"] | "", sizeof out.version); strlcpy(out.url, d["url"] | "", sizeof out.url);
  strlcpy(out.httpUrl, d["http_url"] | "", sizeof out.httpUrl);
  strlcpy(out.sha256, d["sha256"] | "", sizeof out.sha256); strlcpy(out.notes, d["notes"] | "", sizeof out.notes);
  out.size = d["size"] | 0; out.valid = out.url[0] && strlen(out.sha256) == 64;
  latest_ = out;
  available_ = out.valid && cmpVersion(out.version, PIXEL_FW_VERSION) > 0;
  dbg::log("[ota] latest %s (have %s) %s", out.version, PIXEL_FW_VERSION, available_ ? "- update available" : "- up to date");
  if (installReq_ && !available_) installReq_ = false;
}

static void hex(const uint8_t* in, char* out) { for (int i = 0; i < 32; i++) sprintf(out + i * 2, "%02x", in[i]); out[64] = 0; }

static bool doUpdate(const Manifest& m);
struct UpdateJob { const Manifest* m; volatile bool done; bool ok; };
static void updateTask(void* arg) {
  UpdateJob* j = (UpdateJob*)arg;
  j->ok = doUpdate(*j->m); j->done = true;
  vTaskDelete(nullptr);
}

bool update(const Manifest& m) {
  // HTTP + SHA-256 + a 2 KB buffer do not fit on the 8 KB Arduino loop stack (stack-canary panic on 0.4.0): run the
  // download on its own task and just wait here. The brain link is closed first: that frees ~30 KB so the task stack
  // can be allocated at all (with the link up the largest free block is ~16 KB). The image comes over plain HTTP, so
  // no TLS session is needed and heap fragmentation does not matter beyond the stack itself.
  if (!m.valid || !net::wifiUp()) return false;
  net::suspend(); delay(200);
  UpdateJob job{&m, false, false};
  if (xTaskCreatePinnedToCore(updateTask, "ota", 10240, &job, 1, nullptr, 1) != pdPASS) {
    dbg::log("[ota] could not start the update task (heap %u)", ESP.getFreeHeap()); net::resume(); state_ = "failed"; return false;
  }
  while (!job.done) delay(50);
  if (!job.ok) net::resume();
  return job.ok;
}

static bool doUpdate(const Manifest& m) {
  const char* url = m.httpUrl[0] ? m.httpUrl : m.url;              // plain HTTP when offered: the sha256 below is the integrity check
  dbg::log("[ota] downloading %s (%u bytes)", url, m.size);
  state_ = "downloading"; progress_ = 0; report("connecting");
  HTTPClient http; WiFiClientSecure sec; WiFiClient plain;
  http.setTimeout(15000);
  bool ok;
  if (String(url).startsWith("https")) { sec.setCACert(PIXEL_CA_BUNDLE); ok = http.begin(sec, url); } else ok = http.begin(plain, url);
  int code = ok ? http.GET() : -1;
  if (code != 200) { http.end(); state_ = "failed"; dbg::log("[ota] download http %d", code); return false; }
  int len = http.getSize();
  if (len <= 0) len = m.size;
  if (!Update.begin(len)) { http.end(); state_ = "failed"; dbg::log("[ota] not enough space: %s", Update.errorString()); return false; }
  mbedtls_sha256_context sha; mbedtls_sha256_init(&sha); mbedtls_sha256_starts(&sha, 0);
  WiFiClient* stream = http.getStreamPtr();
  static uint8_t buf[2048]; int got = 0; uint32_t lastLog = 0, lastData = millis(), t0 = millis();
  while (http.connected() && got < len) {
    if (millis() - t0 > 600000UL) { dbg::log("[ota] download exceeded 10 minutes"); Update.abort(); http.end(); state_ = "failed"; return false; }
    size_t avail = stream->available();
    if (!avail) {
      if (millis() - lastData > 20000) { dbg::log("[ota] download stalled"); Update.abort(); http.end(); state_ = "failed"; return false; }
      delay(2); continue;
    }
    lastData = millis();
    int n = stream->readBytes(buf, min(avail, sizeof buf));
    if (n <= 0) break;
    if (Update.write(buf, n) != (size_t)n) { dbg::log("[ota] write failed: %s", Update.errorString()); Update.abort(); http.end(); state_ = "failed"; return false; }
    mbedtls_sha256_update(&sha, buf, n);
    got += n; progress_ = (uint8_t)(got * 100UL / len);
    if (millis() - lastLog > 700) { lastLog = millis(); dbg::log("[ota] %u%%", progress_); report("downloading"); }
  }
  http.end();
  state_ = "verifying"; report("verifying");
  uint8_t digest[32]; mbedtls_sha256_finish(&sha, digest); mbedtls_sha256_free(&sha);
  char hexd[65]; hex(digest, hexd);
  if (got != len || strcasecmp(hexd, m.sha256) != 0) {
    dbg::log("[ota] verification FAILED (%d/%d bytes, sha %s)", got, len, strncmp(hexd, m.sha256, 8) ? "mismatch" : "ok"); Update.abort(); state_ = "failed"; return false;
  }
  if (!Update.end(true)) { dbg::log("[ota] finalize failed: %s", Update.errorString()); state_ = "failed"; return false; }
  Preferences p; p.begin("pixel", false); p.putBool("ota_pending", true); p.end();   // next boot must prove itself
  dbg::log("[ota] verified %s - rebooting into it", m.version); report("rebooting");
  delay(300);
  ESP.restart();
  return true;
}

void loop() {
  // self-rollback: a freshly flashed image that cannot reach the brain within 3 minutes is rolled back
  if (pendingValidation_ && millis() - bootMs_ > 180000) {
    if (Update.canRollBack()) { dbg::log("[ota] new firmware never reached the brain - rolling back"); Update.rollBack(); delay(200); ESP.restart(); }
    pendingValidation_ = false;
  }
  // daily check once online (first one 90 s after boot)
  if (checkSent_ && millis() - checkSent_ > 10000) { checkSent_ = 0; lastCheck_ = millis(); state_ = "idle"; installReq_ = false; dbg::log("[ota] manifest request timed out"); }
  if (net::connected() && !checkSent_ && (lastCheck_ == 0 ? millis() - bootMs_ > 90000 : millis() - lastCheck_ > 86400000UL)) requestCheck();
}
}
