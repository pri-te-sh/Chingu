"""Tools Pixel can call mid-conversation: the real world (Ollama web search / fetch) and its own memory.
Executed concurrently; results are trimmed hard because they go straight back into a spoken-reply prompt."""
import asyncio, json, re
import httpx
from . import config as C, memory, settings

SEARCH_HOST = "https://ollama.com"          # web_search/web_fetch live on ollama.com regardless of chat host


def _headers():
    return {"Authorization": f"Bearer {C.OLLAMA_API_KEY}"} if C.OLLAMA_API_KEY else {}


def definitions() -> list[dict]:
    cfg = settings.get()
    tools = [
        {"type": "function", "function": {"name": "remember", "description": "Save a durable fact, preference or project about the owner to long-term memory. Use when they tell you something worth remembering or ask you to remember.",
            "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "Concise third-person statement"}, "type": {"type": "string", "enum": ["fact", "preference", "project"]}}, "required": ["text"]}}},
        {"type": "function", "function": {"name": "follow_up", "description": "Note something to ask the owner about later (a reminder, an event to check on).",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "due": {"type": "string", "description": "YYYY-MM-DD or null"}}, "required": ["text"]}}},
    ]
    if cfg.get("tools_web", True) and C.OLLAMA_API_KEY:
        tools += [
            {"type": "function", "function": {"name": "web_search", "description": "Search the web for current information: weather, news, sports, prices, facts you are unsure about. Use it whenever the answer depends on the real world right now.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "web_fetch", "description": "Fetch the readable content of a specific web page URL.",
                "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
        ]
    return tools


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:n] + ("…" if len(text) > n else "")


async def web_search(query: str, max_results: int | None = None) -> str:
    n = max_results or settings.get().get("web_results", 3)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as client:
        r = await client.post(f"{SEARCH_HOST}/api/web_search", json={"query": query, "max_results": n}, headers=_headers())
        r.raise_for_status()
        results = r.json().get("results", [])
    if not results:
        return "No results."
    return "\n".join(f"- {_clip(x.get('title',''), 80)} ({x.get('url','')}): {_clip(x.get('content',''), 400)}" for x in results[:n])


async def web_fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as client:
        r = await client.post(f"{SEARCH_HOST}/api/web_fetch", json={"url": url}, headers=_headers())
        r.raise_for_status()
        d = r.json()
    return f"{_clip(d.get('title',''), 100)}\n{_clip(d.get('content',''), 1500)}"


async def remember(text: str, type: str = "fact") -> str:
    f = memory.add_fact(text, type, pinned=True)
    return f"Saved to memory: {f['text']}"


async def follow_up(text: str, due: str | None = None) -> str:
    memory.add_followup(text, due if due and due != "null" else None)
    return f"Noted to follow up: {text}"


REGISTRY = {"web_search": web_search, "web_fetch": web_fetch, "remember": remember, "follow_up": follow_up}


async def run(call: dict) -> tuple[str, str, str]:
    """Execute one tool call -> (name, args_json, result_text)."""
    fn = call.get("function", {})
    name, args = fn.get("name"), fn.get("arguments") or {}
    if isinstance(args, str):
        try: args = json.loads(args)
        except Exception: args = {}
    if name not in REGISTRY:
        return name, json.dumps(args), f"Unknown tool {name}"
    try:
        res = await asyncio.wait_for(REGISTRY[name](**args), timeout=25)
    except Exception as e:
        res = f"Tool failed: {e!r}"
    return name, json.dumps(args), res
