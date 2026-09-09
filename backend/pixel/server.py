"""Pixel's brain: one WebSocket per device, REST API, portal. State lives in Postgres (repo) and Redis (bus).

Client -> server (WebSocket /ws)
  text   {"type":"hello","token":"...","device":"pixel","device_type":"lite|3s|sim","fw":"...","capabilities":{...},"aec":true}
  binary PCM16 mono 16 kHz audio chunks (server runs VAD)
  text   {"type":"end"} | {"type":"text","text":".."} | {"type":"ping"} | {"type":"status",...} | {"type":"playback_end"} | {"type":"interrupt"}
Server -> client
  {"type":"ready"} {"type":"config",...} {"type":"vad",speaking} {"type":"transcript",text} {"type":"expression",name,intensity}
  {"type":"tool",name,args} {"type":"step",text} {"type":"speech_start",sr} + binary PCM16 + {"type":"speech_end"} {"type":"speech_cancel"}
  {"type":"reply",text} {"type":"redeploy",wait_s} {"type":"error",message}
"""
import asyncio, json, re, time
from pathlib import Path

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse

from . import ambient, bus, config as C, inference, llm, memory, obs, repo, settings, tools
from .vad import EnergyVAD

obs.setup_logging()
log = structlog.get_logger("pixel.server")

app = FastAPI(title="pixel-brain")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
FLUSH = "\x00FLUSH"     # in-band marker: "the model's pre-tool sentence is complete, start the final reply clean"
PORTAL_DIR = Path(__file__).resolve().parent / "portal"
STT_JUNK = {"thank you.", "thank you", "thanks.", "you", "you.", "bye.", "bye", ".", "thank you for watching.", "thanks for watching.", "hmm.", "uh.", "okay.", "so."}
MIN_UTTERANCE_S, POST_SPEECH_GUARD_S = 0.4, 0.4

sessions: dict[str, "Session"] = {}       # device_id -> live session in THIS process
started_at = time.time()


class Session:
    def __init__(self, ws: WebSocket, pixel: dict):
        self.ws, self.pixel = ws, pixel
        self.device_id, self.pid, self.hid = pixel["device_id"], pixel["id"], pixel["household_id"]
        self.is_sim = pixel["device_type"] == "sim"
        self.connected_at = time.time(); self.last_turn = None
        self.busy = False; self.status: dict = {}; self.status_at = None
        self.turn_task: asyncio.Task | None = None
        self.speaking_until = 0.0
        self.first_status = False


@app.on_event("startup")
async def _startup():
    await inference.warm()
    log.info("brain.started", inference="worker" if inference.URL else "in-process")


@app.get("/health")
async def health():
    h = await repo.default_household()
    present = await bus.presence_all()
    return {"ok": True, "store": "postgres", "inference": "worker" if inference.URL else "in-process",
            "devices": list(present), "household": h["name"], "auth_required": bool(C.PIXEL_TOKEN)}


@app.get("/metrics")
async def metrics():
    body, ctype = obs.metrics_payload()
    return Response(content=body, media_type=ctype)


async def send_json(ws: WebSocket, **msg):
    await ws.send_text(json.dumps(msg))


# =============================================================== conversation turn
async def respond(ws: WebSocket | None, sess: "Session | None", cfg: dict, user_text: str, want_audio=True) -> dict:
    """LLM (with tools) -> sentence-level TTS, streaming to the client as it becomes available. Returns the logged turn."""
    hid, pid = cfg["_household_id"], cfg["_pixel_id"]
    tools.current_cfg.set(cfg)
    structlog.contextvars.bind_contextvars(pixel=pid, household=hid)
    t0 = time.time()
    ambient.maybe_refresh(cfg)
    if ws: await send_json(ws, type="expression", name="thinking", intensity=0.7)

    hist = []
    for t in await repo.turns(pid=pid, limit=cfg["history_turns"]):
        if t.get("user_text"): hist.append({"role": "user", "content": t["user_text"]})
        if t.get("reply"): hist.append({"role": "assistant", "content": t["reply"]})
    messages = hist + [{"role": "user", "content": user_text}]
    system_prompt = await memory.system_prompt(cfg)

    tagged, pending, spoken_all = "", "", ""
    tag_done = speech_started = had_expr = False
    expr, inten = "neutral", 0.6
    t_expr = t_audio = None
    audio_bytes = 0
    tools_used: list[dict] = []; steps: list[str] = []
    tool_defs = tools.definitions(cfg) if cfg["tools_enabled"] else None

    async def speak(text: str):
        nonlocal speech_started, t_audio, audio_bytes
        text = text.strip()
        if not text or not ws or not want_audio: return
        await asyncio.sleep(0)
        if not speech_started:
            await send_json(ws, type="speech_start", sr=C.SAMPLE_RATE)
            speech_started = True; t_audio = time.time() - t0
            obs.STAGE.labels("first_audio").observe(t_audio)
            if sess: sess.speaking_until = time.time() + 30
        pcm = await inference.synthesize(text)
        audio_bytes += len(pcm)
        for i in range(0, len(pcm), C.AUDIO_FRAME_BYTES):
            await ws.send_bytes(pcm[i:i + C.AUDIO_FRAME_BYTES])

    async def say_step(text: str, expression: str):
        text = text.strip()
        if not text: return
        steps.append(text)
        if ws:
            await send_json(ws, type="expression", name=expression, intensity=0.8)
            await send_json(ws, type="step", text=text)
        await speak(text)

    async def stream_with_tools():
        nonlocal messages, tagged, tag_done
        narrate = cfg["tool_narration"]
        for _round in range(3):
            calls, said = None, ""
            async for kind, payload in llm.stream_reply(system_prompt, messages, cfg["chat_model"], cfg["chat_think"], tool_defs):
                if kind == "delta": said += payload; yield payload
                else: calls = payload
            if not calls or _round == 2: return
            said_words = re.sub(r"^\s*\[[^\]]*\]?\s*", "", said).strip()
            if ws:
                for c in calls: await send_json(ws, type="tool", name=c.get("function", {}).get("name"), args=c.get("function", {}).get("arguments"))
            if said_words:
                yield FLUSH
                if ws: await send_json(ws, type="expression", name="curious", intensity=0.8)
            else:
                tagged = ""; tag_done = False
                if narrate: await say_step(*tools.narrate_before(calls))
                elif ws: await send_json(ws, type="expression", name="curious", intensity=0.8)
            results = await asyncio.gather(*(tools.run(c) for c in calls))
            if narrate:
                bridge = tools.narrate_after(results)
                if bridge: await say_step(*bridge)
            messages = messages + [{"role": "assistant", "content": said or "", "tool_calls": calls}]
            for (name, args, res), c in zip(results, calls):
                tools_used.append({"name": name, "args": args, "result": res[:300]})
                obs.TOOL_CALLS.labels(name or "?").inc()
                messages = messages + [{"role": "tool", "tool_name": name, "content": res}]

    error = None
    try:
        async for delta in stream_with_tools():
            if delta == FLUSH:
                if pending.strip(): await speak(pending)
                if spoken_all.strip(): steps.append(re.sub(r"\s+", " ", spoken_all).strip())
                pending, tagged, spoken_all = "", "", ""
                tag_done = False; had_expr = True
                continue
            if not tag_done:
                tagged += delta
                parsed = llm.parse_tag(tagged)
                if parsed is None: continue
                name_, inten_, rest = parsed
                if had_expr and not tagged.lstrip().startswith("["): name_, inten_ = expr, inten
                expr, inten = name_, inten_
                tag_done = True; t_expr = time.time() - t0
                obs.STAGE.labels("expression").observe(t_expr)
                if ws: await send_json(ws, type="expression", name=expr, intensity=inten)
                delta = rest
            pending += delta; spoken_all += delta
            parts = _SENTENCE_END.split(pending)
            if len(parts) > 1:
                for sentence in parts[:-1]: await speak(sentence)
                pending = parts[-1]
        if not tag_done and tagged: pending += tagged; spoken_all += tagged
        await speak(pending)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        error = repr(e); log.error("turn.failed", error=error)
        if ws: await send_json(ws, type="error", message=str(e))
    if speech_started and ws: await send_json(ws, type="speech_end")
    spoken_all = spoken_all.strip()
    if not spoken_all and steps: spoken_all = steps.pop()
    t_done = time.time() - t0
    obs.STAGE.labels("done").observe(t_done)
    obs.TURNS.labels(sess.pixel["device_type"] if sess else "portal", "error" if error else "ok").inc()
    if ws: await send_json(ws, type="reply", text=spoken_all)

    turn = await repo.log_turn(pid, hid, user_text=user_text, reply=spoken_all, expr=expr, intensity=inten,
                               t_expr=round((t_expr or 0) * 1000), t_audio=round((t_audio or 0) * 1000), t_done=round(t_done * 1000),
                               audio_s=round(audio_bytes / 32000, 1), model=cfg["chat_model"], tools=tools_used, steps=steps, error=error)
    log.info("turn", expr=expr, t_expr=turn["t_expr"], t_audio=turn["t_audio"], t_done=turn["t_done"], tools=[t["name"] for t in tools_used])
    asyncio.create_task(memory.extract_after_turn(cfg))
    return turn


# =============================================================== WebSocket (devices / simulator)
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try: hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
    except Exception: await ws.close(code=4000); return
    if C.PIXEL_TOKEN and hello.get("token") != C.PIXEL_TOKEN:
        await ws.close(code=4001); return

    device_id = re.sub(r"[^a-z0-9_-]", "", (hello.get("device") or "pixel").lower()) or "pixel"
    device_type = hello.get("device_type") or ("sim" if device_id.startswith("sim") else "lite")
    pixel = await repo.get_or_create_pixel(device_id, device_type, capabilities=hello.get("capabilities") or {})
    if hello.get("fw"): await repo.update_pixel(pixel["id"], fw_version=hello["fw"])
    sess = Session(ws, pixel); sessions[device_id] = sess
    obs.WS_SESSIONS.inc()
    structlog.contextvars.bind_contextvars(pixel=pixel["id"], device=device_id)
    cfg = await settings.resolve(sess.hid, sess.pid)
    if not sess.is_sim:
        await bus.presence_set(device_id, pixel_id=sess.pid, household_id=sess.hid, connected_at=time.time(), status={}, busy=False)
        await repo.device_event(sess.pid, "connected", ip=ws.client.host if ws.client else None)
    vad = EnergyVAD()
    barge = EnergyVAD(start_rms=cfg["barge_rms"], end_rms=C.VAD_END_RMS, min_speech_ms=cfg["barge_min_ms"])
    barge_ms = 0.0; was_speaking = False
    await send_json(ws, type="ready")
    await ws.send_text(json.dumps(settings.device_config(cfg)))
    log.info("device.connected", device_type=device_type)

    async def _turn(text: str):
        nonlocal cfg
        sess.busy = True
        try:
            cfg = await settings.resolve(sess.hid, sess.pid)
            turn = await respond(ws, sess, cfg, text)
            sess.last_turn = time.time()
            sess.speaking_until = time.time() + turn.get("audio_s", 0) + POST_SPEECH_GUARD_S
            if not sess.is_sim: await bus.presence_set(device_id, last_turn=sess.last_turn)
        except asyncio.CancelledError:
            log.info("turn.cancelled")
            try:
                await send_json(ws, type="speech_cancel"); await send_json(ws, type="expression", name="listening", intensity=0.8)
            except Exception: pass
            raise
        finally: sess.busy = False

    async def run_turn(text: str):
        if sess.turn_task and not sess.turn_task.done():
            sess.turn_task.cancel()
            try: await sess.turn_task
            except (asyncio.CancelledError, Exception): pass
        sess.speaking_until = 0.0; vad.reset()
        sess.turn_task = asyncio.create_task(_turn(text))
        try: await sess.turn_task
        except asyncio.CancelledError: pass

    def mic_gated() -> bool: return time.time() < sess.speaking_until

    async def handle_utterance(pcm: bytes):
        if len(pcm) < MIN_UTTERANCE_S * C.SAMPLE_RATE * 2: return
        await send_json(ws, type="expression", name="thinking", intensity=0.5)
        t = time.time()
        text = await inference.transcribe(pcm)
        obs.STAGE.labels("stt").observe(time.time() - t)
        if len(text.strip()) < 2 or text.strip().lower() in STT_JUNK:
            obs.STT_DROPPED.inc(); await send_json(ws, type="expression", name="curious", intensity=0.4); return
        await send_json(ws, type="transcript", text=text)
        asyncio.create_task(run_turn(text))

    async def injector():
        while True:
            try: msgs = await bus.inbox_drain(device_id)
            except Exception as e: log.warning("inbox.error", error=repr(e)); msgs = []
            for msg in msgs:
                if msg.get("type") == "say":
                    await send_json(ws, type="transcript", text=msg["text"]); asyncio.create_task(run_turn(msg["text"]))
                elif msg.get("type") == "config":
                    c = await settings.resolve(sess.hid, sess.pid); await ws.send_text(json.dumps(settings.device_config(c)))
                elif msg.get("type") == "redeploy":
                    await send_json(ws, type="redeploy", wait_s=msg.get("wait_s", 60)); await ws.close(code=4002)
            await asyncio.sleep(1)

    inj = asyncio.create_task(injector())
    try:
        while True:
            msg = await ws.receive()
            if msg.get("bytes") is not None:
                if mic_gated():
                    if not cfg["barge_in"] or hello.get("aec") is False: vad.reset(); continue
                    barge.feed(msg["bytes"])
                    if barge.speaking:
                        barge_ms += len(msg["bytes"]) / (C.SAMPLE_RATE * 2) * 1000
                        if barge_ms >= cfg["barge_min_ms"]:
                            log.info("barge_in")
                            if sess.turn_task and not sess.turn_task.done(): sess.turn_task.cancel()
                            sess.speaking_until = 0.0
                            vad.reset(); vad.buffer = bytearray(barge.buffer); vad.speaking = True; vad.speech_ms = barge.speech_ms
                            barge.reset(); barge_ms = 0.0; was_speaking = True
                            await send_json(ws, type="vad", speaking=True)
                    else: barge_ms = 0.0
                    continue
                barge.reset(); barge_ms = 0.0
                utt = vad.feed(msg["bytes"])
                if vad.speaking != was_speaking:
                    was_speaking = vad.speaking; await send_json(ws, type="vad", speaking=was_speaking)
                if utt:
                    was_speaking = False; await send_json(ws, type="vad", speaking=False)
                    await handle_utterance(utt)
            elif msg.get("text") is not None:
                data = json.loads(msg["text"]); t = data.get("type")
                if t == "ping": await send_json(ws, type="pong", t=time.time())
                elif t == "status":
                    sess.status = {k: data.get(k) for k in ("rssi", "heap", "uptime_s", "ip", "fw", "expr", "name", "battery_v", "reset_reason", "fps")}
                    sess.status_at = time.time()
                    if not sess.is_sim:
                        await bus.presence_set(device_id, status=sess.status, connected_at=sess.connected_at, busy=sess.busy)
                        if not sess.first_status:
                            sess.first_status = True
                            await repo.device_event(sess.pid, "status", rssi=data.get("rssi"), ip=data.get("ip"), fw=data.get("fw"))
                            if data.get("fw") or data.get("reset_reason"):
                                await repo.update_pixel(sess.pid, fw_version=data.get("fw"), reset_reason=data.get("reset_reason"))
                    ambient.maybe_refresh(cfg)
                elif t == "log":
                    for line in (data.get("lines") or [])[:50]: await repo.device_log(sess.pid, data.get("level", "info"), str(line))
                elif t == "playback_end": sess.speaking_until = time.time() + POST_SPEECH_GUARD_S; vad.reset()
                elif t == "interrupt":
                    if sess.turn_task and not sess.turn_task.done(): sess.turn_task.cancel()
                    sess.speaking_until = 0.0; vad.reset()
                elif t == "end":
                    utt = vad.flush()
                    if utt: await handle_utterance(utt)
                elif t == "text":
                    await send_json(ws, type="transcript", text=data["text"]); asyncio.create_task(run_turn(data["text"]))
            elif msg.get("type") == "websocket.disconnect": break
    except WebSocketDisconnect: pass
    except Exception as e:
        log.error("ws.error", error=repr(e))
        try: await send_json(ws, type="error", message=str(e))
        except Exception: pass
    finally:
        inj.cancel(); obs.WS_SESSIONS.dec()
        if not sess.is_sim:
            await repo.device_event(sess.pid, "disconnected", duration_s=int(time.time() - sess.connected_at), rssi=sess.status.get("rssi"))
        if sessions.get(device_id) is sess:
            del sessions[device_id]
            if not sess.is_sim: await bus.presence_clear(device_id)
        log.info("device.disconnected")


# =============================================================== REST API (portal)
def auth(request: Request):
    if not C.PIXEL_TOKEN: return
    tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.cookies.get("pixel_token")
    if tok != C.PIXEL_TOKEN: raise HTTPException(401, "bad token")


async def scope(pixel: int | None = None) -> tuple[dict, dict, dict]:
    """P0 shim: the default household; the requested pixel or the household's first physical one (then any)."""
    h = await repo.default_household()
    px = await repo.pixels_in_household(h["id"])
    p = next((x for x in px if x["id"] == pixel), None) if pixel else None
    if not p:
        p = next((x for x in px if x["device_type"] != "sim"), None) or (px[0] if px else None)
    if not p:
        p = await repo.get_or_create_pixel("pixel", "lite", household_id=h["id"])
    return h, p, await settings.resolve(h["id"], p["id"])


@app.get("/")
async def root(): return RedirectResponse("/portal")

@app.get("/portal")
async def portal(): return FileResponse(PORTAL_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/status", dependencies=[Depends(auth)])
async def api_status(pixel: int | None = None):
    h, p, cfg = await scope(pixel)
    tz = cfg["timezone"]; today = memory.today_key(tz)
    px = await repo.pixels_in_household(h["id"])
    present = await bus.presence_all([x["device_id"] for x in px if x["device_type"] != "sim"])
    devices = []
    for d, v in present.items():
        devices.append({"id": d, "pixel_id": v.get("pixel_id"), "connected_s": int(time.time() - v.get("connected_at", time.time())), "busy": v.get("busy", False),
                        "last_turn_s": int(time.time() - v["last_turn"]) if v.get("last_turn") else None,
                        "status": v.get("status") or {}, "status_age_s": int(time.time() - v["last_seen"])})
    recent = await repo.turns(hid=h["id"], limit=30)
    return {"name": cfg["name"], "uptime_s": int(time.time() - started_at), "devices": devices,
            "pixels": [{"id": x["id"], "device_id": x["device_id"], "device_type": x["device_type"], "name": x["name"], "fw": x.get("fw_version")} for x in px],
            "pixel_id": p["id"], "household": {"id": h["id"], "name": h["name"]},
            "turns_today": await repo.count_turns(h["id"], today, tz), "turns_total": await repo.count_turns(h["id"]),
            "facts": len(await repo.facts(h["id"])), "followups": len(await repo.followups(h["id"])),
            "latency": [{k: t.get(k) for k in ("ts", "t_expr", "t_audio", "t_done")} for t in recent],
            "summary_today": (await repo.summaries(h["id"])).get(today), "chat_model": cfg["chat_model"], "memory_model": cfg["memory_model"]}


@app.get("/api/config", dependencies=[Depends(auth)])
async def api_config(pixel: int | None = None):
    _, _, cfg = await scope(pixel); return cfg

@app.get("/api/config/defaults", dependencies=[Depends(auth)])
async def api_config_defaults(): return settings.DEFAULTS

@app.put("/api/config", dependencies=[Depends(auth)])
async def api_config_put(patch: dict, pixel: int | None = None):
    h, p, _ = await scope(pixel)
    cfg = await settings.update(h["id"], p["id"], patch)
    for x in await repo.pixels_in_household(h["id"]):
        await bus.inbox_push(x["device_id"], {"type": "config"})
    return cfg

@app.get("/api/models", dependencies=[Depends(auth)])
async def api_models():
    try: return {"host": C.OLLAMA_HOST, "models": await llm.list_models()}
    except Exception as e: return {"host": C.OLLAMA_HOST, "models": [], "error": str(e)}

@app.get("/api/prompt", dependencies=[Depends(auth)])
async def api_prompt(pixel: int | None = None):
    _, _, cfg = await scope(pixel); return {"system_prompt": await memory.system_prompt(cfg)}

@app.get("/api/ambient", dependencies=[Depends(auth)])
async def api_ambient(pixel: int | None = None):
    h, _, _ = await scope(pixel); return await repo.ambient_get(h["id"])

@app.post("/api/ambient/refresh", dependencies=[Depends(auth)])
async def api_ambient_refresh(pixel: int | None = None):
    _, _, cfg = await scope(pixel); return await ambient.refresh(cfg)

@app.get("/api/turns", dependencies=[Depends(auth)])
async def api_turns(day: str | None = None, limit: int = 200, pixel: int | None = None):
    h, _, cfg = await scope(pixel)
    rows = await repo.turns(hid=h["id"], limit=limit, day=day, tz=cfg["timezone"])
    for r in rows: r["user"] = r.pop("user_text", None)   # portal compatibility
    return rows

@app.delete("/api/turns/{turn_id}", dependencies=[Depends(auth)])
async def api_turn_delete(turn_id: int): await repo.delete_turn(turn_id); return {"ok": True}

@app.get("/api/facts", dependencies=[Depends(auth)])
async def api_facts(archived: bool = False, pixel: int | None = None):
    h, _, _ = await scope(pixel); return await repo.facts(h["id"], include_archived=archived)

@app.post("/api/facts", dependencies=[Depends(auth)])
async def api_fact_add(body: dict, pixel: int | None = None):
    if not body.get("text"): raise HTTPException(400, "text required")
    h, _, _ = await scope(pixel); return await repo.add_fact(h["id"], body["text"], body.get("type", "fact"), pinned=bool(body.get("pinned")))

@app.put("/api/facts/{fid}", dependencies=[Depends(auth)])
async def api_fact_put(fid: int, body: dict):
    f = await repo.update_fact(fid, **{k: body.get(k) for k in ("text", "type", "pinned", "archived")})
    if not f: raise HTTPException(404)
    return f

@app.delete("/api/facts/{fid}", dependencies=[Depends(auth)])
async def api_fact_delete(fid: int): await repo.delete_fact(fid); return {"ok": True}

@app.post("/api/facts/forget_all", dependencies=[Depends(auth)])
async def api_forget_all(pixel: int | None = None):
    h, _, _ = await scope(pixel); await repo.forget_all(h["id"]); return {"ok": True}

@app.get("/api/followups", dependencies=[Depends(auth)])
async def api_followups(pixel: int | None = None):
    h, _, _ = await scope(pixel); return await repo.followups(h["id"], open_only=False)

@app.put("/api/followups/{fid}", dependencies=[Depends(auth)])
async def api_followup_put(fid: int, body: dict): await repo.resolve_followup(fid, bool(body.get("done", True))); return {"ok": True}

@app.delete("/api/followups/{fid}", dependencies=[Depends(auth)])
async def api_followup_delete(fid: int): await repo.delete_followup(fid); return {"ok": True}

@app.get("/api/summaries", dependencies=[Depends(auth)])
async def api_summaries(pixel: int | None = None):
    h, _, _ = await scope(pixel); return await repo.summaries(h["id"])

@app.post("/api/memory/extract", dependencies=[Depends(auth)])
async def api_extract_now(pixel: int | None = None):
    h, _, cfg = await scope(pixel); await memory.extract(cfg); return {"ok": True, "facts": await repo.facts(h["id"])}

@app.post("/api/chat", dependencies=[Depends(auth)])
async def api_chat(body: dict, pixel: int | None = None):
    if not body.get("text"): raise HTTPException(400, "text required")
    h, p, cfg = await scope(pixel)
    return await respond(None, None, cfg, body["text"], want_audio=False)

@app.post("/api/admin/drain", dependencies=[Depends(auth)])
async def api_drain():
    n = 0
    for d in await bus.presence_all():
        await bus.inbox_push(d, {"type": "redeploy", "wait_s": 60}); n += 1
    return {"notified": n}

@app.get("/api/device/events", dependencies=[Depends(auth)])
async def api_device_events(limit: int = 60, pixel: int | None = None):
    _, p, _ = await scope(pixel); return await repo.device_events(p["id"], limit)

@app.post("/api/device/say", dependencies=[Depends(auth)])
async def api_device_say(body: dict, pixel: int | None = None):
    present = await bus.presence_all()
    device = body.get("device") or next(iter(present), None)
    if not device or device not in present: raise HTTPException(409, "no device connected")
    await bus.inbox_push(device, {"type": "say", "text": body["text"]}); return {"ok": True, "device": device}
