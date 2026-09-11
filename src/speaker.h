// Speaker output for Pixel-Lite: PCM16 mono 16 kHz from the brain -> ESP32 built-in DAC (GPIO26) -> on-board amp -> speaker connector.
#pragma once
#include <Arduino.h>

namespace speaker {
void begin();
void feed(const uint8_t* pcm16, size_t len);   // audio frame from the brain (2 KB, 16 kHz, mono)
void loop();                                    // main-loop housekeeping (fires the playback_end callback); the DAC is fed by its own task
void flush();                                   // drop everything queued (interrupt / cancel)
void endOfSpeech();                             // brain finished sending; playback_end fires when the buffer drains
bool playing();
float level();                                  // 0..1 recent loudness, drives the mouth
void setVolume(float v);                        // 0..1
void onPlaybackEnd(void (*cb)());               // called once when the queue drains after endOfSpeech()
void tone(float hz, uint16_t ms);              // test tone, generated on the fly
void setClock(uint32_t hz);
void reinit(bool apll, uint32_t hz);            // diagnostic: reinstall the I2S driver with/without APLL                     // diagnostic: re-program the I2S clock (the DAC path runs fast on IDF 4.4)
}
