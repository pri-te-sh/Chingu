"""Fun-CosyVoice3-0.5B on Modal (L4): zero-shot cloning, instructed emotion, native streaming. Same POST contract as modal_tts.py.
Body extras: instruct (e.g. "Speak in a happy, playful tone."), ref_audio_b64 + ref_text (cloning; a default reference ships in the image).
Deploy: backend/.venv/bin/modal deploy voicelab/modal_cosyvoice.py"""
import base64, io, os, time
import modal

app = modal.App("voicelab-cosyvoice")
SECRET = modal.Secret.from_name("voicelab-tts")
MODEL = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"

def dl():
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL, local_dir="/models/cosyvoice3")

img = (modal.Image.debian_slim(python_version="3.10").apt_install("git", "git-lfs", "sox", "libsox-dev", "ffmpeg", "build-essential")
       .run_commands("git clone --recursive --depth 1 https://github.com/FunAudioLLM/CosyVoice.git /opt/CosyVoice")
       .pip_install("torch==2.6.0", "torchaudio==2.6.0", index_url="https://download.pytorch.org/whl/cu124")
       .run_commands("cd /opt/CosyVoice && grep -viE '^(torch|torchaudio|tensorrt|onnxruntime-gpu|deepspeed|vllm)' requirements.txt > req.txt && pip install -r req.txt")
       .pip_install("fastapi[standard]", "huggingface_hub", "soundfile", "numpy", "onnxruntime")
       .run_function(dl))

@app.cls(gpu="L4", image=img, scaledown_window=120, timeout=900, secrets=[SECRET])
@modal.concurrent(max_inputs=2)
class CosyVoice:
    @modal.enter()
    def load(self):
        import sys; sys.path.insert(0, "/opt/CosyVoice"); sys.path.insert(0, "/opt/CosyVoice/third_party/Matcha-TTS")
        from cosyvoice.cli.cosyvoice import AutoModel
        t0 = time.time()
        self.model = AutoModel(model_dir="/models/cosyvoice3", fp16=True)
        self.sr = self.model.sample_rate
        self.default_ref = "/opt/CosyVoice/asset/zero_shot_prompt.wav"; self.default_ref_text = "希望你以后能够做的比我还好呦。"
        for _ in self.model.inference_instruct2("Warm up.", "You are a helpful assistant.<|endofprompt|>", self.default_ref, stream=False): pass
        self.loaded_in = time.time() - t0
    @modal.fastapi_endpoint(method="POST", docs=False)
    def tts(self, body: dict):
        import numpy as np, soundfile as sf, tempfile, torch
        from fastapi import HTTPException, Response
        if body.get("key") != os.environ["VOICELAB_TTS_KEY"]: raise HTTPException(401, "bad key")
        text = (body.get("text") or "").strip()
        if not text: raise HTTPException(400, "text required")
        ref = self.default_ref; ref_text = self.default_ref_text
        if body.get("ref_audio_b64"):
            x, sr = sf.read(io.BytesIO(base64.b64decode(body["ref_audio_b64"])), dtype="float32")
            if x.ndim > 1: x = x.mean(axis=1)
            f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); sf.write(f.name, x, sr); ref = f.name; ref_text = body.get("ref_text") or ""
        t0 = time.time(); chunks = []; first = None
        instruct = body.get("instruct")
        if instruct:
            gen = self.model.inference_instruct2(text, instruct.rstrip() + ("" if instruct.endswith("<|endofprompt|>") else "<|endofprompt|>"), ref, stream=True)
        else:
            gen = self.model.inference_zero_shot(text, ref_text, ref, stream=True)
        for j in gen:
            if first is None: first = (time.time() - t0) * 1000
            chunks.append(j["tts_speech"].squeeze().float().cpu().numpy())
        w = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        pcm = (np.clip(w, -1, 1) * 32767).astype(np.int16).tobytes()
        return Response(pcm, media_type="application/octet-stream", headers={"X-Sample-Rate": str(self.sr), "X-Gen-ms": f"{(time.time()-t0)*1000:.0f}", "X-First-ms": f"{first or 0:.0f}",
                                                                              "X-Audio-s": f"{len(w)/self.sr:.2f}", "X-Load-s": f"{self.loaded_in:.1f}"})
