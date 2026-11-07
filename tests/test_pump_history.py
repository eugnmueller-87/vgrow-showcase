"""Actual reported pump states: app switching, short runs, persistence, local days and Hank's exact question."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import measurements
from vgrow.alerts import Alerts
from vgrow.box import Box
from vgrow.hank import context, core, pump_report
from vgrow.hank.router import Router
from vgrow.measurements import MeasurementStore
from vgrow.portguard import PortGuard
from vgrow.pump_history import PumpHistory
from vgrow.server import COOKIE, HankBox, make_app

try:
    BERLIN = ZoneInfo("Europe/Berlin")
except ZoneInfoNotFoundError:
    BERLIN = None  # Windows can use its own Berlin zone without an IANA database, like the live store.


def at(hour, minute=0, second=0, *, day=24, month=10, fold=0):
    return datetime(2026, month, day, hour, minute, second, tzinfo=BERLIN, fold=fold).timestamp()


def snapshot(on=False):
    return {"online": True, "mqtt": True, "ports": {
        "usb1": {"on": on, "mode": 0, "accessory": 0},
        "usb2": {"on": True, "mode": 0, "accessory": 0}}}


def setup(monkeypatch, now=None):
    clock = [at(14) if now is None else now]
    monkeypatch.setattr("vgrow.portguard.time.time", lambda: clock[0] - 3600)
    guard = PortGuard()
    guard.assign("usb1", "pump", snapshot())
    guard.assign("usb2", "humidifier", snapshot())
    store = MeasurementStore(tz=BERLIN, clock=lambda: clock[0])
    return PumpHistory(store, guard, clock=lambda: clock[0]), store, clock


def observation(at, on, port="usb1", device="pump", source="shadow"):
    return {"at": at, "port": port, "on": on, "device": device, "source": source}


def test_a_short_reported_run_survives_between_polls_and_restart(monkeypatch):
    reader, store, clock = setup(monkeypatch)
    store.add_port_observations([observation(at(13, 5), False), observation(at(13, 5, 12), True),
                                 observation(at(13, 5, 17), False)], "dev1")
    reopened = PumpHistory(MeasurementStore(tz=BERLIN), reader.guard, clock=lambda: clock[0])
    report = reopened.brief(snapshot())
    assert report["observed_on"] and report["current_on"] is False
    assert len(report["runs"]) == 1 and report["runs"][0]["off_seen"] == at(13, 5, 17)
    answer = pump_report.answer({"snapshot": {"pump_history": report}}, "de", answer_id="a")
    assert "Ja, die Pumpe war heute laut Box eingeschaltet." in answer.text
    assert "13:05:12" in answer.text and "13:05:17" in answer.text and "Wassermenge" in answer.text
    assert not answer.proposals and not answer.gap


def test_no_on_sample_is_never_presented_as_proof_that_no_watering_happened(monkeypatch):
    reader, store, _ = setup(monkeypatch)
    store.add_port_observations([observation(at(13), False), observation(at(13, 2), False)])
    report = reader.brief(snapshot())
    assert report["observed_on"] is False and report["complete"] is False
    answer = pump_report.answer({"snapshot": {"pump_history": report}}, "de", answer_id="a")
    assert "kein Pumpenlauf erfasst" in answer.text and "nicht lückenlos" in answer.text
    assert "nicht gegossen" not in answer.text and not answer.gap


def test_only_current_day_in_berlin_and_not_yesterdays_on_state(monkeypatch):
    reader, store, _ = setup(monkeypatch, at(0, 10))
    store.add_port_observations([observation(at(23, 59, day=23), True), observation(at(0, 1), False)])
    report = reader.brief(snapshot())
    assert report["date"] == "2026-10-24" and not report["observed_on"]
    store.add_port_observations([observation(at(0, 2), True)])
    assert reader.brief(snapshot())["observed_on"]


def test_both_repeated_hours_on_the_dst_day_are_kept_with_their_offsets(monkeypatch):
    if at(2, 10, day=25, fold=0) == at(2, 10, day=25, fold=1):
        pytest.skip("neither an IANA Berlin zone nor a Berlin machine zone")
    reader, store, _ = setup(monkeypatch, at(4, day=25))
    store.add_port_observations([observation(at(2, 10, day=25, fold=0), True),
                                 observation(at(2, 11, day=25, fold=0), False),
                                 observation(at(2, 10, day=25, fold=1), True),
                                 observation(at(2, 11, day=25, fold=1), False)])
    runs = reader.brief(snapshot())["runs"]
    assert len(runs) == 2
    assert runs[0]["first_on_local"].endswith("+02:00")
    assert runs[1]["first_on_local"].endswith("+01:00")


def test_humidifier_on_does_not_count_as_pump_activity(monkeypatch):
    reader, store, _ = setup(monkeypatch)
    store.add_port_observations([observation(at(13, 5), True, "usb2", "humidifier")])
    assert not reader.brief(snapshot())["observed_on"]


def test_legacy_on_state_before_confirmed_identity_stays_unidentified(monkeypatch):
    reader, store, _ = setup(monkeypatch)
    store.add([{"ts": at(12), "ports": json.dumps({"usb1": 1})}], measurements.SOURCE_POLL)
    report = reader.brief(snapshot())
    assert not report["observed_on"] and report["unidentified_on"] == 1
    store.add([{"ts": at(13, 1), "ports": json.dumps({"usb1": 1})}], measurements.SOURCE_POLL)
    assert reader.brief(snapshot())["observed_on"]


def test_empty_store_and_offline_current_state_stay_unknown(monkeypatch):
    reader, store, _ = setup(monkeypatch)
    snap = snapshot(True)
    snap["mqtt"] = False
    report = reader.brief(snap)
    assert report["available"] is False and report["current_on"] is None and not report["observed_on"]
    assert not store.path().exists()
    assert reader.brief(snapshot(True))["observed_on"]  # a live on report needs no guessed start time


def test_a_read_failure_never_becomes_a_claim_that_the_pump_did_not_run(monkeypatch):
    reader, store, _ = setup(monkeypatch)
    def broken(*args):
        raise sqlite3.OperationalError("disk error")
    monkeypatch.setattr(store, "port_observations", broken)
    report = reader.brief(snapshot())
    assert not report["available"] and report["recording_error"]
    reply = pump_report.answer({"snapshot": {"pump_history": report}}, "de", answer_id="a")
    assert "keinen lesbaren Pumpenverlauf" in reply.text and "nicht gegossen" not in reply.text


def test_schema_upgrade_preserves_existing_minute_rows(monkeypatch):
    _, store, _ = setup(monkeypatch)
    store.add([{"ts": at(12), "ports": '{"usb1":0}'}], measurements.SOURCE_POLL)
    with sqlite3.connect(store.path()) as con:
        con.execute("DROP TABLE port_observations")
        con.execute("PRAGMA user_version = 2")
    upgraded = MeasurementStore(tz=BERLIN)
    upgraded.add_port_observations([observation(at(13), True)])
    assert upgraded.legacy_port_observations(at(0), at(14))[0]["state"] == 0
    assert upgraded.port_observations(at(0), at(14))[0]["state"] == 1


def test_port_observations_follow_the_device_database(monkeypatch):
    _, store, _ = setup(monkeypatch)
    store.add_port_observations([observation(at(13), True)], "first-box")
    store.add_port_observations([observation(at(13), False)], "second-box")
    assert store.port_observations(at(0), at(14))[0]["state"] == 0
    assert store.file_for() != store.path()
    store.device_id = "first-box"
    assert store.port_observations(at(0), at(14))[0]["state"] == 1


def test_mqtt_observer_records_actual_reports_and_never_desired_or_failed_commands(monkeypatch):
    reader, store, clock = setup(monkeypatch)

    async def go():
        box = Box("", "", remember=False)
        box._mqtt = SimpleNamespace(is_connected=True)
        box.device = SimpleNamespace(device_id="test-box", name="Box", online=True,
                                     client_id="vivosun-VSCTLY18-acct-test-box")
        reader.start(box)
        def message(doc):
            box._on_message("thing/update/accepted", json.dumps(doc).encode(), 1)
        try:
            monkeypatch.setattr("vgrow.box.time.time", lambda: clock[0])
            message({"state": {"desired": {"usb1": {"manu": {"lv": 100}}}}})
            box._on_message("thing/update/rejected", b'{"clientToken":"rejected"}', 1)
            assert reader.queue.empty()
            for value in (0, 100, 0):
                clock[0] += 2
                message({"state": {"reported": {"usb1": {"lv": value, "mode": 0, "inPlan": 0}}}})
            await asyncio.wait_for(reader.queue.join(), 5)
            rows = store.port_observations(at(0), clock[0] + 1)
            assert [r["state"] for r in rows] == [0, 1, 0]
            assert all(r["device"] == "pump" for r in rows)
            assert box.pending == {} and len(reader.brief(snapshot())["runs"]) == 1
        finally:
            await reader.stop()
        assert box.port_listeners == [] and box.listeners == []
    asyncio.run(go())


def test_an_offline_retained_shadow_is_not_a_pump_run(monkeypatch):
    reader, store, clock = setup(monkeypatch)
    async def go():
        box = Box("", "", remember=False)
        box._mqtt = SimpleNamespace(is_connected=True)
        reader.start(box)
        try:
            box._on_message("thing/get/accepted", json.dumps({"state": {"reported": {
                "connection": {"connected": False}, "usb1": {"lv": 100}}}}).encode(), 1)
            await asyncio.wait_for(reader.queue.join(), 5)
            assert store.port_observations(0, clock[0] + 86400)[0]["state"] is None
            assert not reader.brief(snapshot())["observed_on"]
        finally:
            await reader.stop()
    asyncio.run(go())


@pytest.mark.parametrize("question,lang", [
    ("wurde heute schon gegossen?", "de"), ("War die Pumpe heute schon an?", "de"),
    ("Wann lief die Pumpe heute?", "de"), ("Hast du schon gegossen?", "de"),
    ("War die Wasserpumpe heute an?", "de"),
    ("Wurde heute Morgen gegossen?", "de"),
    ("Has the pump run today?", "en"), ("Was the plant watered today?", "en"),
    ("Did you water today?", "en"), ("Has irrigation run today?", "en"),
])
def test_history_questions_bypass_retrieval_and_the_model(question, lang):
    router = Router(core.shared_index())
    route = router.route(question, lang_hint=lang)
    assert route.name == "pump_history" and route.retrieval is None


@pytest.mark.parametrize("question", ["Soll ich heute gießen?", "Wie gieße ich richtig?", "Schalte die Pumpe an",
                                      "War die Pumpe gestern an?", "Wird morgen gegossen?", "How much should I water?",
                                      "Ich habe heute schon gegossen.", "I already watered today.",
                                      "Was ist die Pumpe?", "Hast du Tipps für die Pumpe?"])
def test_advice_commands_and_other_days_are_not_today_history(question):
    assert not pump_report.asks(question)


def test_exact_owner_question_works_through_the_real_hank_and_api(monkeypatch):
    reader, store, _ = setup(monkeypatch)
    store.add_port_observations([observation(at(13, 5), True), observation(at(13, 5, 5), False)])
    box = SimpleNamespace(snapshot=snapshot)
    wrapped = HankBox(box, None, reader)
    hank = core.Hank(live=lambda lang: {"snapshot": wrapped.snapshot()}, client=None, index=core.shared_index())
    async def go():
        app = make_app(box, Alerts(), "test-key", hank=hank, measurements=store, pump_history=reader)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/pump-history")).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: "test-key"})
            data = await (await client.get("/api/pump-history")).json()
            assert data["observed_on"] and data["current_on"] is False
            response = await client.post("/api/hank/ask", json={"q": "wurde heute schon gegossen?",
                                                               "client_id": "pump-test", "lang": "de"})
            assert response.status == 200
            result = await response.json()
            answer = result["answer"]
            assert result["done"] and answer["route"] == "pump_history" and not answer["gap"]
            assert "13:05:00" in answer["text"] and answer["proposals"] == []
    asyncio.run(go())


def test_watering_advice_keeps_todays_pump_history_as_a_mandatory_fact(monkeypatch):
    reader, store, _ = setup(monkeypatch)
    store.add_port_observations([observation(at(13), True)])
    report = reader.brief(snapshot())
    live = {"snapshot": {**snapshot(), "pump_history": report}}
    sheet = context.build(None, live, None, "de", question="Soll ich heute gießen?")
    history = [fid for fid in sheet.mandatory if "Pumpenverlauf" in sheet.facts[fid].text]
    assert history and history[0] in sheet.draft_facts
    assert "13:00:00" in sheet.facts[history[0]].text


def test_catalogs_have_matching_placeholders():
    from vgrow import i18n
    assert set(pump_report.DE) == set(pump_report.EN)
    for key in pump_report.DE:
        assert i18n.placeholders(pump_report.DE[key]) == i18n.placeholders(pump_report.EN[key])
