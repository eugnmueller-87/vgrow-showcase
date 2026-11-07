"""Resume the sourced stage plan, never a stale preview or another plant's requirements."""
import asyncio
import copy
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, server, workspace
from vgrow.alerts import Alerts
from vgrow.box import CommandRefused
from vgrow.budget import CommandBudget
from vgrow.demo import DemoBox
from vgrow.light_schedule import DEFAULTS, LightSchedule, automatic_plan, settings
from vgrow.plant import PlantLog, load_profile
from test_light_schedule import Box, at


@pytest.fixture
def rig():
    clock, box, profile = [at("2026-10-27T12:00:00")], Box(), load_profile()
    stage = {**copy.deepcopy(profile["stages"][0]), "set_by": "manual", "since": "2026-10-24"}
    ctl = LightSchedule(box, CommandBudget(lambda: clock[0]), lambda: clock[0])
    ctl.requirements_source = lambda: (profile, stage)
    return ctl, box, clock, profile, stage


@pytest.mark.parametrize("key,level", [("seedling", 60), ("veg", 80), ("early_flower", 100),
                                      ("mid_flower", 100), ("late_flower", 100)])
def test_real_card_supplies_cycle_and_each_confirmed_stage_brightness(rig, key, level):
    ctl, box, _, profile, stage = rig
    stage.update(next(s for s in profile["stages"] if s["key"] == key))
    plan = ctl.view(box.snapshot())["automatic"]["plan"]
    assert plan["settings"] == settings({"enabled": True, "on_time": "06:00", "off_time": "00:00",
                                        "level": level, "from_requirements": True})
    assert plan["hours_on"] == 18 and plan["default_start"]
    assert {s["id"] for s in plan["sources"]} == {"S1", "S6"}
    assert all(s["url"].startswith("https://") for s in plan["sources"])
    assert not ctl.path.exists() and not box.calls


@pytest.mark.parametrize("off,expected", [("10:00", "16:00"), ("16:00", "16:00"), ("18:00", "18:00")])
def test_preserves_saved_start_and_supported_alternative_without_photo_flower_guess(rig, off, expected):
    ctl, _, _, profile, stage = rig
    cfg = settings({"on_time": "22:00", "off_time": off, "level": 75})
    plan = automatic_plan(profile, stage, cfg)
    assert plan["settings"]["on_time"] == "22:00" and plan["settings"]["off_time"] == expected
    assert not plan["default_start"]
    # A photoperiod cycle must be explicit in that stage, never inferred from a name.
    stage["light_schedule"] = {"hours_on": 12, "sources": ["S6"]}
    assert automatic_plan(profile, stage, cfg)["hours_on"] == 12


def test_unconfirmed_unknown_harvest_and_unsourced_requirements_are_not_actionable(rig):
    ctl, box, _, profile, stage = rig
    for bad in ({**stage, "set_by": "estimate"}, {**stage, "preset": {}},
                {**stage, "preset_sources": []}, {**stage, "preset_sources": ["missing"]}):
        assert automatic_plan(profile, bad, DEFAULTS) is None
    assert automatic_plan(None, stage, DEFAULTS) is None
    stage.update(next(s for s in profile["stages"] if s["key"] == "harvest"))
    # update() retains the seedling preset in this test dict unless explicitly removed.
    stage.pop("preset", None)
    assert ctl.view(box.snapshot())["automatic"]["reason"] == "requirements"
    assert not box.calls and not ctl.path.exists()


@pytest.mark.parametrize("hour,reported,wanted", [(12, 30, 60), (2, 75, 0)])
def test_automatic_ends_manual_override_and_reconciles_day_or_dark_now(rig, hour, reported, wanted):
    ctl, box, clock, _, _ = rig
    async def go():
        clock[0] = at(f"2026-10-27T{hour:02d}:00:00")
        await ctl.configure({"enabled": True, "on_time": "06:00", "off_time": "00:00", "level": 75})
        ctl.manual()
        box.snap["light"]["level"] = reported
        assert ctl.view(box.snapshot())["status"] == "manual"
        await ctl.resume_automatic(ctl.automatic(box.snapshot())["plan"]["fingerprint"])
        await ctl.tick()
        assert box.calls == [wanted] and ctl.override_until is None
        assert ctl.view(box.snapshot())["status"] == "scheduled" and ctl.cfg["from_requirements"]
        assert json.loads(ctl.path.read_text())["settings"] == ctl.cfg
        restored = LightSchedule(box, ctl.budget, lambda: clock[0])
        restored.requirements_source = ctl.requirements_source
        await restored.tick()
        assert box.calls == [wanted], "restart must not replay a switch already reported"
    asyncio.run(go())


def test_off_stops_automatic_and_automatic_switches_the_light_on_at_once(rig):
    """Owner, 01.11.2026: after Off the plan stays out; tapping Automatic again is when it switches."""
    ctl, box, clock, _, _ = rig
    async def go():
        await ctl.resume_automatic(ctl.automatic(box.snapshot())["plan"]["fingerprint"])
        await ctl.tick()
        on = ctl.cfg["level"]
        assert box.calls == [on]
        ctl.stop()
        box.snap["light"]["level"] = 0
        clock[0] += 86400  # a whole day later, in the next light period
        await ctl.tick()
        assert box.calls == [on] and ctl.view(box.snapshot())["status"] == "stopped"
        await ctl.resume_automatic(ctl.automatic(box.snapshot())["plan"]["fingerprint"])
        assert box.calls == [on, on]
        await ctl.tick()  # the box's report confirms the switch
        assert ctl.view(box.snapshot())["status"] == "scheduled"
    asyncio.run(go())


def test_future_confirmed_stage_updates_brightness_and_respects_manual_pause(rig):
    ctl, box, clock, profile, stage = rig
    async def go():
        await ctl.resume_automatic(ctl.automatic(box.snapshot())["plan"]["fingerprint"])
        await ctl.tick()
        ctl.manual()
        box.snap["light"]["level"] = 40
        stage.update(next(s for s in profile["stages"] if s["key"] == "veg"))
        await ctl.tick()
        assert box.calls == [60] and ctl.cfg["level"] == 60
        await ctl.resume_automatic(ctl.automatic(box.snapshot())["plan"]["fingerprint"])
        await ctl.tick()
        assert box.calls == [60, 80] and ctl.cfg["level"] == 80
        stage.update(next(s for s in profile["stages"] if s["key"] == "early_flower"))
        await ctl.tick()
        await ctl.tick()
        assert box.calls == [60, 80, 100] and ctl.cfg["level"] == 100
        stage["set_by"] = "estimate"
        clock[0] += 60
        await ctl.tick()
        assert ctl.view(box.snapshot())["status"] == "requirements"
        clock[0] = at("2026-10-28T00:00:00")
        await ctl.tick()
        await ctl.tick()
        assert box.calls == [60, 80, 100, 0], "saved dark period still turns OFF without inventing an ON"
        clock[0] = at("2026-10-28T06:00:00")
        await ctl.tick()
        assert box.calls == [60, 80, 100, 0]
    asyncio.run(go())


def test_stale_preview_connection_vendor_plan_and_storage_failure_never_apply(rig, monkeypatch):
    ctl, box, _, _, stage = rig
    async def go():
        fingerprint = ctl.automatic(box.snapshot())["plan"]["fingerprint"]
        stage["since"] = "2026-10-25"
        with pytest.raises(CommandRefused):
            await ctl.resume_automatic(fingerprint)
        fingerprint = ctl.automatic(box.snapshot())["plan"]["fingerprint"]
        for key, value in (("shadow_age", 301), ("mqtt", False), ("online", False)):
            before = box.snap[key]
            box.snap[key] = value
            with pytest.raises(CommandRefused):
                await ctl.resume_automatic(fingerprint)
            box.snap[key] = before
        box.snap["light"]["in_plan"] = True
        with pytest.raises(CommandRefused):
            await ctl.resume_automatic(fingerprint)
        box.snap["light"]["in_plan"] = False
        def broken(*_):
            raise OSError("disk full")
        monkeypatch.setattr(config, "atomic_write", broken)
        with pytest.raises(CommandRefused):
            await ctl.resume_automatic(fingerprint)
        assert ctl.cfg == DEFAULTS and not box.calls and not ctl.path.exists()
    asyncio.run(go())


def test_custom_config_and_disabling_leave_requirements_mode(rig):
    ctl, box, _, _, _ = rig
    async def go():
        await ctl.resume_automatic(ctl.automatic(box.snapshot())["plan"]["fingerprint"])
        await ctl.configure({**ctl.cfg, "enabled": False})
        await ctl.tick()
        assert not ctl.cfg["enabled"] and not ctl.cfg["from_requirements"]
        assert box.calls == [60], "disabling retains the last brightness"
        await ctl.configure({"enabled": True, "on_time": "09:00", "off_time": "21:00", "level": 50})
        assert not ctl.cfg["from_requirements"]
        with pytest.raises(ValueError):
            await ctl.configure({**ctl.cfg, "from_requirements": True})
    asyncio.run(go())


def test_existing_12_hour_plan_is_preserved_until_automatic_is_clicked(rig):
    ctl, box, clock, _, _ = rig
    legacy = {"enabled": True, "on_time": "22:00", "off_time": "10:00", "level": 100,
              "timezone": "Europe/Berlin"}
    ctl.path.parent.mkdir(parents=True, exist_ok=True)
    ctl.path.write_text(json.dumps({"settings": legacy}), encoding="utf-8")
    restored = LightSchedule(box, ctl.budget, lambda: clock[0])
    restored.requirements_source = ctl.requirements_source
    async def go():
        await restored.tick()
        preview = restored.view(box.snapshot())["automatic"]["plan"]
        assert preview["settings"]["off_time"] == "16:00"
        assert restored.cfg == {**legacy, "from_requirements": False}
        assert not box.calls, "the current 12/12 dark period must survive the update"
        await restored.resume_automatic(preview["fingerprint"])
        assert box.calls == [60] and restored.cfg["off_time"] == "16:00"
    asyncio.run(go())


def test_api_requires_auth_fresh_fingerprint_and_the_linked_plant(rig, monkeypatch):
    _, _, clock, profile, stage = rig
    box = DemoBox()
    ctl = LightSchedule(box, CommandBudget(lambda: clock[0]), lambda: clock[0])
    monkeypatch.setattr(server, "load_profile", lambda: profile)
    monkeypatch.setattr(server, "current_stage", lambda *_: stage)
    log = PlantLog()
    workspace.initialize("Existing box", profile, log)
    other = workspace.mutate({"revision": workspace.view()["revision"], "action": "plant_create",
                              "box": "box-1", "name": "Other plant"})
    async def go():
        async with TestClient(TestServer(server.make_app(box, Alerts(), "k", plant_log=log, light_schedule=ctl))) as client:
            assert (await client.post("/api/cmd", json={"action": "light_schedule_auto"})).status == 403
            client.session.cookie_jar.update_cookies({server.COOKIE: "k"})
            before = box.snapshot()["light"]
            preview = (await (await client.get("/api/state")).json())["light_schedule"]["automatic"]
            assert preview["available"] and box.snapshot()["light"] == before and not ctl.path.exists()
            body = {"action": "light_schedule_auto", "fingerprint": preview["plan"]["fingerprint"]}
            assert (await client.post("/api/cmd", json=body, headers={"X-Workspace-Box": "box-1",
                    "X-Workspace-Plant": other})).status == 409
            assert (await client.post("/api/cmd", json={**body, "fingerprint": "stale"})).status == 409
            assert not ctl.path.exists() and box.snapshot()["light"] == before
            assert (await client.post("/api/cmd", json=body)).status == 200
            assert ctl.cfg["from_requirements"] and box.snapshot()["light"]["level"] == 60
    asyncio.run(go())
