// Throughput test against the brain over the same TLS path speech uses. Blocking (seconds); run it from the
// diagnostics screen only, with the brain link suspended so two TLS contexts never coexist on this heap.
#pragma once
#include <Arduino.h>

namespace speedtest {
uint32_t download(size_t bytes);   // kbit/s, 0 on failure (handshake excluded: timed from first body byte)
uint32_t upload(size_t bytes);     // kbit/s, 0 on failure (a 1-byte warm-up POST is subtracted to exclude the handshake)
}
