"""Qwen3-TTS 0.6B CustomVoice on a Modal T4: scale-to-zero GPU text-to-speech for the Voice Lab (and later the brain).
Deploy:  backend/.venv/bin/modal deploy voicelab/modal_tts.py   -> prints the endpoint URL (put it in voicelab/.env as QWEN_MODAL_URL)
POST /tts  {"text","speaker":"Ryan","instruct":"...","language":"English"}  header x-voicelab-key  -> raw PCM16 mono 24 kHz"""
import os, time
import modal

MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
app = modal.App("voicelab-tts")

def download():
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL, local_dir="/models/qwen3-tts")

image = (modal.Image.debian_slim(python_version="3.12")
         .apt_install("sox", "libsox-dev", "ffmpeg")
         .pip_install("torch==2.6.0", "torchaudio==2.6.0", index_url="https://download.pytorch.org/whl/cu124")
         .pip_install("qwen-tts", "fastapi[standard]", "huggingface_hub", "soundfile", "numpy")
         .run_function(download))            # weights baked into the image: cold start = container boot + load, no download

@app.cls(gpu="L4", image=image, scaledown_window=120, timeout=600, secrets=[modal.Secret.from_name("voicelab-tts")])
@modal.concurrent(max_inputs=4)
class TTS:
    @modal.enter()
    def load(self):
        import torch
        from qwen_tts import Qwen3TTSModel
        t0 = time.time()
        self.model = Qwen3TTSModel.from_pretrained("/models/qwen3-tts", device_map="cuda", dtype=torch.bfloat16)     # fp16 overflows the sampler; bf16 needs an Ampere+ GPU (L4)
        self.model.generate_custom_voice(text="Warm up.", speaker="Ryan", language="English")
        self.loaded_in = time.time() - t0
        print(f"[tts] model ready in {self.loaded_in:.1f}s")

    @modal.fastapi_endpoint(method="GET", docs=False)
    def info(self):
        import torch
        p = next(self.model.model.parameters())
        return {"torch": torch.__version__, "cuda": torch.cuda.is_available(), "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
                "model_device": str(p.device), "model_dtype": str(p.dtype), "codec_device": str(self.model.model.speech_tokenizer.device) if hasattr(self.model.model, "speech_tokenizer") else "?", "load_s": self.loaded_in}

    @modal.fastapi_endpoint(method="POST", docs=False)
    def tts(self, body: dict, x_voicelab_key: str = None):
        from fastapi import Header, HTTPException, Response
        import numpy as np
        return self._run(body)

    def _run(self, body: dict):
        import numpy as np
        from fastapi import HTTPException, Response
        if body.get("key") != os.environ["VOICELAB_TTS_KEY"]: raise HTTPException(401, "bad key")
        text = (body.get("text") or "").strip()
        if not text: raise HTTPException(400, "text required")
        t0 = time.time()
        wavs, sr = self.model.generate_custom_voice(text=text, speaker=body.get("speaker") or "Ryan", language=body.get("language") or "English",
                                                    instruct=body.get("instruct") or None)
        w = np.asarray(wavs[0]).squeeze().astype(np.float32)
        pcm = (np.clip(w, -1, 1) * 32767).astype(np.int16).tobytes()
        gen_ms = (time.time() - t0) * 1000
        return Response(pcm, media_type="application/octet-stream",
                        headers={"X-Sample-Rate": str(sr), "X-Gen-ms": f"{gen_ms:.0f}", "X-Audio-s": f"{len(w)/sr:.2f}", "X-Load-s": f"{self.loaded_in:.1f}"})

@app.function(image=image)
@modal.fastapi_endpoint(method="GET", docs=False)
def health():
    return {"ok": True, "model": MODEL}
