"""STT engines behind one interface: load() once, transcribe(pcm16) -> text. Registry keyed by short name."""
import urllib.request
from ..common import MODELS, SR, THREADS, pcm_to_f32

class STT:
    name = ""; note = ""
    def load(self): ...
    def transcribe(self, pcm16: bytes) -> str: ...

class FasterWhisper(STT):
    def __init__(self, model: str, name: str, note: str):
        self.model_name, self.name, self.note, self._m = model, name, note, None
    def load(self):
        if self._m is None:
            from faster_whisper import WhisperModel
            self._m = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=THREADS, num_workers=1, download_root=str(MODELS / "whisper"))
    def transcribe(self, pcm16):
        self.load()
        segs, _ = self._m.transcribe(pcm_to_f32(pcm16), language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()

class Parakeet(STT):
    name, note = "parakeet-0.6b", "NVIDIA Parakeet-TDT 0.6B v2, onnx int8, CPU"
    _m = None
    def load(self):
        if self._m is None:
            import onnx_asr, onnxruntime as ort
            so = ort.SessionOptions(); so.intra_op_num_threads = THREADS; so.inter_op_num_threads = 1
            self._m = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", quantization="int8", sess_options=so)
    def transcribe(self, pcm16):
        self.load()
        return (self._m.recognize(pcm_to_f32(pcm16), sample_rate=SR) or "").strip()

ENGINES: dict[str, STT] = {e.name: e for e in [
    FasterWhisper("base.en", "fw-base", "faster-whisper base.en int8 (production today)"),
    FasterWhisper("small.en", "fw-small", "faster-whisper small.en int8"),
    FasterWhisper("distil-small.en", "fw-distil-small", "distil-whisper small.en int8 (faster decoder)"),
    Parakeet(),
]}
