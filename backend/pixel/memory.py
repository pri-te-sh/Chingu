"""Pixel's memory (per household): facts (deduplicated/superseded), follow-ups, daily summaries, plus the prompt builder.
Extraction runs in the background with the household's memory model after each exchange - never on the reply path."""
import asyncio, datetime as dt, json, re, time
from zoneinfo import ZoneInfo
from . import repo, llm, config as C

FACT_TYPES = ("fact", "preference", "project")
STALE_DAYS = 90


def now_local(tz: str) -> dt.datetime:
    return dt.datetime.now(ZoneInfo(tz))

def today_key(tz: str) -> str:
    return now_local(tz).strftime("%Y-%m-%d")

def _part_of_day(h: int) -> str:
    return "early morning" if h < 6 else "morning" if h < 12 else "afternoon" if h < 17 else "evening" if h < 22 else "late night"

def _ago(now: dt.datetime, t: dt.datetime | None) -> str:
    if not t: return "This is your first ever conversation with them."
    m = int((now - t).total_seconds() // 60)
    if m < 2: return "You spoke moments ago."
    if m < 60: return f"You last spoke {m} minutes ago."
    h = m // 60
    if h < 24: return f"You last spoke about {h} hour{'s' if h > 1 else ''} ago."
    return f"You last spoke {h // 24} day{'s' if h // 24 > 1 else ''} ago."


async def active_facts(hid: int) -> list[dict]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=STALE_DAYS)
    out = []
    for f in await repo.facts(hid):
        try: fresh = dt.datetime.fromisoformat(f["last_confirmed"]) >= cutoff
        except Exception: fresh = True
        if f["pinned"] or fresh: out.append(f)
    return out


async def system_prompt(cfg: dict) -> str:
    hid, tz = cfg["_household_id"], cfg["timezone"]
    now = now_local(tz)
    cheeky, chatty = cfg["tone"]["cheeky"], cfg["tone"]["chatty"]
    tone = [("Lean into gentle teasing and wit." if cheeky > 0.66 else "Be kind and warm; tease only lightly." if cheeky > 0.33 else "Be gentle and sincere; no teasing."),
            ("Two short sentences are fine." if chatty > 0.66 else "Prefer one short sentence, two at most." if chatty > 0.33 else "Answer in a single short sentence.")]
    last = await repo.last_turn_ts(hid)
    lines = [
        f"You are {cfg['name']}, a small desk companion robot with an animated face and a voice, living on {cfg['owner']}'s desk.",
        f"Personality: {cfg['persona']}", " ".join(tone),
        f"Right now it is {now.strftime('%A %-d %B %Y, %-I:%M %p')} ({_part_of_day(now.hour)}). {_ago(now, last.astimezone(ZoneInfo(tz)) if last else None)}",
    ]
    gap = None if not last else (now - last.astimezone(ZoneInfo(tz))).total_seconds() / 60
    if gap is None or gap > cfg["session_gap_min"]:
        lines.append("This is the first exchange after a break, so behave like someone who has been living their own day meanwhile: greet them in a way that fits the time of day "
                     "and how long it has been, and you may bring up ONE thing - an open follow-up, something they mentioned last time, or one item from the world brief - "
                     "then respond to what they actually said. Do not list several things, do not summarise the news.")
    fs = await active_facts(hid)
    if fs:
        lines.append(f"\nWhat you know about {cfg['owner']} (use naturally and sparingly, only when relevant - never recite):")
        lines += [f"- {f['text']}" for f in fs]
    sm = await repo.summaries(hid)
    today = today_key(tz)
    if sm.get(today): lines.append(f"\nToday so far: {sm[today]}")
    recent = [(d, sm[d]) for d in sorted(sm, reverse=True) if d != today][:3]
    if recent:
        lines.append("\nRecent days:"); lines += [f"- {d}: {t}" for d, t in recent]
    from . import ambient
    amb = await ambient.prompt_section(hid)
    if amb: lines.append(amb)
    fu = await repo.followups(hid)
    if fu:
        lines.append("\nThings you meant to ask about when the moment is right:")
        lines += [f"- {f['text']}" + (f" (around {f['due']})" if f.get("due") else "") for f in fu[:6]]
    if cfg["tools_enabled"]:
        lines.append("\nTools: use web_search whenever the answer depends on the real world right now (weather, news, scores, prices, opening hours, facts you are not sure of); "
                     "You MUST call remember when the owner asks you to remember something or tells you a durable fact about themselves, and follow_up when they ask to be reminded - never claim you saved or noted something without actually calling the tool. "
                     "When you decide to use a tool, first say ONE short natural sentence about what you are doing, in your own voice (e.g. 'Let me see what the weather's doing over there.'), then call the tool. "
                     "After a tool result, answer in one or two spoken sentences - never read out URLs or lists.")
    lines.append("\nEvery reply MUST start with an expression tag in square brackets: the expression name and an intensity 0-1, e.g. \"[happy 0.8] \". "
                 f"Allowed expressions: {', '.join(e for e in C.EXPRESSIONS if e not in ('asleep', 'listening'))}. "
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

_last: dict[int, float] = {}
_pending: set[int] = set()


async def extract_after_turn(cfg: dict):
    hid = cfg["_household_id"]
    if not cfg.get("memory_enabled", True) or hid in _pending: return
    _pending.add(hid)
    try:
        await asyncio.sleep(max(0.0, 15 - (time.time() - _last.get(hid, 0))))
        _last[hid] = time.time()
        await extract(cfg)
    except Exception as e:
        print(f"[memory] extraction failed: {e!r}")
    finally:
        _pending.discard(hid)


async def extract(cfg: dict):
    hid, tz = cfg["_household_id"], cfg["timezone"]
    recent = await repo.turns(hid=hid, limit=8)
    if not recent: return
    conv = "\n".join(f"U: {t.get('user_text','')}\nA: {t.get('reply','')}" for t in recent)
    fs = await repo.facts(hid)
    sm = await repo.summaries(hid)
    prompt = EXTRACT_PROMPT.format(name=cfg["name"], owner=cfg["owner"],
        facts="\n".join(f"{f['id']}: {f['text']}" for f in fs) or "(none yet)",
        followups="\n".join(f"{f['id']}: {f['text']}" for f in await repo.followups(hid)) or "(none)",
        summary=sm.get(today_key(tz)) or "(nothing yet)", turns=conv)
    t0 = time.time()
    raw = await llm.chat_once([{"role": "user", "content": prompt}], model=cfg["memory_model"], think=cfg["memory_think"], json_mode=True)
    data = parse_json(raw)
    if data is None:
        print(f"[memory] unparsable extraction: {raw[:200]!r}"); return
    src = recent[-1]["id"]
    n_new = n_upd = 0
    own_facts = {f["id"] for f in fs}                        # ids the model was shown; anything else is ignored
    def _id(v):
        try: return int(v)
        except (TypeError, ValueError): return None
    for item in data.get("facts", []) or []:
        text, action, fid = (item.get("text") or "").strip(), item.get("action"), _id(item.get("id"))
        if fid is not None and fid not in own_facts: continue
        if action == "confirm" and fid is not None: await repo.confirm_fact(fid, hid)
        elif action == "update" and fid is not None and text:
            if await repo.update_fact(fid, hid, text=text, type=item.get("type")): n_upd += 1
        elif text and not any(text.lower() == f["text"].lower() for f in fs):
            await repo.add_fact(hid, text, item.get("type", "fact"), source_turn=src); n_new += 1
    open_fu = await repo.followups(hid)
    for fu in data.get("followups", []) or []:
        if fu.get("text") and not any(fu["text"].lower() == x["text"].lower() for x in open_fu):
            await repo.add_followup(hid, fu["text"], fu.get("due") if fu.get("due") not in (None, "null") else None)
    own_fu = {x["id"] for x in open_fu}
    for fid in data.get("resolved_followups", []) or []:
        fid = _id(fid)
        if fid is not None and fid in own_fu: await repo.resolve_followup(fid, hid)
    if data.get("today_summary"): await repo.set_summary(hid, today_key(tz), data["today_summary"])
    print(f"[memory] hid={hid} extracted in {time.time() - t0:.1f}s with {cfg['memory_model']}: +{n_new} facts, {n_upd} updated")


def parse_json(raw: str):
    raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    try: return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return None
    return None
