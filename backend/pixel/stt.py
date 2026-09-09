"""Speech-to-text with faster-whisper on CPU (int8). Model is loaded once per process."""
import numpy as np
from faster_whisper import WhisperModel
from . import config as C

_model: WhisperModel | None = None


def load():
    global _model
    if _model is None:
        _model = WhisperModel(C.WHISPER_MODEL, device="cpu", compute_type="int8", download_root=str(C.MODELS_DIR / "whisper"))
    return _model


def transcribe(pcm16: bytes) -> str:
    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = load().transcribe(audio, language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()
