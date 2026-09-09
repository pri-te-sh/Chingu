"""Two end-pointers with the same feed() contract: energy (production) and Silero (neural)."""
import numpy as np
from ..common import SR

class EnergyVAD:
    name = "energy"
    def __init__(self, start_rms=900, end_rms=500, end_silence_ms=700, min_speech_ms=300, max_s=15):
        self.p = dict(start_rms=start_rms, end_rms=end_rms, end_silence_ms=end_silence_ms, min_speech_ms=min_speech_ms, max_s=max_s); self.reset()
    def reset(self): self.speaking = False; self.buf = bytearray(); self.sil = 0.0; self.sp = 0.0; self.level = 0.0
    def feed(self, pcm: bytes):
        s = np.frombuffer(pcm, dtype=np.int16)
        if not s.size: return None
        rms = float(np.sqrt(np.mean(s.astype(np.float32) ** 2))); self.level = rms / 4000; dur = s.size / SR * 1000
        if not self.speaking:
            self.buf += pcm; keep = int(SR * 2 * 0.4)
            if len(self.buf) > keep: del self.buf[:len(self.buf) - keep]
            if rms > self.p["start_rms"]: self.speaking = True; self.sp = dur; self.sil = 0
            return None
        self.buf += pcm; self.sp += dur
        self.sil = self.sil + dur if rms < self.p["end_rms"] else 0
        if (self.sil >= self.p["end_silence_ms"] and self.sp >= self.p["min_speech_ms"]) or self.sp > self.p["max_s"] * 1000:
            u = bytes(self.buf); self.reset(); return u
        return None

class SileroVAD:
    name = "silero"
    def __init__(self, threshold=0.5, end_silence_ms=500, min_speech_ms=250, max_s=15):
        from silero_vad import load_silero_vad
        import torch
        torch.set_num_threads(1)
        self.model = load_silero_vad(onnx=True)
        self.p = dict(threshold=threshold, end_silence_ms=end_silence_ms, min_speech_ms=min_speech_ms, max_s=max_s); self.reset()
    def reset(self):
        self.speaking = False; self.buf = bytearray(); self.sil = 0.0; self.sp = 0.0; self.level = 0.0; self.pend = b""
        try: self.model.reset_states()
        except Exception: pass
    def _prob(self, frame: np.ndarray) -> float:
        import torch
        return float(self.model(torch.from_numpy(frame), SR).item())
    def feed(self, pcm: bytes):
        # silero wants exactly 512-sample frames at 16 kHz
        self.pend += pcm; out = None
        while len(self.pend) >= 1024:
            fr, self.pend = self.pend[:1024], self.pend[1024:]
            p = self._prob(np.frombuffer(fr, dtype=np.int16).astype(np.float32) / 32768); self.level = p; dur = 32.0
            if not self.speaking:
                self.buf += fr; keep = int(SR * 2 * 0.4)
                if len(self.buf) > keep: del self.buf[:len(self.buf) - keep]
                if p >= self.p["threshold"]: self.speaking = True; self.sp = dur; self.sil = 0
                continue
            self.buf += fr; self.sp += dur
            self.sil = self.sil + dur if p < self.p["threshold"] else 0
            if (self.sil >= self.p["end_silence_ms"] and self.sp >= self.p["min_speech_ms"]) or self.sp > self.p["max_s"] * 1000:
                out = bytes(self.buf); self.reset()
        return out

def make(name: str, **kw):
    return SileroVAD(**kw) if name == "silero" else EnergyVAD(**kw)
