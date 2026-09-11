// Throughput test against the brain over the same TLS path speech uses. Runs on its own 16 KB task (TLS on the
// loop task overflowed its stack during OTA); the caller suspends the brain link first so two TLS contexts never coexist.
#pragma once
#include <Arduino.h>

namespace speedtest {
struct Result { uint32_t downKbps = 0, upKbps = 0; bool done = false; int downErr = 0, upErr = 0; };
bool start(size_t downBytes, size_t upBytes);   // false if a run is already in progress or the task could not start
bool busy();
const Result& result();
}
