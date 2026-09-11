// Speaker output for Pixel-Lite: PCM16 mono 16 kHz from the brain -> ESP32 built-in DAC (GPIO26) -> on-board amp -> speaker connector.
#pragma once
#include <Arduino.h>

namespace speaker {
void begin();
void feed(const uint8_t* pcm16, size_t len);   // audio frame from the brain (2 KB, 16 kHz, mono)
void loop();                                    // push buffered audio to the DAC; call often
void flush();                                   // drop everything queued (interrupt / cancel)
void endOfSpeech();                             // brain finished sending; playback_end fires when the buffer drains
bool playing();
float level();                                  // 0..1 recent loudness, drives the mouth
void setVolume(float v);                        // 0..1
void onPlaybackEnd(void (*cb)());               // called once when the queue drains after endOfSpeech()
}
