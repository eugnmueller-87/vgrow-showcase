"""Light timing changes real outputs: cover boundaries, overrides, acknowledgments and recovery."""
import asyncio
import copy
import json
from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config
from vgrow.alerts import Alerts
from vgrow.budget import CommandBudget
from vgrow.light_schedule import LightSchedule, ZONE, boundaries, settings
from vgrow.server import COOKIE, make_app


def at(text, fold=0):
    return datetime.fromisoformat(text).replace(tzinfo=ZONE, fold=fold).timestamp()


class Box:
    def __init__(self):
        self.snap = {"online": True, "mqtt": True, "shadow_age": 1,
                     "light": {"level": 0, "mode": 0, "in_plan": False, "in_plan_known": True}}
        self.calls = []
        self.ack = True

    def snapshot(self):
        return copy.deepcopy(self.snap)

    async def set_light(self, level):
        self.calls.append(level)
        if self.ack:
            self.snap["light"]["level"] = level


@pytest.fixture
def rig():
    clock, box = [at("2026-10-26T12:00:00")], Box()
    budget = CommandBudget(lambda: clock[0])
    return LightSchedule(box, budget, lambda: clock[0]), box, clock


async def enable(ctl, **changes):
    await ctl.configure({"enabled": True, "on_time": "08:00", "off_time": "20:00", "level": 50, **changes})


@pytest.mark.parametrize("change", [{"on_time": "25:01"}, {"enabled": "true"}, {"level": True}, {"level": 24},
    {"level": 101}, {"on_time": "20:00"}, {"off_time": None}, {"timezone": "UTC"}])
def test_invalid_configuration_is_rejected(change):
    with pytest.raises(ValueError):
        settings({"enabled": True, "on_time": "08:00", "off_time": "20:00", "level": 50, **change})


def test_default_is_read_only_and_disabled(rig):
    ctl, box, _ = rig
    asyncio.run(ctl.tick())
    assert ctl.view(box.snapshot())["status"] == "disabled"
    assert not box.calls and not ctl.path.exists()


def test_daily_on_and_off_are_once_only_and_not_limited_by_history(rig):
    ctl, box, clock = rig
    async def go():
        for _ in range(30):
            ctl.budget.record("watering", "port.usb1")
        await enable(ctl)
        await ctl.tick()
        await ctl.tick()
        for _ in range(5):
            await ctl.tick()
        assert box.calls == [50] and ctl.view(box.snapshot())["status"] == "scheduled"
        assert ctl.budget.counts()["auto_day"] == 31
        clock[0] = at("2026-10-26T20:00:00")
        await ctl.tick()
        await ctl.tick()
        assert box.calls == [50, 0]
        assert ctl.view(box.snapshot())["next_switch"]["at"] == at("2026-10-27T08:00:00")
        assert ctl.budget.counts()["safety_hour"] == 1
    asyncio.run(go())


def test_overnight_schedule_and_clock_changes_do_not_replay_edges(rig):
    ctl, box, clock = rig
    async def go():
        clock[0] = at("2026-10-26T23:00:00")
        await enable(ctl, on_time="20:00", off_time="08:00")
        await ctl.tick()
        await ctl.tick()
        assert box.calls == [50]
        clock[0] = at("2026-10-26T19:00:00")
        await ctl.tick()
        assert ctl.view(box.snapshot())["status"] == "clock" and box.calls == [50]
        recovered = LightSchedule(box, ctl.budget, lambda: clock[0])
        await recovered.tick()
        assert box.calls == [50]
        clock[0] = at("2026-10-27T08:00:00")
        await recovered.tick()
        assert box.calls == [50, 0]
    asyncio.run(go())


def test_manual_override_and_restart_yield_until_the_next_boundary(rig):
    ctl, box, clock = rig
    async def go():
        await enable(ctl)
        await ctl.tick()
        await ctl.tick()
        ctl.manual()
        box.snap["light"]["level"] = 70
        recovered = LightSchedule(box, ctl.budget, lambda: clock[0])
        await recovered.tick()
        assert recovered.view(box.snapshot())["status"] == "manual" and box.calls == [50]
        clock[0] = at("2026-10-26T20:00:00")
        await recovered.tick()
        assert box.calls == [50, 0]
    asyncio.run(go())


def test_a_change_reported_from_the_vendor_app_is_not_fought(rig):
    ctl, box, _ = rig
    async def go():
        await enable(ctl)
        await ctl.tick()
        await ctl.tick()
        box.snap["light"]["level"] = 0
        await ctl.tick()
        await ctl.tick()
        assert box.calls == [50] and ctl.view(box.snapshot())["status"] == "manual"
    asyncio.run(go())


def test_connection_and_vendor_plan_prevent_writes_then_reconcile_current_period(rig):
    ctl, box, clock = rig
    async def go():
        await enable(ctl)
        box.snap["mqtt"] = False
        await ctl.tick()
        assert not box.calls
        box.snap["mqtt"] = True
        box.snap["light"]["in_plan"] = True
        await ctl.tick()
        assert not box.calls and ctl.view(box.snapshot())["status"] == "mode"
        clock[0] = at("2026-10-26T21:00:00")
        box.snap["light"]["in_plan"] = False
        await ctl.tick()  # already off; the missed ON is not replayed
        assert not box.calls
        clock[0] = at("2026-10-27T08:00:00")
        await ctl.tick()
        assert box.calls == [50]
    asyncio.run(go())


def test_unknown_modes_and_stale_reports_never_enable_control(rig):
    ctl, box, _ = rig
    async def go():
        for changes in ({"mode": None}, {"in_plan_known": False}, {"in_plan": None}):
            before = dict(box.snap["light"])
            box.snap["light"].update(changes)
            with pytest.raises(Exception, match="manual mode"):
                await enable(ctl)
            box.snap["light"] = before
        box.snap["shadow_age"] = 301
        with pytest.raises(Exception, match="fresh device"):
            await enable(ctl)
        assert not box.calls
    asyncio.run(go())


def test_reconnect_and_restart_restore_the_current_period_without_manual_override(rig):
    ctl, box, clock = rig
    async def go():
        await enable(ctl)
        await ctl.tick()
        await ctl.tick()
        box.snap["mqtt"] = False
        await ctl.tick()
        box.snap["light"]["level"] = 0
        box.snap["mqtt"] = True
        await ctl.tick()
        await ctl.tick()
        assert box.calls == [50, 50]
        box.snap["light"]["level"] = 0
        recovered = LightSchedule(box, ctl.budget, lambda: clock[0])
        await recovered.tick()
        assert box.calls == [50, 50, 50]
    asyncio.run(go())


def test_enabled_timer_owns_the_light_even_with_the_brightness_autopilot_off():
    from test_autopilot import FakeBox, Rig
    class FreshBox(FakeBox):
        def snapshot(self):
            return {**super().snapshot(), "shadow_age": 1}
    rig = Rig(box=FreshBox(), lamp=True)
    ctl = LightSchedule(rig.box, rig.budget, lambda: rig.clock[0])
    asyncio.run(enable(ctl))
    rig.pilot.light_schedule = ctl
    rig.run(90)
    assert not [c for c in rig.box.sent if c[0] == "light"]
    assert rig.pilot.control_info(rig.box.snapshot(), "light", rig.clock[0])[1]["key"] == "ap.hold.light_schedule"
    rig.pilot.set_mode("off")
    rig.clock[0] = at("2026-10-26T20:00:00")
    asyncio.run(ctl.tick())
    assert rig.box.sent[-1] == ("light", 0)


def test_unconfirmed_switch_retries_once_and_never_retries_on_in_an_off_period(rig):
    ctl, box, clock = rig
    async def go():
        box.ack = False
        await enable(ctl)
        await ctl.tick()
        clock[0] += 29
        await ctl.tick()
        assert box.calls == [50]
        clock[0] += 1
        await ctl.tick()
        clock[0] += 30
        await ctl.tick()
        assert box.calls == [50, 50] and ctl.view(box.snapshot())["status"] == "unconfirmed"
        clock[0] = at("2026-10-26T20:00:00")
        await ctl.tick()
        assert box.calls == [50, 50]  # reported off already, never a late ON
    asyncio.run(go())


def test_disabled_timer_stops_retries_and_keeps_the_current_output(rig):
    ctl, box, _ = rig
    async def go():
        await enable(ctl)
        await ctl.tick()
        await ctl.configure({**ctl.cfg, "enabled": False})
        await ctl.tick()
        assert box.calls == [50] and box.snap["light"]["level"] == 50
    asyncio.run(go())


def test_owner_off_stops_the_timer_until_it_is_switched_on_again(rig):
    """Owner, 01.11.2026: off must not come back on with the next scheduled ON; switching on applies the period."""
    ctl, box, clock = rig
    async def go():
        await enable(ctl)
        await ctl.tick()
        ctl.stop()
        box.snap["light"]["level"] = 0
        assert ctl.view(box.snapshot())["status"] == "stopped" and not ctl.cfg["enabled"]
        for moment in ("2026-10-26T20:00:00", "2026-10-27T08:00:00", "2026-10-27T12:00:00"):
            clock[0] = at(moment)
            await ctl.tick()
        assert box.calls == [50]
        restored = LightSchedule(box, ctl.budget, lambda: clock[0])
        await restored.tick()
        assert box.calls == [50] and restored.view(box.snapshot())["status"] == "stopped"
        assert (restored.cfg["on_time"], restored.cfg["off_time"], restored.cfg["level"]) == ("08:00", "20:00", 50)
        await restored.configure({**restored.cfg, "enabled": True})
        await restored.tick()
        assert box.calls == [50, 50]
        await restored.tick()  # the box's report confirms the switch
        assert restored.view(box.snapshot())["status"] == "scheduled"
    asyncio.run(go())


def test_cockpit_off_stops_the_timer_and_another_level_only_holds_it():
    from vgrow.demo import DemoBox
    box = DemoBox()
    ctl = LightSchedule(box)
    ctl.cfg = settings({"enabled": True, "on_time": "00:00", "off_time": "23:59", "level": 60})
    async def go():
        async with TestClient(TestServer(make_app(box, Alerts(), "k", light_schedule=ctl))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            assert (await client.post("/api/cmd", json={"action": "light", "level": 40})).status == 200
            assert ctl.cfg["enabled"] and ctl.override_until is not None
            assert (await client.post("/api/cmd", json={"action": "light", "level": 0})).status == 200
            assert not ctl.cfg["enabled"] and ctl.override_until is None
            state = await (await client.get("/api/state")).json()
            assert state["light_schedule"]["status"] == "stopped"
            assert json.loads(ctl.path.read_text())["settings"]["enabled"] is False
    asyncio.run(go())


def test_corrupt_or_unwritable_storage_does_not_energize_light(rig, monkeypatch):
    ctl, box, _ = rig
    async def go():
        def broken(*_):
            raise OSError("disk full")
        with monkeypatch.context() as patch:
            patch.setattr(config, "atomic_write", broken)
            with pytest.raises(Exception, match="saved safely"):
                await enable(ctl)
            await ctl.tick()
            assert not box.calls
        ctl.path.parent.mkdir(parents=True, exist_ok=True)
        ctl.path.write_text("{bad")
        recovered = LightSchedule(box)
        assert recovered.cfg["enabled"] is False and recovered.error == "storage"
    asyncio.run(go())


def test_dst_uses_first_fold_and_skips_nonexistent_clock_times():
    cfg = {"on_time": "02:30", "off_time": "08:00", "level": 50}
    edge, nxt = boundaries(cfg, at("2026-10-25T02:15:00", fold=1))
    assert edge["at"] == at("2026-10-25T02:30:00", fold=0) and edge["on"] is True
    assert nxt["at"] == at("2026-10-25T08:00:00")
    edge, nxt = boundaries(cfg, at("2026-03-29T03:15:00"))
    assert edge["on"] is False and nxt["at"] == at("2026-03-29T08:00:00")


def test_timer_api_is_authenticated_and_reading_it_sends_nothing():
    from vgrow.demo import DemoBox
    box = DemoBox()
    ctl = LightSchedule(box)
    async def go():
        async with TestClient(TestServer(make_app(box, Alerts(), "k", light_schedule=ctl))) as client:
            assert (await client.post("/api/cmd", json={"action": "light_schedule_config"})).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            before = box.snapshot()["light"]
            assert (await (await client.get("/api/state")).json())["light_schedule"]["status"] == "disabled"
            assert (await (await client.get("/api/light")).json())["light_schedule"]["status"] == "disabled"
            assert box.snapshot()["light"] == before and not ctl.path.exists()
            response = await client.post("/api/cmd", json={"action": "light_schedule_config", "enabled": False,
                "on_time": "09:00", "off_time": "21:00", "level": 60})
            assert response.status == 200
            assert json.loads(ctl.path.read_text())["settings"]["level"] == 60
    asyncio.run(go())


def test_presets_are_valid_timer_settings_and_reach_the_page(rig):
    from vgrow.light_schedule import PRESETS
    ctl, box, _ = rig
    view = ctl.view(box.snapshot())
    assert [p["key"] for p in view["presets"]] == ["18-6", "20-4", "12-12"]
    for preset in view["presets"]:
        assert preset["sources"] and all(s["url"].startswith("https://") for s in preset["sources"])
        on = 22 * 60  # the page computes the off time from the on time, across midnight
        off = (on + preset["hours"] * 60) % 1440
        cfg = settings({"enabled": True, "on_time": "22:00", "off_time": f"{off // 60:02d}:{off % 60:02d}", "level": 100})
        asyncio.run(ctl.configure(cfg))
        assert ctl.view(box.snapshot())["hours_on"] == preset["hours"]
    view["presets"][0]["sources"].clear()
    assert PRESETS[0]["sources"], "the view hands out copies"
    assert not any(p["autoflower"] for p in PRESETS if p["key"] == "12-12"), "12/12 is the photoperiod flower switch"
