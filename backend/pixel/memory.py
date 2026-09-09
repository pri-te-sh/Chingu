"""Pixel's memory: permanent turn log, long-term facts (deduplicated/superseded), follow-ups, daily summaries.
Extraction runs in the background with the 'memory' model after each exchange - never on the latency path."""
import asyncio, datetime as dt, json, re, time
from zoneinfo import ZoneInfo
from . import store, settings, llm

FACT_TYPES = ("fact", "preference", "project")
STALE_DAYS = 90


def now_local() -> dt.datetime:
    return dt.datetime.now(ZoneInfo(settings.get()["timezone"]))


def today_key(t: dt.datetime | None = None) -> str:
    return (t or now_local()).strftime("%Y-%m-%d")


# ---------- turns ----------
def log_turn(row: dict) -> dict:
    row = {"id": int(time.time() * 1000), "ts": now_local().isoformat(timespec="seconds"), **row}
    store.append_jsonl("turns.jsonl", row)
    return row


def turns(limit: int = 200, day: str | None = None) -> list[dict]:
    rows = store.read_jsonl("turns.jsonl")
    if day:
        rows = [r for r in rows if r.get("ts", "").startswith(day)]
    return rows[-limit:]


def delete_turn(turn_id: int):
    store.rewrite_jsonl("turns.jsonl", [r for r in store.read_jsonl("turns.jsonl") if r.get("id") != turn_id])


def last_turn_time() -> dt.datetime | None:
    rows = store.read_jsonl("turns.jsonl")
    if not rows:
        return None
    try:
        return dt.datetime.fromisoformat(rows[-1]["ts"])
    except Exception:
        return None


# ---------- facts ----------
def _facts_doc() -> dict:
    return store.read_json("facts.json", {"next_id": 1, "facts": []})


def facts(include_archived=False) -> list[dict]:
    fs = _facts_doc()["facts"]
    return fs if include_archived else [f for f in fs if not f.get("archived")]


def add_fact(text: str, ftype="fact", source_turn=None, pinned=False) -> dict:
    doc = _facts_doc()
    ts = now_local().isoformat(timespec="seconds")
    f = {"id": doc["next_id"], "type": ftype if ftype in FACT_TYPES else "fact", "text": text.strip(),
         "first_seen": ts, "last_confirmed": ts, "source_turn": source_turn, "pinned": pinned, "archived": False}
    doc["facts"].append(f); doc["next_id"] += 1
    store.write_json("facts.json", doc)
    return f


def update_fact(fid: int, **patch) -> dict | None:
    doc = _facts_doc()
    for f in doc["facts"]:
        if f["id"] == fid:
            for k, v in patch.items():
                if k in ("text", "type", "pinned", "archived") and v is not None:
                    f[k] = v
            if "text" in patch:
                f["last_confirmed"] = now_local().isoformat(timespec="seconds")
            store.write_json("facts.json", doc)
            return f
    return None


def confirm_fact(fid: int):
    doc = _facts_doc()
    for f in doc["facts"]:
        if f["id"] == fid:
            f["last_confirmed"] = now_local().isoformat(timespec="seconds")
    store.write_json("facts.json", doc)


def delete_fact(fid: int):
    doc = _facts_doc()
    doc["facts"] = [f for f in doc["facts"] if f["id"] != fid]
    store.write_json("facts.json", doc)


def forget_all():
    store.write_json("facts.json", {"next_id": 1, "facts": []})
    store.write_json("followups.json", [])
    store.write_json("summaries.json", {})


def active_facts() -> list[dict]:
    """Facts injected into the prompt: pinned, or confirmed within STALE_DAYS."""
    cutoff = now_local() - dt.timedelta(days=STALE_DAYS)
    out = []
    for f in facts():
        try:
            fresh = dt.datetime.fromisoformat(f["last_confirmed"]) >= cutoff
        except Exception:
            fresh = True
        if f.get("pinned") or fresh:
            out.append(f)
    return out


# ---------- follow-ups & summaries ----------
def followups(open_only=True) -> list[dict]:
    fs = store.read_json("followups.json", [])
    return [f for f in fs if not f.get("done")] if open_only else fs


def add_followup(text: str, due: str | None = None) -> dict:
    fs = store.read_json("followups.json", [])
    f = {"id": int(time.time() * 1000) % 10_000_000, "text": text.strip(), "due": due, "created": today_key(), "done": False}
    fs.append(f); store.write_json("followups.json", fs)
    return f


def resolve_followup(fid: int, done=True):
    fs = store.read_json("followups.json", [])
    for f in fs:
        if f["id"] == fid:
            f["done"] = done
    store.write_json("followups.json", fs)


def delete_followup(fid: int):
    store.write_json("followups.json", [f for f in store.read_json("followups.json", []) if f["id"] != fid])


def summaries() -> dict:
    return store.read_json("summaries.json", {})


def set_summary(day: str, text: str):
    s = summaries(); s[day] = text.strip(); store.write_json("summaries.json", s)


# ---------- prompt construction ----------
def _ago(t: dt.datetime | None) -> str:
    if not t:
        return "This is your first ever conversation with them."
    d = now_local() - t
    m = int(d.total_seconds() // 60)
    if m < 2: return "You spoke moments ago."
    if m < 60: return f"You last spoke {m} minutes ago."
    h = m // 60
    if h < 24: return f"You last spoke about {h} hour{'s' if h > 1 else ''} ago."
    return f"You last spoke {h // 24} day{'s' if h // 24 > 1 else ''} ago."


def system_prompt() -> str:
    cfg = settings.get()
    now = now_local()
    cheeky, chatty = cfg["tone"]["cheeky"], cfg["tone"]["chatty"]
    tone = []
    tone.append("Lean into gentle teasing and wit." if cheeky > 0.66 else "Be kind and warm; tease only lightly." if cheeky > 0.33 else "Be gentle and sincere; no teasing.")
    tone.append("Two short sentences are fine." if chatty > 0.66 else "Prefer one short sentence, two at most." if chatty > 0.33 else "Answer in a single short sentence.")
    lines = [
        f"You are {cfg['name']}, a small desk companion robot with an animated face and a voice, living on {cfg['owner']}'s desk.",
        f"Personality: {cfg['persona']}", " ".join(tone),
        f"Right now it is {now.strftime('%A %-d %B %Y, %-I:%M %p')}. {_ago(last_turn_time())}",
    ]
    fs = active_facts()
    if fs:
        lines.append(f"\nWhat you know about {cfg['owner']} (use naturally and sparingly, only when relevant - never recite):")
        lines += [f"- {f['text']}" for f in fs]
    s = summaries().get(today_key())
    if s:
        lines.append(f"\nToday so far: {s}")
    fu = followups()
    if fu:
        lines.append("\nThings you meant to ask about when the moment is right:")
        lines += [f"- {f['text']}" + (f" (around {f['due']})" if f.get("due") else "") for f in fu[:6]]
    lines.append(
        "\nEvery reply MUST start with an expression tag in square brackets: the expression name and an intensity 0-1, e.g. \"[happy 0.8] \". "
        f"Allowed expressions: {', '.join(e for e in llm.C.EXPRESSIONS if e not in ('asleep', 'listening'))}. "
        "Pick the expression that matches how you feel about what was said, then the spoken sentence(s).")
    return "\n".join(lines)


# ---------- background extraction (the "subconscious") ----------
EXTRACT_PROMPT = """You maintain the long-term memory of a desk companion robot named {name} about its owner {owner}.
Given the existing memory and the latest conversation, decide what to remember. Be conservative: only durable things.

Types: "fact" (stable facts about the owner: family, work, pets, routines, health, places), "preference" (likes/dislikes, how they want to be treated),
"project" (ongoing things they are building or working toward). One-off events (left for work, skipped gym today) are NOT facts - they belong in today's summary,
unless they reveal a pattern ("often skips the gym on Mondays").

Existing facts (id: text):
{facts}

Open follow-ups (id: text):
{followups}

Today's summary so far: {summary}

Latest conversation (owner = U, {name} = A):
{turns}

Return ONLY JSON of this shape:
{{"facts": [{{"action": "new"|"update"|"confirm", "id": <existing id or null>, "type": "fact"|"preference"|"project", "text": "<concise third-person statement>"}}],
 "followups": [{{"text": "<something to ask about later>", "due": "YYYY-MM-DD or null"}}],
 "resolved_followups": [<ids that are now answered/irrelevant>],
 "today_summary": "<1-2 sentences summarising today so far, merging the previous summary>"}}
Use "update" when a new statement supersedes an existing fact (same subject, changed value); "confirm" when the conversation re-affirms an existing fact unchanged.
Return empty lists when nothing qualifies."""

_last_extract = 0.0
_pending = False


async def extract_after_turn():
    """Debounced: at most one extraction pass per 15 s, using the last few turns."""
    global _last_extract, _pending
    if not settings.get().get("memory_enabled", True):
        return
    if _pending:
        return
    _pending = True
    try:
        wait = max(0.0, 15 - (time.time() - _last_extract))
        await asyncio.sleep(wait)
        _last_extract = time.time()
        await _extract()
    except Exception as e:
        print(f"[memory] extraction failed: {e!r}")
    finally:
        _pending = False


async def _extract():
    cfg = settings.get()
    recent = turns(limit=8)
    if not recent:
        return
    conv = "\n".join(f"U: {t.get('user','')}\nA: {t.get('reply','')}" for t in recent)
    fs = facts()
    prompt = EXTRACT_PROMPT.format(
        name=cfg["name"], owner=cfg["owner"],
        facts="\n".join(f"{f['id']}: {f['text']}" for f in fs) or "(none yet)",
        followups="\n".join(f"{f['id']}: {f['text']}" for f in followups()) or "(none)",
        summary=summaries().get(today_key()) or "(nothing yet)", turns=conv)
    t0 = time.time()
    raw = await llm.chat_once([{"role": "user", "content": prompt}], model=cfg["memory_model"], think=cfg["memory_think"], json_mode=True)
    data = _parse_json(raw)
    if data is None:
        print(f"[memory] unparsable extraction: {raw[:200]!r}")
        return
    src = recent[-1].get("id")
    n_new = n_upd = 0
    for item in data.get("facts", []) or []:
        text = (item.get("text") or "").strip()
        action = item.get("action")
        fid = item.get("id")
        if action == "confirm" and fid is not None:
            confirm_fact(int(fid))
        elif action == "update" and fid is not None and text:
            if update_fact(int(fid), text=text, type=item.get("type")): n_upd += 1
        elif text:
            if not any(text.lower() == f["text"].lower() for f in fs):
                add_fact(text, item.get("type", "fact"), source_turn=src); n_new += 1
    for fu in data.get("followups", []) or []:
        if fu.get("text") and not any(fu["text"].lower() == x["text"].lower() for x in followups()):
            add_followup(fu["text"], fu.get("due"))
    for fid in data.get("resolved_followups", []) or []:
        try: resolve_followup(int(fid))
        except Exception: pass
    if data.get("today_summary"):
        set_summary(today_key(), data["today_summary"])
    print(f"[memory] extracted in {time.time() - t0:.1f}s with {cfg['memory_model']}: +{n_new} facts, {n_upd} updated")


def _parse_json(raw: str):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return None
    return None
