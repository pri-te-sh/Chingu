"""Chat with Ollama models (local daemon or Ollama Cloud). Conversation replies stream and lead with an expression
tag like [happy 0.8]; parse_tag() strips it so only the spoken words reach TTS. chat_once() serves the memory model."""
import json
import re
import httpx
from . import config as C

_TAG = re.compile(r"^\s*\[\s*([a-z]+)\s*([0-9]*\.?[0-9]+)?\s*\]\s*", re.I)


def parse_tag(text: str) -> tuple[str, float, str] | None:
    """Return (expression, intensity, remainder) if `text` begins with a complete tag; None if not yet decidable."""
    m = _TAG.match(text)
    if m:
        name = m.group(1).lower()
        if name not in C.EXPRESSIONS:
            name = "neutral"
        inten = float(m.group(2)) if m.group(2) else 0.8
        return name, max(0.0, min(1.0, inten)), text[m.end():]
    stripped = text.lstrip()
    if stripped and not stripped.startswith("["):
        return "neutral", 0.6, text
    if len(text) > 40:
        return "neutral", 0.6, text.lstrip("[ ")
    return None


def _headers():
    return {"Authorization": f"Bearer {C.OLLAMA_API_KEY}"} if C.OLLAMA_API_KEY else {}


async def stream_reply(system_prompt: str, history: list[dict], model: str, think: bool = False):
    """Async generator of text deltas from the conversation model."""
    body = {"model": model, "stream": True, "think": think, "options": {"temperature": 0.8, "num_predict": 160},
            "messages": [{"role": "system", "content": system_prompt}, *history]}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
        async with client.stream("POST", f"{C.OLLAMA_HOST}/api/chat", json=body, headers=_headers()) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                msg = json.loads(line)
                delta = msg.get("message", {}).get("content", "")
                if delta:
                    yield delta
                if msg.get("done"):
                    break


async def chat_once(messages: list[dict], model: str, think: bool = False, json_mode: bool = False, timeout: float = 120) -> str:
    """Non-streaming completion (used by the background memory pass)."""
    body = {"model": model, "stream": False, "think": think, "messages": messages, "options": {"temperature": 0.2}}
    if json_mode:
        body["format"] = "json"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        r = await client.post(f"{C.OLLAMA_HOST}/api/chat", json=body, headers=_headers())
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")


async def list_models() -> list[dict]:
    """Models available on the configured Ollama host (cloud: the account's catalogue; local: pulled models)."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=10)) as client:
        r = await client.get(f"{C.OLLAMA_HOST}/api/tags", headers=_headers())
        r.raise_for_status()
        out = []
        for m in r.json().get("models", []):
            d = m.get("details", {}) or {}
            out.append({"name": m.get("name") or m.get("model"), "family": d.get("family"), "size": d.get("parameter_size")})
        return sorted(out, key=lambda x: x["name"] or "")
