import os
os.environ["PIXEL_ENV"] = "dev"; os.environ["DEV_LOGIN_EMAIL"] = "t@example.com"; os.environ["DEV_LOGIN_PASSWORD"] = "pw"
import sqlalchemy as sa
from pixel import auth, repo, models as m


async def test_dev_login_creates_user_and_claims_or_creates_household():
    auth.DEV_EMAIL, auth.DEV_PASSWORD, auth.ENV = "t@example.com", "pw", "dev"
    assert auth.dev_login("T@example.com", "pw") and not auth.dev_login("t@example.com", "nope")
    u = await auth.upsert_user("dev", "t-auth-test", "t@example.com", "Test User")
    try:
        hs = await auth.households_for(u["id"])
        assert len(hs) == 1 and hs[0]["role"] == "owner"
        # idempotent: second login does not create another household
        u2 = await auth.upsert_user("dev", "t-auth-test", "t@example.com", "Test User")
        assert u2["id"] == u["id"] and len(await auth.households_for(u["id"])) == 1
        tok = await auth.create_session(u["id"])
        assert tok and len(tok) > 20
    finally:
        for h in await auth.households_for(u["id"]):
            if (await repo.household(h["id"]))["owner_user_id"] == u["id"] and h["name"].endswith("'s home"):
                await repo.execute(sa.delete(m.households).where(m.households.c.id == h["id"]))
        await repo.execute(sa.delete(m.users).where(m.users.c.id == u["id"]))
