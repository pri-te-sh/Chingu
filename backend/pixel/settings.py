"""Configuration resolution. One flat dict per (household, pixel) = DEFAULTS <- household columns/settings <- persona.
Household-level keys shape memory/ambient (shared across the household's Pixels); pixel-level keys shape one Pixel."""
import copy, os
from . import repo

DEFAULTS = {
    # ---- pixel-level (persona) ----
    "name": "Pixel",
    "persona": ("warm, playful, a little cheeky, genuinely curious about the owner's day. Concise because you speak aloud: "
                "usually one or two short sentences, never lists, never markdown, no emojis. Ask a short follow-up question sometimes, not always."),
    "tone": {"cheeky": 0.6, "chatty": 0.4},
    "chat_model": os.environ.get("PIXEL_DEFAULT_CHAT_MODEL", "gemma4:cloud"),
    "chat_think": False,
    "eye_color": "#EBE128",
    "auto_sleep_s": 45,
    "history_turns": 12,
    "tools_enabled": True, "tools_web": True, "web_results": 3, "tool_narration": True,
    "barge_in": True, "barge_rms": 2200, "barge_min_ms": 300,
    "mood_colors": {"love": "#FF6AD5", "annoyed": "#FF4A4A", "sad": "#4C8DFF", "thinking": "#4CC9F0",
                    "surprised": "#FFFFFF", "excited": "#FFD23F", "suspicious": "#B388FF",
                    "happy": "-", "curious": "-", "listening": "-", "sleepy": "-", "asleep": "-", "neutral": "-"},
    # ---- household-level ----
    "owner": "Pritesh",
    "timezone": "America/New_York",
    "location": "Atlanta",
    "interests": "tech, Formula 1, Toronto Raptors",
    "memory_model": os.environ.get("PIXEL_DEFAULT_MEMORY_MODEL", "deepseek-v4-flash:cloud"),
    "memory_think": True, "memory_enabled": True,
    "brief_enabled": True, "brief_refresh_min": 60, "session_gap_min": 30,
}
HOUSEHOLD_COLUMNS = {"timezone", "location", "interests"}
HOUSEHOLD_KEYS = HOUSEHOLD_COLUMNS | {"owner", "memory_model", "memory_think", "memory_enabled", "brief_enabled", "brief_refresh_min", "session_gap_min"}
PIXEL_KEYS = set(DEFAULTS) - HOUSEHOLD_KEYS


def _coerce(k, v):
    d = DEFAULTS[k]
    if k == "tone" and isinstance(v, dict): return {**d, **{kk: float(vv) for kk, vv in v.items() if kk in d}}
    if k == "mood_colors" and isinstance(v, dict): return {**d, **{kk: str(vv) for kk, vv in v.items() if kk in d}}
    if isinstance(d, bool): return bool(v)
    if isinstance(d, int): return int(v)
    if isinstance(d, float): return float(v)
    return v


async def resolve(hid: int, pid: int) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    h = await repo.household(hid) or {}
    for k in HOUSEHOLD_COLUMNS:
        if h.get(k): cfg[k] = h[k]
    for k, v in (h.get("settings") or {}).items():
        if k in HOUSEHOLD_KEYS and v is not None: cfg[k] = _coerce(k, v)
    for k, v in (await repo.persona(pid)).items():
        if k in PIXEL_KEYS and v is not None: cfg[k] = _coerce(k, v)
    px = await repo.pixel(pid)
    if px and px.get("name"): cfg["name"] = px["name"]            # the pixels row is authoritative for the name
    cfg["_household_id"], cfg["_pixel_id"] = hid, pid
    return cfg


async def update(hid: int, pid: int, patch: dict) -> dict:
    hcols, hset, pset = {}, {}, {}
    for k, v in patch.items():
        if k not in DEFAULTS or v is None: continue
        v = _coerce(k, v)
        if k in HOUSEHOLD_COLUMNS: hcols[k] = v
        elif k in HOUSEHOLD_KEYS: hset[k] = v
        else: pset[k] = v
    if hcols or hset: await repo.update_household(hid, **hcols, settings=hset or None)
    if "name" in pset: await repo.update_pixel(pid, name=str(pset.pop("name")).strip()[:40])
    if pset: await repo.update_persona(pid, pset)
    return await resolve(hid, pid)


def device_config(cfg: dict) -> dict:
    """The subset pushed to a device."""
    return {"type": "config", "name": cfg["name"], "eye_color": cfg["eye_color"], "auto_sleep_s": cfg["auto_sleep_s"],
            "mood_colors": cfg["mood_colors"]}
