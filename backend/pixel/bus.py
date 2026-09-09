"""Redis-backed cross-process state: device presence, per-device inbox, pub/sub for live updates."""
import json, time
from .db import redis

PRESENCE_TTL = 90


async def presence_set(device_id: str, **fields):
    r = redis()
    cur = await presence_get(device_id) or {}
    cur.update(fields); cur["last_seen"] = time.time()
    await r.set(f"presence:{device_id}", json.dumps(cur), ex=PRESENCE_TTL)


async def presence_get(device_id: str) -> dict | None:
    v = await redis().get(f"presence:{device_id}")
    return json.loads(v) if v else None


async def presence_clear(device_id: str): await redis().delete(f"presence:{device_id}")


async def presence_all(device_ids: list[str] | None = None) -> dict:
    r = redis()
    keys = [f"presence:{d}" for d in device_ids] if device_ids is not None else [k async for k in r.scan_iter("presence:*")]
    out = {}
    if keys:
        vals = await r.mget(keys)
        for k, v in zip(keys, vals):
            if v: out[k.split(":", 1)[1]] = json.loads(v)
    return out


async def inbox_push(device_id: str, msg: dict):
    await redis().rpush(f"inbox:{device_id}", json.dumps(msg))
    await redis().expire(f"inbox:{device_id}", 600)


async def inbox_drain(device_id: str) -> list[dict]:
    r = redis()
    key = f"inbox:{device_id}"
    async with r.pipeline() as p:
        p.lrange(key, 0, -1); p.delete(key)
        items, _ = await p.execute()
    return [json.loads(i) for i in items]
