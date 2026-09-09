# Chingu — Pixel, a tiny AI desk companion

An always-listening voice companion with an animated face, running on a Hosyond 3.2" ESP32-32E touch display
(ESP32-WROOM-32E, ST7789P3 240x320, XPT2046 touch, onboard speaker amp, LiPo charging).

```
ESP32 "Pixel"  ──wss──►  Modal (scale-to-zero CPU container)
  animated face             faster-whisper  →  Ollama Cloud gemma4  →  Piper TTS
  wake word / mic           JSON {expression, intensity} + PCM16 speech back
  speaker
```

## Layout
- `src/` — firmware (PlatformIO, Arduino): `face.*` expression engine, `net.*` Wi-Fi/WebSocket client,
  `debug_ui.*` on-device diagnostics (long-press BOOT), `board.h` pin map.
- `backend/` — Python brain: `pixel/server.py` WebSocket protocol, `stt.py`, `llm.py`, `tts.py`, `vad.py`;
  `modal_app.py` deployment; `tools/laptop_client.py` stands in for the device.
- `tools/` — serial helpers (`monitor.py`, `expr_tour.py`).
- `docs/vendor/schematic.pdf` — board schematic (vendor demo pack is downloaded, not committed).

## Firmware
```bash
python3 -m venv .venv && .venv/bin/pip install platformio esptool pyserial
cp include/secrets.h.example include/secrets.h   # fill in Wi-Fi + backend
.venv/bin/pio run -t upload
.venv/bin/python tools/monitor.py 10             # serial log; add --send "say hello"
```
Serial commands: `expr <name> [intensity] [holdMs]`, `say <text>`, `look x y`, `sleep`, `wake`, `net`, `debug`, `verbose`.

## Backend
```bash
cd backend
./run_local.sh                                   # local: Ollama at localhost:11434, model gemma4:e2b
uv run --extra laptop python tools/laptop_client.py --text "hello"
modal deploy modal_app.py                        # cloud: needs secrets pixel-token + ollama-api-key
```

## Hardware notes
- Pins: LCD CS15 DC2 SCK14 MOSI13 MISO12 BL27; touch CS33 IRQ36 (shared SPI); RGB LED 22/16/17 (active low);
  speaker amp enable IO4 (low = on), DAC IO26; expansion connectors are 1.25 mm pitch.
- Planned I2S mic (INMP441): SCK IO25, WS IO32, SD IO35.
