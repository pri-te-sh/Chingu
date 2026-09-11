#include "battery.h"
#include "board.h"
#include "log.h"
#include <driver/adc.h>
#include <esp_adc_cal.h>

namespace battery {
static esp_adc_cal_characteristics_t chars_;
static float ema_ = 0; static bool have_ = false;
static uint32_t lastSample_ = 0, lastSlow_ = 0, fullSince_ = 0;
static uint16_t fast_[5]; static uint8_t fastN_ = 0, fastI_ = 0;      // 2 s apart -> 10 s window (plug/unplug steps)
static uint16_t slow_[30]; static uint8_t slowN_ = 0, slowI_ = 0;     // 20 s apart -> 10 min window (charge/discharge trend)
static State state_ = ST_UNKNOWN;
static bool changed_ = false;

static const struct { uint16_t mv; uint8_t pct; } CURVE[] = {          // LiPo open-circuit voltage -> charge, coarse
  {4200, 100}, {4100, 90}, {4000, 78}, {3900, 62}, {3800, 45}, {3700, 25}, {3600, 12}, {3500, 5}, {3400, 2}, {3300, 0}};

static uint16_t readMv() {
  uint32_t acc = 0;
  for (int i = 0; i < 16; i++) acc += adc1_get_raw(ADC1_CHANNEL_6);
  return esp_adc_cal_raw_to_voltage(acc / 16, &chars_) * 2;            // divider halves BAT+
}

void begin() {
  adc1_config_width(ADC_WIDTH_BIT_12);
  adc1_config_channel_atten(ADC1_CHANNEL_6, ADC_ATTEN_DB_11);         // IO34, up to ~3.1 V at the pin (4.2 V / 2 = 2.1 V)
  esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_11, ADC_WIDTH_BIT_12, 1100, &chars_);
  ema_ = readMv(); have_ = true;
  dbg::log("[bat] %u mV at boot", (unsigned)ema_);
}

uint16_t millivolts() { return have_ ? (uint16_t)(ema_ + 0.5f) : 0; }

uint8_t percent() {
  uint16_t mv = millivolts();
  if (mv >= CURVE[0].mv) return 100;
  for (size_t i = 1; i < sizeof CURVE / sizeof CURVE[0]; i++)
    if (mv >= CURVE[i].mv) {
      float f = (float)(mv - CURVE[i].mv) / (CURVE[i - 1].mv - CURVE[i].mv);
      return (uint8_t)(CURVE[i].pct + f * (CURVE[i - 1].pct - CURVE[i].pct) + 0.5f);
    }
  return 0;
}

State state() { return state_; }
const char* stateName() {
  static const char* N[] = {"unknown", "battery", "charging", "full", "low", "critical"};
  return N[state_];
}

int trendMvPerMin() {
  if (slowN_ < 30) return 0;
  int oldest = slow_[slowI_];                                          // ring: slot about to be overwritten is the oldest (10 min ago)
  return ((int)millivolts() - oldest) / 10;
}

static void setState(State s) {
  if (s == state_) return;
  dbg::log("[bat] %s -> %s (%u mV, %u%%)", stateName(), (const char*[]){"unknown", "battery", "charging", "full", "low", "critical"}[s], millivolts(), percent());
  state_ = s; changed_ = true;
}

void loop() {
  uint32_t now = millis();
  if (now - lastSample_ < 2000) return;
  lastSample_ = now;
  uint16_t raw = readMv();
  ema_ = ema_ * 0.7f + raw * 0.3f;
  uint16_t mv = millivolts();

  if (mv < 2500) { setState(ST_UNKNOWN); fastN_ = slowN_ = 0; return; }   // JP2 empty (or ADC floating)

  // fast window: a step of >= 25 mV within 10 s is USB being plugged (up) or pulled (down)
  int step = 0;
  if (fastN_ == 5) step = (int)mv - (int)fast_[fastI_];
  fast_[fastI_] = mv; fastI_ = (fastI_ + 1) % 5; if (fastN_ < 5) fastN_++;
  if (step >= 25 && state_ != ST_CHARGING && state_ != ST_FULL) { setState(ST_CHARGING); fullSince_ = 0; fastN_ = 0; }
  else if (step <= -25 && (state_ == ST_CHARGING || state_ == ST_FULL || state_ == ST_UNKNOWN)) { setState(mv <= 3300 ? ST_CRITICAL : mv <= 3500 ? ST_LOW : ST_BATTERY); fastN_ = 0; }

  // slow window: one sample per 20 s; the trend settles what a reboot can't see
  if (now - lastSlow_ >= 20000) {
    lastSlow_ = now;
    slow_[slowI_] = mv; slowI_ = (slowI_ + 1) % 30; if (slowN_ < 30) slowN_++;
    int tr = trendMvPerMin() * 10;                                     // mV over the 10 min window
    if (state_ == ST_UNKNOWN && slowN_ >= 2)                          // ~20 s after boot: provisional verdict from voltage alone
      setState(mv >= 4150 ? ST_FULL : mv <= 3300 ? ST_CRITICAL : mv <= 3500 ? ST_LOW : ST_BATTERY);
    if (slowN_ == 30) {
      if ((state_ == ST_BATTERY || state_ == ST_LOW) && tr >= 6) setState(ST_CHARGING);
      else if (state_ == ST_CHARGING && tr <= -6 && mv < 4100) setState(ST_BATTERY);
    }
  }

  // full: the TP4054 holds ~4.2 V while topping off and after it terminates
  if (state_ == ST_CHARGING) {
    if (mv >= 4180) { if (!fullSince_) fullSince_ = now; else if (now - fullSince_ > 180000) setState(ST_FULL); }
    else fullSince_ = 0;
  }
  if (state_ == ST_FULL && mv < 4050) setState(ST_BATTERY);            // drained a bit: USB must be gone
  if (state_ == ST_BATTERY && mv <= 3500) setState(mv <= 3300 ? ST_CRITICAL : ST_LOW);
  if (state_ == ST_LOW && mv <= 3300) setState(ST_CRITICAL);
  if ((state_ == ST_LOW || state_ == ST_CRITICAL) && mv >= 3650) setState(ST_BATTERY);   // recovered (e.g. load dropped); charging is caught by the step
}

bool changed() { bool c = changed_; changed_ = false; return c; }
}
