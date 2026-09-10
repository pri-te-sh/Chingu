# Voice Lab — refining Pixel's conversation layer (P2c)

> **Status 2026-09-10: PARKED. Decision: keep Piper as Pixel's voice for now; Parakeet-TDT 0.6B is the STT to adopt.**
> The lab (`voicelab/`) and every engine adapter stay in the repo. The Modal GPU apps were stopped (they cost nothing stopped; redeploy
> with `backend/.venv/bin/modal deploy voicelab/modal_*.py` after recreating the `voicelab-tts` secret with a `VOICELAB_TTS_KEY`).
> Resume by reading "Findings" and "If we revisit" below.

## Findings (complete, 2026-09-09/10)
### STT — clear winner
| Engine (CPU, 2 threads) | Latency per utterance | Accuracy on synthetic audio | Verdict |
|---|---|---|---|
| **Parakeet-TDT 0.6B v2 (onnx int8)** | ~250 ms (first call ~1 s) | 0 % WER | **adopt** — 30 % faster than whisper-base, far better published WER; needs `onnx-asr` in the worker image (~0.7 GB) |
| faster-whisper base.en int8 (today) | ~380 ms | 0 % | fallback |
| faster-whisper small.en / distil-small.en | ~1.1–1.2 s | 0 % / 3 % | too slow on 2 vCPU |
Real-mic accuracy was **not** measured (HEAR tab never got a full run with Pritesh's voice) — do that before switching production STT.

### TTS on CPU (the VM has 2 vCPU, no GPU)
| Engine | First audio | RTF | Voice | Extras | Verdict |
|---|---|---|---|---|---|
| **Piper lessac-medium** | ~90 ms | 0.04 | robotic but clear | — | **keep (decision)** |
| Pocket TTS (Kyutai, 100M) | ~60 ms after voice-state warm-up (600 ms once per voice) | 0.25 | good | native streaming, **clones any WAV**, English only, CC-BY-4.0 | best CPU upgrade path if we ever want cloning without a GPU |
| Supertonic 3 (99M onnx) | ~500 ms per chunk | 0.14 at 4 flow steps | good | 31 languages, 10 preset voices, no cloning | strong CPU alternative |
| Kokoro-82M | 0.7–1.4 s per chunk (≈550 ms fixed per call) | 0.6–0.7 | Pritesh's favourite | 8 languages, no cloning | too slow on 2 vCPU; fine on 4 vCPU; int8 is *slower* on CPU |
| Chatterbox 0.5B | n/a | 5–6.5 | superb | cloning, emotion dial | GPU-only |
| Qwen3-TTS 0.6B | n/a | 7–13 | superb | cloning, instruct | GPU-only |

### TTS on Modal GPU (L4, scale-to-zero; `voicelab/modal_*.py`)
| Engine | Cold call | Warm gen for ~4–6 s speech | Notes |
|---|---|---|---|
| Kokoro (PyTorch) | 34 s | **87 ms** | GPU removes Kokoro's only weakness |
| Chatterbox Multilingual | 77 s (45 s load) | 2.7–3.1 s (RTF ≈ 0.8) | 23 langs, cloning, exaggeration; works with clause chunking |
| VoxCPM 1.5 (0.8B) | ~45 s | ~3 s (RTF ≈ 0.9) eager; `optimize=True` (torch.compile) crashes with CUDA asserts on L4 | cloning + voice design; 44.1 kHz |
| Qwen3-TTS 0.6B CustomVoice | 59 s | 13–17 s (RTF ≈ 2.5) | plain-PyTorch path unusable; needs their vLLM-Omni server. bf16 on T4 is emulated (slow); fp16 overflows the sampler |
| Fun-CosyVoice3 0.5B | image builds after fixes (setuptools<75, openai-whisper>=2025, keep gdown/matplotlib); **never measured** — the last container failed on `gdown` import before the fix was deployed | native streaming, cloning, instructed emotion — the most promising GPU candidate on paper |
| Fish / OpenAudio S1-mini | not deployed (gated weights → needs HF token in a Modal secret `huggingface`) | | |
Cost model: speech itself is ~free (0.4 GPU-s per reply); the bill is idle window + cold starts. Light use (4 chats/day, 2-min window) ≈ $11/mo on T4, inside the $30 Starter credit; typical use ≈ $34; keep-warm evenings ≈ $130. Any GPU voice needs a CPU fallback for the 30–80 s cold start.

### Turn-taking / pipeline (kept — these apply to the brain regardless of engine)
- Clause-level chunker (`voicelab/chunker.py`): first clause out after ≥12 chars, then sentences, scan *every* boundary, cap 160 chars. Fixes the "one giant chunk" bug.
- Punctuation-aware pauses between chunks: 140 ms after a clause, 320 ms after a sentence, none after the last — "natural" preset felt right.
- Echo-proof barge-in: while Pixel speaks, ignore the mic unless RMS > 2500 for ≥ 300 ms (speaker bleed was cancelling turns).
- Whole-turn budget measured (Parakeet + Gemma 4 cloud + Piper): transcript ~170–250 ms, first token ~270–440 ms, **first audio ~450 ms** after end of speech.
- LLM: `num_predict` 600 with a prompt that stays short by default but goes long when asked.

## If we revisit
1. Port to the brain first (no GPU needed): Parakeet STT, the chunker + pauses, echo-proof barge-in, the per-chunk pipeline events (nice for the portal simulator).
2. Voice upgrade path without GPU: Pocket TTS (cloning) or Supertonic (multilingual). With GPU: finish CosyVoice3 measurement, then Kokoro-on-Modal as the cheap, fast option.
3. Decide whose voice Pixel gets before investing in cloning (5–10 s clean reference clip).


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
**Supertonic 3** (99M, ONNX, CPU-first, 31 languages) · **Fish Speech / OpenAudio S1-mini** and Chatterbox (0.5B class; superb voices but GPU-only in practice — quality references, and a hosting decision if one is wanted).
**VAD**: energy VAD (today) vs **Silero VAD** (neural, 1 ms per 30 ms frame).
**Turn-taking**: sentence-chunked TTS streaming (start speaking after the first clause), pre-roll, barge-in, filler while tools run.

## Findings so far (2026-09-09, Mac, 2 threads, typed turns through TALK)
| TTS | first audio after end of speech | RTF | verdict |
|---|---|---|---|
| Piper lessac | ~450 ms | 0.04 | instant, robotic |
| **Supertonic 3** (4 steps) | ~800–1000 ms | ~0.15 | best speed/quality candidate on CPU; 10 voices (F1–F5, M1–M5) |
| Kokoro-82M | ~1.7–2.5 s | 0.6–0.7 | fixed ~550 ms per call; int8 is *slower* on this CPU; too slow for 2 vCPU |
| Chatterbox 0.5B | n/a | 5–6.5 (MPS and CPU alike) | superb voice, needs a CUDA GPU; ~20 s per sentence here |
STT at 2 threads: Parakeet 0.6B ~250 ms, fw-base ~380 ms, fw-small/distil ~1.1 s. LLM first token (Ollama Cloud Gemma 4): 250–330 ms.
Whole-turn budget with Parakeet + Gemma 4 + Supertonic ≈ 0.25 + 0.3 + 0.4 ≈ **1.0 s to first word** on this Mac; expect ~1.3 s on the VM.
Next: listen (`voicelab/results/*_sample.wav`), then tune the chunker so the first clause is spoken sooner, and try 3 steps.

### GPU engines on Modal (L4, scale-to-zero, `voicelab/modal_tts.py`, 2026-09-09)
| Engine | Cold call (boot + load + gen) | Warm generation for ~4–6 s speech | Notes |
|---|---|---|---|
| Kokoro-82M (PyTorch) | 34 s (load 12 s) | **87 ms** (round trip ~0.5 s) | the CPU bottleneck simply disappears; 8 languages, no cloning |
| Chatterbox Multilingual | 77 s (load 45 s) | 2.7–3.1 s (RTF ≈ 0.8) | 23 languages, zero-shot cloning, exaggeration dial; needs chunk streaming to feel OK |
| Qwen3-TTS 0.6B CustomVoice | 59 s (load 10 s) | 13–17 s (RTF ≈ 2.5) | plain-PyTorch path is unusable; needs vLLM-Omni serving |
| Fish / OpenAudio S1-mini | not deployed yet | | gated weights: needs a Hugging Face token in a Modal secret `huggingface` |
Budget model (`tools`): speech itself is ~free; cost = idle window + cold starts. Light use fits in the $30 Starter credit on a T4/L4.

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
