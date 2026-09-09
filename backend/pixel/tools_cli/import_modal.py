"""One-shot import of the legacy Modal Dict store into Postgres.
Usage (host, with Modal auth):  DATABASE_URL=postgresql+asyncpg://pixel:pixel@localhost:5434/pixel uv run --extra modal python -m pixel.tools_cli.import_modal"""
import asyncio, datetime as dt, json
import modal
import sqlalchemy as sa
from pixel import repo, models as m


async def main():
    d = modal.Dict.from_name("pixel-store")
    data = {k: v for k, v in d.items()}
    print("keys:", {k: (len(v) if hasattr(v, "__len__") else v) for k, v in data.items()})
    h = await repo.default_household()
    hid = h["id"]
    px = await repo.get_or_create_pixel("pixel", "lite", household_id=hid)
    pid = px["id"]

    cfg = data.get("config.json") or {}
    if cfg:
        from pixel import settings
        await settings.update(hid, pid, cfg)
        print("config ->", {k: cfg[k] for k in ("name", "eye_color", "chat_model") if k in cfg})

    n = 0
    for f in (data.get("facts.json") or {}).get("facts", []):
        if f.get("archived"): continue
        r = await repo.execute(sa.insert(m.facts).values(household_id=hid, type=f.get("type", "fact"), text=f["text"], pinned=bool(f.get("pinned")),
                                                        first_seen=dt.datetime.fromisoformat(f["first_seen"]), last_confirmed=dt.datetime.fromisoformat(f["last_confirmed"])))
        n += 1
    print("facts:", n)
    for fu in data.get("followups.json") or []:
        await repo.execute(sa.insert(m.followups).values(household_id=hid, text=fu["text"], due=fu.get("due"), done=bool(fu.get("done"))))
    print("followups:", len(data.get("followups.json") or []))
    for day, text in (data.get("summaries.json") or {}).items():
        await repo.set_summary(hid, day, text)
    print("summaries:", len(data.get("summaries.json") or {}))
    n = 0
    for t in data.get("turns.jsonl") or []:
        await repo.execute(sa.insert(m.turns).values(pixel_id=pid, household_id=hid, ts=dt.datetime.fromisoformat(t["ts"]), user_text=t.get("user"), reply=t.get("reply"),
                                                     expr=t.get("expr"), intensity=t.get("intensity"), t_expr=t.get("t_expr"), t_audio=t.get("t_audio"), t_done=t.get("t_done"),
                                                     audio_s=t.get("audio_s"), model=t.get("model"), tools=t.get("tools") or [], steps=t.get("steps") or []))
        n += 1
    print("turns:", n)
    n = 0
    for e in data.get("device_events.jsonl") or []:
        meta = {k: v for k, v in e.items() if k not in ("ts", "device", "event")}
        await repo.execute(sa.insert(m.device_events).values(pixel_id=pid, ts=dt.datetime.fromisoformat(e["ts"]), event=e["event"], meta=meta))
        n += 1
    print("device events:", n)
    amb = data.get("ambient.json")
    if amb: await repo.ambient_set(hid, amb); print("ambient: yes")
    print("done")

asyncio.run(main())
