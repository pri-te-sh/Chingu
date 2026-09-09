"""Ollama (Cloud) streaming chat, minimal copy of the brain's client so the lab has no dependency on backend/."""
import json, os
import httpx

HOST = os.environ.get("OLLAMA_HOST", "https://ollama.com").rstrip("/")
KEY = os.environ.get("OLLAMA_API_KEY", "")
MODEL = os.environ.get("VOICELAB_CHAT_MODEL", "gemma4:cloud")
SYSTEM = ("You are Pixel, a small desk companion with an animated face. Warm, playful, a little cheeky. You speak aloud: plain sentences, never lists, "
          "never markdown, no emojis. Default to one or two short sentences. When the user asks for detail, a story, an explanation or a longer answer, "
          "give it properly in flowing spoken paragraphs. Ask a short follow-up question sometimes, not always.")

def _headers():
    h = {"content-type": "application/json"}
    if KEY: h["authorization"] = f"Bearer {KEY}"
    return h

async def stream(history: list[dict], model: str | None = None):
    """Yields text deltas."""
    body = {"model": model or MODEL, "messages": [{"role": "system", "content": SYSTEM}, *history], "stream": True, "think": False,
            "options": {"temperature": 0.8, "num_predict": 600}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as c:
        async with c.stream("POST", f"{HOST}/api/chat", json=body, headers=_headers()) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line: continue
                d = json.loads(line)
                if t := d.get("message", {}).get("content"): yield t
                if d.get("done"): break
