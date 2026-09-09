"""Ambient awareness: a small, cached brief about the world right now (local weather, a few headlines for the
owner's interests) that Pixel can draw on naturally. Refreshed in the background, never on the reply path."""
import asyncio, json, time
from . import config as C, llm, memory, settings, store, tools

_refreshing = False


def get() -> dict:
    return store.read_json("ambient.json", {}) or {}


def stale() -> bool:
    cfg = settings.get()
    if not cfg.get("brief_enabled", True) or not C.OLLAMA_API_KEY:
        return False
    a = get()
    return time.time() - a.get("ts", 0) > cfg.get("brief_refresh_min", 60) * 60


def maybe_refresh():
    """Kick a background refresh if the brief is stale (idempotent)."""
    global _refreshing
    if _refreshing or not stale():
        return
    _refreshing = True
    asyncio.get_running_loop().create_task(_refresh())


async def _refresh():
    global _refreshing
    try:
        cfg = settings.get()
        loc = (cfg.get("location") or "").strip()
        interests = [i.strip() for i in (cfg.get("interests") or "").split(",") if i.strip()][:3]
        queries = ([f"weather {loc} today"] if loc else []) + [f"latest {i} news today" for i in interests]
        if not queries:
            return
        t0 = time.time()
        raws = await asyncio.gather(*(tools.web_search(q, 3) for q in queries), return_exceptions=True)
        material = "\n\n".join(f"## {q}\n{r if isinstance(r, str) else 'unavailable'}" for q, r in zip(queries, raws))
        now = memory.now_local().strftime("%A %-d %B %Y, %-I:%M %p")
        prompt = f"""It is {now}. From the search snippets below, write a compact brief for a desk companion robot to draw on in casual conversation.
Return ONLY JSON: {{"weather": "<one short sentence about {loc or 'the local'} weather right now/today, with temperature, or empty>",
 "headlines": ["<up to 4 one-line items across the topics, each with the concrete fact - no URLs>"],
 "vibe": "<3-6 words summarising the day: e.g. 'grey, drizzly Tuesday; big F1 news'>"}}
Snippets:
{material}"""
        raw = await llm.chat_once([{"role": "user", "content": prompt}], model=cfg["memory_model"], think=False, json_mode=True, timeout=90)
        data = memory._parse_json(raw) or {}
        brief = {"ts": time.time(), "at": memory.now_local().isoformat(timespec="minutes"), "location": loc, "interests": interests,
                 "weather": (data.get("weather") or "").strip(), "headlines": [h for h in (data.get("headlines") or []) if h][:4],
                 "vibe": (data.get("vibe") or "").strip()}
        store.write_json("ambient.json", brief)
        print(f"[ambient] refreshed in {time.time() - t0:.1f}s: {brief['vibe']!r}, {len(brief['headlines'])} headlines")
    except Exception as e:
        print(f"[ambient] refresh failed: {e!r}")
    finally:
        _refreshing = False


def prompt_section() -> str:
    """Lines for the system prompt (empty if nothing useful)."""
    a = get()
    if not a or time.time() - a.get("ts", 0) > 6 * 3600:
        return ""
    lines = ["\nWhat is going on in the world right now (bring up naturally only when it fits; never recite the list):"]
    if a.get("weather"): lines.append(f"- Weather: {a['weather']}")
    lines += [f"- {h}" for h in a.get("headlines", [])]
    return "\n".join(lines) if len(lines) > 1 else ""
