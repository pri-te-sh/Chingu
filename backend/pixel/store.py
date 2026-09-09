"""Persistence for Pixel's brain.

Two backends behind one tiny API:
  * files  - JSON / JSONL under DATA_DIR (local development)
  * modal  - a modal.Dict (PIXEL_STORE=modal). Shared and consistent across every container, so
             rollovers cannot lose writes and any container can answer for the device.
"""
import json, os, threading
from pathlib import Path
from . import config as C

USE_DICT = os.environ.get("PIXEL_STORE") == "modal"
_lock = threading.Lock()
_dict = None


def _d():
    global _dict
    if _dict is None:
        import modal
        _dict = modal.Dict.from_name("pixel-store", create_if_missing=True)
    return _dict


def _path(name: str) -> Path:
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return C.DATA_DIR / name


def read_json(name: str, default):
    if USE_DICT:
        try:
            return _d().get(name, default)
        except Exception as e:
            print(f"[store] read {name} failed: {e!r}"); return default
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def write_json(name: str, data):
    if USE_DICT:
        _d()[name] = data
        return
    with _lock:
        tmp = _path(name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        tmp.replace(_path(name))


# "jsonl" collections are just lists of dicts
def read_jsonl(name: str) -> list[dict]:
    if USE_DICT:
        return list(read_json(name, []))
    p = _path(name)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def append_jsonl(name: str, row: dict, keep: int = 5000):
    if USE_DICT:
        with _lock:
            rows = read_jsonl(name); rows.append(row)
            write_json(name, rows[-keep:])
        return
    with _lock:
        with _path(name).open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def rewrite_jsonl(name: str, rows: list[dict]):
    if USE_DICT:
        write_json(name, rows); return
    with _lock:
        _path(name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


# kept for API compatibility with modal_app; the Dict backend needs no commits
def set_commit(fn): pass
async def commit_loop():
    import asyncio
    while True:
        await asyncio.sleep(3600)
