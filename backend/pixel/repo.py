"""Repository layer: every read/write to Postgres goes through here (plain SQLAlchemy Core, async).
Scoping rule (D5): facts/follow-ups/summaries/ambient are per household; persona/turns/events/logs per pixel."""
import datetime as dt
import hashlib, secrets
from typing import Any
import sqlalchemy as sa
from .db import engine
from . import models as m

def _row(r):  # Row -> dict (with datetimes ISO'd for JSON friendliness)
    if r is None: return None
    d = dict(r._mapping)
    for k, v in d.items():
        if isinstance(v, dt.datetime): d[k] = v.isoformat(timespec="seconds")
    return d

def _rows(rs): return [_row(r) for r in rs]

async def fetch_one(q):
    async with engine().connect() as c:
        return _row((await c.execute(q)).first())

async def fetch_all(q):
    async with engine().connect() as c:
        return _rows((await c.execute(q)).all())

async def execute(q, commit=True):
    async with engine().begin() as c:
        return await c.execute(q)

async def fetch_val(q):
    async with engine().connect() as c:
        return (await c.execute(q)).scalar()

# ---------------- households ----------------
DEFAULT_HOUSEHOLD_NAME = "Home"

async def default_household() -> dict:
    """P0/P1 single-tenant shim: one household exists until pairing/auth assign devices explicitly."""
    h = await fetch_one(sa.select(m.households).order_by(m.households.c.id).limit(1))
    if h: return h
    await execute(sa.insert(m.households).values(name=DEFAULT_HOUSEHOLD_NAME))
    return await fetch_one(sa.select(m.households).order_by(m.households.c.id).limit(1))

async def household(hid: int) -> dict | None:
    return await fetch_one(sa.select(m.households).where(m.households.c.id == hid))

async def update_household(hid: int, **fields):
    settings = fields.pop("settings", None)
    if settings is not None:
        cur = (await household(hid))["settings"] or {}
        fields["settings"] = {**cur, **settings}
    if fields:
        await execute(sa.update(m.households).where(m.households.c.id == hid).values(**fields))
    return await household(hid)

# ---------------- pixels & personas ----------------
def hash_token(tok: str) -> str: return hashlib.sha256(tok.encode()).hexdigest()

def new_pairing_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))

async def pixel_by_device(device_id: str) -> dict | None:
    return await fetch_one(sa.select(m.pixels).where(m.pixels.c.device_id == device_id))

async def pixel(pid: int) -> dict | None:
    return await fetch_one(sa.select(m.pixels).where(m.pixels.c.id == pid))

def new_token() -> str: return secrets.token_hex(24)


async def get_or_create_pixel(device_id: str, device_type: str = "lite", household_id: int | None = None, capabilities: dict | None = None) -> dict:
    """Register a device. household_id=None registers it UNPAIRED (shows a pairing code until claimed).
    An existing row is NOT changed by hello metadata (type/capabilities) - that is only applied via touch_pixel after authentication."""
    p = await pixel_by_device(device_id)
    now = dt.datetime.now(dt.timezone.utc)
    if p:
        vals = {"last_seen_at": now}
        if p.get("archived"): vals.update(archived=False, household_id=None, token_hash=None, pairing_code=new_pairing_code())
        await execute(sa.update(m.pixels).where(m.pixels.c.id == p["id"]).values(**vals))
        return await pixel(p["id"])
    await execute(sa.insert(m.pixels).values(household_id=household_id, device_id=device_id, device_type=device_type,
                                             capabilities=capabilities or {}, pairing_code=new_pairing_code(), last_seen_at=now,
                                             paired_at=now if household_id else None))
    p = await pixel_by_device(device_id)
    await execute(sa.insert(m.personas).values(pixel_id=p["id"], config={}))
    return p


async def touch_pixel(pid: int, capabilities: dict | None = None, fw_version: str | None = None):
    """Post-authentication refresh of what the device reports about itself."""
    vals = {"last_seen_at": dt.datetime.now(dt.timezone.utc)}
    if capabilities: vals["capabilities"] = capabilities
    if fw_version: vals["fw_version"] = str(fw_version)[:40]
    await execute(sa.update(m.pixels).where(m.pixels.c.id == pid).values(**vals))


def key_valid(p: dict, key: str | None) -> bool:
    return bool(key) and bool(p.get("device_key_hash")) and secrets.compare_digest(p["device_key_hash"], hash_token(key))


async def set_pixel_key(pid: int, key: str):
    await execute(sa.update(m.pixels).where(m.pixels.c.id == pid).values(device_key_hash=hash_token(key)))


async def count_pending() -> int:
    return await fetch_val(sa.select(sa.func.count()).select_from(m.pixels).where(m.pixels.c.household_id.is_(None)))


async def expire_pending(days: int = 7) -> int:
    """Drop registrations that were NEVER claimed (no previous household, no history) and have been silent for `days`.
    A device that once belonged to a household keeps its row and its turns forever, whatever its pairing state."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    has_turns = sa.exists().where(m.turns.c.pixel_id == m.pixels.c.id)
    ids = [r["id"] for r in await fetch_all(sa.select(m.pixels.c.id).where(
        m.pixels.c.household_id.is_(None), m.pixels.c.prev_household_id.is_(None), m.pixels.c.paired_at.is_(None),
        m.pixels.c.archived == sa.false(), ~has_turns, m.pixels.c.last_seen_at < cutoff))]
    if ids: await execute(sa.delete(m.pixels).where(m.pixels.c.id.in_(ids)))
    return len(ids)


async def pixel_by_code(code: str) -> dict | None:
    """Only UNPAIRED devices are claimable by code."""
    return await fetch_one(sa.select(m.pixels).where(m.pixels.c.pairing_code == code.strip().upper(), m.pixels.c.household_id.is_(None)))


async def issue_token(pid: int) -> str:
    """New device token; only the hash is stored."""
    tok = new_token()
    await execute(sa.update(m.pixels).where(m.pixels.c.id == pid).values(token_hash=hash_token(tok)))
    return tok


async def pair(pid: int, household_id: int, name: str | None = None) -> str:
    prev = await pixel(pid)
    vals = {"household_id": household_id, "paired_at": dt.datetime.now(dt.timezone.utc), "pairing_code": None}
    if name: vals["name"] = name.strip()[:40]
    await execute(sa.update(m.pixels).where(m.pixels.c.id == pid).values(**vals))
    last = prev.get("household_id") or prev.get("prev_household_id") if prev else None
    if last is not None and last != household_id:                            # ownership transfer (incl. unpair -> claim): persona starts clean
        await execute(sa.update(m.personas).where(m.personas.c.pixel_id == pid).values(config={}))
    return await issue_token(pid)


async def unpair(pid: int):
    """Release from its household; remember where it came from so a later claim by someone else resets the persona."""
    await execute(sa.update(m.pixels).where(m.pixels.c.id == pid).values(
        prev_household_id=sa.func.coalesce(m.pixels.c.household_id, m.pixels.c.prev_household_id),
        household_id=None, token_hash=None, paired_at=None, pairing_code=new_pairing_code()))


async def archive_pixel(pid: int):
    """'Remove' in the portal: unpair and hide, but keep the conversation history (turns stay attached)."""
    await execute(sa.update(m.pixels).where(m.pixels.c.id == pid).values(
        archived=True, prev_household_id=sa.func.coalesce(m.pixels.c.household_id, m.pixels.c.prev_household_id), household_id=None, token_hash=None, pairing_code=None, paired_at=None))


async def delete_pixel(pid: int): await execute(sa.delete(m.pixels).where(m.pixels.c.id == pid))


def token_valid(p: dict, token: str | None) -> bool:
    return bool(token) and bool(p.get("token_hash")) and hash_token(token) == p["token_hash"]

async def pixels_in_household(hid: int, include_archived=False) -> list[dict]:
    q = sa.select(m.pixels).where(m.pixels.c.household_id == hid)
    if not include_archived: q = q.where(m.pixels.c.archived == sa.false())
    return await fetch_all(q.order_by(m.pixels.c.id))

async def update_pixel(pid: int, **fields):
    await execute(sa.update(m.pixels).where(m.pixels.c.id == pid).values(**fields))

async def persona(pid: int) -> dict:
    r = await fetch_one(sa.select(m.personas).where(m.personas.c.pixel_id == pid))
    return (r or {}).get("config") or {}

async def update_persona(pid: int, patch: dict) -> dict:
    cur = await persona(pid)
    cur.update(patch)
    exists = await fetch_one(sa.select(m.personas.c.pixel_id).where(m.personas.c.pixel_id == pid))
    if exists: await execute(sa.update(m.personas).where(m.personas.c.pixel_id == pid).values(config=cur))
    else: await execute(sa.insert(m.personas).values(pixel_id=pid, config=cur))
    return cur

# ---------------- memory: facts / follow-ups / summaries (household) ----------------
async def facts(hid: int, include_archived=False) -> list[dict]:
    q = sa.select(m.facts).where(m.facts.c.household_id == hid)
    if not include_archived: q = q.where(m.facts.c.archived == sa.false())
    return await fetch_all(q.order_by(m.facts.c.id))

async def add_fact(hid: int, text: str, ftype="fact", source_turn=None, pinned=False) -> dict:
    r = await execute(sa.insert(m.facts).values(household_id=hid, type=ftype, text=text.strip(), source_turn_id=source_turn, pinned=pinned).returning(m.facts.c.id))
    fid = r.scalar_one()
    return await fetch_one(sa.select(m.facts).where(m.facts.c.id == fid))

async def update_fact(fid: int, hid: int, **patch) -> dict | None:
    """Household-scoped: a fact id from another household is a no-op returning None."""
    vals = {k: v for k, v in patch.items() if k in ("text", "type", "pinned", "archived") and v is not None}
    if "text" in vals: vals["last_confirmed"] = sa.func.now()
    if vals: await execute(sa.update(m.facts).where(m.facts.c.id == fid, m.facts.c.household_id == hid).values(**vals))
    return await fetch_one(sa.select(m.facts).where(m.facts.c.id == fid, m.facts.c.household_id == hid))

async def confirm_fact(fid: int, hid: int):
    await execute(sa.update(m.facts).where(m.facts.c.id == fid, m.facts.c.household_id == hid).values(last_confirmed=sa.func.now()))

async def delete_fact(fid: int, hid: int): await execute(sa.delete(m.facts).where(m.facts.c.id == fid, m.facts.c.household_id == hid))

async def forget_all(hid: int):
    await execute(sa.delete(m.facts).where(m.facts.c.household_id == hid))
    await execute(sa.delete(m.followups).where(m.followups.c.household_id == hid))
    await execute(sa.delete(m.summaries).where(m.summaries.c.household_id == hid))

async def followups(hid: int, open_only=True) -> list[dict]:
    q = sa.select(m.followups).where(m.followups.c.household_id == hid)
    if open_only: q = q.where(m.followups.c.done == sa.false())
    return await fetch_all(q.order_by(m.followups.c.id))

async def add_followup(hid: int, text: str, due: str | None = None) -> dict:
    r = await execute(sa.insert(m.followups).values(household_id=hid, text=text.strip(), due=due).returning(m.followups.c.id))
    return await fetch_one(sa.select(m.followups).where(m.followups.c.id == r.scalar_one()))

async def resolve_followup(fid: int, hid: int, done=True): await execute(sa.update(m.followups).where(m.followups.c.id == fid, m.followups.c.household_id == hid).values(done=done))
async def delete_followup(fid: int, hid: int): await execute(sa.delete(m.followups).where(m.followups.c.id == fid, m.followups.c.household_id == hid))

async def summaries(hid: int) -> dict:
    rows = await fetch_all(sa.select(m.summaries).where(m.summaries.c.household_id == hid))
    return {r["day"]: r["text"] for r in rows}

async def set_summary(hid: int, day: str, text: str):
    from sqlalchemy.dialects.postgresql import insert
    stmt = insert(m.summaries).values(household_id=hid, day=day, text=text.strip())
    await execute(stmt.on_conflict_do_update(index_elements=["household_id", "day"], set_={"text": text.strip()}))

# ---------------- turns / events / logs (pixel) ----------------
async def log_turn(pid: int, hid: int, **row) -> dict:
    r = await execute(sa.insert(m.turns).values(pixel_id=pid, household_id=hid, **row).returning(m.turns.c.id))
    return await fetch_one(sa.select(m.turns).where(m.turns.c.id == r.scalar_one()))

async def turns(hid: int | None = None, pid: int | None = None, limit=200, day: str | None = None, tz: str | None = None) -> list[dict]:
    q = sa.select(m.turns)
    if hid is not None: q = q.where(m.turns.c.household_id == hid)
    if pid is not None: q = q.where(m.turns.c.pixel_id == pid)
    if day:
        local = sa.func.timezone(tz or "UTC", m.turns.c.ts)
        q = q.where(sa.func.to_char(local, "YYYY-MM-DD") == day)
    rows = await fetch_all(q.order_by(m.turns.c.id.desc()).limit(limit))
    return list(reversed(rows))

async def last_turn_ts(hid: int) -> dt.datetime | None:
    async with engine().connect() as c:
        return (await c.execute(sa.select(sa.func.max(m.turns.c.ts)).where(m.turns.c.household_id == hid))).scalar()

async def delete_turn(tid: int, hid: int): await execute(sa.delete(m.turns).where(m.turns.c.id == tid, m.turns.c.household_id == hid))

async def count_turns(hid: int, day: str | None = None, tz: str | None = None) -> int:
    q = sa.select(sa.func.count()).select_from(m.turns).where(m.turns.c.household_id == hid)
    if day: q = q.where(sa.func.to_char(sa.func.timezone(tz or "UTC", m.turns.c.ts), "YYYY-MM-DD") == day)
    async with engine().connect() as c:
        return (await c.execute(q)).scalar_one()

async def device_event(pid: int, event: str, **meta):
    await execute(sa.insert(m.device_events).values(pixel_id=pid, event=event, meta=meta))

async def device_events(pid: int | None, limit=60) -> list[dict]:
    q = sa.select(m.device_events)
    if pid is not None: q = q.where(m.device_events.c.pixel_id == pid)
    rows = await fetch_all(q.order_by(m.device_events.c.id.desc()).limit(limit))
    return list(reversed(rows))

async def device_log(pid: int, level: str, line: str):
    await execute(sa.insert(m.device_logs).values(pixel_id=pid, level=level, line=line[:500]))

# ---------------- ambient (household) ----------------
async def ambient_get(hid: int) -> dict:
    r = await fetch_one(sa.select(m.ambient).where(m.ambient.c.household_id == hid))
    return (r or {}).get("brief") or {}

async def ambient_set(hid: int, brief: dict):
    from sqlalchemy.dialects.postgresql import insert
    stmt = insert(m.ambient).values(household_id=hid, brief=brief, ts=sa.func.now())
    await execute(stmt.on_conflict_do_update(index_elements=["household_id"], set_={"brief": brief, "ts": sa.func.now()}))


async def retention(events_days: int = 90, logs_days: int = 14) -> dict:
    """Scheduled cleanup: device events, device logs, expired sessions, stale pending registrations."""
    now = dt.datetime.now(dt.timezone.utc)
    ev = await execute(sa.delete(m.device_events).where(m.device_events.c.ts < now - dt.timedelta(days=events_days)))
    lg = await execute(sa.delete(m.device_logs).where(m.device_logs.c.ts < now - dt.timedelta(days=logs_days)))
    se = await execute(sa.delete(m.sessions).where(m.sessions.c.expires_at < now))
    pend = await expire_pending()
    return {"events": getattr(ev, "rowcount", None), "logs": getattr(lg, "rowcount", None), "sessions": getattr(se, "rowcount", None), "pending_pixels": pend}
