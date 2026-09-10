"""Speech-to-text behind one interface. Engines: faster-whisper (CPU int8) and NVIDIA Parakeet-TDT 0.6B v2 (onnx int8, via onnx-asr).
PIXEL_STT_ENGINE picks the primary; whisper is always loaded as the fallback so a Parakeet failure never drops a turn."""
import os, time
import numpy as np
from . import config as C

os.environ.setdefault("HF_HOME", str(C.MODELS_DIR / "hf"))          # Parakeet weights land on the models volume
_whisper = None
_parakeet = None
_engine = C.STT_ENGINE


def _f32(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0


def load_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(C.WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=C.STT_THREADS, num_workers=1, download_root=str(C.MODELS_DIR / "whisper"))
    return _whisper


def load_parakeet():
    global _parakeet
    if _parakeet is None:
        import onnx_asr, onnxruntime as ort
        so = ort.SessionOptions(); so.intra_op_num_threads = C.STT_THREADS; so.inter_op_num_threads = 1
        _parakeet = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", quantization="int8", sess_options=so)
    return _parakeet


def load():
    """Load the primary engine (and whisper as fallback). Falls back to whisper if Parakeet cannot load."""
    global _engine
    load_whisper()
    if _engine == "parakeet":
        try:
            t = time.time(); load_parakeet(); print(f"[stt] parakeet ready in {time.time() - t:.1f}s")
        except Exception as e:
            print(f"[stt] parakeet unavailable ({e!r}) - using whisper"); _engine = "whisper"


def engine() -> str: return _engine


def transcribe_whisper(pcm16: bytes) -> str:
    segments, _ = load_whisper().transcribe(_f32(pcm16), language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe_parakeet(pcm16: bytes) -> str:
    return (load_parakeet().recognize(_f32(pcm16), sample_rate=C.SAMPLE_RATE) or "").strip()


def transcribe(pcm16: bytes, engine_name: str | None = None) -> str:
    name = engine_name or _engine
    if name == "parakeet":
        try: return transcribe_parakeet(pcm16)
        except Exception as e: print(f"[stt] parakeet failed ({e!r}) - whisper fallback")
    return transcribe_whisper(pcm16)
