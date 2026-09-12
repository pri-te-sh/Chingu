#include "board.h"
#ifdef PIXEL_BOARD_3S
// Pixel-3S battery: the AXP2101 PMIC measures the cell and manages charging, so nothing here is inferred.
#include "battery.h"
#include "log.h"
#include <Wire.h>
#define XPOWERS_CHIP_AXP2101
#include <XPowersLib.h>

namespace battery {
static XPowersPMU pmu_;
static bool ok_ = false, changed_ = false;
static State state_ = ST_UNKNOWN;
static uint16_t mv_ = 0; static int pct_ = -1;
static uint32_t lastSample_ = 0, chargeStart_ = 0; static uint16_t chargeStartMv_ = 0;
static uint16_t hist_[30]; static uint8_t histN_ = 0, histI_ = 0; static uint32_t lastSlow_ = 0;

void begin() {
  ok_ = pmu_.begin(Wire, AXP2101_SLAVE_ADDRESS, PIN_I2C_SDA, PIN_I2C_SCL);
  if (!ok_) { dbg::log("[bat] AXP2101 not found"); return; }
  pmu_.enableBattDetection(); pmu_.enableBattVoltageMeasure(); pmu_.enableVbusVoltageMeasure(); pmu_.enableSystemVoltageMeasure();
  pmu_.setChargeTargetVoltage(XPOWERS_AXP2101_CHG_VOL_4V2);
  pmu_.setChargerConstantCurr(XPOWERS_AXP2101_CHG_CUR_500MA);      // 3000 mAh cell: 0.17C, gentle and cool
  pmu_.setVbusCurrentLimit(XPOWERS_AXP2101_VBUS_CUR_LIM_1500MA);
  pmu_.enableCellbatteryCharge();
  // Camera rails (OV2640 core 1.5 V / IO 2.8 V). An unpowered sensor on the shared I2C bus clamps SDA/SCL, so they go up first.
  pmu_.setBLDO1Voltage(1500); pmu_.setBLDO2Voltage(2800); pmu_.enableBLDO1(); pmu_.enableBLDO2();
  mv_ = pmu_.getBattVoltage(); pct_ = pmu_.getBatteryPercent();
  dbg::log("[bat] AXP2101 ok: %u mV, %d%%, vbus %d, charging %d", mv_, pct_, pmu_.isVbusIn(), pmu_.isCharging());
}

static void setState(State s) {
  if (s == state_) return;
  static const char* N[] = {"unknown", "battery", "charging", "full", "low", "critical", "usb"};
  dbg::log("[bat] %s -> %s (%u mV, %d%%)", N[state_], N[s], mv_, pct_);
  if (s == ST_CHARGING) { chargeStart_ = millis(); chargeStartMv_ = mv_; } else if (s != ST_FULL) chargeStart_ = 0;
  state_ = s; changed_ = true;
}

void loop() {
  if (!ok_) return;
  uint32_t now = millis();
  if (now - lastSample_ < 2000) return;
  lastSample_ = now;
  bool bat = pmu_.isBatteryConnect();
  mv_ = bat ? pmu_.getBattVoltage() : 0; pct_ = bat ? pmu_.getBatteryPercent() : -1;
  if (now - lastSlow_ >= 20000) { lastSlow_ = now; hist_[histI_] = mv_; histI_ = (histI_ + 1) % 30; if (histN_ < 30) histN_++; }
  if (!bat) { setState(ST_UNKNOWN); return; }
  bool vbus = pmu_.isVbusIn();
  if (vbus) {
    if (pmu_.isCharging()) setState(ST_CHARGING);
    else if (mv_ >= 4100 || pct_ >= 99) setState(ST_FULL);
    else setState(ST_USB);
  } else setState(mv_ <= 3300 ? ST_CRITICAL : mv_ <= 3500 ? ST_LOW : ST_BATTERY);
}

uint16_t millivolts() { return mv_; }
uint16_t ocvMillivolts() { return mv_; }                  // the PMIC's fuel gauge already accounts for load
uint8_t percent() {
  if (pct_ >= 0) return (uint8_t)min(100, pct_);
  int mv = mv_; return (uint8_t)constrain((mv - 3300) * 100 / 900, 0, 100);   // gauge not ready yet: crude linear fallback
}
State state() { return state_; }
const char* stateName() { static const char* N[] = {"unknown", "battery", "charging", "full", "low", "critical", "usb"}; return N[state_]; }
int trendMvPerMin() { if (histN_ < 30) return 0; return ((int)mv_ - (int)hist_[histI_]) / 10; }
int sinceChargeMv() { return chargeStart_ ? (int)mv_ - (int)chargeStartMv_ : 0; }
uint32_t chargeMinutes() { return chargeStart_ ? (millis() - chargeStart_) / 60000 : 0; }
bool changed() { bool c = changed_; changed_ = false; return c; }
}
#endif
