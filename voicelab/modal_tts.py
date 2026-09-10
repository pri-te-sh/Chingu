"""GPU text-to-speech engines on Modal for the Voice Lab: Qwen3-TTS, Kokoro, Chatterbox (multilingual). Scale-to-zero L4 containers.
Deploy:  backend/.venv/bin/modal deploy voicelab/modal_tts.py   (Fish Speech lives in modal_fish.py: gated weights, needs an HF token)

Common contract, every engine:
  POST <url>  {"key", "text", "voice", "language": "en", "instruct": "...", "ref_audio_b64": "<wav, optional for cloning engines>",
               "exaggeration": 0.5 (chatterbox)}
  -> raw PCM16 mono; headers X-Sample-Rate, X-Gen-ms, X-Audio-s, X-Load-s
"""
import base64, io, os, time
import modal

app = modal.App("voicelab-tts")
SECRET = modal.Secret.from_name("voicelab-tts")

def _auth(body):
    from fastapi import HTTPException
    if body.get("key") != os.environ["VOICELAB_TTS_KEY"]: raise HTTPException(401, "bad key")
    text = (body.get("text") or "").strip()
    if not text: raise HTTPException(400, "text required")
    return text

def _resp(wav, sr, gen_ms, load_s):
    import numpy as np
    from fastapi import Response
    w = np.asarray(wav, dtype=np.float32).squeeze()
    pcm = (np.clip(w, -1, 1) * 32767).astype(np.int16).tobytes()
    return Response(pcm, media_type="application/octet-stream",
                    headers={"X-Sample-Rate": str(sr), "X-Gen-ms": f"{gen_ms:.0f}", "X-Audio-s": f"{len(w)/sr:.2f}", "X-Load-s": f"{load_s:.1f}"})

def _ref_wav(body):
    """Optional reference clip for cloning: base64 WAV -> (float32 mono, sr) or None."""
    b64 = body.get("ref_audio_b64")
    if not b64: return None
    import soundfile as sf, numpy as np
    x, sr = sf.read(io.BytesIO(base64.b64decode(b64)), dtype="float32")
    if x.ndim > 1: x = x.mean(axis=1)
    return x, sr

# ------------------------------------------------------------------ Qwen3-TTS 0.6B CustomVoice
QWEN = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
def dl_qwen():
    from huggingface_hub import snapshot_download; snapshot_download(QWEN, local_dir="/models/qwen3-tts")
qwen_img = (modal.Image.debian_slim(python_version="3.12").apt_install("sox", "libsox-dev", "ffmpeg")
            .pip_install("torch==2.6.0", "torchaudio==2.6.0", index_url="https://download.pytorch.org/whl/cu124")
            .pip_install("qwen-tts", "fastapi[standard]", "huggingface_hub", "soundfile", "numpy").run_function(dl_qwen))

@app.cls(gpu="L4", image=qwen_img, scaledown_window=120, timeout=600, secrets=[SECRET])
@modal.concurrent(max_inputs=4)
class Qwen:
    @modal.enter()
    def load(self):
        import torch
        from qwen_tts import Qwen3TTSModel
        t0 = time.time()
        self.model = Qwen3TTSModel.from_pretrained("/models/qwen3-tts", device_map="cuda", dtype=torch.bfloat16)
        self.model.generate_custom_voice(text="Warm up.", speaker="Ryan", language="English")
        self.loaded_in = time.time() - t0
    @modal.fastapi_endpoint(method="POST", docs=False)
    def tts(self, body: dict):
        text = _auth(body); t0 = time.time()
        lang = {"en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "ru": "Russian"}.get(body.get("language", "en"), "English")
        wavs, sr = self.model.generate_custom_voice(text=text, speaker=body.get("voice") or "Ryan", language=lang, instruct=body.get("instruct") or None)
        return _resp(wavs[0], sr, (time.time() - t0) * 1000, self.loaded_in)

# ------------------------------------------------------------------ Kokoro-82M (PyTorch, GPU)
def dl_kokoro():
    from huggingface_hub import snapshot_download; snapshot_download("hexgrad/Kokoro-82M", local_dir="/models/kokoro")
kokoro_img = (modal.Image.debian_slim(python_version="3.12").apt_install("espeak-ng", "ffmpeg")
              .pip_install("torch==2.6.0", index_url="https://download.pytorch.org/whl/cu124")
              .pip_install("kokoro>=0.9.4", "misaki[en,ja,zh]>=0.9.4", "fastapi[standard]", "huggingface_hub", "soundfile", "numpy").run_function(dl_kokoro))

@app.cls(gpu="L4", image=kokoro_img, scaledown_window=120, timeout=600, secrets=[SECRET])
@modal.concurrent(max_inputs=8)
class Kokoro:
    @modal.enter()
    def load(self):
        from kokoro import KPipeline
        t0 = time.time()
        self.pipes = {}
        self.pipe("a").__call__("Warm up.", voice="af_heart").__next__()
        self.loaded_in = time.time() - t0
    def pipe(self, lang):
        if lang not in self.pipes:
            from kokoro import KPipeline
            self.pipes[lang] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", device="cuda")
        return self.pipes[lang]
    @modal.fastapi_endpoint(method="POST", docs=False)
    def tts(self, body: dict):
        import numpy as np
        text = _auth(body); t0 = time.time()
        lang = {"en": "a", "en-gb": "b", "es": "e", "fr": "f", "hi": "h", "it": "i", "pt": "p", "ja": "j", "zh": "z"}.get(body.get("language", "en"), "a")
        voice = body.get("voice") or "af_heart"
        chunks = [np.asarray(r.audio.cpu().numpy() if hasattr(r.audio, "cpu") else r.audio) for r in self.pipe(lang)(text, voice=voice, speed=1.05)]
        return _resp(np.concatenate(chunks) if chunks else np.zeros(1), 24000, (time.time() - t0) * 1000, self.loaded_in)

# ------------------------------------------------------------------ Chatterbox Multilingual (cloning + exaggeration)
def dl_chatterbox():
    from huggingface_hub import snapshot_download; snapshot_download("ResembleAI/chatterbox", local_dir="/models/chatterbox")
cb_img = (modal.Image.debian_slim(python_version="3.11").apt_install("ffmpeg", "libsndfile1")
          .pip_install("torch==2.6.0", "torchaudio==2.6.0", index_url="https://download.pytorch.org/whl/cu124")
          .pip_install("chatterbox-tts", "fastapi[standard]", "huggingface_hub", "soundfile", "numpy").run_function(dl_chatterbox))

@app.cls(gpu="L4", image=cb_img, scaledown_window=120, timeout=600, secrets=[SECRET])
@modal.concurrent(max_inputs=2)
class Chatterbox:
    @modal.enter()
    def load(self):
        import perth
        if getattr(perth, "PerthImplicitWatermarker", None) is None: perth.PerthImplicitWatermarker = perth.DummyWatermarker
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        t0 = time.time()
        self.model = ChatterboxMultilingualTTS.from_pretrained(device="cuda")
        self.model.generate("Warm up.", language_id="en")
        self.loaded_in = time.time() - t0
    @modal.fastapi_endpoint(method="POST", docs=False)
    def tts(self, body: dict):
        import tempfile, soundfile as sf
        text = _auth(body); t0 = time.time()
        kw = {"language_id": body.get("language", "en"), "exaggeration": float(body.get("exaggeration", 0.5)), "cfg_weight": float(body.get("cfg_weight", 0.5))}
        ref = _ref_wav(body)
        if ref is not None:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f: sf.write(f.name, ref[0], ref[1]); kw["audio_prompt_path"] = f.name
        wav = self.model.generate(text, **kw)
        return _resp(wav.squeeze().cpu().numpy(), self.model.sr, (time.time() - t0) * 1000, self.loaded_in)
