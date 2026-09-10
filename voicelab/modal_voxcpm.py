"""VoxCPM1.5 (0.8B) on Modal (L4): cloning with transcript, voice design by description, streaming. Same POST contract.
Body extras: ref_audio_b64 + ref_text (cloning), instruct -> voice-design prefix "(description)" when no reference is given.
Deploy: backend/.venv/bin/modal deploy voicelab/modal_voxcpm.py"""
import base64, io, os, time
import modal

app = modal.App("voicelab-voxcpm")
SECRET = modal.Secret.from_name("voicelab-tts")
MODEL = os.environ.get("VOXCPM_MODEL", "openbmb/VoxCPM1.5")

def dl():
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL, local_dir="/models/voxcpm")

img = (modal.Image.debian_slim(python_version="3.11").apt_install("ffmpeg", "libsndfile1", "git")
       .pip_install("torch==2.6.0", "torchaudio==2.6.0", index_url="https://download.pytorch.org/whl/cu124")
       .pip_install("voxcpm", "fastapi[standard]", "huggingface_hub", "soundfile", "numpy").run_function(dl))

@app.cls(gpu="L4", image=img, scaledown_window=120, timeout=900, secrets=[SECRET])
@modal.concurrent(max_inputs=2)
class VoxCPM:
    @modal.enter()
    def load(self):
        from voxcpm import VoxCPM as V
        t0 = time.time()
        self.model = V.from_pretrained("/models/voxcpm", load_denoiser=False)
        self.model.generate(text="Warm up.", cfg_value=2.0, inference_timesteps=10)
        self.loaded_in = time.time() - t0
    @modal.fastapi_endpoint(method="POST", docs=False)
    def tts(self, body: dict):
        import numpy as np, soundfile as sf, tempfile
        from fastapi import HTTPException, Response
        if body.get("key") != os.environ["VOICELAB_TTS_KEY"]: raise HTTPException(401, "bad key")
        text = (body.get("text") or "").strip()
        if not text: raise HTTPException(400, "text required")
        kw = {"cfg_value": float(body.get("cfg_value", 2.0)), "inference_timesteps": int(body.get("steps", 10))}
        if body.get("ref_audio_b64"):
            x, sr = sf.read(io.BytesIO(base64.b64decode(body["ref_audio_b64"])), dtype="float32")
            if x.ndim > 1: x = x.mean(axis=1)
            f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); sf.write(f.name, x, sr)
            kw["prompt_wav_path"] = f.name; kw["prompt_text"] = body.get("ref_text") or None
        elif body.get("instruct"):
            text = f"({body['instruct']})" + text                      # voice design by description
        t0 = time.time()
        w = np.asarray(self.model.generate(text=text, **kw), dtype=np.float32).squeeze()
        sr = getattr(self.model, "sample_rate", None) or getattr(getattr(self.model, "tts_model", None), "sample_rate", 44100)
        pcm = (np.clip(w, -1, 1) * 32767).astype(np.int16).tobytes()
        return Response(pcm, media_type="application/octet-stream", headers={"X-Sample-Rate": str(sr), "X-Gen-ms": f"{(time.time()-t0)*1000:.0f}", "X-Audio-s": f"{len(w)/sr:.2f}", "X-Load-s": f"{self.loaded_in:.1f}"})
