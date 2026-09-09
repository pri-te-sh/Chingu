"""Ambient awareness per household: a cached brief (local weather, headlines for the owner's interests).
Refreshed in the background, never on the reply path."""
import asyncio, time
from . import config as C, llm, memory, repo, tools

_refreshing: set[int] = set()


async def stale(cfg: dict) -> bool:
    if not cfg.get("brief_enabled", True) or not C.OLLAMA_API_KEY: return False
    a = await repo.ambient_get(cfg["_household_id"])
    return time.time() - a.get("ts", 0) > cfg.get("brief_refresh_min", 60) * 60


def maybe_refresh(cfg: dict):
    hid = cfg["_household_id"]
    if hid in _refreshing: return
    async def go():
        try:
            if await stale(cfg): await refresh(cfg)
        finally: _refreshing.discard(hid)
    _refreshing.add(hid)
    asyncio.get_running_loop().create_task(go())


async def refresh(cfg: dict) -> dict:
    hid = cfg["_household_id"]
    loc = (cfg.get("location") or "").strip()
    interests = [i.strip() for i in (cfg.get("interests") or "").split(",") if i.strip()][:3]
    queries = ([f"weather {loc} today"] if loc else []) + [f"latest {i} news today" for i in interests]
    if not queries: return {}
    t0 = time.time()
    raws = await asyncio.gather(*(tools.web_search(q, 3) for q in queries), return_exceptions=True)
    material = "\n\n".join(f"## {q}\n{r if isinstance(r, str) else 'unavailable'}" for q, r in zip(queries, raws))
    now = memory.now_local(cfg["timezone"]).strftime("%A %-d %B %Y, %-I:%M %p")
    prompt = f"""It is {now}. From the search snippets below, write a compact brief for a desk companion robot to draw on in casual conversation.
Return ONLY JSON: {{"weather": "<one short sentence about {loc or 'the local'} weather right now/today, with temperature, or empty>",
 "headlines": ["<up to 4 one-line items across the topics, each with the concrete fact - no URLs>"],
 "vibe": "<3-6 words summarising the day: e.g. 'grey, drizzly Tuesday; big F1 news'>"}}
Snippets:
{material}"""
    raw = await llm.chat_once([{"role": "user", "content": prompt}], model=cfg["memory_model"], think=False, json_mode=True, timeout=90)
    data = memory.parse_json(raw) or {}
    brief = {"ts": time.time(), "at": memory.now_local(cfg["timezone"]).isoformat(timespec="minutes"), "location": loc, "interests": interests,
             "weather": (data.get("weather") or "").strip(), "headlines": [h for h in (data.get("headlines") or []) if h][:4],
             "vibe": (data.get("vibe") or "").strip()}
    await repo.ambient_set(hid, brief)
    print(f"[ambient] hid={hid} refreshed in {time.time() - t0:.1f}s: {brief['vibe']!r}")
    return brief


async def prompt_section(hid: int) -> str:
    a = await repo.ambient_get(hid)
    if not a or time.time() - a.get("ts", 0) > 6 * 3600: return ""
    lines = ["\nWhat is going on in the world right now (bring up naturally only when it fits; never recite the list):"]
    if a.get("weather"): lines.append(f"- Weather: {a['weather']}")
    lines += [f"- {h}" for h in a.get("headlines", [])]
    return "\n".join(lines) if len(lines) > 1 else ""
