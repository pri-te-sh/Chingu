import pytest
from pixel import repo, settings


async def test_facts_are_household_scoped(household, pixel):
    f = await repo.add_fact(household, "Owner has a dog named Bruno", "fact")
    other = await repo.execute(__import__("sqlalchemy").insert(repo.m.households).values(name="other").returning(repo.m.households.c.id))
    oid = other.scalar_one()
    try:
        assert [x["text"] for x in await repo.facts(household)] == ["Owner has a dog named Bruno"]
        assert await repo.facts(oid) == []
        upd = await repo.update_fact(f["id"], text="Owner has a dog named Bruno who steals socks")
        assert "socks" in upd["text"]
        await repo.delete_fact(f["id"])
        assert await repo.facts(household) == []
    finally:
        await repo.execute(__import__("sqlalchemy").delete(repo.m.households).where(repo.m.households.c.id == oid))


async def test_config_split_between_household_and_pixel(household, pixel):
    cfg = await settings.update(household, pixel["id"], {"name": "Dot", "location": "Toronto", "eye_color": "#112233", "brief_refresh_min": 30, "tone": {"cheeky": 0.9}})
    assert cfg["name"] == "Dot" and cfg["eye_color"] == "#112233" and cfg["location"] == "Toronto" and cfg["brief_refresh_min"] == 30
    assert cfg["tone"]["cheeky"] == 0.9 and cfg["tone"]["chatty"] == settings.DEFAULTS["tone"]["chatty"]
    # a second pixel in the same household shares household keys but not persona keys
    p2 = await repo.get_or_create_pixel(f"test2-{household}", "sim", household_id=household)
    cfg2 = await settings.resolve(household, p2["id"])
    assert cfg2["location"] == "Toronto" and cfg2["name"] == "Pixel" and cfg2["eye_color"] == settings.DEFAULTS["eye_color"]
    dev = settings.device_config(cfg)
    assert dev["type"] == "config" and dev["eye_color"] == "#112233" and "mood_colors" in dev


async def test_turns_and_summaries(household, pixel):
    t = await repo.log_turn(pixel["id"], household, user_text="hi", reply="hello", expr="happy", intensity=0.8, t_expr=300, t_audio=400, t_done=900, audio_s=1.2, model="x", tools=[], steps=[])
    assert t["id"] and (await repo.count_turns(household)) == 1
    assert (await repo.last_turn_ts(household)) is not None
    await repo.set_summary(household, "2026-09-09", "a day"); await repo.set_summary(household, "2026-09-09", "a better day")
    assert (await repo.summaries(household))["2026-09-09"] == "a better day"
    await repo.delete_turn(t["id"]); assert (await repo.count_turns(household)) == 0
