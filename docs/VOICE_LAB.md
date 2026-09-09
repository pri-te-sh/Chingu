# Voice Lab — refining Pixel's conversation layer (P2c)

Goal: make a Pixel turn feel like ChatGPT voice mode — natural voice, accurate hearing, sub-second reaction — and pick
the engines by measurement, not by reputation. Everything runs natively on the Mac in `voicelab/`, **CPU-only and capped at 2 threads** so the numbers predict the target
VM (Hetzner CX23: 2 vCPU, 4 GB, no GPU). One M1 Pro core is roughly 1.5–2× a shared cloud vCPU, so read latencies as optimistic by that factor.
GPU-only engines (Fish Speech) are measured for *voice quality* only; if one wins, it is a hosting decision for P3. LLM stays Ollama Cloud
Gemma 4 (`voicelab/.env` → `OLLAMA_API_KEY`).

## What "quality" means here (the metrics the lab records)
| Layer | Metric | Target |
|---|---|---|
| VAD / end-pointing | false starts per minute of silence; ms from last word to end-of-speech | 0 false starts in a quiet room; < 500 ms end-point |
| STT | word accuracy on our own phrases (kitchen noise, Pritesh's voice), latency per utterance | ≥ 95 % words right; < 400 ms after end-of-speech |
| LLM | time to first token, tokens/s | < 700 ms TTFT (network to Ollama Cloud) |
| TTS | time-to-first-audio, real-time factor, MOS-style rating (1–5) you give while listening | TTFA < 300 ms; RTF < 0.5 so audio never starves; rating ≥ 4 |
| Whole turn | end-of-speech → first audible word | **< 1.2 s** (today ≈ 1.8–2.5 s) |

First bench on the Mac, all cores, synthetic audio: fw-base 250 ms · fw-small 785 ms · Parakeet 430 ms (all 0 % WER on clean TTS audio; real mic audio is the actual test) · Piper TTFA 90 ms, RTF 0.04 · Kokoro TTFA 600–800 ms, RTF 0.35 (needs clause-level streaming to feel instant).

## Candidates
**STT** (all local, CPU): faster-whisper `base.en` int8 (today) · `small.en` · `distil-small.en` ·
NVIDIA **Parakeet-TDT 0.6B v2** int8 via onnx-asr (currently #1 open English WER, CPU-friendly) · Moonshine (later, if we need tiny).
**TTS** (all local): Piper `lessac-medium` (today) · Piper `ryan-high`/`amy-medium` · **Kokoro-82M** (several voices; the likely winner on quality-per-ms) ·
**Fish Speech / OpenAudio S1-mini** (0.5B, best naturalness + voice cloning; runs via its own API server on MPS — expect RTF ≈ 1, so streaming matters) ·
Chatterbox (0.5B, expressive; optional).
**VAD**: energy VAD (today) vs **Silero VAD** (neural, 1 ms per 30 ms frame).
**Turn-taking**: sentence-chunked TTS streaming (start speaking after the first clause), pre-roll, barge-in, filler while tools run.

## The test bed (`voicelab/`, http://localhost:8790)
1. **HEAR** — talk into the browser mic; live VAD meter (energy vs Silero side by side, false-start counter); the utterance is
   sent to every selected STT engine at once → transcripts, ms, diff highlighting against the engine you trust most.
   Fixed phrase set (`voicelab/phrases.txt`) for repeatable accuracy runs.
2. **SPEAK** — type a sentence → every selected TTS engine/voice synthesises it → play, A/B, rate 1–5; TTFA/RTF recorded.
3. **TALK** — the full pipeline as a virtual Pixel: mic → VAD → STT → Gemma 4 (streaming) → sentence chunker → TTS →
   speaker, with barge-in; a timeline bar per turn (end-of-speech / transcript / first token / first audio / done).
   Engines are switchable live, so the same conversation can be replayed with different stacks.
4. Every run appends to `voicelab/results/*.jsonl`; a RESULTS tab aggregates medians and your ratings per engine.

## Exit criteria → what moves into the brain
- One STT and one TTS engine chosen (plus a fallback), VAD decision, chunking strategy, target voice.
- The worker gains an engine abstraction (`pixel/engines/*`) with the winners; Docker image stays CPU-only for the VM
  (Parakeet + Kokoro run fine on CPU). If Fish/S1-mini wins on voice, it needs a GPU or the Mac at home → that becomes a
  hosting input for P3.
- Firmware: no change for Pixel-Lite yet (speaker path lands with the DAC/INMP441 work); the browser simulator is the client.

## Steps
1. Scaffold + install engines; smoke-test each with one phrase (`uv run python -m voicelab.bench`).
2. HEAR tab + phrase set; run 20 phrases × engines; pick STT.
3. SPEAK tab; rate voices; pick TTS + voice. Fish S1-mini via its API server as an optional engine.
4. TALK tab with streaming + barge-in; measure whole-turn latency per stack; tune VAD end-point and chunking.
5. Port winners into `backend/pixel/worker.py` behind the abstraction; update simulator; measure on the board.
