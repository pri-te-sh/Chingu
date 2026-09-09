"""Pixel's brain: one WebSocket per conversation, plus a REST API and the portal.

Client -> server (WebSocket /ws)
  text   {"type":"hello","token":"...","device":"pixel"}
  binary PCM16 mono 16 kHz audio chunks (server runs VAD)
  text   {"type":"end"}              force end of utterance
  text   {"type":"text","text":".."}  bypass STT (typed input)
  text   {"type":"ping"}             -> {"type":"pong"}
Server -> client
  {"type":"ready"}  {"type":"config",name,eye_color,auto_sleep_s}  {"type":"vad",speaking}  {"type":"transcript",text}
  {"type":"expression",name,intensity}  {"type":"speech_start",sr} + binary PCM16 + {"type":"speech_end"}
  {"type":"reply",text}  {"type":"error",message}
"""
import asyncio, json, re, time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import config as C, llm, memory, settings, store, stt, tts
from .vad import EnergyVAD

app = FastAPI(title="pixel-brain")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
PORTAL_DIR = Path(__file__).resolve().parent / "portal"

# connected devices: device id -> session
sessions: dict[str, "Session"] = {}
started_at = time.time()
latency_log: list[dict] = []          # last 50 turns' timings for the dashboard


class Session:
    def __init__(self, ws: WebSocket, device: str):
        self.ws, self.device = ws, device
        self.connected_at = time.time()
        self.last_turn = None
        self.inject: asyncio.Queue = asyncio.Queue()
        self.busy = False
        self.status: dict = {}          # last heartbeat from the board (rssi, heap, uptime, ip, fw, expr)
        self.status_at = None


PRESENCE_TTL = 90          # s without heartbeat before a device is considered gone


def presence_all() -> dict:
    """Devices connected to ANY container, from the shared store; stale entries dropped."""
    now = time.time()
    p = {k: v for k, v in (store.read_json("presence.json", {}) or {}).items() if now - v.get("last_seen", 0) < PRESENCE_TTL}
    return p


def presence_set(device: str, **fields):
    p = store.read_json("presence.json", {}) or {}
    p[device] = {**p.get(device, {}), **fields, "last_seen": time.time()}
    store.write_json("presence.json", p)


def presence_clear(device: str):
    p = store.read_json("presence.json", {}) or {}
    if device in p:
        del p[device]; store.write_json("presence.json", p)


def inbox_push(device: str, msg: dict):
    q = store.read_json(f"inbox:{device}", []) or []
    q.append(msg); store.write_json(f"inbox:{device}", q[-20:])


def inbox_drain(device: str) -> list[dict]:
    q = store.read_json(f"inbox:{device}", []) or []
    if q:
        store.write_json(f"inbox:{device}", [])
    return q


def device_event(device: str, event: str, **extra):
    """Connection history: connected / disconnected / heartbeat summary, kept on the volume."""
    row = {"ts": memory.now_local().isoformat(timespec="seconds"), "device": device, "event": event, **extra}
    store.append_jsonl("device_events.jsonl", row)
    return row


@app.get("/health")
async def health():
    cfg = settings.get()
    return {"ok": True, "name": cfg["name"], "chat_model": cfg["chat_model"], "memory_model": cfg["memory_model"],
            "whisper": C.WHISPER_MODEL, "voice": C.PIPER_VOICE, "devices": list(presence_all()),
            "store": "modal-dict" if store.USE_DICT else "files"}


@app.on_event("startup")
async def warm():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, stt.load)
    await loop.run_in_executor(None, tts.load)
    asyncio.create_task(store.commit_loop())
    print("[pixel] models loaded")


async def send_json(ws: WebSocket, **msg):
    await ws.send_text(json.dumps(msg))


def history_for_prompt() -> list[dict]:
    cfg = settings.get()
    hist = []
    for t in memory.turns(limit=cfg["history_turns"]):
        if t.get("user"): hist.append({"role": "user", "content": t["user"]})
        if t.get("reply"): hist.append({"role": "assistant", "content": t["reply"]})
    return hist


async def respond(ws: WebSocket | None, device: str, user_text: str, want_audio=True) -> dict:
    """LLM -> sentence-level TTS, streaming to the client as it becomes available. Returns the logged turn."""
    loop = asyncio.get_running_loop()
    cfg = settings.get()
    t0 = time.time()
    if ws: await send_json(ws, type="expression", name="thinking", intensity=0.7)

    messages = history_for_prompt() + [{"role": "user", "content": user_text}]
    tagged, pending, spoken_all = "", "", ""
    tag_done = speech_started = False
    expr, inten = "neutral", 0.6
    t_expr = t_audio = None
    audio_bytes = 0

    async def speak(text: str):
        nonlocal speech_started, t_audio, audio_bytes
        text = text.strip()
        if not text or not ws or not want_audio:
            return
        if not speech_started:
            await send_json(ws, type="speech_start", sr=C.SAMPLE_RATE)
            speech_started = True
            t_audio = time.time() - t0
        chunks = await loop.run_in_executor(None, lambda: list(tts.synthesize(text)))
        pcm = b"".join(chunks)
        audio_bytes += len(pcm)
        for i in range(0, len(pcm), C.AUDIO_FRAME_BYTES):
            await ws.send_bytes(pcm[i:i + C.AUDIO_FRAME_BYTES])

    async for delta in llm.stream_reply(memory.system_prompt(), messages, cfg["chat_model"], cfg["chat_think"]):
        if not tag_done:
            tagged += delta
            parsed = llm.parse_tag(tagged)
            if parsed is None:
                continue
            expr, inten, rest = parsed
            tag_done = True
            t_expr = time.time() - t0
            if ws: await send_json(ws, type="expression", name=expr, intensity=inten)
            delta = rest
        pending += delta; spoken_all += delta
        parts = _SENTENCE_END.split(pending)
        if len(parts) > 1:
            for sentence in parts[:-1]:
                await speak(sentence)
            pending = parts[-1]
    if not tag_done and tagged:
        pending += tagged; spoken_all += tagged
    await speak(pending)
    if speech_started:
        await send_json(ws, type="speech_end")
    spoken_all = spoken_all.strip()
    t_done = time.time() - t0
    if ws: await send_json(ws, type="reply", text=spoken_all)

    turn = memory.log_turn({"device": device, "user": user_text, "reply": spoken_all, "expr": expr, "intensity": inten,
                            "t_expr": round((t_expr or 0) * 1000), "t_audio": round((t_audio or 0) * 1000), "t_done": round(t_done * 1000),
                            "audio_s": round(audio_bytes / 32000, 1), "model": cfg["chat_model"]})
    latency_log.append({k: turn[k] for k in ("ts", "t_expr", "t_audio", "t_done")}); del latency_log[:-50]
    print(f"[pixel] {device}: {expr} {inten:.1f} @{turn['t_expr']}ms, audio @{turn['t_audio']}ms, done {turn['t_done']}ms: {spoken_all!r}")
    asyncio.create_task(memory.extract_after_turn())
    return turn


# =============================================================== WebSocket (device / laptop)
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
    except Exception:
        await ws.close(code=4000); return
    if C.PIXEL_TOKEN and hello.get("token") != C.PIXEL_TOKEN:
        await ws.close(code=4001); return

    device = re.sub(r"[^a-z0-9_-]", "", (hello.get("device") or "pixel").lower()) or "pixel"
    sess = Session(ws, device)
    sessions[device] = sess
    presence_set(device, connected_at=time.time(), status={}, busy=False)
    device_event(device, "connected", ip=ws.client.host if ws.client else None)
    vad = EnergyVAD()
    loop = asyncio.get_running_loop()
    await send_json(ws, type="ready")
    await ws.send_text(json.dumps(settings.device_config()))
    was_speaking = False

    async def run_turn(text: str):
        sess.busy = True
        try:
            await respond(ws, device, text)
            sess.last_turn = time.time()
            presence_set(device, last_turn=sess.last_turn)
        finally:
            sess.busy = False

    async def handle_utterance(pcm: bytes):
        await send_json(ws, type="expression", name="thinking", intensity=0.5)
        t = time.time()
        text = await loop.run_in_executor(None, stt.transcribe, pcm)
        print(f"[pixel] stt {time.time() - t:.2f}s: {text!r}")
        await send_json(ws, type="transcript", text=text)
        if len(text.strip()) < 2:
            await send_json(ws, type="expression", name="curious", intensity=0.5)
            return
        await run_turn(text)

    async def injector():                      # portal -> device: "say this", config pushes (via the shared inbox)
        loop = asyncio.get_running_loop()
        while True:
            try:
                msgs = await loop.run_in_executor(None, inbox_drain, device)
            except Exception as e:
                print(f"[pixel] inbox error: {e!r}"); msgs = []
            for msg in msgs:
                if msg.get("type") == "say":
                    await send_json(ws, type="transcript", text=msg["text"])
                    await run_turn(msg["text"])
                elif msg.get("type") == "config":
                    settings.reload()
                    await ws.send_text(json.dumps(settings.device_config()))
            await asyncio.sleep(1)

    inj = asyncio.create_task(injector())
    try:
        while True:
            msg = await ws.receive()
            if msg.get("bytes") is not None:
                utt = vad.feed(msg["bytes"])
                if vad.speaking != was_speaking:
                    was_speaking = vad.speaking
                    await send_json(ws, type="vad", speaking=was_speaking)
                if utt:
                    was_speaking = False
                    await send_json(ws, type="vad", speaking=False)
                    await handle_utterance(utt)
            elif msg.get("text") is not None:
                data = json.loads(msg["text"])
                t = data.get("type")
                if t == "ping":
                    await send_json(ws, type="pong", t=time.time())
                elif t == "status":
                    sess.status = {k: data.get(k) for k in ("rssi", "heap", "uptime_s", "ip", "fw", "expr", "name")}
                    sess.status_at = time.time()
                    presence_set(device, status=sess.status, connected_at=sess.connected_at, busy=sess.busy)
                    if not getattr(sess, "first_status", False):
                        sess.first_status = True
                        device_event(device, "status", rssi=data.get("rssi"), ip=data.get("ip"), fw=data.get("fw"))
                elif t == "end":
                    utt = vad.flush()
                    if utt: await handle_utterance(utt)
                elif t == "text":
                    await send_json(ws, type="transcript", text=data["text"])
                    await run_turn(data["text"])
            elif msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[pixel] error: {e!r}")
        try: await send_json(ws, type="error", message=str(e))
        except Exception: pass
    finally:
        inj.cancel()
        device_event(device, "disconnected", duration_s=int(time.time() - sess.connected_at), rssi=sess.status.get("rssi"))
        if sessions.get(device) is sess:
            del sessions[device]
            presence_clear(device)


# =============================================================== REST API (portal)
def auth(request: Request):
    if not C.PIXEL_TOKEN:
        return
    tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.cookies.get("pixel_token")
    if tok != C.PIXEL_TOKEN:
        raise HTTPException(401, "bad token")


@app.get("/")
async def root():
    return RedirectResponse("/portal")


@app.get("/portal", response_class=HTMLResponse)
async def portal():
    return FileResponse(PORTAL_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/status", dependencies=[Depends(auth)])
async def api_status():
    cfg = settings.get()
    today = memory.today_key()
    return {"name": cfg["name"], "uptime_s": int(time.time() - started_at),
            "devices": [{"id": d, "connected_s": int(time.time() - p.get("connected_at", time.time())), "busy": p.get("busy", False),
                         "last_turn_s": int(time.time() - p["last_turn"]) if p.get("last_turn") else None,
                         "status": p.get("status") or {}, "status_age_s": int(time.time() - p["last_seen"])} for d, p in presence_all().items()],
            "turns_today": len(memory.turns(limit=1000, day=today)), "turns_total": len(memory.turns(limit=100000)),
            "facts": len(memory.facts()), "followups": len(memory.followups()),
            "latency": [{k: t.get(k) for k in ("ts", "t_expr", "t_audio", "t_done")} for t in memory.turns(limit=30)],
            "summary_today": memory.summaries().get(today), "chat_model": cfg["chat_model"], "memory_model": cfg["memory_model"]}


@app.get("/api/config", dependencies=[Depends(auth)])
async def api_config():
    return settings.get()


@app.get("/api/config/defaults", dependencies=[Depends(auth)])
async def api_config_defaults():
    return settings.DEFAULTS


@app.put("/api/config", dependencies=[Depends(auth)])
async def api_config_put(patch: dict):
    cfg = settings.update(patch)
    for d in presence_all():
        inbox_push(d, {"type": "config"})
    return cfg


@app.get("/api/models", dependencies=[Depends(auth)])
async def api_models():
    try:
        return {"host": C.OLLAMA_HOST, "models": await llm.list_models()}
    except Exception as e:
        return {"host": C.OLLAMA_HOST, "models": [], "error": str(e)}


@app.get("/api/prompt", dependencies=[Depends(auth)])
async def api_prompt():
    return {"system_prompt": memory.system_prompt()}


@app.get("/api/turns", dependencies=[Depends(auth)])
async def api_turns(day: str | None = None, limit: int = 200):
    return memory.turns(limit=limit, day=day)


@app.delete("/api/turns/{turn_id}", dependencies=[Depends(auth)])
async def api_turn_delete(turn_id: int):
    memory.delete_turn(turn_id); return {"ok": True}


@app.get("/api/facts", dependencies=[Depends(auth)])
async def api_facts(archived: bool = False):
    return memory.facts(include_archived=archived)


@app.post("/api/facts", dependencies=[Depends(auth)])
async def api_fact_add(body: dict):
    if not body.get("text"): raise HTTPException(400, "text required")
    return memory.add_fact(body["text"], body.get("type", "fact"), pinned=bool(body.get("pinned")))


@app.put("/api/facts/{fid}", dependencies=[Depends(auth)])
async def api_fact_put(fid: int, body: dict):
    f = memory.update_fact(fid, **{k: body.get(k) for k in ("text", "type", "pinned", "archived")})
    if not f: raise HTTPException(404)
    return f


@app.delete("/api/facts/{fid}", dependencies=[Depends(auth)])
async def api_fact_delete(fid: int):
    memory.delete_fact(fid); return {"ok": True}


@app.post("/api/facts/forget_all", dependencies=[Depends(auth)])
async def api_forget_all():
    memory.forget_all(); return {"ok": True}


@app.get("/api/followups", dependencies=[Depends(auth)])
async def api_followups():
    return memory.followups(open_only=False)


@app.put("/api/followups/{fid}", dependencies=[Depends(auth)])
async def api_followup_put(fid: int, body: dict):
    memory.resolve_followup(fid, bool(body.get("done", True))); return {"ok": True}


@app.delete("/api/followups/{fid}", dependencies=[Depends(auth)])
async def api_followup_delete(fid: int):
    memory.delete_followup(fid); return {"ok": True}


@app.get("/api/summaries", dependencies=[Depends(auth)])
async def api_summaries():
    return memory.summaries()


@app.post("/api/memory/extract", dependencies=[Depends(auth)])
async def api_extract_now():
    await memory._extract(); return {"ok": True, "facts": memory.facts()}


@app.post("/api/chat", dependencies=[Depends(auth)])
async def api_chat(body: dict):
    """Text-only test conversation from the portal (no device, no audio)."""
    if not body.get("text"): raise HTTPException(400, "text required")
    return await respond(None, "portal", body["text"], want_audio=False)


@app.post("/api/admin/drain", dependencies=[Depends(auth)])
async def api_drain():
    """Before a redeploy: close device sessions with code 4002 so boards back off and the old container can exit."""
    n = 0
    for sess in list(sessions.values()):
        try:
            await send_json(sess.ws, type="redeploy", wait_s=60)
            await sess.ws.close(code=4002, reason="redeploying")
            n += 1
        except Exception:
            pass
    return {"closed": n}


@app.get("/api/device/events", dependencies=[Depends(auth)])
async def api_device_events(limit: int = 60):
    return store.read_jsonl("device_events.jsonl")[-limit:]


@app.post("/api/device/say", dependencies=[Depends(auth)])
async def api_device_say(body: dict):
    present = presence_all()
    device = body.get("device") or next(iter(present), None)
    if not device or device not in present: raise HTTPException(409, "no device connected")
    inbox_push(device, {"type": "say", "text": body["text"]})
    return {"ok": True, "device": device}
