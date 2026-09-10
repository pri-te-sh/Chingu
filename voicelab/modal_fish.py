"""Fish Speech / OpenAudio S1-mini on Modal (L4). Weights are gated: accept the licence at
https://huggingface.co/fishaudio/openaudio-s1-mini and create a Modal secret `huggingface` with HF_TOKEN=<your token>.
Deploy: backend/.venv/bin/modal deploy voicelab/modal_fish.py.  Same POST contract as modal_tts.py; supports ref_audio_b64 (+ ref_text) cloning
and inline emotion tags like (excited) (whispering) (laughs)."""
import base64, io, os, time
import modal

app = modal.App("voicelab-fish")
SECRET = modal.Secret.from_name("voicelab-tts")
HF = modal.Secret.from_name("huggingface")

def dl():
    from huggingface_hub import snapshot_download
    snapshot_download("fishaudio/openaudio-s1-mini", local_dir="/models/s1-mini", token=os.environ["HF_TOKEN"])

img = (modal.Image.debian_slim(python_version="3.12").apt_install("git", "ffmpeg", "libsox-dev", "portaudio19-dev", "build-essential")
       .pip_install("torch==2.6.0", "torchaudio==2.6.0", index_url="https://download.pytorch.org/whl/cu124")
       .run_commands("git clone --depth 1 https://github.com/fishaudio/fish-speech /opt/fish-speech && cd /opt/fish-speech && pip install -e .[cu126] || pip install -e .")
       .pip_install("fastapi[standard]", "huggingface_hub", "soundfile", "numpy")
       .run_function(dl, secrets=[HF]))

@app.cls(gpu="L4", image=img, scaledown_window=120, timeout=900, secrets=[SECRET])
@modal.concurrent(max_inputs=2)
class Fish:
    @modal.enter()
    def load(self):
        import sys, torch; sys.path.insert(0, "/opt/fish-speech")
        from fish_speech.inference_engine import TTSInferenceEngine
        from fish_speech.models.dac.inference import load_model as load_decoder
        from fish_speech.models.text2semantic.inference import launch_thread_safe_queue
        t0 = time.time()
        self.llama_queue = launch_thread_safe_queue(checkpoint_path="/models/s1-mini", device="cuda", precision=torch.bfloat16, compile=False)
        self.decoder = load_decoder(config_name="modded_dac_vq", checkpoint_path="/models/s1-mini/codec.pth", device="cuda")
        self.engine = TTSInferenceEngine(llama_queue=self.llama_queue, decoder_model=self.decoder, precision=torch.bfloat16, compile=False)
        from fish_speech.utils.schema import ServeTTSRequest
        list(self.engine.inference(ServeTTSRequest(text="Warm up.", references=[], max_new_tokens=256)))
        self.loaded_in = time.time() - t0
    @modal.fastapi_endpoint(method="POST", docs=False)
    def tts(self, body: dict):
        import numpy as np, soundfile as sf
        from fastapi import HTTPException, Response
        from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio
        if body.get("key") != os.environ["VOICELAB_TTS_KEY"]: raise HTTPException(401, "bad key")
        text = (body.get("text") or "").strip()
        if not text: raise HTTPException(400, "text required")
        refs = []
        if body.get("ref_audio_b64"): refs = [ServeReferenceAudio(audio=base64.b64decode(body["ref_audio_b64"]), text=body.get("ref_text") or "")]
        t0 = time.time(); chunks = []
        for r in self.engine.inference(ServeTTSRequest(text=text, references=refs, max_new_tokens=1024, chunk_length=200, format="wav")):
            if r.code == "final": sr, audio = r.audio; chunks.append(np.asarray(audio, dtype=np.float32))
            elif r.code == "error": raise HTTPException(500, str(r.error))
        w = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        pcm = (np.clip(w, -1, 1) * 32767).astype(np.int16).tobytes()
        return Response(pcm, media_type="application/octet-stream", headers={"X-Sample-Rate": str(sr), "X-Gen-ms": f"{(time.time()-t0)*1000:.0f}", "X-Audio-s": f"{len(w)/sr:.2f}", "X-Load-s": f"{self.loaded_in:.1f}"})
