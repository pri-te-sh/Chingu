"""Inference worker: faster-whisper STT and Piper TTS behind a tiny HTTP API, so the brain's event loop never blocks.
POST /stt  (body: PCM16 mono 16 kHz)  -> {"text": ...}
POST /tts  {"text": ...}              -> PCM16 mono 16 kHz bytes"""
import asyncio, time
from fastapi import FastAPI, Request, Response
from . import stt, tts, config as C

app = FastAPI(title="pixel-worker")

@app.on_event("startup")
async def _warm():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, stt.load); await loop.run_in_executor(None, tts.load)
    print("[worker] models loaded")

@app.get("/health")
async def health(): return {"ok": True, "whisper": C.WHISPER_MODEL, "voice": C.PIPER_VOICE}

@app.post("/stt")
async def do_stt(request: Request):
    pcm = await request.body()
    t = time.time()
    text = await asyncio.get_running_loop().run_in_executor(None, stt.transcribe, pcm)
    return {"text": text, "seconds": round(time.time() - t, 3), "audio_s": round(len(pcm) / 32000, 2)}

@app.post("/tts")
async def do_tts(body: dict):
    pcm = await asyncio.get_running_loop().run_in_executor(None, lambda: b"".join(tts.synthesize(body.get("text", ""))))
    return Response(content=pcm, media_type="application/octet-stream")
