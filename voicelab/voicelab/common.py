import json, os, time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"; MODELS.mkdir(exist_ok=True)
RESULTS = ROOT / "results"; RESULTS.mkdir(exist_ok=True)
SR = 16000                                  # everything in the lab is PCM16 mono 16 kHz, like the devices

def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
load_env()

# Emulate the target server (Hetzner CX23: 2 vCPU, no GPU). Every engine runs on CPU with this many threads.
THREADS = int(os.environ.get("VOICELAB_THREADS", "2"))
for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "CT2_INTRA_THREADS"): os.environ.setdefault(k, str(THREADS))

def pcm_to_f32(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0

def f32_to_pcm(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()

def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst: return x
    n = int(len(x) * dst / src)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)

def record(kind: str, **row):
    row["ts"] = time.time()
    with open(RESULTS / f"{kind}.jsonl", "a") as f: f.write(json.dumps(row) + "\n")

class Timer:
    def __enter__(self): self.t0 = time.perf_counter(); return self
    def __exit__(self, *a): self.ms = (time.perf_counter() - self.t0) * 1000
