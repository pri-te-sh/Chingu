#include "log.h"

namespace dbg {
bool verbose = false;
static char ring[LOG_LINES][LOG_COLS];
static int total = 0;

void log(const char* fmt, ...) {
  char buf[160];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
  Serial.println(buf);
  char* slot = ring[total % LOG_LINES];
  snprintf(slot, LOG_COLS, "%6lu %s", millis() / 1000, buf);
  total++;
}
int count() { return total; }
const char* line(int fromNewest) {
  if (fromNewest < 0 || fromNewest >= min(total, LOG_LINES)) return nullptr;
  return ring[(total - 1 - fromNewest) % LOG_LINES];
}
}
