// Shared log: every line goes to Serial and into a small ring buffer the debug UI can display.
#pragma once
#include <Arduino.h>

namespace dbg {
static const int LOG_LINES = 24, LOG_COLS = 44;
void log(const char* fmt, ...);
int count();                              // total lines ever logged
const char* line(int fromNewest);         // 0 = newest; nullptr when out of range
extern bool verbose;                      // extra chatter (binary frame counts, VAD flips, pings)
}
