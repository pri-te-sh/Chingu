"""Tools Pixel can call mid-conversation: the real world (Ollama web search / fetch) and its own memory.
Executed concurrently; results are trimmed hard because they go straight back into a spoken-reply prompt."""
import asyncio, json, re
import httpx
import contextvars
from . import config as C, repo

current_cfg: contextvars.ContextVar[dict] = contextvars.ContextVar("current_cfg", default={})

SEARCH_HOST = "https://ollama.com"          # web_search/web_fetch live on ollama.com regardless of chat host


def _headers():
    return {"Authorization": f"Bearer {C.OLLAMA_API_KEY}"} if C.OLLAMA_API_KEY else {}


def definitions(cfg: dict) -> list[dict]:
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
    n = max_results or current_cfg.get().get("web_results", 3)
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
    hid = current_cfg.get()["_household_id"]
    f = await repo.add_fact(hid, text, type, pinned=True)
    return f"Saved to memory: {f['text']}"


async def follow_up(text: str, due: str | None = None) -> str:
    hid = current_cfg.get()["_household_id"]
    await repo.add_followup(hid, text, due if due and due != "null" else None)
    return f"Noted to follow up: {text}"


# ---- narration: what Pixel says out loud while it works (used when the model didn't narrate itself) ----
import random
BEFORE = {
    "web_search": ["Hmm, let me look that up.", "One sec, let me check {q}.", "Let me see what I can find on {q}.", "Good question, give me a moment.", "Let me have a quick look."],
    "web_fetch":  ["Let me read that page.", "Opening that up, one second.", "Let me skim through that."],
    "remember":   ["Noted, writing that down.", "Got it, I'll remember that.", "Okay, adding that to what I know about you."],
    "follow_up":  ["I'll make a note to ask you about that.", "Reminding myself to check on that later."],
}
AFTER = {
    "web_search": ["Okay, found it.", "Right, here's what I've got.", "Got something.", "Alright."],
    "web_fetch":  ["Okay, read it.", "Right, got the gist."],
}
FAILED = ["Hmm, that didn't work, let me think.", "I couldn't reach that, sorry."]


def _short_query(args) -> str:
    q = ""
    if isinstance(args, dict):
        q = args.get("query") or args.get("url") or ""
    q = re.sub(r"^(current|latest|today's|todays)\s+", "", str(q), flags=re.I)
    q = re.sub(r"https?://(www\.)?", "", q).split("/")[0]
    return q[:40].rstrip(" .")


def narrate_before(calls: list[dict]) -> tuple[str, str]:
    """(sentence, expression) to say when tool calls start."""
    names = [c.get("function", {}).get("name") for c in calls]
    lead = next((n for n in names if n in ("web_search", "web_fetch")), names[0] if names else "web_search")
    tmpl = random.choice(BEFORE.get(lead, BEFORE["web_search"]))
    q = _short_query(next((c.get("function", {}).get("arguments") for c in calls if c.get("function", {}).get("name") == lead), {}))
    text = tmpl.format(q=q) if q else re.sub(r"\s*\{q\}.*$", ".", tmpl).replace("check .", "check.")
    return text, ("curious" if lead.startswith("web") else "happy")


def narrate_after(results: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    """Optional bridge sentence once results are back (web tools only; memory tools need none)."""
    web = [r for r in results if r[0] in ("web_search", "web_fetch")]
    if not web:
        return None
    if all(r[2].startswith("Tool failed") or r[2] == "No results." for r in web):
        return random.choice(FAILED), "sad"
    return random.choice(AFTER[web[0][0]]), "thinking"


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
