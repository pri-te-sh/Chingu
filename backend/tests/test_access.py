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


def ws_refused(client, hello, cookies=None, codes=(4001, 4003, 4029)):
    """True only if the server *deliberately* closes the socket with one of the expected policy codes."""
    from starlette.websockets import WebSocketDisconnect
    try:
        with client.websocket_connect("/ws", cookies=cookies) as ws:
            ws.send_text(json.dumps(hello)); ws.receive_text()
        return False
    except WebSocketDisconnect as e:
        assert e.code in codes, f"closed with unexpected code {e.code}"
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


def test_legacy_row_cannot_be_hijacked_by_planting_a_key(client):
    """R1: a paired device that has no identity key yet must not accept an attacker's key on a bad-token connection."""
    ua, ha, ca = make_user(client, "owner")
    dev = f"lite-{uuid.uuid4().hex[:6]}"
    try:
        p = run(client, repo.get_or_create_pixel, dev, "lite", household_id=ha)
        token = run(client, repo.issue_token, p["id"])                     # legacy: token, no key
        assert ws_refused(client, {"type": "hello", "device": dev, "device_type": "lite", "device_key": "attacker-planted-key-000000", "token": "wrong"})
        row = run(client, repo.pixel_by_device, dev)
        assert row["household_id"] == ha and row["device_key_hash"] is None      # still owned, nothing enrolled
        # the real unit (valid token) enrols its key on its next connection
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "hello", "device": dev, "device_type": "lite", "device_key": "real-unit-key-1234567890", "token": token}))
            assert json.loads(ws.receive_text())["type"] == "ready"
        assert run(client, repo.pixel_by_device, dev)["device_key_hash"] is not None
        assert ws_refused(client, {"type": "hello", "device": dev, "device_type": "lite", "device_key": "attacker-planted-key-000000", "token": token})
    finally: cleanup(client, ha)


def test_removed_device_loses_household_access(client):
    """R2: portal REMOVE (archive) ends authorization; the unit comes back only as an unpaired registration."""
    ua, ha, ca = make_user(client, "owner")
    dev = f"lite-{uuid.uuid4().hex[:6]}"
    try:
        p = run(client, repo.get_or_create_pixel, dev, "lite", household_id=ha)
        run(client, repo.issue_token, p["id"]); run(client, repo.set_pixel_key, p["id"], "unit-key-abcdefghijklmnop")
        run(client, repo.archive_pixel, p["id"])
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "hello", "device": dev, "device_type": "lite", "device_key": "unit-key-abcdefghijklmnop"}))
            msg = json.loads(ws.receive_text()); assert msg["type"] == "pairing"
        row = run(client, repo.pixel_by_device, dev)
        assert row["household_id"] is None and row["token_hash"] is None and row["archived"] is False
    finally: cleanup(client, ha)


def test_unpair_then_claim_by_another_household_resets_persona(client):
    """R4: the supported transfer path (unpair -> claim) must not carry the previous owner's persona."""
    ua, ha, ca = make_user(client, "alice"); ub, hb, cb = make_user(client, "bob")
    dev = f"lite-{uuid.uuid4().hex[:6]}"
    try:
        p = run(client, repo.get_or_create_pixel, dev, "lite", household_id=ha)
        run(client, repo.update_persona, p["id"], {"persona": "knows alice's bank pin is 4242"})
        run(client, repo.unpair, p["id"]); code = run(client, repo.pixel, p["id"])["pairing_code"]
        assert client.post("/api/pixels/claim", json={"code": code}, cookies=cb, headers=HDR).status_code == 200
        assert run(client, repo.persona, p["id"]) == {}
        # alice re-pairing her own device keeps its persona
        p2 = run(client, repo.get_or_create_pixel, f"lite-{uuid.uuid4().hex[:6]}", "lite", household_id=ha)
        run(client, repo.update_persona, p2["id"], {"persona": "mine"}); run(client, repo.unpair, p2["id"])
        code2 = run(client, repo.pixel, p2["id"])["pairing_code"]
        assert client.post("/api/pixels/claim", json={"code": code2}, cookies=ca, headers=HDR).status_code == 200
        assert run(client, repo.persona, p2["id"]).get("persona") == "mine"
    finally: cleanup(client, ha, hb)


def test_retention_never_deletes_devices_with_history(client):
    """R3: expire_pending removes only never-claimed, history-free registrations."""
    import datetime as dt
    ua, ha, ca = make_user(client, "owner")
    dev = f"lite-{uuid.uuid4().hex[:6]}"; fresh = f"lite-{uuid.uuid4().hex[:6]}"
    try:
        p = run(client, repo.get_or_create_pixel, dev, "lite", household_id=ha)
        run(client, repo.log_turn, p["id"], ha, user_text="hi", reply="hello")
        run(client, repo.unpair, p["id"])
        q = run(client, repo.get_or_create_pixel, fresh, "lite")                  # never claimed, no history
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)
        run(client, repo.update_pixel, p["id"], last_seen_at=old); run(client, repo.update_pixel, q["id"], last_seen_at=old)
        run(client, repo.expire_pending, 7)
        assert run(client, repo.pixel, p["id"]) is not None and run(client, repo.count_turns, ha) == 1
        assert run(client, repo.pixel, q["id"]) is None
    finally: cleanup(client, ha)


def test_health_reports_503_when_a_dependency_is_down(client, monkeypatch):
    from pixel import inference
    async def down(): return False
    monkeypatch.setattr(inference, "ready", down)
    r = client.get("/health"); assert r.status_code == 503 and r.json()["parts"]["worker"] is False


def test_live_socket_is_revoked_when_device_changes_hands(client):
    """V1: a socket authenticated for household A must not keep working after the device is released and claimed by B."""
    ua, ha, ca = make_user(client, "alice"); ub, hb, cb = make_user(client, "bob")
    dev = f"lite-{uuid.uuid4().hex[:6]}"
    try:
        p = run(client, repo.get_or_create_pixel, dev, "lite", household_id=ha)
        token = run(client, repo.issue_token, p["id"]); run(client, repo.set_pixel_key, p["id"], "unit-key-revoke-0123456789")
        with client.websocket_connect("/ws") as ws_a:
            ws_a.send_text(json.dumps({"type": "hello", "device": dev, "device_type": "lite", "device_key": "unit-key-revoke-0123456789", "token": token}))
            assert json.loads(ws_a.receive_text())["type"] == "ready"; ws_a.receive_text()          # config
            # the unit reconnects without its token (reset) -> released; the old socket is superseded
            with client.websocket_connect("/ws") as ws_b:
                ws_b.send_text(json.dumps({"type": "hello", "device": dev, "device_type": "lite", "device_key": "unit-key-revoke-0123456789"}))
                msg = json.loads(ws_b.receive_text()); assert msg["type"] == "pairing"
                assert client.post("/api/pixels/claim", json={"code": msg["code"]}, cookies=cb, headers=HDR).status_code == 200
            assert run(client, repo.pixel_by_device, dev)["household_id"] == hb
            # any further use of the original socket is refused (closed 4000 superseded or 4001 revoked), never served under A
            from starlette.websockets import WebSocketDisconnect
            try:
                ws_a.send_text(json.dumps({"type": "text", "text": "what do you know about alice?"}))
                for _ in range(10):
                    d = json.loads(ws_a.receive_text())
                    assert d.get("type") not in ("transcript", "reply", "speech_start"), f"old socket still served: {d}"
                assert False, "old socket was not closed"
            except WebSocketDisconnect as e:
                assert e.code in (4000, 4001)
    finally: cleanup(client, ha, hb)


def test_tokenless_legacy_row_is_not_authenticated_by_device_id_alone(client):
    """V2: the grandfather branch is gone - a paired row with no token and no key refuses anonymous hellos."""
    ua, ha, ca = make_user(client, "owner")
    dev = f"lite-{uuid.uuid4().hex[:6]}"
    try:
        run(client, repo.get_or_create_pixel, dev, "lite", household_id=ha)              # paired_at set, token_hash NULL, no key
        assert ws_refused(client, {"type": "hello", "device": dev, "device_type": "lite"})
        assert ws_refused(client, {"type": "hello", "device": dev, "device_type": "lite", "device_key": "some-new-key-0123456789ab"})
        assert run(client, repo.pixel_by_device, dev)["household_id"] == ha
    finally: cleanup(client, ha)


def test_history_search_is_server_side_and_scoped(client):
    """UX03: history search/pagination covers the whole household history and never another household's turns."""
    ua, ha, ca = make_user(client, "alice"); ub, hb, cb = make_user(client, "bob")
    try:
        pa = run(client, repo.get_or_create_pixel, f"lite-{uuid.uuid4().hex[:6]}", "lite", household_id=ha)
        pb = run(client, repo.get_or_create_pixel, f"lite-{uuid.uuid4().hex[:6]}", "lite", household_id=hb)
        for i in range(7): run(client, repo.log_turn, pa["id"], ha, user_text=f"alice turn {i} about basil", reply="ok")
        run(client, repo.log_turn, pb["id"], hb, user_text="bob secret basil recipe", reply="ok")
        r = client.get("/api/history?q=basil&limit=3", cookies=ca).json()
        assert r["total"] == 7 and len(r["turns"]) == 3 and all("alice" in t["user"] for t in r["turns"])
        r2 = client.get("/api/history?q=basil&limit=3&offset=6", cookies=ca).json()
        assert len(r2["turns"]) == 1 and sum(d["n"] for d in r2["days"]) == 7
        assert client.get(f"/api/history?device={pb['id']}", cookies=ca).status_code == 404       # bob's pixel is not filterable by alice
    finally: cleanup(client, ha, hb)
