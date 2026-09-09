"""Runtime configuration, editable from the portal. Stored as config.json; environment gives the defaults."""
import copy, os
from . import config as C, store

DEFAULTS = {
    "name": "Pixel",
    "owner": "Pritesh",
    "persona": ("warm, playful, a little cheeky, genuinely curious about the owner's day. Concise because you speak aloud: "
                "usually one or two short sentences, never lists, never markdown, no emojis. Ask a short follow-up question sometimes, not always."),
    "chat_model": os.environ.get("PIXEL_DEFAULT_CHAT_MODEL", "gemma4:cloud"),
    "chat_think": False,
    "memory_model": os.environ.get("PIXEL_DEFAULT_MEMORY_MODEL", "deepseek-v4-flash:cloud"),
    "memory_think": True,
    "memory_enabled": True,
    "eye_color": "#EBE128",
    "auto_sleep_s": 45,
    "history_turns": 12,
    "timezone": "America/New_York",
    "tone": {"cheeky": 0.6, "chatty": 0.4},
    "tools_enabled": True,
    "tools_web": True,
    "web_results": 3,
    "tool_narration": True,
    "location": "Atlanta",
    "interests": "tech, Formula 1, Toronto Raptors",
    "brief_enabled": True,
    "brief_refresh_min": 60,
    "session_gap_min": 30,
    # eye tint per expression while it holds ("-" = keep base colour); eases back to eye_color afterwards
    "mood_colors": {"love": "#FF6AD5", "annoyed": "#FF4A4A", "sad": "#4C8DFF", "thinking": "#4CC9F0",
                    "surprised": "#FFFFFF", "excited": "#FFD23F", "suspicious": "#B388FF",
                    "happy": "-", "curious": "-", "listening": "-", "sleepy": "-", "asleep": "-", "neutral": "-"},
}

_cfg: dict | None = None
_loaded_at = 0.0


def reload():
    global _cfg, _loaded_at
    _cfg = copy.deepcopy(DEFAULTS)
    _cfg.update({k: v for k, v in (store.read_json("config.json", {}) or {}).items() if k in DEFAULTS})
    import time; _loaded_at = time.time()
    return _cfg


def get() -> dict:
    import time
    if _cfg is None or time.time() - _loaded_at > 10:      # other containers may have saved; cheap re-read
        reload()
    return _cfg


def update(patch: dict) -> dict:
    cfg = get()
    for k, v in patch.items():
        if k in DEFAULTS and v is not None:
            if k == "tone" and isinstance(v, dict):
                cfg["tone"] = {**cfg["tone"], **{kk: float(vv) for kk, vv in v.items() if kk in cfg["tone"]}}
            elif k == "mood_colors" and isinstance(v, dict):
                cfg["mood_colors"] = {**DEFAULTS["mood_colors"], **{kk: str(vv) for kk, vv in v.items() if kk in DEFAULTS["mood_colors"]}}
            elif isinstance(DEFAULTS[k], bool):
                cfg[k] = bool(v)
            elif isinstance(DEFAULTS[k], int):
                cfg[k] = int(v)
            else:
                cfg[k] = v
    store.write_json("config.json", cfg)
    return cfg


def device_config() -> dict:
    """The subset pushed to the ESP32."""
    cfg = get()
    return {"type": "config", "name": cfg["name"], "eye_color": cfg["eye_color"], "auto_sleep_s": cfg["auto_sleep_s"],
            "mood_colors": cfg.get("mood_colors", DEFAULTS["mood_colors"])}
