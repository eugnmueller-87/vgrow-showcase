"""Watering has real actuator consequences: verify deadlines, recovery, confirmations and once-only schedules."""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config
from vgrow.alerts import Alerts
from vgrow.box import CommandRefused
from vgrow.budget import CommandBudget
from vgrow.demo import DemoBox
from vgrow.hank import pump_report
from vgrow.portguard import PortGuard
from vgrow.server import COOKIE, HankBox, make_app
from vgrow.watering import WateringController, settings

BERLIN = ZoneInfo("Europe/Berlin")


def at(text, fold=0):
    return datetime.fromisoformat(text).replace(tzinfo=BERLIN, fold=fold).timestamp()


class Clock:
    def __init__(self):
        self.wall = at("2026-10-25T10:00:00")
        self.mono = 100.0

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class Box:
    def __init__(self):
        self.snap = {"online": True, "mqtt": True, "shadow_age": 1, "sensors_age": 20,
                     "climate": {"water_kind": "wet_dry", "water_state": "wet"}, "pending": {},
                     "ports": {"usb1": {"on": False, "mode": 0, "in_plan": 0, "accessory": 0, "can_write": True},
                               "usb2": {"on": True, "mode": 0, "in_plan": 0, "accessory": 0, "can_write": True}}}
        self.port_listeners = []
        self.calls = []
        self.ack_on = True
        self.ack_off = True
        self.refuse_on = False
        self.fail_on = False
        self.fail_off = False

    def snapshot(self):
        return copy.deepcopy(self.snap)

    def report(self, on):
        self.snap["ports"]["usb1"]["on"] = on
        for listener in self.port_listeners:
            listener(self, {"usb1": on}, None)

    async def set_port(self, port, on):
        assert port == "usb1"
        if on and self.refuse_on:
            raise CommandRefused("refused before publish")
        self.calls.append(on)
        if on and self.fail_on or not on and self.fail_off:
            raise OSError("publish result unknown")
        if on and self.ack_on or not on and self.ack_off:
            self.report(on)


@pytest.fixture
def rig():
    clock, box, guard = Clock(), Box(), PortGuard()
    guard.assign("usb1", "pump", box.snapshot())
    ctl = WateringController(box, guard, CommandBudget(lambda: clock.wall), clock=lambda: clock.wall,
                             monotonic=lambda: clock.mono)
    box.port_listeners.append(ctl.observe)
    return ctl, box, clock


async def configured(ctl, **kwargs):
    await ctl.configure({"duration_s": 10, "times": ["10:01"], "every_days": 1, "first_date": "2026-10-25",
                         "power_confirmed": True, **kwargs})


def test_defaults_never_energize_or_adopt_an_existing_powered_pump(rig):
    ctl, box, clock = rig
    async def go():
        box.report(True)
        for _ in range(4):
            clock.advance(3600)
            await ctl.tick()
        assert box.calls == []
        view = ctl.view(box.snapshot())
        assert view["status"] == "unmanaged" and view["settings"]["duration_s"] is None
        assert view["next_at"] is None
        with pytest.raises(CommandRefused):
            await ctl.start()
    asyncio.run(go())


def test_timer_stops_at_deadline_without_a_sensor_poll_and_records_confirmation(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl)
        await ctl.start()
        await ctl.tick()
        assert ctl.view(box.snapshot())["status"] == "running"
        clock.advance(9)
        await ctl.tick()
        assert box.calls == [True]
        clock.advance(1)
        await ctl.tick()
        assert box.calls == [True, False] and ctl.active is None
        assert ctl.events[-1]["kind"] == "stopped" and ctl.events[-1]["reason"] == "duration"
        assert ctl.budget.counts()["manual_hour"] == 1
        assert ctl.budget.counts()["safety_hour"] == 1
    asyncio.run(go())


def test_stale_false_state_is_not_a_confirmed_stop_and_no_overlap_is_possible(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl, duration_s=1)
        box.ack_on = box.ack_off = False
        await ctl.start()
        clock.advance(1)
        await ctl.tick()
        assert ctl.active["phase"] == "stopping"  # snapshot is still false from before ON
        with pytest.raises(CommandRefused):
            await ctl.start()
        clock.advance(29)
        await ctl.tick()
        assert box.calls == [True, False]
        clock.advance(1)
        await ctl.tick()
        assert box.calls == [True, False, False]
        box.report(False)
        await ctl.tick()
        assert ctl.active is None
    asyncio.run(go())


def test_new_true_report_cannot_confirm_off_and_off_publish_failure_is_retried(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl)
        await ctl.start()
        box.fail_off = True
        await ctl.stop()
        box.report(True)
        await ctl.tick()
        assert ctl.active["phase"] == "stopping"
        box.fail_off = False
        clock.advance(30)
        await ctl.tick()
        assert ctl.active is None and box.calls == [True, False, False]
    asyncio.run(go())


def test_restart_stops_an_interrupted_cycle_never_resumes_or_replays(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl, enabled=True)
        await ctl.start()
        recovered = WateringController(box, ctl.guard, ctl.budget, clock=lambda: clock.wall, monotonic=lambda: clock.mono)
        box.port_listeners.append(recovered.observe)
        assert recovered.active["reason"] == "restart"
        await recovered.tick()
        assert box.calls == [True, False] and recovered.active is None
        clock.advance(61)
        restarted = WateringController(box, ctl.guard, ctl.budget, clock=lambda: clock.wall, monotonic=lambda: clock.mono)
        await restarted.tick()
        assert box.calls == [True, False]  # 10:01 missed during downtime, no catch-up
    asyncio.run(go())


@pytest.mark.parametrize("change,reason", [
    ({"online": False}, "connection"), ({"mqtt": False}, "connection"), ({"shadow_age": 301}, "connection"),
    ({"shadow_age": float("nan")}, "connection"), ({"sensors_age": 301}, "supply"),
    ({"sensors_age": -1}, "supply"), ({"sensors_age": True}, "supply"), ({"sensors_age": float("inf")}, "supply"),
    ({"climate": {"water_kind": "wet_dry", "water_state": "dry"}}, "supply"),
    ({"climate": {"water_kind": "level", "water_level": float("inf")}}, "supply"),
    ({"climate": {"water_kind": "level", "water_level": 0}}, "supply"),
    ({"climate": {}}, "supply")])
def test_starts_require_connection_and_fresh_water_supply(rig, change, reason):
    ctl, box, _ = rig
    async def go():
        await configured(ctl)
        box.snap.update(change)
        assert ctl.blocked(box.snapshot()) == reason
        with pytest.raises(CommandRefused):
            await ctl.start()
        assert box.calls == []
    asyncio.run(go())


def test_role_plan_assignment_and_off_lock_veto_starts(rig, monkeypatch):
    ctl, box, _ = rig
    async def go():
        await configured(ctl)
        box.snap["ports"]["usb1"]["can_write"] = False
        assert ctl.blocked(box.snapshot()) == "port"
        box.snap["ports"]["usb1"]["can_write"] = True
        box.snap["ports"]["usb1"]["accessory"] = 99
        assert ctl.blocked(box.snapshot()) == "assignment"
        box.snap["ports"]["usb1"]["accessory"] = 0
        ctl.guard.assign("usb1", "other", box.snapshot())
        assert ctl.blocked(box.snapshot()) == "role"
        ctl.guard.assign("usb1", "pump", box.snapshot())
        monkeypatch.setattr("vgrow.watering.configured_device", lambda port: "humidifier")
        assert ctl.blocked(box.snapshot()) == "role"
        monkeypatch.setattr("vgrow.watering.configured_device", lambda port: "pump")
        def locked(port, on):
            raise CommandRefused("locked")
        monkeypatch.setattr("vgrow.watering.check_port_lock", locked)
        assert ctl.blocked(box.snapshot()) == "port"
        assert not box.calls
    asyncio.run(go())


def test_loss_of_water_stops_owned_cycle_even_with_auto_switch_off(rig):
    ctl, box, _ = rig
    async def go():
        await configured(ctl)
        await ctl.start()
        box.snap["climate"]["water_state"] = "dry"
        await ctl.tick()
        assert box.calls == [True, False] and ctl.active is None
        assert ctl.events[-1]["reason"] == "supply"
    asyncio.run(go())


def test_full_command_history_blocks_neither_scheduled_start_nor_timed_stop(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl, enabled=True)
        for _ in range(30):
            ctl.budget.record("autopilot", "dfan")
        clock.advance(60)
        await ctl.tick()
        assert box.calls == [True] and ctl.active is not None
        assert ctl.budget.counts()["auto_hour"] == 31
        clock.advance(10)
        await ctl.tick()
        assert box.calls == [True, False]
        assert ctl.budget.counts()["safety_hour"] == 1
    asyncio.run(go())


def test_scheduled_starts_share_budget_and_each_local_slot_runs_once(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl, enabled=True)
        clock.advance(60)
        await asyncio.gather(ctl.tick(), ctl.tick())
        assert box.calls == [True]
        assert ctl.budget.counts()["auto_hour"] == 1
        await ctl.tick()
        assert box.calls == [True]
        clock.advance(10)
        await ctl.tick()
        clock.wall -= 100
        await ctl.tick()
        clock.advance(110)
        await ctl.tick()
        assert box.calls == [True, False]
    asyncio.run(go())


def test_late_blocked_and_overlapping_slots_are_skipped_without_makeup(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl, enabled=True, duration_s=120, times=["10:01", "10:02", "10:05"])
        clock.advance(60)
        await ctl.tick()
        clock.advance(60)
        await ctl.tick()
        assert ctl.events[-1]["reason"] == "busy" and box.calls == [True]
        clock.advance(60)
        await ctl.tick()
        clock.advance(180)
        await ctl.tick()  # at 10:06, 10:05 is just within the grace window
        assert box.calls[-1] is True
        clock.advance(120)
        await ctl.tick()
        clock.advance(86400 + 400)
        await ctl.tick()
        assert ctl.events[-1]["reason"] == "missed"
    asyncio.run(go())


def test_off_at_monotonic_deadline_even_when_wall_clock_moves_backward(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl)
        await ctl.start()
        clock.wall -= 3600
        clock.mono += 10
        await ctl.tick()
        assert box.calls == [True, False]
    asyncio.run(go())


def test_never_retry_on_after_an_ambiguous_publish_failure(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl)
        box.fail_on = True
        with pytest.raises(CommandRefused):
            await ctl.start()
        clock.advance(30)
        await ctl.tick()
        assert box.calls == [True, False] and ctl.active is None
    asyncio.run(go())


def test_refused_publish_clears_intent_without_spurious_off(rig):
    ctl, box, _ = rig
    async def go():
        await configured(ctl)
        box.refuse_on = True
        with pytest.raises(CommandRefused):
            await ctl.start()
        assert ctl.active is None and not box.calls
        assert ctl.budget.counts()["all_day"] == 0
    asyncio.run(go())


def test_durable_intent_precedes_on_and_storage_failure_cannot_block_off(rig, monkeypatch):
    ctl, box, _ = rig
    async def go():
        await configured(ctl)
        original = config.atomic_write
        def broken(path, text):
            if path == ctl.path:
                raise OSError("disk full")
            original(path, text)
        monkeypatch.setattr(config, "atomic_write", broken)
        with pytest.raises(CommandRefused):
            await ctl.start()
        assert not box.calls
        box.report(True)
        await ctl.stop()
        assert box.calls == [False]
    asyncio.run(go())


def test_readable_intent_exists_at_publish_time(rig):
    ctl, box, _ = rig
    async def go():
        await configured(ctl)
        original = box.set_port
        async def checked(port, on):
            data = json.loads(ctl.path.read_text())
            assert data["active"]["phase"] == ("starting" if on else "stopping")
            await original(port, on)
        box.set_port = checked
        await ctl.start()
        await ctl.stop()
    asyncio.run(go())


def test_corrupt_record_disables_starts_and_does_not_claim_a_new_timer(rig):
    ctl, box, clock = rig
    for content in ("{bad", "[]", '{"settings": [], "events": [], "cursor": 1}',
                    '{"settings": {}, "events": [{"kind": "start"}], "cursor": 1}'):
        ctl.path.parent.mkdir(parents=True, exist_ok=True)
        ctl.path.write_text(content)
        recovered = WateringController(box, ctl.guard, clock=lambda: clock.wall)
        assert recovered.error == "storage" and not recovered.cfg["enabled"]
        assert not box.calls


@pytest.mark.parametrize("bad", [{"duration_s": 0}, {"duration_s": True}, {"duration_s": 3601},
                                  {"duration_s": 1.5}, {"enabled": "false"}, {"power_confirmed": "true"},
                                  {"every_days": 0}, {"every_days": True}, {"times": ["24:00"]},
                                  {"times": ["12:00", "12:00"]}, {"times": "12:00"}, {"first_date": "2026-02-30"},
                                  {"enabled": True}, {"timezone": "UTC"}, {"first_date": "9999-01-01"}])
def test_invalid_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        settings(bad)


def test_saving_disabled_settings_does_not_start_but_enable_requires_an_idle_pump(rig):
    ctl, box, _ = rig
    async def go():
        box.report(True)
        await configured(ctl)
        assert not box.calls
        with pytest.raises(CommandRefused):
            await configured(ctl, enabled=True)
        assert not ctl.cfg["enabled"]
        await ctl.stop()  # owner's explicit stop, also available without configured dose
        await configured(ctl, enabled=True)
        assert box.calls == [False] and ctl.cfg["enabled"]
    asyncio.run(go())


def test_disabling_and_shutdown_stop_owned_cycles_but_not_unmanaged_power(rig):
    ctl, box, _ = rig
    async def go():
        await configured(ctl, enabled=True)
        await ctl.start()
        await ctl.configure({**ctl.cfg, "enabled": False})
        assert box.calls == [True, False]
        await ctl.start()
        await ctl.close()
        assert box.calls == [True, False, True, False]
        box.report(True)
        await ctl.close()
        assert box.calls == [True, False, True, False]
    asyncio.run(go())


def test_concurrent_owner_requests_cannot_start_two_cycles(rig):
    ctl, box, _ = rig
    async def go():
        await configured(ctl)
        results = await asyncio.gather(ctl.start(), ctl.start(), return_exceptions=True)
        assert sum(isinstance(r, CommandRefused) for r in results) == 1
        assert box.calls == [True]
    asyncio.run(go())


def test_dst_slots_and_every_other_day_use_local_dates(rig):
    ctl, _, _ = rig
    ctl.cfg = settings({"duration_s": 10, "times": ["02:30"], "first_date": "2026-10-25", "every_days": 2})
    slots = ctl.slots(at("2026-10-25T00:00"), at("2026-10-28T00:00"))
    assert [name for _, name in slots] == ["2026-10-25 02:30", "2026-10-27 02:30"]
    assert slots[0][0] == at("2026-10-25T02:30", fold=0)
    ctl.cfg = settings({"duration_s": 10, "times": ["02:30"], "first_date": "2026-03-28"})
    slots = ctl.slots(at("2026-03-28T00:00"), at("2026-03-31T00:00"))
    assert [name for _, name in slots] == ["2026-03-28 02:30", "2026-03-30 02:30"]


def test_watchdog_runs_without_poll_listeners(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl, duration_s=1)
        await ctl.start()
        clock.advance(1)
        ctl.launch()
        await asyncio.sleep(0.01)
        assert box.calls == [True, False]
        await ctl.close()
        assert ctl.task is None and ctl.observe not in box.port_listeners
    asyncio.run(go())


def test_unconfirmed_stop_sends_one_notice_and_retries_with_backoff(rig):
    ctl, box, clock = rig
    class Alerts:
        def __init__(self):
            self.calls = []
        async def push(self, *args, **kwargs):
            self.calls.append((args, kwargs))
    alerts = Alerts()
    ctl.alerts = alerts
    async def go():
        await configured(ctl)
        await ctl.start()
        box.ack_off = False
        await ctl.stop()
        assert not alerts.calls
        clock.advance(30)
        await ctl.tick()
        await asyncio.sleep(0)
        assert len(alerts.calls) == 1
        assert box.calls == [True, False, False]
        clock.advance(30)
        await ctl.tick()
        assert box.calls == [True, False, False]  # second retry waits 60 seconds
        clock.advance(30)
        await ctl.tick()
        assert box.calls == [True, False, False, False]
        assert len(alerts.calls) == 1
    asyncio.run(go())


def test_failed_restart_stop_remains_durable_for_the_following_restart(rig):
    ctl, box, clock = rig
    async def go():
        await configured(ctl)
        await ctl.start()
        box.fail_off = True
        await ctl.close()
        assert json.loads(ctl.path.read_text())["active"]["phase"] == "stopping"
        recovered = WateringController(box, ctl.guard, ctl.budget, clock=lambda: clock.wall, monotonic=lambda: clock.mono)
        box.port_listeners.append(recovered.observe)
        await recovered.tick()
        assert recovered.active is not None
        box.fail_off = False
        clock.advance(30)
        await recovered.tick()
        assert recovered.active is None
        assert box.calls.count(True) == 1
    asyncio.run(go())


def test_api_uses_bounded_cycles_for_both_watering_and_generic_usb1_controls(rig):
    ctl, fake, _ = rig
    async def go():
        box = DemoBox()
        # A real normalized snapshot is needed by the rest of /api/state.
        ctl.box = box
        ctl.guard.assign("usb1", "pump", box.snapshot())
        client = TestClient(TestServer(make_app(box, Alerts(), "k", watering=ctl)))
        await client.start_server()
        try:
            assert (await client.post("/api/cmd", json={"action": "watering_start"})).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            assert (await client.post("/api/cmd", json={"action": "port", "port": "usb1", "on": True})).status == 409
            assert box.ports["usb1"] is False
            await configured(ctl)
            assert (await client.post("/api/cmd", json={"action": "port", "port": "usb1", "on": True})).status == 200
            assert ctl.active is not None and box.ports["usb1"] is True
            view = await (await client.get("/api/state")).json()
            assert view["watering"]["active"] and view["watering"]["remaining_s"] == 10
            assert (await client.post("/api/cmd", json={"action": "watering_stop"})).status == 200
            assert ctl.active is None and box.ports["usb1"] is False
            assert (await client.post("/api/cmd", json={"action": "watering_config", "duration_s": -1})).status == 400
            assert (await client.post("/api/cmd", json={"action": "port", "port": "usb1", "on": "false"})).status == 400
        finally:
            await client.close()
    asyncio.run(go())


@pytest.mark.parametrize("lang", ["de", "en"])
def test_hank_reads_cycle_and_next_schedule_without_actuator_access(rig, lang):
    ctl, box, _ = rig
    pump_report.register()
    async def go():
        await configured(ctl, enabled=True)
        snap = HankBox(box, None, watering=ctl).snapshot()
        answer = pump_report.answer({"snapshot": snap}, lang, answer_id="a")
        assert "2026-10-25 10:01" in answer.text and "10" in answer.text
        assert not answer.proposals
        assert pump_report.asks("When is the next watering cycle?")
        assert pump_report.asks("Wann ist der nächste Gießzyklus?")
        await ctl.start()
        answer = pump_report.answer({"snapshot": HankBox(box, None, watering=ctl).snapshot()}, lang, answer_id="b")
        assert "10" in answer.text and not answer.proposals
    asyncio.run(go())
