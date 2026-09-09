"""Runtime configuration, editable from the portal. Stored as config.json; environment gives the defaults."""
import copy
from . import config as C, store

DEFAULTS = {
    "name": "Pixel",
    "owner": "Pritesh",
    "persona": ("warm, playful, a little cheeky, genuinely curious about the owner's day. Concise because you speak aloud: "
                "usually one or two short sentences, never lists, never markdown, no emojis. Ask a short follow-up question sometimes, not always."),
    "chat_model": C.OLLAMA_MODEL,
    "chat_think": False,
    "memory_model": "deepseek-v4-flash:cloud" if "cloud" in C.OLLAMA_MODEL else C.OLLAMA_MODEL,
    "memory_think": True,
    "memory_enabled": True,
    "eye_color": "#EBE128",
    "auto_sleep_s": 45,
    "history_turns": 12,
    "timezone": "America/New_York",
    "tone": {"cheeky": 0.6, "chatty": 0.4},
}

_cfg: dict | None = None


def get() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = copy.deepcopy(DEFAULTS)
        _cfg.update({k: v for k, v in store.read_json("config.json", {}).items() if k in DEFAULTS})
    return _cfg


def update(patch: dict) -> dict:
    cfg = get()
    for k, v in patch.items():
        if k in DEFAULTS and v is not None:
            if k == "tone" and isinstance(v, dict):
                cfg["tone"] = {**cfg["tone"], **{kk: float(vv) for kk, vv in v.items() if kk in cfg["tone"]}}
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
    return {"type": "config", "name": cfg["name"], "eye_color": cfg["eye_color"], "auto_sleep_s": cfg["auto_sleep_s"]}
