"""Small JSON/JSONL persistence on the data dir (a Modal Volume in the cloud).
Writes are debounced into an explicit Volume commit so they survive container restarts."""
import asyncio, json, threading, time
from pathlib import Path
from . import config as C

_commit_fn = None            # set by modal_app: memory_volume.commit
_dirty_at = 0.0
_lock = threading.Lock()


def set_commit(fn):
    global _commit_fn
    _commit_fn = fn


def _path(name: str) -> Path:
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return C.DATA_DIR / name


def read_json(name: str, default):
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def write_json(name: str, data):
    global _dirty_at
    with _lock:
        tmp = _path(name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        tmp.replace(_path(name))
        _dirty_at = time.time()


def append_jsonl(name: str, row: dict):
    global _dirty_at
    with _lock:
        with _path(name).open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _dirty_at = time.time()


def read_jsonl(name: str) -> list[dict]:
    p = _path(name)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def rewrite_jsonl(name: str, rows: list[dict]):
    global _dirty_at
    with _lock:
        _path(name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        _dirty_at = time.time()


async def commit_loop():
    """Background task: commit the volume a few seconds after the last write."""
    global _dirty_at
    while True:
        await asyncio.sleep(2)
        if _commit_fn and _dirty_at and time.time() - _dirty_at > 3:
            _dirty_at = 0.0
            try:
                await asyncio.get_running_loop().run_in_executor(None, _commit_fn)
            except Exception as e:
                print(f"[store] commit failed: {e!r}")
