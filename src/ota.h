// Over-the-air firmware updates: manifest check, verified download into the inactive slot, self-rollback.
#pragma once
#include <Arduino.h>

namespace ota {
struct Manifest { char version[24] = ""; char url[200] = ""; char sha256[65] = ""; uint32_t size = 0; char notes[120] = ""; bool valid = false; };

void begin();                       // call at boot: handles the post-update validation / rollback timer
void loop();                        // periodic manifest check (boot + daily)
void markHealthy();                 // brain reached after an update -> keep this image
bool check(Manifest& out);          // synchronous manifest fetch; true if a newer version exists
bool update(const Manifest& m);     // download + verify + reboot (does not return on success)
const char* version();
const char* state();                // "idle" | "checking" | "downloading" | "verifying" | "failed"
uint8_t progress();                 // 0..100 while downloading
bool available();                   // a newer version is known
const Manifest& latest();
void requestInstall();               // portal/brain asked us to update (checked + installed from the main loop)
bool takeInstallRequest();
void onProgress(void (*cb)(uint8_t pct, const char* stage));   // UI hook while downloading
uint32_t lastCheckAge();            // ms since the last manifest check, 0 = never
}
