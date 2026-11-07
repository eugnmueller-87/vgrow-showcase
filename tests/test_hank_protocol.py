"""Persistent observations, not a second controller: confirmations and measured responses stay separate."""
import asyncio
import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, server
from vgrow.alerts import Alerts
from vgrow.box import DFAN_LEVEL_MAP
from vgrow.demo import DemoBox
from vgrow.hank_protocol import Protocol, checks, stamp


@pytest.fixture
def rig():
    clock = [datetime(2026, 10, 28, 17, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()]
    data = {"context": "same-plant-same-stage", "snapshot": {"online": True, "mqtt": True, "shadow_age": 0,
        "sensors_age": 0, "light": {"level": 100}, "dfan": {"step": 8}, "cfan": {"step": 10},
        "ports": {"usb2": {"on": False}}, "pending": {}},
        "plant": {"checks": [{"metric": "probe_humidity", "unit": "%", "actual": 67, "min": 45, "max": 60, "status": "high"}]},
        "pilot": {"devices": [{"id": "dfan", "value": 8, "control_state": "ready", "status": "On target",
                                "reason_key": "ap.hold.on_target"}], "limits": {"dfan": [4, 8]}, "log": []}}
    ctl = Protocol(lambda: clock[0])
    ctl.observe(data)
    return ctl, data, clock


def task(ctl):
    return ctl.view()["tasks"][0]


def command(rig, target=9):
    ctl, data, _ = rig
    after = copy.deepcopy(data)
    after["snapshot"]["pending"] = {"dFan.level": {"status": "pending", "target": DFAN_LEVEL_MAP[target]}}
    ctl.capture({"action": "dfan", "step": target, "proposal": "p-real"}, 200, data, after, "hank")
    data.update(after)


def confirm(rig, target=9):
    ctl, data, clock = rig
    clock[0] += 1
    data["snapshot"].update(shadow_age=0, sensors_age=1, dfan={"step": target}, pending={
        "dFan.level": {"status": "ok", "target": DFAN_LEVEL_MAP[target]}})
    ctl.observe(data)


def reading(rig, value, status):
    ctl, data, clock = rig
    clock[0] += 90
    data["snapshot"]["sensors_age"] = 1
    data["plant"]["checks"][0].update(actual=value, status=status)
    ctl.observe(data)


def test_issue_records_the_actual_limit_and_deduplicates_unchanged_decisions(rig):
    ctl, data, clock = rig
    view = ctl.view()
    issue = view["issues"][0]
    assert issue["current"] == issue["baseline"] == 67 and view["totals"]["blocked"] == 1
    assert issue["decisions"][0]["code"] == "limit" and issue["decisions"][0]["limit"] == 8
    before = len(issue["events"])
    clock[0] += 3
    ctl.observe(data)
    assert len(ctl.view()["issues"][0]["events"]) == before
    device = data["pilot"]["devices"][0]
    device.update(control_state="paused", paused_until=clock[0] + 3600, reason_key="ap.hold.paused")
    ctl.observe(data)
    issue = ctl.view()["issues"][0]
    assert issue["decisions"][0]["code"] == "ap.hold.paused" and len(issue["events"]) == before + 1


def test_http_device_confirmation_and_later_sensor_response_are_different(rig):
    ctl, data, clock = rig
    command(rig)
    assert task(ctl)["status"] == "requested"
    confirm(rig)
    row = task(ctl)
    assert row["origin"] == "hank" and row["before"] == 8 and row["target"] == 9
    assert row["status"] == "confirmed" and row["response"] == "waiting"
    assert ctl.view()["issues"], "device acknowledgment does not resolve a humidity problem"
    clock[0] += 60
    data["snapshot"]["sensors_age"] = 80
    ctl.observe(data)
    assert task(ctl)["response"] == "waiting", "cached pre-command measurements are not evidence of improvement"
    reading(rig, 65, "high")
    assert task(ctl)["response"] == "improving"
    assert task(ctl)["observations"][0]["delta"] == -2
    reading(rig, 55, "ok")
    assert task(ctl)["response"] == "in_range" and ctl.view()["issues"] == []
    restored = Protocol(lambda: clock[0])
    assert task(restored)["status"] == "confirmed" and task(restored)["response"] == "in_range"
    assert [e["kind"] for e in task(restored)["events"]] == ["requested", "confirmed", "improving", "in_range"]


@pytest.mark.parametrize("condition,expected", [("rejected", "refused"), ("timeout", "unconfirmed"), ("offline", "unconfirmed")])
def test_failed_or_missing_confirmation_never_becomes_a_success(rig, condition, expected):
    ctl, data, clock = rig
    command(rig)
    if condition == "offline":
        data["snapshot"]["online"] = False
    else:
        data["snapshot"]["pending"]["dFan.level"]["status"] = condition
    clock[0] += 21
    ctl.observe(data)
    assert task(ctl)["status"] == expected and task(ctl)["confirmed_at"] is None
    assert ctl.view()["issues"]


def test_stale_or_missing_readings_keep_the_problem_open(rig):
    ctl, data, _ = rig
    data["snapshot"]["sensors_age"] = 900
    data["plant"]["checks"][0].update(actual=55, status="ok")
    ctl.observe(data)
    assert not ctl.view()["fresh"] and ctl.view()["issues"][0]["current"] == 67
    data["snapshot"]["sensors_age"] = 1
    data["plant"]["checks"][0]["actual"] = None
    ctl.observe(data)
    assert ctl.view()["issues"][0]["decisions"][0]["code"] == "missing"


def test_opposite_problem_and_phase_change_are_not_reported_as_fixed(rig):
    ctl, data, _ = rig
    command(rig)
    confirm(rig)
    old = ctl.view()["issues"][0]["id"]
    reading(rig, 40, "low")
    assert task(ctl)["response"] == "overshot"
    assert ctl.view()["issues"][0]["id"] != old and ctl.view()["issues"][0]["direction"] == "low"
    with ctl.connect() as db:
        prior = json.loads(db.execute("SELECT data FROM records WHERE id=?", (old,)).fetchone()[0])
        assert prior["state"] == "ended"
    data["context"] = "new-plant"
    ctl.observe(data)
    assert task(ctl)["response"] == "superseded" and not task(ctl)["monitoring"]


def test_later_command_ends_attribution_to_the_previous_one(rig):
    ctl, _, _ = rig
    command(rig)
    confirm(rig)
    first = task(ctl)["id"]
    command(rig, 10)
    older = next(t for t in ctl.view()["tasks"] if t["id"] == first)
    assert older["status"] == "confirmed" and older["response"] == "superseded" and not older["monitoring"]


def test_no_clear_improvement_and_scheduled_darkness_are_reported_honestly(rig):
    ctl, data, clock = rig
    command(rig)
    confirm(rig)
    clock[0] += 1800
    data["snapshot"]["sensors_age"] = 1
    ctl.observe(data)
    assert task(ctl)["response"] == "not_improved"
    data["plant"]["checks"].append({"metric": "light_percent", "actual": 0, "min": 100, "max": 100, "status": "low", "unit": "%"})
    data["snapshot"]["light"]["level"] = 0
    data["light_schedule"] = {"settings": {"enabled": True}, "expected_level": 0, "status": "scheduled"}
    assert not any(r["metric"] == "light_percent" for r in checks(data))
    ctl.observe(data)
    assert {i["metric"] for i in ctl.view()["issues"]} == {"probe_humidity"}


def test_historical_hank_record_keeps_baseline_but_does_not_invent_confirmation(rig):
    ctl, data, clock = rig
    path = config.STATE_DIR / "hank"
    path.mkdir(parents=True)
    row = {"time": "2026-10-28T16:52:55", "origin": "hank_accepted", "body": {"action": "dfan", "step": 9}, "proposal": "p-old", "ok": True}
    (path / "actions.jsonl").write_text(json.dumps(row) + "\n")
    (path / "ledger.json").write_text(json.dumps({"items": [{"metric": "probe_humidity", "baseline": {"value": 66.91, "dfan": 8}, "recommendation": {"proposal_id": "p-old"}}]}))
    with ctl.connect() as db:
        db.execute("DELETE FROM meta WHERE key='imported'")
    data["snapshot"]["dfan"]["step"] = 9
    ctl.observe(data)
    row = task(ctl)
    assert row["before"] == 8 and row["historical_baseline"]["value"] == 66.91
    assert row["historical"] and row["status"] == "unrecorded" and row["confirmed_at"] is None
    assert not row["issues"], "past actions cannot be attributed to issues first detected during import"
    ctl.observe(data)
    assert len(ctl.view()["tasks"]) == 1
    assert stamp("2026-10-28T16:52:55") < clock[0]


def test_upgrade_removes_unsupported_historical_links_but_keeps_the_action(rig):
    ctl, data, clock = rig
    issue = ctl.view()["issues"][0]
    with ctl.connect() as db:
        old = ctl.create_task(db, ident="old", at=clock[0] - 3600, origin="hank", device="dfan", before=8,
                              target=9, historical=True, status="unrecorded")
        old["issues"] = [issue["id"]]  # reproduce an early protocol record
        ctl.save(db, "task", old, 0)
        ctl.event(db, issue, "task", task="old", origin="hank")
        db.execute("DELETE FROM meta WHERE key='historical_links_v2'")
    ctl.observe(data)
    assert task(ctl)["id"] == "old" and task(ctl)["before"] == 8 and not task(ctl)["issues"]
    assert not any(e.get("task") == "old" for e in ctl.view()["issues"][0]["events"])


@pytest.mark.parametrize("new_report", [True, False])
def test_fast_ack_during_http_handler_counts_but_an_old_matching_value_does_not(rig, new_report):
    ctl, data, clock = rig
    before = copy.deepcopy(data)
    before["captured_at"] = clock[0]
    clock[0] += .8  # the ordinary handler awaited a publish before returning
    data["snapshot"].update(dfan={"step": 9}, shadow_age=.2 if new_report else 30)
    ctl.capture({"action": "dfan", "step": 9}, 200, before, data)
    assert task(ctl)["status"] == ("confirmed" if new_report else "requested")
    assert task(ctl)["at"] == before["captured_at"]


def test_missing_baseline_does_not_invent_a_response_or_break_the_observer(rig):
    ctl, data, _ = rig
    data["plant"]["checks"][0]["actual"] = None
    command(rig)
    confirm(rig)
    reading(rig, 55, "ok")
    assert task(ctl)["status"] == "confirmed" and task(ctl)["response"] == "unrecorded"
    assert not task(ctl)["baseline"]


def test_observer_queue_delay_does_not_make_a_pre_command_sample_new(rig):
    ctl, data, clock = rig
    command(rig)
    confirm(rig)
    data["captured_at"] = clock[0]
    data["snapshot"]["sensors_age"] = 2
    clock[0] += 30
    ctl.observe(data)
    assert task(ctl)["response"] == "waiting"


def test_fault_and_unmanaged_pump_are_visible_and_offline_does_not_claim_only_a_limit(rig):
    ctl, data, _ = rig
    data.update(automation={"implausible": True}, watering={"reported_on": True, "status": "unmanaged"})
    ctl.observe(data)
    issues = {i["metric"]: i for i in ctl.view()["issues"]}
    assert issues["pump_control"]["decisions"][0]["code"] == "pump_setup"
    assert "humidifier_response" in issues
    data["snapshot"]["online"] = False
    ctl.observe(data)
    assert all(d["code"] == "connection" for i in ctl.view()["issues"] for d in i["decisions"])


def test_watering_stop_report_updates_the_direct_command_without_a_duplicate(rig):
    ctl, data, clock = rig
    data["snapshot"]["ports"]["usb1"] = {"on": True}
    before = copy.deepcopy(data)
    data["snapshot"]["pending"] = {"port.usb1": {"target": False, "status": "pending"}}
    ctl.capture({"action": "watering_stop"}, 200, before, data)
    assert task(ctl)["status"] == "requested"
    clock[0] += 8
    data["snapshot"].update(shadow_age=0, ports={"usb1": {"on": False}}, pending={})
    data["watering"] = {"events": [{"kind": "stopped", "at": clock[0], "reason": "manual"}]}
    ctl.observe(data)
    assert len(ctl.view()["tasks"]) == 1
    assert task(ctl)["status"] == "confirmed" and task(ctl)["confirmed_at"] == clock[0]


def test_timer_confirmations_and_skipped_watering_are_distinct(rig):
    ctl, data, clock = rig
    clock[0] += 1
    data["watering"] = {"events": [{"kind": "start", "at": clock[0]}]}
    data["snapshot"]["ports"]["usb1"] = {"on": True}
    ctl.observe(data)
    assert task(ctl)["origin"] == "watering" and task(ctl)["status"] == "confirmed"
    clock[0] += 10
    data["watering"]["events"].append({"kind": "stopped", "at": clock[0], "reason": "duration"})
    data["snapshot"]["ports"]["usb1"] = {"on": False}
    ctl.observe(data)
    assert task(ctl)["status"] == "confirmed" and task(ctl)["confirmed_at"] == clock[0]
    clock[0] += 1
    data["watering"]["events"].append({"kind": "skipped", "at": clock[0], "reason": "busy"})
    ctl.observe(data)
    assert task(ctl)["status"] == "skipped" and task(ctl)["response"] == "not_executed"
    assert not task(ctl)["monitoring"] and "already on" in task(ctl)["reason"]["en"]
    assert len(ctl.view()["tasks"]) == 3
    data["pilot"]["log"] = [{"id": "failed", "at": clock[0] + 1, "kind": "failed", "device": "dfan", "status": "failed", "to": 9}]
    clock[0] += 2
    ctl.observe(data)
    assert task(ctl)["status"] == "unconfirmed" and task(ctl)["response"] == "unrecorded"
    assert not task(ctl)["monitoring"]


def test_corrupt_protocol_storage_is_visible_but_does_not_block_the_device_api():
    ctl = Protocol()
    ctl.path.parent.mkdir(parents=True, exist_ok=True)
    ctl.path.write_bytes(b"broken database")
    async def go():
        box = DemoBox()
        async with TestClient(TestServer(server.make_app(box, Alerts(), "k"))) as client:
            client.session.cookie_jar.update_cookies({server.COOKIE: "k"})
            assert (await client.get("/api/protocol")).status == 503
            assert (await client.post("/api/cmd", json={"action": "dfan", "step": 9})).status == 200
            assert box.snapshot()["dfan"]["step"] == 9
    asyncio.run(go())


def test_automatic_tasks_and_pagination_survive_restart_without_duplicates(rig):
    ctl, data, clock = rig
    clock[0] += 1
    data["pilot"]["log"] = [{"id": "real-step", "at": clock[0], "kind": "step", "device": "dfan", "from": 8, "to": 9, "status": "ok", "text": "Too humid"}]
    data["snapshot"]["dfan"]["step"] = 9
    ctl.observe(data)
    assert task(ctl)["origin"] == "automatic" and task(ctl)["status"] == "confirmed"
    for step in (9, 10):
        ctl.capture({"action": "dfan", "step": step}, 409, data, data, error="refused")
    restored = Protocol(lambda: clock[0])
    restored.observe(data)
    pages, cursor = [], None
    while True:
        page = restored.view(limit=1, before=cursor)
        pages += [t["id"] for t in page["tasks"]]
        cursor = page["next"]
        if not cursor:
            break
    assert len(pages) == len(set(pages)) == 3


def test_authenticated_protocol_api_and_recording_never_send_extra_commands(monkeypatch):
    box = DemoBox()
    sent = []
    original = box.set_dfan
    async def send(step):
        sent.append(step)
        await original(step)
    box.set_dfan = send
    async def go():
        async with TestClient(TestServer(server.make_app(box, Alerts(), "k"))) as client:
            assert (await client.get("/api/protocol")).status == 403
            client.session.cookie_jar.update_cookies({server.COOKIE: "k"})
            assert (await client.get("/api/protocol")).status == 200
            assert not sent
            assert (await client.get("/api/protocol?before=bad")).status == 400
            assert (await client.get("/api/protocol", headers={"X-Workspace-Box": "other"})).status == 409
            assert (await client.post("/api/cmd", json={"action": "dfan", "step": 9})).status == 200
            data = await (await client.get("/api/protocol")).json()
            assert sent == [9] and data["tasks"][0]["target"] == 9
            assert data["tasks"][0]["origin"] == "manual"
            def broken(*_):
                raise OSError("disk full")
            monkeypatch.setattr(Protocol, "capture", broken)
            assert (await client.post("/api/cmd", json={"action": "dfan", "step": 8})).status == 200
            assert sent == [9, 8], "protocol failures must not repeat or block device commands"
    asyncio.run(go())
