"""Access control at the HTTP and WebSocket boundary (audit F01-F05, F09, F16): two households, adversarial cases.
These tests are synchronous: the app runs inside Starlette's TestClient on its own event loop, so every coroutine
(DB setup, assertions against the repo) is executed on that loop through client.portal."""
import json, uuid
import pytest, sqlalchemy as sa
from starlette.testclient import TestClient
from pixel import auth, repo, models as m, server, db

HDR = {"X-Requested-With": "pixel"}


@pytest.fixture
def client():
    with TestClient(server.app) as c:
        yield c
        async def _dispose():
            if db._engine is not None: await db._engine.dispose(); db._engine = None
            if db._redis is not None: await db._redis.aclose(); db._redis = None
        c.portal.call(_dispose)


def run(client, coro_fn, *a, **kw): return client.portal.call(lambda: coro_fn(*a, **kw))


def make_user(client, tag):
    async def _mk():
        u = await auth.upsert_user("dev", f"t-{tag}-{uuid.uuid4().hex[:6]}", f"{tag}@t.est", tag.upper())
        await auth.ensure_household(u["id"], tag)
        hid = (await auth.households_for(u["id"]))[0]["id"]
        return u, hid, {"pixel_session": await auth.create_session(u["id"])}
    return client.portal.call(_mk)


def cleanup(client, *hids):
    async def _cl():
        for h in hids: await repo.execute(sa.delete(m.households).where(m.households.c.id == h))
    client.portal.call(_cl)


def ws_refused(client, hello, cookies=None):
    """True if the server closes the socket instead of answering the hello."""
    try:
        with client.websocket_connect("/ws", cookies=cookies) as ws:
            ws.send_text(json.dumps(hello)); ws.receive_text()
        return False
    except Exception:
        return True


def test_simulator_ws_requires_session_and_own_household(client):
    ua, ha, ca = make_user(client, "alice"); ub, hb, cb = make_user(client, "bob")
    sim = f"sim-{uuid.uuid4().hex[:6]}"
    try:
        # no cookie -> refused before any registration
        assert ws_refused(client, {"type": "hello", "device": sim, "device_type": "sim"})
        assert run(client, repo.pixel_by_device, sim) is None
        # alice registers her simulator
        with client.websocket_connect("/ws", cookies=ca) as ws:
            ws.send_text(json.dumps({"type": "hello", "device": sim, "device_type": "sim"}))
            assert json.loads(ws.receive_text())["type"] == "ready"
        assert run(client, repo.pixel_by_device, sim)["household_id"] == ha
        # bob cannot use alice's simulator id, even with a valid session
        assert ws_refused(client, {"type": "hello", "device": sim, "device_type": "sim"}, cookies=cb)
    finally: cleanup(client, ha, hb)


def test_paired_physical_device_cannot_be_impersonated(client):
    ua, ha, ca = make_user(client, "owner")
    dev = f"lite-{uuid.uuid4().hex[:6]}"
    try:
        p = run(client, repo.get_or_create_pixel, dev, "lite", household_id=ha)
        token = run(client, repo.issue_token, p["id"]); run(client, repo.set_pixel_key, p["id"], "unit-secret-key-0123456789")
        # claiming it as a "sim" with no cookie: refused (F01)
        assert ws_refused(client, {"type": "hello", "device": dev, "device_type": "sim"})
        assert run(client, repo.pixel_by_device, dev)["device_type"] == "lite"          # type not mutated by hello
        # wrong identity key: refused, no pairing code leaked (F02)
        assert ws_refused(client, {"type": "hello", "device": dev, "device_type": "lite", "device_key": "attacker-key-xxxxxxxxxxxx", "token": "bad"})
        assert run(client, repo.pixel_by_device, dev)["household_id"] == ha              # still paired
        # right key + right token: ready
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "hello", "device": dev, "device_type": "lite", "device_key": "unit-secret-key-0123456789", "token": token}))
            assert json.loads(ws.receive_text())["type"] == "ready"
        # right key, lost token (factory reset): released and shows a code - this is the legitimate re-pair path
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "hello", "device": dev, "device_type": "lite", "device_key": "unit-secret-key-0123456789"}))
            msg = json.loads(ws.receive_text()); assert msg["type"] == "pairing" and len(msg["code"]) == 6
        assert run(client, repo.pixel_by_device, dev)["household_id"] is None
    finally: cleanup(client, ha)


def test_claim_only_works_for_unpaired_devices(client):
    ua, ha, ca = make_user(client, "owner"); ub, hb, cb = make_user(client, "thief")
    dev = f"lite-{uuid.uuid4().hex[:6]}"
    try:
        p = run(client, repo.get_or_create_pixel, dev, "lite", household_id=ha)
        run(client, repo.update_pixel, p["id"], pairing_code="ZZTOP1")            # stale code on a paired row must not be claimable
        r = client.post("/api/pixels/claim", json={"code": "ZZTOP1"}, cookies=cb, headers=HDR)
        assert r.status_code == 404 and run(client, repo.pixel_by_device, dev)["household_id"] == ha
        run(client, repo.unpair, p["id"]); code = run(client, repo.pixel, p["id"])["pairing_code"]
        r = client.post("/api/pixels/claim", json={"code": code, "name": "Stolen?"}, cookies=cb, headers=HDR)
        assert r.status_code == 200 and run(client, repo.pixel_by_device, dev)["household_id"] == hb   # unpaired: whoever has the code
    finally: cleanup(client, ha, hb)


def test_say_requires_login_and_own_device(client):
    ua, ha, ca = make_user(client, "owner")
    try:
        assert client.post("/api/device/say", json={"text": "hi"}).status_code in (401, 403)
        r = client.post("/api/device/say", json={"text": "hi", "device": "lite-someoneelse"}, cookies=ca, headers=HDR)
        assert r.status_code == 404
    finally: cleanup(client, ha)


def test_records_are_household_scoped(client):
    ua, ha, ca = make_user(client, "alice"); ub, hb, cb = make_user(client, "bob")
    try:
        f = run(client, repo.add_fact, ha, "alice likes tea", "fact")
        fu = run(client, repo.add_followup, ha, "call mum", None)
        assert client.put(f"/api/facts/{f['id']}", json={"text": "hacked"}, cookies=cb, headers=HDR).status_code == 404
        client.delete(f"/api/facts/{f['id']}", cookies=cb, headers=HDR)
        client.put(f"/api/followups/{fu['id']}", json={"done": True}, cookies=cb, headers=HDR)
        client.delete(f"/api/followups/{fu['id']}", cookies=cb, headers=HDR)
        facts = run(client, repo.facts, ha); assert facts and facts[0]["text"] == "alice likes tea"
        fus = run(client, repo.followups, ha); assert fus and fus[0]["done"] is False
        assert client.put(f"/api/facts/{f['id']}", json={"text": "tea, strong"}, cookies=ca, headers=HDR).status_code == 200
    finally: cleanup(client, ha, hb)


def test_firmware_publish_and_drain_need_admin(client):
    ua, ha, ca = make_user(client, "plain")
    try:
        r = client.post("/api/firmware/upload", files={"file": ("x.bin", b"\xe9" + b"\x00" * 200000)}, data={"version": "9.9.9"}, cookies=ca, headers=HDR)
        assert r.status_code == 403
        assert client.post("/api/admin/drain", cookies=ca, headers=HDR).status_code == 403
        assert client.get("/api/firmware/manifest?device_type=lite").status_code == 200        # devices still read the manifest
    finally: cleanup(client, ha)


def test_settings_are_validated_before_persisting(client):
    ua, ha, ca = make_user(client, "cfg")
    try:
        r = client.put("/api/config", json={"timezone": "Mars/Olympus", "auto_sleep_s": 60}, cookies=ca, headers=HDR)
        assert r.status_code == 400 and "timezone" in r.text
        assert client.get("/api/config", cookies=ca).json()["auto_sleep_s"] == 45              # nothing from the bad patch was written
        assert client.put("/api/config", json={"eye_color": "red"}, cookies=ca, headers=HDR).status_code == 400
        assert client.put("/api/config", json={"tone": "loud"}, cookies=ca, headers=HDR).status_code == 400
        assert client.put("/api/config", json={"eye_color": "#ff8800", "auto_sleep_s": 60}, cookies=ca, headers=HDR).status_code == 200
    finally: cleanup(client, ha)


def test_public_health_has_no_identities(client):
    d = client.get("/health").json()
    assert "devices" not in d and "household" not in d and "parts" in d
