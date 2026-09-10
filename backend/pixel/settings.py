"""Configuration resolution. One flat dict per (household, pixel) = DEFAULTS <- household columns/settings <- persona.
Household-level keys shape memory/ambient (shared across the household's Pixels); pixel-level keys shape one Pixel."""
import copy, os, re, zoneinfo
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


_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
RANGES = {"auto_sleep_s": (5, 86400), "history_turns": (0, 100), "web_results": (1, 10), "barge_rms": (100, 30000), "barge_min_ms": (50, 5000),
          "brief_refresh_min": (5, 1440), "session_gap_min": (1, 1440)}
MAXLEN = {"name": 40, "persona": 4000, "owner": 80, "location": 120, "interests": 400, "chat_model": 120, "memory_model": 120}


class InvalidSetting(ValueError):
    pass


def _coerce(k, v):
    """Type-check and range-check one setting; raises InvalidSetting instead of persisting garbage."""
    d = DEFAULTS[k]
    try:
        if k == "tone":
            if not isinstance(v, dict): raise InvalidSetting("tone must be an object")
            out = {**d}
            for kk, vv in v.items():
                if kk in d:
                    f = float(vv)
                    if not 0 <= f <= 1: raise InvalidSetting(f"tone.{kk} must be between 0 and 1")
                    out[kk] = f
            return out
        if k == "mood_colors":
            if not isinstance(v, dict): raise InvalidSetting("mood_colors must be an object")
            out = {**d}
            for kk, vv in v.items():
                if kk in d:
                    vv = str(vv).strip()
                    if vv != "-" and not _HEX.match(vv): raise InvalidSetting(f"mood_colors.{kk} must be #RRGGBB or -")
                    out[kk] = vv.upper() if vv != "-" else vv
            return out
        if isinstance(d, bool):
            if isinstance(v, str): v = v.lower() in ("1", "true", "yes", "on")
            return bool(v)
        if isinstance(d, int):
            i = int(v); lo, hi = RANGES.get(k, (-10**9, 10**9))
            if not lo <= i <= hi: raise InvalidSetting(f"{k} must be between {lo} and {hi}")
            return i
        if isinstance(d, float): return float(v)
        v = str(v)
        if k == "eye_color":
            if not _HEX.match(v.strip()): raise InvalidSetting("eye_color must be #RRGGBB")
            return v.strip().upper()
        if k == "timezone":
            try: zoneinfo.ZoneInfo(v)
            except Exception: raise InvalidSetting(f"unknown timezone {v!r}")
        if k in MAXLEN and len(v) > MAXLEN[k]: raise InvalidSetting(f"{k} is too long (max {MAXLEN[k]})")
        return v
    except (TypeError, ValueError) as e:
        if isinstance(e, InvalidSetting): raise
        raise InvalidSetting(f"{k}: invalid value") from e


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
    """Validates the whole patch first (InvalidSetting on any bad value), then writes; nothing is persisted on error."""
    hcols, hset, pset = {}, {}, {}
    cleaned = {k: _coerce(k, v) for k, v in patch.items() if k in DEFAULTS and v is not None}
    for k, v in cleaned.items():
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
