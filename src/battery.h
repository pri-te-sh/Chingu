// Battery awareness for Pixel-Lite: BAT+ through the board's 100K/100K divider into IO34 (ADC1_CH6).
// The TP4054 charger and the SL2305 power-path switch are pure hardware and its CHRG pin is not routed to the ESP32,
// so charging is *inferred* from the voltage: a quick step up/down when USB is (un)plugged, and a slow trend otherwise.
#pragma once
#include <Arduino.h>

namespace battery {
enum State : uint8_t { ST_UNKNOWN, ST_BATTERY, ST_CHARGING, ST_FULL, ST_LOW, ST_CRITICAL, ST_USB };   // ST_USB: powered over USB but the cell is not taking charge

void begin();
void loop();                       // samples every 2 s; cheap
uint16_t millivolts();             // smoothed terminal voltage (0 when nothing is connected)
uint16_t ocvMillivolts();          // terminal voltage corrected for load/charge sag -> what percent() is based on
int sinceChargeMv();               // terminal voltage change since charging began (0 when not charging)
uint32_t chargeMinutes();          // minutes since charging began (0 when not charging)
uint8_t percent();                 // LiPo open-circuit curve, 0..100
State state();
const char* stateName();           // "unknown" | "battery" | "charging" | "full" | "low" | "critical" | "usb"
int trendMvPerMin();               // slow trend (10 min window), 0 until enough history
bool changed();                    // one-shot: state changed since last call (main loop pushes a status to the brain)
}
