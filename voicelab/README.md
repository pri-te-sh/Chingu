# Pixel Voice Lab
Side-by-side STT / TTS / VAD / whole-turn test bed. Runs natively on the Mac but **CPU-only with 2 threads** (`VOICELAB_THREADS`) to emulate the target VM (Hetzner CX23, 2 vCPU); isolated from `backend/`.

```bash
cd voicelab
cp .env.example .env          # paste OLLAMA_API_KEY
uv venv -p 3.12 && uv pip install -r pyproject.toml
uv run python -m voicelab.bench 5                 # smoke test, downloads models (~3 GB first time)
uv run uvicorn voicelab.app:app --port 8790       # http://localhost:8790
```
Optional engines:
- **Fish Speech / OpenAudio S1-mini**: clone https://github.com/fishaudio/fish-speech, follow its macOS install, run
  `python tools/api_server.py --listen 127.0.0.1:8080 --llama-checkpoint-path checkpoints/openaudio-s1-mini --decoder-checkpoint-path checkpoints/openaudio-s1-mini/codec.pth --decoder-config-name modded_dac_vq --device mps`.
  The lab's `fish` engine talks to it. Weights need a (free) Hugging Face token accepted on the model page.

Results accumulate in `results/*.jsonl`; the RESULTS tab shows medians. Plan and exit criteria: `docs/VOICE_LAB.md`.

## Status
Parked 2026-09-10 — decision: Piper stays Pixel's voice for now. Findings and the resume plan: `docs/VOICE_LAB.md`.
Modal apps (`modal_tts.py`, `modal_voxcpm.py`, `modal_cosyvoice.py`, `modal_fish.py`) are stopped; redeploy needs the `voicelab-tts` Modal secret
(`VOICELAB_TTS_KEY`) recreated and the printed URLs pasted into `.env`.
