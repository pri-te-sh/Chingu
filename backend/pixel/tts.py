import os
"""Text-to-speech with Piper on CPU. Output is resampled to the device sample rate as PCM16 mono."""
import json
import urllib.request
from pathlib import Path
import numpy as np
from piper import PiperVoice
from . import config as C

_voice: PiperVoice | None = None
_HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _voice_files() -> tuple[Path, Path]:
    d = C.MODELS_DIR / "piper"
    d.mkdir(parents=True, exist_ok=True)
    onnx, cfg = d / f"{C.PIPER_VOICE}.onnx", d / f"{C.PIPER_VOICE}.onnx.json"
    if not onnx.exists() or not cfg.exists():
        lang, region_voice, quality = C.PIPER_VOICE.split("-")[0], C.PIPER_VOICE.rsplit("-", 1)[0], C.PIPER_VOICE.rsplit("-", 1)[1]
        family, code = lang.split("_")[0], lang                       # en, en_US
        base = f"{_HF}/{family}/{code}/{region_voice.split('-')[1]}/{quality}/{C.PIPER_VOICE}"
        for url, dest in ((f"{base}.onnx", onnx), (f"{base}.onnx.json", cfg)):
            if not dest.exists():
                print(f"[tts] downloading {url}")
                urllib.request.urlretrieve(url, dest)
    return onnx, cfg


def load() -> PiperVoice:
    global _voice
    if _voice is None:
        onnx, cfg = _voice_files()
        _voice = PiperVoice.load(str(onnx), config_path=str(cfg))
    return _voice


def _resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return x
    n = int(len(x) * dst / src)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x.astype(np.float32)).astype(np.int16)


_GAIN_DB = float(os.environ.get("PIXEL_TTS_GAIN_DB", "8"))    # Piper peaks near 0 dBFS but averages ~-18 dBFS; small speakers need more

def _loud(pcm: np.ndarray) -> np.ndarray:
    """Soft-knee limiter: apply make-up gain, squash peaks with tanh instead of hard clipping."""
    if _GAIN_DB <= 0: return pcm
    y = pcm.astype(np.float32) * (10 ** (_GAIN_DB / 20) / 32768.0)
    return (np.tanh(y) * 32000).astype(np.int16)


def synthesize(text: str):
    """Yield PCM16 chunks at C.SAMPLE_RATE for a piece of text."""
    voice = load()
    src_rate = voice.config.sample_rate
    if hasattr(voice, "synthesize_stream_raw"):            # piper-tts 1.2.x
        for raw in voice.synthesize_stream_raw(text):
            yield _loud(_resample(np.frombuffer(raw, dtype=np.int16), src_rate, C.SAMPLE_RATE)).tobytes()
    else:                                                    # piper-tts >= 1.3: AudioChunk objects
        for chunk in voice.synthesize(text):
            yield _loud(_resample(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16), chunk.sample_rate, C.SAMPLE_RATE)).tobytes()
