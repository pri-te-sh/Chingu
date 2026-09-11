// Over-the-air firmware updates: manifest over the brain WebSocket, verified download over plain HTTP into the
// inactive slot (SHA-256 from the manifest), self-rollback if the new image never reaches the brain.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

namespace ota {
struct Manifest { char version[24] = ""; char url[200] = ""; char httpUrl[200] = ""; char sha256[65] = ""; uint32_t size = 0; char notes[120] = ""; bool valid = false; };

void begin();                       // call at boot: handles the post-update validation / rollback timer
void loop();                        // periodic manifest check (boot + daily), check timeouts
void markHealthy();                 // brain reached after an update -> keep this image
void requestCheck();                // ask the brain for the latest release (reply arrives via onManifest)
void onManifest(JsonVariantConst m);// net.cpp hands the brain's ota_manifest reply here
bool checking();
bool update(const Manifest& m);     // download + verify + reboot (does not return on success)
const char* version();
const char* state();                // "idle" | "checking" | "downloading" | "verifying" | "failed"
uint8_t progress();                 // 0..100 while downloading
bool available();                   // a newer version is known
const Manifest& latest();
void requestInstall();              // portal/brain/user asked us to update: checks first if needed, then installs from the main loop
bool takeInstallRequest();          // true once a newer, valid release is known and an install was requested
void onProgress(void (*cb)(uint8_t pct, const char* stage));   // UI hook while downloading
uint32_t lastCheckAge();            // ms since the last manifest check, 0 = never
}
