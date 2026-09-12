"""All tunables in one place. Environment variables override defaults so the same code runs on a laptop and on Modal."""
import os
from pathlib import Path

SAMPLE_RATE = 16000                     # PCM16 mono, both directions (ESP32 I2S mic + DAC)
AUDIO_FRAME_BYTES = 2048                # 64 ms per WebSocket binary frame; keeps ESP32 receive buffers small
MODELS_DIR = Path(os.environ.get("PIXEL_MODELS_DIR", Path(__file__).resolve().parent.parent / "models"))
DATA_DIR = Path(os.environ.get("PIXEL_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

WHISPER_MODEL = os.environ.get("PIXEL_WHISPER_MODEL", "base.en")
STT_ENGINE = os.environ.get("PIXEL_STT_ENGINE", "whisper")          # whisper | parakeet  (Parakeet-TDT 0.6B v2, onnx int8; whisper stays loaded as fallback)
STT_THREADS = int(os.environ.get("PIXEL_STT_THREADS", "2"))
PIPER_VOICE = os.environ.get("PIXEL_PIPER_VOICE", "en_US-lessac-medium")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e2b")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "")   # set for Ollama Cloud

PIXEL_TOKEN = os.environ.get("PIXEL_TOKEN", "")          # shared secret; empty = no auth (local dev only)

# energy VAD
VAD_START_RMS = int(os.environ.get("PIXEL_VAD_START_RMS", 900))
VAD_END_RMS = int(os.environ.get("PIXEL_VAD_END_RMS", 500))
VAD_END_SILENCE_MS = int(os.environ.get("PIXEL_VAD_END_SILENCE_MS", 1100))   # 700 cut sentences at natural pauses on the first 3S test
VAD_MIN_SPEECH_MS = 300
VAD_MAX_UTTERANCE_S = 20

HISTORY_TURNS = 12                       # turns of dialogue kept in the LLM context

EXPRESSIONS = ["neutral", "happy", "excited", "curious", "thinking", "listening", "surprised",
               "suspicious", "annoyed", "sad", "sleepy", "asleep", "love"]
