// Microphone capture (Pixel-3S): ES8311 ADC -> 16 kHz mono PCM -> G.711 mu-law (8 bits/sample, 128 kbit/s) -> brain.
// Half-duplex: nothing is sent while the speaker plays (no acoustic echo cancellation yet). The brain runs the VAD.
#pragma once
#include <Arduino.h>

namespace mic {
void begin();
void loop();                 // main loop: ships queued frames over the brain link
float level();               // 0..1 recent loudness (for a listening indicator)
bool streaming();            // true while frames are being sent
void setMuted(bool m);       // hard mute (settings toggle)
bool muted();
}
