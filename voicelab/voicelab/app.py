"""Voice Lab server: HEAR (VAD + multi-STT), SPEAK (A/B TTS), TALK (full pipeline with streaming + barge-in), RESULTS."""
import asyncio, io, json, statistics, time
from pathlib import Path
import numpy as np, soundfile as sf
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from .common import ROOT, RESULTS, SR, Timer, record, pcm_to_f32
from .engines import stt as S, tts as T, vad as V, llm as L
from .chunker import Chunker
from .bench import wer

app = FastAPI(title="voicelab")
STATIC = ROOT / "static"
_lock = asyncio.Lock()          # engines are not thread-safe; one inference at a time per process is fine for a lab

def wav_bytes(pcm16: bytes) -> bytes:
    b = io.BytesIO(); sf.write(b, np.frombuffer(pcm16, dtype=np.int16), SR, format="WAV", subtype="PCM_16"); return b.getvalue()

@app.on_event("startup")
async def warm():
    """Load the default engines in the background so the first measured turn is not a model load."""
    def work():
        for e in (T.ENGINES["piper"], T.ENGINES["kokoro"], T.ENGINES["supertonic"], S.ENGINES["fw-base"], S.ENGINES["parakeet-0.6b"]):
            try: e.load(); print(f"[warm] {e.name} ready")
            except Exception as ex: print(f"[warm] {e.name} unavailable: {ex}")
        try: V.make("silero"); print("[warm] silero ready")
        except Exception as ex: print(f"[warm] silero unavailable: {ex}")
    asyncio.get_running_loop().run_in_executor(None, work)

@app.get("/")
async def index(): return FileResponse(STATIC / "index.html")

@app.get("/api/engines")
async def engines():
    return {"stt": [{"name": e.name, "note": e.note} for e in S.ENGINES.values()],
            "tts": [{"name": e.name, "note": e.note, "voices": e.voices} for e in T.ENGINES.values()],
            "vad": ["energy", "silero"], "model": L.MODEL, "llm_ready": bool(L.KEY),
            "phrases": [l.strip() for l in (ROOT / "phrases.txt").read_text().splitlines() if l.strip()]}

async def run_stt(pcm: bytes, names: list[str], ref: str | None = None) -> list[dict]:
    out = []
    for n in names:
        e = S.ENGINES.get(n)
        if not e: continue
        try:
            async with _lock:
                with Timer() as t: text = await asyncio.get_running_loop().run_in_executor(None, e.transcribe, pcm)
            row = {"engine": n, "text": text, "ms": round(t.ms)}
            if ref: row["wer"] = round(wer(ref, text), 2)
            record("stt", engine=n, hyp=text, ms=t.ms, ref=ref, wer=row.get("wer"), source="mic", audio_s=len(pcm) / 2 / SR)
        except Exception as ex: row = {"engine": n, "error": str(ex)[:200]}
        out.append(row)
    return out

@app.post("/api/stt")
async def api_stt(request: Request, engines: str = "fw-base", ref: str = ""):
    pcm = await request.body()
    return {"audio_s": round(len(pcm) / 2 / SR, 2), "results": await run_stt(pcm, engines.split(","), ref or None)}

async def synth(engine: str, text: str, voice: str | None):
    """Run a TTS engine's generator in a thread; returns (pcm, ttfa_ms, total_ms)."""
    e = T.ENGINES[engine]
    def work():
        t0 = time.perf_counter(); first = None; chunks = []
        for c in e.stream(text, voice):
            if first is None: first = (time.perf_counter() - t0) * 1000
            chunks.append(c)
        return b"".join(chunks), first or 0, (time.perf_counter() - t0) * 1000
    async with _lock: return await asyncio.get_running_loop().run_in_executor(None, work)

@app.post("/api/tts")
async def api_tts(body: dict):
    engine, text, voice = body["engine"], body["text"], body.get("voice")
    try: pcm, ttfa, total = await synth(engine, text, voice)
    except Exception as ex: return Response(json.dumps({"error": str(ex)[:300]}), status_code=500, media_type="application/json")
    dur = len(pcm) / 2 / SR
    record("tts", engine=engine, voice=voice, text=text, ttfa_ms=ttfa, total_ms=total, audio_s=dur, rtf=total / 1000 / max(dur, .01))
    return Response(wav_bytes(pcm), media_type="audio/wav", headers={"X-TTFA-ms": f"{ttfa:.0f}", "X-Total-ms": f"{total:.0f}", "X-Audio-s": f"{dur:.2f}"})

@app.post("/api/rate")
async def api_rate(body: dict): record("rating", **body); return {"ok": True}

def _rows(kind):
    p = RESULTS / f"{kind}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

@app.get("/api/results")
async def api_results():
    def med(xs): xs = [x for x in xs if x is not None]; return round(statistics.median(xs), 2) if xs else None
    stt = {}
    for r in _rows("stt"): stt.setdefault(r["engine"], []).append(r)
    tts = {}
    for r in _rows("tts"): tts.setdefault(f'{r["engine"]}/{r.get("voice") or "-"}', []).append(r)
    ratings = {}
    for r in _rows("rating"): ratings.setdefault(f'{r.get("engine")}/{r.get("voice") or "-"}', []).append(r.get("rating"))
    turns = _rows("turn")
    return {"stt": [{"engine": k, "n": len(v), "ms": med([x["ms"] for x in v]), "wer": med([x.get("wer") for x in v])} for k, v in stt.items()],
            "tts": [{"engine": k, "n": len(v), "ttfa": med([x["ttfa_ms"] for x in v]), "rtf": med([x["rtf"] for x in v]),
                     "rating": med(ratings.get(k, [])), "votes": len(ratings.get(k, []))} for k, v in tts.items()],
            "turns": [{"stack": k, "n": len(v), "eos_to_transcript": med([x.get("t_transcript") for x in v]), "eos_to_first_token": med([x.get("t_first_token") for x in v]),
                       "eos_to_first_audio": med([x.get("t_first_audio") for x in v]), "eos_to_done": med([x.get("t_done") for x in v])}
                      for k, v in {k: [t for t in turns if t.get("stack") == k] for k in {t.get("stack") for t in turns}}.items()]}

# ---------------- HEAR: live VAD meter + utterance -> all selected STT engines ----------------
@app.websocket("/ws/hear")
async def ws_hear(ws: WebSocket):
    await ws.accept()
    cfg = json.loads(await ws.receive_text())                          # {stt:[...], vad:"energy", ref:""}
    vads = {"energy": V.make("energy"), "silero": V.make("silero")}
    active = cfg.get("vad", "energy"); ref = cfg.get("ref") or None; last_level = 0; starts = 0
    try:
        while True:
            m = await ws.receive()
            if m.get("text"):
                d = json.loads(m["text"])
                if d.get("type") == "config": active = d.get("vad", active); ref = d.get("ref") or None
                continue
            pcm = m.get("bytes")
            if not pcm: continue
            utt = None
            for name, v in vads.items():
                was = v.speaking
                u = v.feed(pcm)
                if name == active:
                    utt = u
                    if v.speaking and not was: starts += 1
            now = time.time()
            if now - last_level > 0.08:
                last_level = now
                await ws.send_text(json.dumps({"type": "level", "energy": round(vads["energy"].level, 3), "silero": round(vads["silero"].level, 3),
                                               "speaking": vads[active].speaking, "starts": starts}))
            if utt:
                await ws.send_text(json.dumps({"type": "utterance", "audio_s": round(len(utt) / 2 / SR, 2)}))
                res = await run_stt(utt, cfg.get("stt", ["fw-base"]), ref)
                await ws.send_text(json.dumps({"type": "results", "results": res, "audio_s": round(len(utt) / 2 / SR, 2)}))
                await ws.send_bytes(wav_bytes(utt))                # so the page can replay what was heard
    except WebSocketDisconnect: pass

# ---------------- TALK: full pipeline as a virtual Pixel ----------------
@app.websocket("/ws/talk")
async def ws_talk(ws: WebSocket):
    await ws.accept()
    cfg = json.loads(await ws.receive_text())                          # {stt, tts, voice, vad, model}
    vad = V.make(cfg.get("vad", "energy"))
    history: list[dict] = []
    turn_task: asyncio.Task | None = None
    cancel = asyncio.Event()

    async def send(**d): await ws.send_text(json.dumps(d))

    async def run_turn(pcm: bytes | None, text: str | None):
        cancel.clear(); t_eos = time.perf_counter(); stamps = {}
        def T_(k): stamps[k] = round((time.perf_counter() - t_eos) * 1000)
        try:
            if pcm is not None:
                res = await run_stt(pcm, [cfg["stt"]]); text = res[0].get("text", "") if res else ""
                T_("t_transcript")
                if not text or len(text.split()) < 1: await send(type="transcript", text="", note="nothing recognised"); return
            await send(type="transcript", text=text, t=stamps.get("t_transcript"))
            history.append({"role": "user", "content": text})
            ch = Chunker(); reply = ""; first_tok = True; first_audio = True; nchunk = 0
            tts_q: asyncio.Queue = asyncio.Queue()
            def ms(): return round((time.perf_counter() - t_eos) * 1000)
            async def enqueue(text):
                nonlocal nchunk
                nchunk += 1; await send(type="chunk", id=nchunk, text=text, state="queued", t=ms()); await tts_q.put((nchunk, text))
            async def speaker():
                nonlocal first_audio
                while True:
                    item = await tts_q.get()
                    if item is None: break
                    cid, chunk = item
                    if cancel.is_set(): await send(type="chunk", id=cid, state="cancelled", t=ms()); continue
                    await send(type="chunk", id=cid, state="synth", t=ms())
                    e = T.ENGINES[cfg["tts"]]
                    def gen():
                        for c in e.stream(chunk, cfg.get("voice")): yield c
                    loop = asyncio.get_running_loop()
                    it = gen(); nbytes = 0; t_synth = time.perf_counter()
                    while not cancel.is_set():
                        async with _lock: c = await loop.run_in_executor(None, lambda: next(it, None))
                        if c is None: break
                        if nbytes == 0: await send(type="chunk", id=cid, state="audio", t=ms())     # frames for this chunk follow
                        if first_audio: first_audio = False; T_("t_first_audio"); await send(type="first_audio", t=stamps["t_first_audio"])
                        nbytes += len(c); await ws.send_bytes(c)
                    await send(type="chunk", id=cid, state="ready", t=ms(), audio_s=round(nbytes / 2 / SR, 2), synth_ms=round((time.perf_counter() - t_synth) * 1000))
            sp = asyncio.create_task(speaker())
            async for delta in L.stream(history, cfg.get("model")):
                if cancel.is_set(): break
                if first_tok: first_tok = False; T_("t_first_token"); await send(type="first_token", t=stamps["t_first_token"])
                reply += delta; await send(type="delta", text=delta)
                for c in ch.feed(delta): await enqueue(c)
            if not cancel.is_set():
                for c in ch.flush(): await enqueue(c)
            await send(type="llm_done", t=ms())
            await tts_q.put(None); await sp
            T_("t_done")
            history.append({"role": "assistant", "content": reply})
            del history[:-12]
            record("turn", stack=f'{cfg["stt"]}+{cfg["tts"]}/{cfg.get("voice") or "-"}+{cfg.get("vad")}', user=text, reply=reply, cancelled=cancel.is_set(), **stamps)
            await send(type="speech_end", reply=reply, cancelled=cancel.is_set(), **stamps)
        except Exception as ex:
            await send(type="error", message=str(ex)[:300])

    try:
        while True:
            m = await ws.receive()
            if m.get("text"):
                d = json.loads(m["text"])
                if d.get("type") == "config": cfg.update({k: v for k, v in d.items() if k != "type"}); vad = V.make(cfg.get("vad", "energy"))
                elif d.get("type") == "interrupt": cancel.set()
                elif d.get("type") == "text":
                    if turn_task and not turn_task.done(): cancel.set(); await turn_task
                    turn_task = asyncio.create_task(run_turn(None, d["text"]))
                elif d.get("type") == "reset": history.clear()
                continue
            pcm = m.get("bytes")
            if not pcm: continue
            utt = vad.feed(pcm)
            if vad.speaking and turn_task and not turn_task.done() and cfg.get("barge_in", True):
                cancel.set()                                            # the user started talking over Pixel
            if utt:
                await send(type="speech_end_detected", audio_s=round(len(utt) / 2 / SR, 2))
                if turn_task and not turn_task.done(): cancel.set(); await turn_task
                turn_task = asyncio.create_task(run_turn(utt, None))
    except WebSocketDisconnect:
        cancel.set()
