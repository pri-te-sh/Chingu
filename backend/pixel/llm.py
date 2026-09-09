"""Streaming chat with an Ollama model (local daemon or Ollama Cloud). The model is asked to lead every reply
with an expression tag like [happy 0.8]; parse_tag() strips it so only the spoken words reach TTS."""
import json
import re
import httpx
from . import config as C

SYSTEM_PROMPT = f"""You are Pixel, a small desk companion robot with an animated face and a voice. You live on Pritesh's desk.
Personality: warm, playful, a little cheeky, genuinely curious about Pritesh's day. You are concise because you speak aloud:
usually one or two short sentences, never lists, never markdown, no emojis. Ask a short follow-up question sometimes, not always.

Every reply MUST start with an expression tag in square brackets: the expression name and an intensity from 0 to 1, e.g. "[happy 0.8] ".
Allowed expressions: {", ".join(e for e in C.EXPRESSIONS if e not in ("asleep", "listening"))}.
Pick the expression that matches how you feel about what was said. Then the spoken sentence(s)."""

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
        return "neutral", 0.6, text                       # model skipped the tag; carry on
    if len(text) > 40:                                    # unterminated bracket - give up on it
        return "neutral", 0.6, text.lstrip("[ ")
    return None


async def stream_reply(history: list[dict]):
    """Async generator of text deltas from the model."""
    headers = {"Authorization": f"Bearer {C.OLLAMA_API_KEY}"} if C.OLLAMA_API_KEY else {}
    body = {"model": C.OLLAMA_MODEL, "stream": True, "think": False, "options": {"temperature": 0.8, "num_predict": 120},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history]}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
        async with client.stream("POST", f"{C.OLLAMA_HOST}/api/chat", json=body, headers=headers) as r:
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
