"""TTS engines: load() once, stream(text) -> iterator of PCM16 16 kHz chunks. First chunk time = time-to-first-audio."""
import json, os, urllib.request
import numpy as np
import httpx
from ..common import MODELS, SR, THREADS, resample, f32_to_pcm

class TTS:
    name = ""; note = ""; voices: list[str] = []
    def load(self): ...
    def stream(self, text: str, voice: str | None = None): ...

def _dl(url: str, dest):
    if not dest.exists():
        print(f"[tts] downloading {url}"); dest.parent.mkdir(parents=True, exist_ok=True); urllib.request.urlretrieve(url, dest)
    return dest

class Piper(TTS):
    name, note = "piper", "Piper (production today: lessac-medium)"
    voices = ["en_US-lessac-medium", "en_US-ryan-high", "en_US-amy-medium", "en_GB-alba-medium", "en_US-hfc_female-medium"]
    _v: dict = {}
    def _voice(self, v):
        if v not in self._v:
            from piper import PiperVoice
            lang = v.split("-")[0]; fam = lang.split("_")[0]; nm = v.split("-")[1]; q = v.rsplit("-", 1)[1]
            base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{fam}/{lang}/{nm}/{q}/{v}"
            d = MODELS / "piper"
            onnx = _dl(f"{base}.onnx", d / f"{v}.onnx"); cfg = _dl(f"{base}.onnx.json", d / f"{v}.onnx.json")
            self._v[v] = PiperVoice.load(str(onnx), config_path=str(cfg))
        return self._v[v]
    def load(self): self._voice(self.voices[0])
    def stream(self, text, voice=None):
        v = self._voice(voice or self.voices[0])
        for ch in v.synthesize(text):
            x = np.frombuffer(ch.audio_int16_bytes, dtype=np.int16).astype(np.float32) / 32768
            yield f32_to_pcm(resample(x, ch.sample_rate, SR))

class Kokoro(TTS):
    name, note = "kokoro", "Kokoro-82M (onnx, CPU) - natural, real-time"
    voices = ["af_heart", "af_bella", "af_sky", "am_michael", "am_fenrir", "bf_emma", "bm_george", "am_puck"]
    _m = None
    def load(self):
        if self._m is None:
            from kokoro_onnx import Kokoro as K
            base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
            m = _dl(base + "kokoro-v1.0.onnx", MODELS / "kokoro" / "kokoro-v1.0.onnx"); v = _dl(base + "voices-v1.0.bin", MODELS / "kokoro" / "voices-v1.0.bin")
            import onnxruntime as ort
            so = ort.SessionOptions(); so.intra_op_num_threads = THREADS; so.inter_op_num_threads = 1
            self._m = K.from_session(ort.InferenceSession(str(m), sess_options=so, providers=["CPUExecutionProvider"]), str(v))
    def stream(self, text, voice=None):
        self.load()
        # kokoro-onnx synthesises per sentence internally; we split on clauses so the first chunk arrives fast
        import re
        parts = [p for p in re.split(r"(?<=[.!?;:,])\s+", text.strip()) if p] or [text]
        for p in parts:
            audio, sr = self._m.create(p, voice=voice or self.voices[0], speed=1.05, lang="en-us")
            yield f32_to_pcm(resample(np.asarray(audio, dtype=np.float32), sr, SR))

class Supertonic(TTS):
    name, note = "supertonic", "Supertonic 3 (99M, onnx, CPU) - flow-matching, 31 languages; steps trade quality for speed"
    voices = ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"]
    steps = int(os.environ.get("SUPERTONIC_STEPS", "4"))     # 4 steps: ~0.14 RTF at 2 threads; 8 is the library default
    _m = None; _styles: dict = {}
    def load(self):
        if self._m is None:
            from supertonic import TTS as ST
            self._m = ST(model_dir=str(MODELS / "supertonic"), intra_op_num_threads=THREADS, inter_op_num_threads=1)
    def _style(self, v):
        if v not in self._styles: self._styles[v] = self._m.get_voice_style(v)
        return self._styles[v]
    def stream(self, text, voice=None):
        self.load()
        import re
        parts = [p for p in re.split(r"(?<=[.!?;:,])\s+", text.strip()) if p] or [text]
        for p in parts:
            audio, dur = self._m.synthesize(p, voice_style=self._style(voice or self.voices[0]), total_steps=self.steps, lang="en", silence_duration=0.05)
            x = np.asarray(audio, dtype=np.float32).squeeze()
            yield f32_to_pcm(resample(x, 44100, SR))

class FishSpeech(TTS):
    """OpenAudio S1-mini through the fish-speech API server (run separately, see README). Streams WAV -> PCM."""
    name, note = "fish", "Fish Speech / OpenAudio S1-mini via local API server :8080 - quality reference only, needs a GPU host"
    voices = ["default"]
    url = os.environ.get("FISH_URL", "http://127.0.0.1:8080")
    def load(self):
        try: httpx.get(self.url + "/v1/health", timeout=2)
        except Exception as e: raise RuntimeError(f"fish-speech server not running at {self.url}: {e}")
    def stream(self, text, voice=None):
        body = {"text": text, "format": "wav", "streaming": True, "chunk_length": 120}
        with httpx.stream("POST", self.url + "/v1/tts", json=body, timeout=120) as r:
            r.raise_for_status()
            first = True; buf = b""
            for chunk in r.iter_bytes():
                if first:                                  # skip the 44-byte WAV header (fish streams 44.1 kHz s16)
                    buf += chunk
                    if len(buf) < 44: continue
                    chunk, buf, first = buf[44:], b"", False
                if len(chunk) % 2: buf = chunk[-1:]; chunk = chunk[:-1]
                x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768
                if len(x): yield f32_to_pcm(resample(x, 44100, SR))

ENGINES: dict[str, TTS] = {e.name: e for e in [Piper(), Kokoro(), Supertonic(), FishSpeech()]}
