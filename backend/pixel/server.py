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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from . import ambient, auth, bus, config as C, firmware, inference, llm, memory, obs, repo, settings, tools
from .chunker import Chunker
from starlette.middleware.sessions import SessionMiddleware
from .vad import EnergyVAD

obs.setup_logging()
log = structlog.get_logger("pixel.server")

app = FastAPI(title="pixel-brain")
app.include_router(firmware.router)
app.add_middleware(SessionMiddleware, secret_key=auth.SESSION_SECRET, session_cookie="pixel_oauth", same_site="lax", https_only=False)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
PAUSE_CLAUSE_MS, PAUSE_SENTENCE_MS = 140, 320
_INLINE_TAG = re.compile(r"\s*\[([a-z_]+)\s+([0-9.]+)\]\s*")   # models sometimes re-tag mid-reply; never read those aloud     # breathing room inserted *before* a chunk, based on how the previous one ended
_chunk_seq = 0
FLUSH = "\x00FLUSH"     # in-band marker: "the model's pre-tool sentence is complete, start the final reply clean"
PORTAL_DIR = Path(__file__).resolve().parent / "portal"
STT_JUNK = {"thank you.", "thank you", "thanks.", "you", "you.", "bye.", "bye", ".", "thank you for watching.", "thanks for watching.", "hmm.", "uh.", "okay.", "so."}
MIN_UTTERANCE_S, POST_SPEECH_GUARD_S = 0.4, 0.4
MAX_PENDING_DEVICES = 50               # unclaimed registrations allowed at once (F11); never-claimed ones expire after 7 days
PAIRING_SESSION_S = 15 * 60            # an unpaired socket is closed after this; the device simply reconnects

sessions: dict[str, "Session"] = {}       # device_id -> live session in THIS process
started_at = time.time()


async def revoke_sessions(device_id: str, reason: str):
    """Close every live socket for a device whose authorization changed (release, claim, un-pair, remove)."""
    old = sessions.pop(device_id, None)
    if old is None: return
    if old.turn_task and not old.turn_task.done(): old.turn_task.cancel()
    try: await old.ws.close(code=4001, reason=reason)
    except Exception: pass
    log.warning("device.revoked", device=device_id, reason=reason)


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
        self.auth_gen = (pixel.get("household_id"), pixel.get("token_hash"), pixel.get("archived"))   # ownership snapshot; any change revokes this socket

    async def still_authorized(self) -> bool:
        """The row's ownership/token must be exactly what this socket authenticated against."""
        p = await repo.pixel(self.pid)
        return bool(p) and (p.get("household_id"), p.get("token_hash"), p.get("archived")) == self.auth_gen


@app.on_event("startup")
async def _startup():
    await inference.warm()
    asyncio.create_task(_retention_loop())
    log.info("brain.started", inference="worker" if inference.URL else "in-process")


async def _retention_loop():
    """Daily: device events 90 d, device logs 14 d, expired sessions, never-claimed registrations 7 d (D13 retention)."""
    while True:
        try: log.info("retention", **(await repo.retention()))
        except Exception as e: log.warning("retention.failed", error=repr(e))
        await asyncio.sleep(24 * 3600)


@app.get("/health")
async def health():
    """Public liveness only: no identities. Readiness (DB, Redis, worker) is reported as ok=false with the failing part named."""
    parts = {}
    try: await repo.default_household(); parts["db"] = True
    except Exception: parts["db"] = False
    try: parts["redis"] = await bus.ping()
    except Exception: parts["redis"] = False
    parts["worker"] = await inference.ready()
    ok = all(parts.values())
    body = {"ok": ok, "parts": parts, "providers": auth.providers()}
    return body if ok else JSONResponse(body, status_code=503)


@app.get("/api/health")
async def api_health(request: Request):
    """Authenticated diagnostics for the household: which of *its* devices are present."""
    h, _, _ = await scope(request)
    mine = [x["device_id"] for x in await repo.pixels_in_household(h["id"])]
    present = await bus.presence_all(mine)
    return {"ok": True, "devices": [d for d in mine if d in present], "household": h["name"], "inference": "worker" if inference.URL else "in-process"}


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
    for t in await repo.turns(hid=hid, pid=pid, limit=cfg["history_turns"]):
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

    # --- speech pipeline: chunks queue up while the LLM keeps streaming; a speaker task synthesises and sends them in order ---
    tts_q: asyncio.Queue = asyncio.Queue()
    last_end = ""

    async def _emit(cid: int, text: str):
        nonlocal speech_started, t_audio, audio_bytes, last_end
        if ws: await send_json(ws, type="chunk", id=cid, state="synth")
        pcm = await inference.synthesize(text)
        if not speech_started:
            await send_json(ws, type="speech_start", sr=C.SAMPLE_RATE)
            speech_started = True; t_audio = time.time() - t0
            obs.STAGE.labels("first_audio").observe(t_audio)
            if sess: sess.speaking_until = time.time() + 30
        pause = 0 if not last_end else PAUSE_SENTENCE_MS if last_end in ".!?" else PAUSE_CLAUSE_MS if last_end in ",;:" else 0
        if pause:
            gap = b"\x00" * (C.SAMPLE_RATE * 2 * pause // 1000); audio_bytes += len(gap); await ws.send_bytes(gap)
        await send_json(ws, type="chunk", id=cid, state="audio")
        audio_bytes += len(pcm)
        for i in range(0, len(pcm), C.AUDIO_FRAME_BYTES):
            await ws.send_bytes(pcm[i:i + C.AUDIO_FRAME_BYTES])
        await send_json(ws, type="chunk", id=cid, state="ready", audio_s=round(len(pcm) / 2 / C.SAMPLE_RATE, 2))
        last_end = text.rstrip()[-1:]

    async def speaker():
        while True:
            item = await tts_q.get()
            if item is None: return
            await _emit(*item)

    speaker_task = asyncio.create_task(speaker()) if (ws and want_audio) else None

    async def speak(text: str):
        """Queue a chunk for speech (returns immediately; audio streams from the speaker task)."""
        global _chunk_seq
        nonlocal expr, inten
        for m_ in _INLINE_TAG.finditer(text):                  # a mid-reply tag becomes an expression change, not speech
            try: expr, inten = m_.group(1), float(m_.group(2))
            except ValueError: pass
            if ws: await send_json(ws, type="expression", name=expr, intensity=inten)
        text = _INLINE_TAG.sub(" ", text).strip()
        if not text or not ws or not want_audio: return
        _chunk_seq += 1
        await send_json(ws, type="chunk", id=_chunk_seq, text=text, state="queued")
        await tts_q.put((_chunk_seq, text))

    async def drain():
        if speaker_task: await tts_q.put(None); await speaker_task

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
    chunker = Chunker()
    try:
        async for delta in stream_with_tools():
            if delta == FLUSH:
                for c in chunker.flush(): await speak(c)
                pending = ""
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
            spoken_all += delta
            for c in chunker.feed(delta): await speak(c)
        if not tag_done and tagged: spoken_all += tagged; chunker.feed(tagged)
        for c in chunker.flush(): await speak(c)
        await drain()
    except asyncio.CancelledError:
        if speaker_task: speaker_task.cancel()
        raise
    except Exception as e:
        error = repr(e); log.error("turn.failed", error=error)
        if speaker_task and not speaker_task.done(): speaker_task.cancel()
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
    device_id = re.sub(r"[^a-z0-9_-]", "", (hello.get("device") or "pixel").lower()) or "pixel"
    requested_type = hello.get("device_type") if hello.get("device_type") in ("lite", "3s", "sim") else ("sim" if device_id.startswith("sim") else "lite")
    pixel = await repo.pixel_by_device(device_id)
    device_type = pixel["device_type"] if pixel else requested_type      # an existing row's type is never changed by a hello
    caps = hello.get("capabilities") if isinstance(hello.get("capabilities"), dict) else {}

    if device_type == "sim":
        # Browser simulator: the session cookie IS the credential, on every connection. It may only use its own household's sims.
        user = await auth.current_user(ws)
        hs = await auth.households_for(user["id"]) if user else []
        if not hs: await ws.close(code=4001, reason="sign in required"); return
        my_hids = {h["id"] for h in hs}
        if pixel and pixel.get("household_id") not in my_hids and pixel.get("household_id") is not None:
            await ws.close(code=4003, reason="simulator belongs to another household"); return
        if not pixel: pixel = await repo.get_or_create_pixel(device_id, "sim", household_id=hs[0]["id"], capabilities=caps)
        elif not pixel.get("household_id"): await repo.pair(pixel["id"], hs[0]["id"], name="Simulator"); pixel = await repo.pixel(pixel["id"])
        paired = True
    else:
        # Physical device: identity key binds device_id to the unit (survives factory reset); token binds it to a household.
        key = hello.get("device_key") if isinstance(hello.get("device_key"), str) and 16 <= len(hello.get("device_key")) <= 128 else None
        source = ws.client.host if ws.client else "?"
        if pixel and pixel.get("archived"):
            # Removed in the portal: that ended its household authorization. It re-enters as a fresh, unpaired registration.
            pixel = await repo.get_or_create_pixel(device_id, pixel["device_type"], capabilities=caps)
            log.info("device.reregistered", device=device_id)
        if not pixel:
            if not await bus.registration_allowed(source): await ws.close(code=4029, reason="too many registrations from this address"); return
            if await repo.count_pending() >= MAX_PENDING_DEVICES: await ws.close(code=4029, reason="too many unclaimed devices"); return
            pixel = await repo.get_or_create_pixel(device_id, requested_type, capabilities=caps)
            if key: await repo.set_pixel_key(pixel["id"], key); pixel = await repo.pixel(pixel["id"])
            log.info("device.registered", device=device_id, keyed=bool(key))
        paired = bool(pixel.get("household_id"))
        token_ok = paired and repo.token_valid(pixel, hello.get("token"))
        if pixel.get("device_key_hash"):
            if not repo.key_valid(pixel, key):
                log.warning("device.identity_mismatch", device=device_id); await ws.close(code=4001, reason="device identity mismatch"); return
        elif key:
            # Legacy row without a key: enrol one ONLY on a connection that is already trusted (valid token, or an unpaired
            # row nobody owns). A paired legacy row with a bad token must not be able to plant an attacker's key (R1).
            if token_ok or not paired:
                await repo.set_pixel_key(pixel["id"], key); pixel = await repo.pixel(pixel["id"]); log.info("device.keyed", device=device_id)
            else:
                log.warning("device.bad_token", device=device_id); await ws.close(code=4001, reason="unauthorized"); return
        if paired and not token_ok:
            if pixel.get("device_key_hash"):
                # The physical unit (identity proven above) lost its token: factory reset or on-device un-pair. Release it for re-pairing,
                # and revoke any socket still authenticated under the old ownership right now (V1).
                await repo.unpair(pixel["id"]); await repo.device_event(pixel["id"], "released", reason="device lost its token")
                await revoke_sessions(device_id, "device released")
                pixel = await repo.pixel(pixel["id"]); paired = False; log.info("device.released", device=device_id)
            else:
                log.warning("device.bad_token", device=device_id); await ws.close(code=4001, reason="unauthorized"); return
        await repo.touch_pixel(pixel["id"], capabilities=caps, fw_version=hello.get("fw"))
    if not paired:
        # stay connected, show the code, wait for the owner to claim us in the portal
        code = pixel.get("pairing_code") or repo.new_pairing_code()
        if not pixel.get("pairing_code"): await repo.update_pixel(pixel["id"], pairing_code=code)
        await send_json(ws, type="pairing", code=code)
        log.info("device.unpaired", device=device_id, code=code)
        pairing_deadline = time.time() + PAIRING_SESSION_S
        try:
            while time.time() < pairing_deadline:
                try: msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                except asyncio.TimeoutError: msg = None
                if msg and msg.get("type") == "websocket.disconnect": return
                for m_ in await bus.inbox_drain(device_id):
                    if m_.get("type") == "paired":
                        await send_json(ws, type="paired", token=m_["token"], name=m_.get("name"))
                        await ws.close(code=4003, reason="paired - reconnect with token"); return
            await ws.close(code=4008, reason="pairing session expired - reconnect"); return
        except WebSocketDisconnect: return

    old = sessions.get(device_id)
    if old is not None:                                   # one live socket per device: a new authenticated connection supersedes the old one
        try: await old.ws.close(code=4000, reason="superseded by a new connection")
        except Exception: pass
        sessions.pop(device_id, None)
    sess = Session(ws, pixel); sessions[device_id] = sess
    obs.WS_SESSIONS.inc()
    structlog.contextvars.bind_contextvars(pixel=pixel["id"], device=device_id)
    cfg = await settings.resolve(sess.hid, sess.pid)
    caps = pixel.get("capabilities") or {}
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
        if not await sess.still_authorized():             # ownership or token changed since this socket authenticated
            log.warning("device.revoked", device=device_id); await ws.close(code=4001, reason="authorization changed"); return
        sess.busy = True
        try:
            cfg = await settings.resolve(sess.hid, sess.pid)
            turn = await respond(ws, sess, cfg, text, want_audio=caps.get("speaker", True))
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
        if not await sess.still_authorized(): await revoke_sessions(device_id, "authorization changed"); return
        await send_json(ws, type="expression", name="thinking", intensity=0.5)
        t = time.time()
        eng = hello.get("stt") if hello.get("stt") in ("whisper", "parakeet") else None     # simulator can pin an engine for A/B
        text = await inference.transcribe(pcm, eng)
        stt_ms = int((time.time() - t) * 1000)
        obs.STAGE.labels("stt").observe(stt_ms / 1000)
        if len(text.strip()) < 2 or text.strip().lower() in STT_JUNK:
            obs.STT_DROPPED.inc(); await send_json(ws, type="expression", name="curious", intensity=0.4); return
        await send_json(ws, type="transcript", text=text, stt_ms=stt_ms, stt=eng or "default", audio_s=round(len(pcm) / 2 / C.SAMPLE_RATE, 1))
        asyncio.create_task(run_turn(text))

    async def injector():
        ticks = 0
        while True:
            ticks += 1
            if ticks % 5 == 0 and not await sess.still_authorized():       # every ~5 s: revoke live sockets whose device was released/claimed/removed
                log.warning("device.revoked", device=device_id)
                if sess.turn_task and not sess.turn_task.done(): sess.turn_task.cancel()
                try: await ws.close(code=4001, reason="authorization changed")
                except Exception: pass
                return
            try: msgs = await bus.inbox_drain(device_id)
            except Exception as e: log.warning("inbox.error", error=repr(e)); msgs = []
            for msg in msgs:
                if msg.get("type") == "say":
                    await send_json(ws, type="transcript", text=msg["text"]); asyncio.create_task(run_turn(msg["text"]))
                elif msg.get("type") == "config":
                    c = await settings.resolve(sess.hid, sess.pid); await ws.send_text(json.dumps(settings.device_config(c)))
                elif msg.get("type") == "redeploy":
                    await send_json(ws, type="redeploy", wait_s=msg.get("wait_s", 60)); await ws.close(code=4002)
                elif msg.get("type") == "unpaired":
                    await send_json(ws, type="unpaired"); await ws.close(code=4004)
                elif msg.get("type") == "ota":
                    await send_json(ws, type="ota", version=msg.get("version"))
                elif msg.get("type") == "brain":
                    await send_json(ws, type="brain", host=msg.get("host"), port=msg.get("port"), tls=msg.get("tls", True))
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
                    def _num(v, lo, hi):
                        try: f = float(v); return f if lo <= f <= hi else None
                        except (TypeError, ValueError): return None
                    def _str(v, n=48): return str(v)[:n] if isinstance(v, (str, int, float)) else None
                    sess.status = {"rssi": _num(data.get("rssi"), -120, 0), "heap": _num(data.get("heap"), 0, 1e9), "uptime_s": _num(data.get("uptime_s"), 0, 1e10),
                                   "ip": _str(data.get("ip"), 45), "fw": _str(data.get("fw"), 24), "build": _str(data.get("build"), 40), "ota": _str(data.get("ota"), 16),
                                   "expr": _str(data.get("expr"), 16), "name": _str(data.get("name"), 40), "battery_v": _num(data.get("battery_v"), 0, 10),
                                   "reset_reason": _str(data.get("reset_reason"), 8), "fps": _num(data.get("fps"), 0, 1000)}
                    sess.status_at = time.time()
                    if not sess.is_sim:
                        await bus.presence_set(device_id, status=sess.status, connected_at=sess.connected_at, busy=sess.busy)
                        if not sess.first_status:
                            sess.first_status = True
                            await repo.device_event(sess.pid, "status", rssi=data.get("rssi"), ip=data.get("ip"), fw=data.get("fw"))
                            if data.get("fw") or data.get("reset_reason") is not None:
                                await repo.update_pixel(sess.pid, fw_version=str(data.get("fw") or ""), reset_reason=str(data.get("reset_reason")))
                    ambient.maybe_refresh(cfg)
                elif t == "log":
                    for line in (data.get("lines") or [])[:50]: await repo.device_log(sess.pid, data.get("level", "info"), str(line))
                elif t == "playback_end": sess.speaking_until = time.time() + POST_SPEECH_GUARD_S; vad.reset()
                elif t == "interrupt":
                    if sess.turn_task and not sess.turn_task.done(): sess.turn_task.cancel()
                    else: await send_json(ws, type="speech_cancel")       # synthesis already finished: still flush queued playback
                    sess.speaking_until = 0.0; vad.reset()
                elif t == "end":
                    utt = vad.flush()
                    if utt: await handle_utterance(utt)
                elif t == "text":
                    if not await sess.still_authorized(): await revoke_sessions(device_id, "authorization changed"); break
                    await send_json(ws, type="transcript", text=data["text"]); asyncio.create_task(run_turn(data["text"]))
            elif msg.get("type") == "websocket.disconnect": break
    except WebSocketDisconnect: pass
    except Exception as e:
        log.error("ws.error", error=repr(e))
        try: await send_json(ws, type="error", message=str(e))
        except Exception: pass
    finally:
        inj.cancel(); obs.WS_SESSIONS.dec()
        if sess.turn_task and not sess.turn_task.done():
            sess.turn_task.cancel()
            try: await sess.turn_task
            except (asyncio.CancelledError, Exception): pass
        if not sess.is_sim:
            await repo.device_event(sess.pid, "disconnected", duration_s=int(time.time() - sess.connected_at), rssi=sess.status.get("rssi"))
        if sessions.get(device_id) is sess:
            del sessions[device_id]
            if not sess.is_sim: await bus.presence_clear(device_id)
        log.info("device.disconnected")


# =============================================================== REST API (portal)
async def scope(request: Request, pixel: int | None = None) -> tuple[dict, dict, dict]:
    """The signed-in user's household; the requested pixel or the household's first physical one (then any)."""
    user = await auth.require_user(request)
    hs = await auth.households_for(user["id"])
    h = (await repo.household(hs[0]["id"])) if hs else await repo.default_household()
    px = await repo.pixels_in_household(h["id"])
    p = next((x for x in px if x["id"] == pixel), None) if pixel else None
    if pixel and not p: raise HTTPException(404, "no such pixel in your household")
    if not p:
        p = next((x for x in px if x["device_type"] != "sim"), None) or (px[0] if px else None)
    if not p:
        # household without any Pixel yet: a placeholder persona holder so settings pages work
        p = await repo.get_or_create_pixel(f"placeholder-{h['id']}", "sim", household_id=h["id"])
    return h, p, await settings.resolve(h["id"], p["id"])


@app.get("/")
async def root(): return RedirectResponse("/portal")


@app.get("/api/me")
async def api_me(request: Request):
    u = await auth.current_user(request)
    if not u: return {"user": None, "providers": auth.providers()}
    hs = await auth.households_for(u["id"])
    mem = await auth.members(hs[0]["id"]) if hs else []
    return {"user": {k: u.get(k) for k in ("id", "email", "name", "avatar", "provider", "is_admin")}, "households": hs, "members": mem, "providers": auth.providers()}


@app.put("/api/me")
async def api_me_put(request: Request, body: dict):
    u = await auth.require_user(request)
    if body.get("name"): await repo.execute(sa_update_user(u["id"], body["name"]))
    return await api_me(request)


def sa_update_user(uid: int, name: str):
    import sqlalchemy as sa
    from . import models as m
    return sa.update(m.users).where(m.users.c.id == uid).values(name=name.strip()[:80])


@app.put("/api/household")
async def api_household_put(request: Request, body: dict):
    u = await auth.require_user(request)
    hs = await auth.households_for(u["id"])
    if not hs: raise HTTPException(404)
    if body.get("name"): await repo.update_household(hs[0]["id"], name=body["name"].strip()[:80])
    return await repo.household(hs[0]["id"])


@app.post("/auth/dev")
async def auth_dev(request: Request, body: dict):
    if not auth.dev_login(body.get("email", ""), body.get("password", "")):
        raise HTTPException(401, "invalid credentials")
    # the dev provider is ONE local account: changing DEV_LOGIN_EMAIL in .env must not create a second user/household
    u = await auth.upsert_user("dev", "local-owner", body["email"].strip().lower(), body.get("name") or body["email"].split("@")[0].title())
    tok = await auth.create_session(u["id"])
    resp = Response(content=json.dumps({"ok": True}), media_type="application/json")
    auth.set_cookie(resp, tok, request)
    log.info("auth.login", provider="dev", user=u["id"])
    return resp


@app.get("/auth/google")
async def auth_google(request: Request):
    if not auth.providers()["google"]: raise HTTPException(404, "google sign-in not configured")
    g = auth.google_client()
    return await g.authorize_redirect(request, str(request.url_for("auth_callback")))


@app.get("/auth/callback")
async def auth_callback(request: Request):
    g = auth.google_client()
    token = await g.authorize_access_token(request)
    info = token.get("userinfo") or await g.userinfo(token=token)
    u = await auth.upsert_user("google", info["sub"], info.get("email"), info.get("name"), info.get("picture"))
    tok = await auth.create_session(u["id"])
    resp = RedirectResponse("/portal")
    auth.set_cookie(resp, tok, request)
    log.info("auth.login", provider="google", user=u["id"])
    return resp


@app.post("/auth/logout")
async def auth_logout(request: Request):
    await auth.destroy_session(request)
    resp = Response(content=json.dumps({"ok": True}), media_type="application/json")
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp

@app.get("/portal")
async def portal(): return FileResponse(PORTAL_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/status")
async def api_status(request: Request, pixel: int | None = None):
    h, p, cfg = await scope(request, pixel)
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


@app.get("/api/config")
async def api_config(request: Request, pixel: int | None = None):
    _, _, cfg = await scope(request, pixel); return cfg

@app.get("/api/config/defaults")
async def api_config_defaults(request: Request):
    await auth.require_user(request)
    return settings.DEFAULTS

@app.put("/api/config")
async def api_config_put(request: Request, patch: dict, pixel: int | None = None):
    h, p, _ = await scope(request, pixel)
    if not isinstance(patch, dict): raise HTTPException(400, "object expected")
    try: cfg = await settings.update(h["id"], p["id"], patch)
    except settings.InvalidSetting as e: raise HTTPException(400, str(e))
    for x in await repo.pixels_in_household(h["id"]):
        await bus.inbox_push(x["device_id"], {"type": "config"})
    return cfg

@app.get("/api/models")
async def api_models(request: Request):
    await auth.require_user(request)
    try: return {"host": C.OLLAMA_HOST, "models": await llm.list_models()}
    except Exception as e: return {"host": C.OLLAMA_HOST, "models": [], "error": str(e)}

@app.get("/api/prompt")
async def api_prompt(request: Request, pixel: int | None = None):
    _, _, cfg = await scope(request, pixel); return {"system_prompt": await memory.system_prompt(cfg)}

@app.get("/api/ambient")
async def api_ambient(request: Request, pixel: int | None = None):
    h, _, _ = await scope(request, pixel); return await repo.ambient_get(h["id"])

@app.post("/api/ambient/refresh")
async def api_ambient_refresh(request: Request, pixel: int | None = None):
    _, _, cfg = await scope(request, pixel); return await ambient.refresh(cfg)

@app.get("/api/history")
async def api_history(request: Request, q: str | None = None, day: str | None = None, device: int | None = None, limit: int = 200, offset: int = 0, pixel: int | None = None):
    """Paginated, searchable history over ALL turns of the household (the portal groups a page into conversations)."""
    h, _, cfg = await scope(request, pixel)
    limit = max(1, min(limit, 500)); offset = max(0, offset)
    if device is not None and not any(x["id"] == device for x in await repo.pixels_in_household(h["id"], include_archived=True)): raise HTTPException(404, "no such pixel")
    rows, total = await repo.search_turns(h["id"], q=q, day=day, tz=cfg["timezone"], pid=device, limit=limit, offset=offset)
    for r in rows: r["user"] = r.pop("user_text", None)
    return {"total": total, "offset": offset, "limit": limit, "turns": rows, "days": await repo.turn_days(h["id"], cfg["timezone"], device),
            "session_gap_min": cfg["session_gap_min"], "timezone": cfg["timezone"]}


@app.get("/api/turns")
async def api_turns(request: Request, day: str | None = None, limit: int = 200, pixel: int | None = None):
    h, _, cfg = await scope(request, pixel)
    rows = await repo.turns(hid=h["id"], limit=limit, day=day, tz=cfg["timezone"])
    for r in rows: r["user"] = r.pop("user_text", None)   # portal compatibility
    return rows

@app.delete("/api/turns/{turn_id}")
async def api_turn_delete(request: Request, turn_id: int):
    h, _, _ = await scope(request)
    await repo.delete_turn(turn_id, h["id"]); return {"ok": True}

@app.get("/api/facts")
async def api_facts(request: Request, archived: bool = False, pixel: int | None = None):
    h, _, _ = await scope(request, pixel); return await repo.facts(h["id"], include_archived=archived)

@app.post("/api/facts")
async def api_fact_add(request: Request, body: dict, pixel: int | None = None):
    if not body.get("text"): raise HTTPException(400, "text required")
    h, _, _ = await scope(request, pixel); return await repo.add_fact(h["id"], body["text"], body.get("type", "fact"), pinned=bool(body.get("pinned")))

@app.put("/api/facts/{fid}")
async def api_fact_put(request: Request, fid: int, body: dict):
    h, _, _ = await scope(request)
    f = await repo.update_fact(fid, h["id"], **{k: body.get(k) for k in ("text", "type", "pinned", "archived")})
    if not f: raise HTTPException(404)
    return f

@app.delete("/api/facts/{fid}")
async def api_fact_delete(request: Request, fid: int):
    h, _, _ = await scope(request)
    await repo.delete_fact(fid, h["id"]); return {"ok": True}

@app.post("/api/facts/forget_all")
async def api_forget_all(request: Request, pixel: int | None = None):
    h, _, _ = await scope(request, pixel); await repo.forget_all(h["id"]); return {"ok": True}

@app.get("/api/followups")
async def api_followups(request: Request, pixel: int | None = None):
    h, _, _ = await scope(request, pixel); return await repo.followups(h["id"], open_only=False)

@app.put("/api/followups/{fid}")
async def api_followup_put(request: Request, fid: int, body: dict):
    h, _, _ = await scope(request)
    await repo.resolve_followup(fid, h["id"], bool(body.get("done", True))); return {"ok": True}

@app.delete("/api/followups/{fid}")
async def api_followup_delete(request: Request, fid: int):
    h, _, _ = await scope(request)
    await repo.delete_followup(fid, h["id"]); return {"ok": True}

@app.get("/api/summaries")
async def api_summaries(request: Request, pixel: int | None = None):
    h, _, _ = await scope(request, pixel); return await repo.summaries(h["id"])

@app.post("/api/memory/extract")
async def api_extract_now(request: Request, pixel: int | None = None):
    h, _, cfg = await scope(request, pixel); await memory.extract(cfg); return {"ok": True, "facts": await repo.facts(h["id"])}

@app.post("/api/chat")
async def api_chat(request: Request, body: dict, pixel: int | None = None):
    if not body.get("text"): raise HTTPException(400, "text required")
    h, p, cfg = await scope(request, pixel)
    return await respond(None, None, cfg, body["text"], want_audio=False)

@app.get("/api/pixels")
async def api_pixels(request: Request):
    h, _, _ = await scope(request)
    px = await repo.pixels_in_household(h["id"])
    present = await bus.presence_all([x["device_id"] for x in px])
    out = []
    for x in px:
        if x["device_id"].startswith("placeholder-"): continue
        pr = present.get(x["device_id"])
        lat = await firmware.latest(x["device_type"], x.get("fw_channel") or "stable") if x["device_type"] != "sim" else None
        out.append({"id": x["id"], "device_id": x["device_id"], "device_type": x["device_type"], "name": x["name"], "fw": x.get("fw_version"), "fw_channel": x.get("fw_channel") or "stable",
                    "fw_build": ((pr or {}).get("status") or {}).get("build"), "latest_fw": lat and {"version": lat["version"], "notes": lat.get("notes") or "", "created_at": lat.get("created_at")},
                    "update_available": bool(lat and x.get("fw_version") and firmware.VERSION_RE.match(x["fw_version"]) and firmware.vkey(lat["version"]) > firmware.vkey(x["fw_version"])),
                    "capabilities": x.get("capabilities") or {}, "online": bool(pr), "status": (pr or {}).get("status") or {}, "paired_at": x.get("paired_at"), "last_seen_at": x.get("last_seen_at")})
    return out


@app.post("/api/pixels/claim")
async def api_pixels_claim(request: Request, body: dict):
    """Owner types the code shown on the device -> it joins the household and receives its token."""
    h, _, _ = await scope(request)
    code = (body.get("code") or "").strip().upper()
    p = await repo.pixel_by_code(code) if len(code) == 6 else None
    if not p: raise HTTPException(404, "no unpaired device is showing that code")
    # Only an UNPAIRED device is claimable (pixel_by_code filters on that). A paired device must be released first:
    # portal UNPAIR by its owner, or factory reset / un-pair on the device itself (identity-key verified).
    tok = await repo.pair(p["id"], h["id"], name=body.get("name") or p.get("name") or ("Pixel-3S" if p["device_type"] == "3s" else "Pixel"))
    await revoke_sessions(p["device_id"], "claimed")           # a stale socket from before the claim must not continue
    await bus.inbox_push(p["device_id"], {"type": "paired", "token": tok, "name": body.get("name")})
    await repo.device_event(p["id"], "paired", household=h["id"])
    log.info("device.paired", device=p["device_id"], household=h["id"])
    return await repo.pixel(p["id"])


@app.patch("/api/pixels/{pid}")
async def api_pixel_patch(request: Request, pid: int, body: dict):
    h, _, _ = await scope(request)
    p = await repo.pixel(pid)
    if not p or p["household_id"] != h["id"]: raise HTTPException(404)
    if body.get("name"):
        await repo.update_pixel(pid, name=body["name"].strip()[:40])
        await bus.inbox_push(p["device_id"], {"type": "config"})
    return await repo.pixel(pid)


@app.post("/api/pixels/{pid}/revoke")
async def api_pixel_revoke(request: Request, pid: int):
    """Un-pair: the device forgets its token and shows a fresh pairing code."""
    h, _, _ = await scope(request)
    p = await repo.pixel(pid)
    if not p or p["household_id"] != h["id"]: raise HTTPException(404)
    await repo.unpair(pid)
    await bus.inbox_push(p["device_id"], {"type": "unpaired"})       # tells the device to forget its token (if this brain process has its socket, revoke now too)
    await revoke_sessions(p["device_id"], "unpaired by owner")
    await repo.device_event(pid, "unpaired")
    return {"ok": True}


@app.delete("/api/pixels/{pid}")
async def api_pixel_delete(request: Request, pid: int):
    h, _, _ = await scope(request)
    p = await repo.pixel(pid)
    if not p or p["household_id"] != h["id"]: raise HTTPException(404)
    await bus.inbox_push(p["device_id"], {"type": "unpaired"})
    await repo.archive_pixel(pid)                      # history is kept; the device shows a pairing code again
    await revoke_sessions(p["device_id"], "removed by owner")
    await repo.device_event(pid, "removed")
    return {"ok": True}


@app.post("/api/pixels/{pid}/brain")
async def api_pixel_move_brain(request: Request, pid: int, body: dict):
    """Cutover helper: tell a paired device to reconnect to another brain (host/port/tls). Its token stays valid there if the DB was migrated."""
    h, _, _ = await scope(request)
    p = await repo.pixel(pid)
    if not p or p["household_id"] != h["id"]: raise HTTPException(404)
    if not body.get("host"): raise HTTPException(400, "host required")
    await bus.inbox_push(p["device_id"], {"type": "brain", "host": body["host"], "port": int(body.get("port", 443)), "tls": bool(body.get("tls", True))})
    await repo.device_event(pid, "brain_moved", host=body["host"])
    return {"ok": True}


@app.post("/api/admin/drain")
async def api_drain(request: Request):
    await auth.require_admin(request)
    n = 0
    for d in await bus.presence_all():
        await bus.inbox_push(d, {"type": "redeploy", "wait_s": 60}); n += 1
    return {"notified": n}

@app.get("/api/device/events")
async def api_device_events(request: Request, limit: int = 60, pixel: int | None = None):
    _, p, _ = await scope(request, pixel); return await repo.device_events(p["id"], limit)

@app.post("/api/device/say")
async def api_device_say(request: Request, body: dict, pixel: int | None = None):
    h, p, _ = await scope(request, pixel)
    mine = {x["device_id"] for x in await repo.pixels_in_household(h["id"])}
    device = body.get("device") or p["device_id"]
    if device not in mine: raise HTTPException(404, "no such device in your household")
    present = await bus.presence_all([device])
    if device not in present: raise HTTPException(409, "device is offline")
    if not isinstance(body.get("text"), str) or not body["text"].strip(): raise HTTPException(400, "text required")
    await bus.inbox_push(device, {"type": "say", "text": body["text"]}); return {"ok": True, "device": device}
