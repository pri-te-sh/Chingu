"""Authentication: pluggable providers behind one session model.
  dev    - email/password from .env, only when PIXEL_ENV=dev (local testing without any OAuth setup)
  google - OAuth 2.0 / OIDC via Authlib, enabled when GOOGLE_CLIENT_ID is set
Sessions are server-side rows (sessions table); the cookie carries only a signed session id."""
import datetime as dt, os, secrets
import sqlalchemy as sa
from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeSerializer
from . import repo, models as m

ENV = os.environ.get("PIXEL_ENV", "dev")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-secret")
COOKIE = "pixel_session"
SESSION_DAYS = 30
_signer = URLSafeSerializer(SESSION_SECRET, salt="pixel-session")

GOOGLE_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
DEV_EMAIL = os.environ.get("DEV_LOGIN_EMAIL", "")
DEV_PASSWORD = os.environ.get("DEV_LOGIN_PASSWORD", "")


def providers() -> dict:
    return {"dev": (ENV == "dev" or os.environ.get("PIXEL_ALLOW_DEV_LOGIN") == "1") and bool(DEV_EMAIL and DEV_PASSWORD), "google": bool(GOOGLE_ID and GOOGLE_SECRET)}


# ---------------- users & households ----------------
async def upsert_user(provider: str, provider_id: str, email: str | None, name: str | None, avatar: str | None = None) -> dict:
    u = await repo.fetch_one(sa.select(m.users).where(m.users.c.provider == provider, m.users.c.provider_id == provider_id))
    if u:
        await repo.execute(sa.update(m.users).where(m.users.c.id == u["id"]).values(email=email or u["email"], name=name or u["name"], avatar=avatar or u["avatar"]))
        return await repo.fetch_one(sa.select(m.users).where(m.users.c.id == u["id"]))
    r = await repo.execute(sa.insert(m.users).values(provider=provider, provider_id=provider_id, email=email, name=name, avatar=avatar).returning(m.users.c.id))
    uid = r.scalar_one()
    await ensure_household(uid, name)
    return await repo.fetch_one(sa.select(m.users).where(m.users.c.id == uid))


async def ensure_household(uid: int, display_name: str | None):
    """First login: claim the pre-existing unowned household (where the current data lives) or create one."""
    if await repo.fetch_one(sa.select(m.household_members).where(m.household_members.c.user_id == uid)):
        return
    h = await repo.fetch_one(sa.select(m.households).where(m.households.c.owner_user_id.is_(None)).order_by(m.households.c.id).limit(1))
    if h:
        await repo.execute(sa.update(m.households).where(m.households.c.id == h["id"]).values(owner_user_id=uid))
        hid = h["id"]
    else:
        r = await repo.execute(sa.insert(m.households).values(name=f"{(display_name or 'My').split()[0]}'s home", owner_user_id=uid).returning(m.households.c.id))
        hid = r.scalar_one()
    await repo.execute(sa.insert(m.household_members).values(household_id=hid, user_id=uid, role="owner"))


async def households_for(uid: int) -> list[dict]:
    q = (sa.select(m.households, m.household_members.c.role).join(m.household_members, m.household_members.c.household_id == m.households.c.id)
         .where(m.household_members.c.user_id == uid).order_by(m.households.c.id))
    return await repo.fetch_all(q)


async def members(hid: int) -> list[dict]:
    q = (sa.select(m.users.c.id, m.users.c.name, m.users.c.email, m.users.c.avatar, m.household_members.c.role)
         .join(m.household_members, m.household_members.c.user_id == m.users.c.id).where(m.household_members.c.household_id == hid))
    return await repo.fetch_all(q)


# ---------------- sessions ----------------
async def create_session(uid: int) -> str:
    sid = secrets.token_urlsafe(32)
    await repo.execute(sa.insert(m.sessions).values(id=sid, user_id=uid, expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=SESSION_DAYS)))
    return _signer.dumps(sid)


def set_cookie(resp: Response, token: str, request: Request):
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")


async def destroy_session(request: Request):
    tok = request.cookies.get(COOKIE)
    if not tok: return
    try: sid = _signer.loads(tok)
    except BadSignature: return
    await repo.execute(sa.delete(m.sessions).where(m.sessions.c.id == sid))


async def current_user(request) -> dict | None:
    tok = request.cookies.get(COOKIE)
    if not tok: return None
    try: sid = _signer.loads(tok)
    except BadSignature: return None
    row = await repo.fetch_one(sa.select(m.users, m.sessions.c.expires_at).join(m.sessions, m.sessions.c.user_id == m.users.c.id).where(m.sessions.c.id == sid))
    if not row: return None
    if dt.datetime.fromisoformat(row["expires_at"]) < dt.datetime.now(dt.timezone.utc): return None
    return row


async def require_admin(request: Request) -> dict:
    """Platform-wide operations (firmware releases, drain): a signed-in user with is_admin."""
    u = await require_user(request)
    if not u.get("is_admin"): raise HTTPException(403, "platform admin required")
    return u


async def require_user(request: Request) -> dict:
    """Portal API guard: valid session + CSRF header on state-changing requests."""
    u = await current_user(request)
    if not u: raise HTTPException(401, "sign in required")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "pixel":
        raise HTTPException(403, "missing X-Requested-With")
    return u


# ---------------- providers ----------------
def dev_login(email: str, password: str) -> bool:
    return providers()["dev"] and secrets.compare_digest(email.strip().lower(), DEV_EMAIL.lower()) and secrets.compare_digest(password, DEV_PASSWORD)


def google_client():
    from authlib.integrations.starlette_client import OAuth
    oauth = OAuth()
    oauth.register("google", client_id=GOOGLE_ID, client_secret=GOOGLE_SECRET,
                   server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid email profile"})
    return oauth.google
