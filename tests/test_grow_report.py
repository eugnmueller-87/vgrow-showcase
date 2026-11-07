"""The grow report judges from the box's measurements, the stage card and the pest watch, never from a model."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, feeding, grow_report, pest_watch, server, vision, workspace as ws
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.measurements import MeasurementStore
from vgrow.plant import PlantLog

TARGETS = [
    {"metric": "probe_temp", "when": "day", "min": 20, "max": 26, "label": "Temperatur bei Licht",
     "advice_high": "Zu warm: Abluft höher stellen.", "advice_low": "Zu kühl: Abluft eine Stufe runter."},
    {"metric": "probe_temp", "when": "night", "min": 18, "max": 22, "label": "Temperatur ohne Licht",
     "advice_high": "Nachts zu warm: Abluft höher.", "advice_low": "Nachts zu kühl: Abluft runter."},
    {"metric": "probe_humidity", "min": 45, "max": 60, "label": "Luftfeuchte",
     "advice_high": "Zu feucht: Befeuchter aus.", "advice_low": "Zu trocken: Abluft eine Stufe runter."},
    {"metric": "light_percent", "min": 100, "max": 100, "label": "Licht"},
]
PROFILE = {"plant": {"strain": "Ice Cream Cake", "start_date": "2026-09-10"},
           "stages": [{"key": "early_flower", "label": "Frühe Blüte", "label_en": "Early flower", "targets": TARGETS}]}


def midnight(day: date) -> float:
    return datetime.combine(day, datetime.min.time()).timestamp()


def test_stage_spans_are_cut_to_the_window_and_a_later_tap_of_the_same_day_wins() -> None:
    d = date(2026, 10, 24)
    spans = grow_report.stage_spans([{"key": "veg", "since": "2026-10-20"}, {"key": "early_flower", "since": "2026-10-24"},
                                     {"key": "mid_flower", "since": "2026-10-24"}], midnight(d) - 3600, midnight(d) + 86400)
    assert spans == [("veg", midnight(d) - 3600, midnight(d)), ("mid_flower", midnight(d), midnight(d) + 86400)]
    assert grow_report.stage_spans([], 0, 100) == []


def test_measure_splits_day_and_night_by_the_light_and_counts_both_sides() -> None:
    rows = [{"time": 0, "light": 100, "pTemp": 25.0}, {"time": 60, "pTemp": 27.0},  # light still known: day
            {"time": 120, "light": 0, "pTemp": 21.0},
            {"time": 500, "pTemp": 30.0}]  # the last light reading is 380 s old: neither day nor night
    assert grow_report.measure(rows, TARGETS[0]) == {"rows": 2, "inside": 50.0, "below": 0.0, "above": 50.0}
    assert grow_report.measure(rows, TARGETS[1]) == {"rows": 1, "inside": 100.0, "below": 0.0, "above": 0.0}
    assert grow_report.measure(rows, TARGETS[2]) == {"rows": 0, "inside": None, "below": None, "above": None}
    assert grow_report.verdict({"rows": 10, "inside": 100.0}) is None  # too few minutes for a verdict
    assert [grow_report.verdict({"rows": 400, "inside": v}) for v in (80.0, 79.9, 50.0, 49.9)] == [
        "well", "mixed", "mixed", "poor"]


@pytest.fixture
def record(monkeypatch):
    log = PlantLog()
    log.set_stage("early_flower", "2026-10-24")
    monkeypatch.setattr(server, "load_profile", lambda: PROFILE)
    ws.initialize("VGrow Y18", PROFILE, log)
    ident = ws.mutate({"revision": ws.view()["revision"], "action": "plant_create", "box": ws.NATIVE_BOX,
                       "name": "ICC", "variety": "Ice Cream Cake", "phase": "Blüte", "started": "2026-09-10"})
    start, end = midnight(date(2026, 10, 24)), midnight(date(2026, 10, 25))
    with ws.database(write=True) as db:
        db.execute("UPDATE record_places SET since=?, until=? WHERE plant=?", (start, end, ident))
    store = MeasurementStore(config.STATE_DIR / "measurements.sqlite3")
    rows = []
    for minute in range(24 * 60):
        day = minute < 18 * 60  # 18/6: light from midnight for 18 hours
        rows.append({"ts": int(start + minute * 60), "light": 100 if day else 0,
                     "pTemp": 2400 if day else 2500,  # day inside 20 to 26; night 25 above 18 to 22
                     "pHumi": 4000})  # humidity 40 under 45 to 60 all day
    store.add(rows, source="poll")
    return ident, store, log, start


def confirmed_aphids(at: float) -> pest_watch.PestWatch:
    pest = pest_watch.PestWatch(vision.Vision(None))
    record = {"id": "2026-10-24_12-00-00_aaaaaa", "date": "2026-10-24T12:00", "at": at, "result": {"ok": True},
              "flags": [{"key": "insects", "seen": "yes", "where": "leaf"}],
              "finding": {"status": "open", "answer": None, "answered_at": None}}
    pest._write(record)
    pest.answer(record["id"], "yes", now=at + 60)
    pest.identify(record["id"], "problems/aphid", now=at + 120)
    return pest


def test_the_report_names_what_went_well_what_did_not_and_the_cards_advice(record) -> None:
    ident, store, log, start = record
    pest = confirmed_aphids(start + 12 * 3600)
    out = grow_report.build(ident, store=store, log=log, profile=PROFILE, pest=pest, lang="de", now=start + 2 * 86400)
    by = {(c["metric"], c["when"]): c for c in out["climate"]}
    assert set(by) == {("probe_temp", "day"), ("probe_temp", "night"), ("probe_humidity", None)}  # light is no climate
    assert by[("probe_temp", "day")]["inside"] == 100.0 and by[("probe_temp", "day")]["verdict"] == "well"
    assert by[("probe_temp", "night")]["above"] == 100.0 and by[("probe_temp", "night")]["verdict"] == "poor"
    assert by[("probe_humidity", None)]["below"] == 100.0 and by[("probe_humidity", None)]["verdict"] == "poor"
    lines = out["lines"]
    assert lines["well"] == ["Temperatur bei Licht in der Phase Frühe Blüte: 100 % der gemessenen Minuten im "
                             "Zielband (1080 Minuten)."]
    assert "Temperatur ohne Licht in der Phase Frühe Blüte: nur 0 % im Zielband, 100 % darüber (360 Minuten)." in lines["poor"]
    assert "Luftfeuchte in der Phase Frühe Blüte: nur 0 % im Zielband, 100 % darunter (1440 Minuten)." in lines["poor"]
    assert "Schädlinge bestätigt am 24.10.2026: Blattläuse." in lines["poor"]
    # next time: the card's advice for the side it missed on, and the aphid card's first prevention line
    assert lines["next"][:2] == ["Nachts zu warm: Abluft höher.", "Zu trocken: Abluft eine Stufe runter."]
    assert lines["next"][2].startswith("Gesunde Sämlinge aus verlässlicher Quelle")
    assert out["pests"]["checks"] == 1 and out["pests"]["confirmed"][0]["card"] == "problems/aphid"
    english = grow_report.build(ident, store=store, log=log, profile=PROFILE, pest=pest, lang="en", now=start + 2 * 86400)
    assert english["lines"]["well"] == ["Temperatur bei Licht in Early flower: 100% of the measured minutes inside the "
                                        "target band (1080 minutes)."]  # these targets have no label_en
    assert "Pests confirmed on Oct 24, 2026: Aphids." in english["lines"]["poor"]


class Thirst:
    def __init__(self, entries: list[dict]) -> None:
        self.log = entries

    def entries(self) -> list[dict]:
        return self.log


def test_use_counts_what_was_logged_and_never_turns_a_pump_run_into_litres(record) -> None:
    ident, store, log, start = record
    thirst = Thirst([{"kind": "water", "at": start + 3600, "litres": 1.5, "origin": "hand"},
                     {"kind": "water", "at": start + 7200, "litres": None, "minutes": 20, "counted": "unknown"},
                     {"kind": "pump_seen", "at": start + 8000, "minutes": 5, "why": "jug", "counted": False},
                     {"kind": "water", "at": start - 3600, "litres": 9.0, "origin": "hand"}])  # before its time in the box
    day = datetime.fromtimestamp(start + 3600).isoformat(timespec="seconds")
    item = {"component": "bio_grow", "chart": 2, "per_liter": 1.0, "total": 2.0}
    feeding.log_path().write_text(
        json.dumps({"time": day, "card": "biobizz", "unit": "ml", "liters": 2.0, "items": [item]}) + "\n" + "broken\n"
        + json.dumps({"time": "2020-01-01T10:00:00", "card": "biobizz", "unit": "ml", "liters": 7.0, "items": [item]}) + "\n",
        encoding="utf-8")
    out = grow_report.build(ident, store=store, log=log, profile=PROFILE, thirst=thirst, lang="de", now=start + 2 * 86400)
    assert out["use"]["water"] == {"litres": 3.5, "unmeasured_runs": 1, "unmeasured_minutes": 20}
    assert [(i["total"], i["unit"]) for i in out["use"]["feed"]] == [(2.0, "ml")]
    assert out["use"]["power"] == {"kwh": 1.8, "watts": 100, "estimated": True}  # 18 hours at full brightness
    use = out["lines"]["use"]
    assert use[0] == "Wasser: 3,5 Liter gezählt." and "1, zusammen 20 Minuten" in use[1]
    assert "2 ml" in use[2] and "geschätzt" in use[3] and "nicht dabei" in use[3]
    english = grow_report.build(ident, store=store, log=log, profile=PROFILE, thirst=thirst, lang="en", now=start + 2 * 86400)
    assert english["lines"]["use"][0] == "Water: 3.5 litres counted." and "estimated" in english["lines"]["use"][3]


def test_a_plant_with_nothing_logged_has_no_water_line_and_no_invented_power(record) -> None:
    ident, _, log, start = record
    out = grow_report.build(ident, store=None, log=log, profile=PROFILE, lang="de", now=start + 2 * 86400)
    assert out["use"] == {"water": {"litres": 0, "unmeasured_runs": 0, "unmeasured_minutes": 0}, "feed": [], "power": None}
    assert out["lines"]["use"] == []


def test_the_route_answers_the_report_and_an_unknown_plant_with_404(record) -> None:
    ident, store, log, _ = record

    async def go():
        app = server.make_app(DemoBox(), Alerts(), "k" * 32, plant_log=log, measurements=store)
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({server.COOKIE: "k" * 32})
            good = await client.get(f"/api/grow-report/{ident}")
            missing = await client.get("/api/grow-report/nope")
            return good.status, json.loads(await good.text()), missing.status

    status, body, missing = asyncio.run(go())
    assert status == 200 and body["plant"]["name"] == "ICC" and body["lines"]["poor"]
    assert missing == 404
