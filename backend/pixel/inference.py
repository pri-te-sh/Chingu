"""STT/TTS access. If PIXEL_INFERENCE_URL is set the work goes to the worker service (separate process/container,
swap-able for a GPU box later); otherwise it runs in-process in a thread (dev without Docker)."""
import asyncio, os
import httpx
from . import config as C

URL = os.environ.get("PIXEL_INFERENCE_URL", "").rstrip("/")
_client: httpx.AsyncClient | None = None

def _c():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5))
    return _client

async def transcribe(pcm16: bytes) -> str:
    if URL:
        r = await _c().post(f"{URL}/stt", content=pcm16, headers={"content-type": "application/octet-stream"})
        r.raise_for_status(); return r.json()["text"]
    from . import stt
    return await asyncio.get_running_loop().run_in_executor(None, stt.transcribe, pcm16)

async def synthesize(text: str) -> bytes:
    if URL:
        r = await _c().post(f"{URL}/tts", json={"text": text})
        r.raise_for_status(); return r.content
    from . import tts
    return await asyncio.get_running_loop().run_in_executor(None, lambda: b"".join(tts.synthesize(text)))

async def warm():
    if URL:
        try: await _c().get(f"{URL}/health")
        except Exception: pass
    else:
        from . import stt, tts
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, stt.load); await loop.run_in_executor(None, tts.load)
