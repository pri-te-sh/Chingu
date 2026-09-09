"""Pixel's brain: one WebSocket per conversation.

Client -> server
  text   {"type":"hello","token":"...","device":"pixel"}
  binary PCM16 mono 16 kHz audio chunks (server runs VAD)
  text   {"type":"end"}            force end of utterance (e.g. button release)
  text   {"type":"text","text":".."} bypass STT (testing / typed input)
  text   {"type":"ping"}           -> {"type":"pong"} (latency probe)
Server -> client
  text   {"type":"ready"}
  text   {"type":"vad","speaking":true|false}
  text   {"type":"transcript","text":".."}
  text   {"type":"expression","name":"happy","intensity":0.8}
  text   {"type":"speech_start","sr":16000}  then binary PCM16 chunks  then {"type":"speech_end"}
  text   {"type":"reply","text":".."}   full spoken text, for logs
  text   {"type":"error","message":".."}
"""
import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from . import config as C, llm, stt, tts
from .vad import EnergyVAD

app = FastAPI(title="pixel-brain")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@app.get("/health")
async def health():
    return {"ok": True, "model": C.OLLAMA_MODEL, "whisper": C.WHISPER_MODEL, "voice": C.PIPER_VOICE}


@app.on_event("startup")
async def warm():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, stt.load)
    await loop.run_in_executor(None, tts.load)
    print("[pixel] models loaded")


class Memory:
    """Rolling dialogue history persisted to disk so Pixel remembers across container restarts."""
    def __init__(self, device: str):
        C.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = C.DATA_DIR / f"history-{re.sub(r'[^a-z0-9_-]', '', device.lower()) or 'pixel'}.json"
        self.turns: list[dict] = json.loads(self.path.read_text()) if self.path.exists() else []

    def add(self, role: str, content: str):
        self.turns.append({"role": role, "content": content})
        self.turns = self.turns[-C.HISTORY_TURNS * 2:]
        self.path.write_text(json.dumps(self.turns, indent=1))


async def send_json(ws: WebSocket, **msg):
    await ws.send_text(json.dumps(msg))


async def respond(ws: WebSocket, memory: Memory, user_text: str):
    """Run LLM -> sentence-level TTS, streaming everything to the client as it becomes available."""
    loop = asyncio.get_running_loop()
    t0 = time.time()
    memory.add("user", user_text)
    await send_json(ws, type="expression", name="thinking", intensity=0.7)

    tagged = ""            # text until the expression tag is resolved
    pending = ""           # spoken text not yet synthesised
    spoken_all = ""
    tag_done = False
    speech_started = False

    async def speak(text: str):
        nonlocal speech_started
        text = text.strip()
        if not text:
            return
        if not speech_started:
            await send_json(ws, type="speech_start", sr=C.SAMPLE_RATE)
            speech_started = True
            print(f"[pixel] first audio after {time.time() - t0:.2f}s")
        chunks = await loop.run_in_executor(None, lambda: list(tts.synthesize(text)))
        pcm = b"".join(chunks)
        for i in range(0, len(pcm), C.AUDIO_FRAME_BYTES):        # small frames: the ESP32 buffers each frame in RAM
            await ws.send_bytes(pcm[i:i + C.AUDIO_FRAME_BYTES])

    async for delta in llm.stream_reply(memory.turns):
        if not tag_done:
            tagged += delta
            parsed = llm.parse_tag(tagged)
            if parsed is None:
                continue
            name, inten, rest = parsed
            tag_done = True
            await send_json(ws, type="expression", name=name, intensity=inten)
            print(f"[pixel] expression {name} {inten:.1f} after {time.time() - t0:.2f}s")
            delta = rest
        pending += delta
        spoken_all += delta
        parts = _SENTENCE_END.split(pending)
        if len(parts) > 1:                                  # complete sentence(s) ready
            for sentence in parts[:-1]:
                await speak(sentence)
            pending = parts[-1]
    if not tag_done and tagged:                             # very short reply without a tag
        pending += tagged; spoken_all += tagged
    await speak(pending)
    if speech_started:
        await send_json(ws, type="speech_end")
    spoken_all = spoken_all.strip()
    memory.add("assistant", spoken_all)
    await send_json(ws, type="reply", text=spoken_all)
    print(f"[pixel] reply done in {time.time() - t0:.2f}s: {spoken_all!r}")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
    except Exception:
        await ws.close(code=4000); return
    if C.PIXEL_TOKEN and hello.get("token") != C.PIXEL_TOKEN:
        await ws.close(code=4001); return

    memory = Memory(hello.get("device", "pixel"))
    vad = EnergyVAD()
    loop = asyncio.get_running_loop()
    await send_json(ws, type="ready")
    was_speaking = False

    async def handle_utterance(pcm: bytes):
        await send_json(ws, type="expression", name="thinking", intensity=0.5)
        t = time.time()
        text = await loop.run_in_executor(None, stt.transcribe, pcm)
        print(f"[pixel] stt {time.time() - t:.2f}s: {text!r}")
        await send_json(ws, type="transcript", text=text)
        if len(text.strip()) < 2:
            await send_json(ws, type="expression", name="curious", intensity=0.5)
            return
        await respond(ws, memory, text)

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
                if data.get("type") == "ping":
                    await send_json(ws, type="pong", t=time.time())
                elif data.get("type") == "end":
                    utt = vad.flush()
                    if utt:
                        await handle_utterance(utt)
                elif data.get("type") == "text":
                    await send_json(ws, type="transcript", text=data["text"])
                    await respond(ws, memory, data["text"])
            elif msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # keep the device informed instead of dying silently
        print(f"[pixel] error: {e!r}")
        try:
            await send_json(ws, type="error", message=str(e))
        except Exception:
            pass
