"""The water sensor by its accessory type (shadow aux35Dev), 23.10.2026.

Type 9, the owner's VGrow: waterLv is a wet/dry signal, 20000 with water at the sensor and 0 dry. Measured on
23.10.2026 with the owner's test (sensor out of the water from 16:05 to 16:08), not documented by VIVOSUN. Type 10,
the DWC kit: hundredths of a percent and of a degree Celsius (lientry/homeassistant-vivosun-growhub issue #43).
Covered here: decoding per type and value, the time of the last change, the tank alarm with its debounce, the
cockpit's water tile in both languages (in node), Hank's digest, water fact and today line, and the pump hook.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import hank_live_kit as kit
from hank_eval import fixtures
from vgrow import alerts as alerts_mod
from vgrow import config
from vgrow.box import (
    AUX_LEVEL,
    AUX_WET_DRY,
    WATER_STATE_FILE,
    Box,
    aux_type,
    climate_view,
    scale_row,
    water_change,
    water_state_of,
    water_view,
    watering_allowed,
)
from vgrow.demo import DemoBox
from vgrow.hank import context, texts_live, today
from vgrow.hank.livestate import collect
from vgrow.i18n import fmt_time

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself


def at(hour: int, minute: int) -> float:
    """23.10.2026 at this local time, in epoch seconds (the owner's test day)."""
    return datetime(2026, 10, 23, hour, minute).timestamp()


def row(stamp: float, water: int | None, **extra: Any) -> dict[str, Any]:
    out = {"time": int(stamp), "bTemp": 2400, "bHumi": 5000, "pTemp": 2300, "pHumi": 6000, **extra}
    if water is not None:
        out["waterLv"] = water
    return out


# ---------- 1. decoding per type and value ----------

def test_type_9_is_wet_or_dry_never_a_level() -> None:
    wet = water_view(scale_row(row(at(16, 4), 20000)), 9)
    assert wet == {"water_kind": "wet_dry", "water_state": "wet", "water_level": None, "water_raw": None,
                   "water_temp": None}
    dry = water_view(scale_row(row(at(16, 5), 0)), 9)
    assert dry["water_state"] == "dry" and dry["water_level"] is None and dry["water_raw"] is None
    odd = water_view(scale_row(row(at(16, 6), 12345)), 9)  # a value the test did not show: kept raw, not guessed
    assert odd["water_state"] == "unknown" and odd["water_raw"] == 12345 and odd["water_level"] is None
    assert water_view(scale_row(row(at(16, 6), None)), 9)["water_state"] is None  # no value: nothing to say
    assert water_view(scale_row(row(at(16, 6), -6666)), 9)["water_state"] is None  # the cloud's "unavailable"
    assert water_state_of("9", 20000) == "wet" and water_state_of(AUX_WET_DRY, 0) == "dry"
    assert water_state_of(AUX_LEVEL, 0) is None  # a level of 0 % is not "dry" of the other type


def test_type_10_is_a_level_in_percent_with_the_tank_temperature() -> None:
    for raw, level in ((7500, 75.0), (10000, 100.0), (0, 0.0)):
        view = water_view(scale_row(row(at(12, 0), raw, waterT=2226)), 10)
        assert (view["water_kind"], view["water_level"], view["water_temp"]) == ("level", level, 22.26)
        assert view["water_state"] is None and view["water_raw"] is None
    raw_row = water_view(row(at(12, 0), 7500, waterT=2226), 10)  # a point-log row as the cloud sends it
    assert (raw_row["water_level"], raw_row["water_temp"]) == (75.0, 22.26)
    out_of_range = water_view(scale_row(row(at(12, 0), 20000)), 10)
    assert out_of_range["water_level"] is None and out_of_range["water_raw"] == 20000


def test_an_unknown_type_keeps_only_the_raw_value() -> None:
    for aux in (None, 7, "x"):
        view = climate_view(scale_row(row(at(12, 0), 7500, waterT=2226)), aux)
        assert view["water_kind"] is None and view["water_state"] is None
        assert view["water_level"] is None and view["water_temp"] is None and view["water_raw"] == 7500
    # The owner's box once its type is known: the 20000 of 22.10.2026 reads as water at the sensor.
    owner = climate_view(scale_row({"bTemp": 2568, "bHumi": 4638, "bVpd": 176, "pTemp": 2230, "pHumi": 6841,
                                    "pVpd": 85, "waterLv": 20000, "coreTemp": 4870}), 9)
    assert owner["water_state"] == "wet" and owner["water_raw"] is None and owner["probe_humidity"] == 68.41


# ---------- the time of the last change ----------

OWNER_TEST = [(at(16, m), 20000) for m in range(0, 5)] + [(at(16, m), 0) for m in range(5, 9)] + \
             [(at(16, m), 20000) for m in range(9, 15)]


def window(until_minute: int) -> list[dict[str, Any]]:
    """The 15-minute point-log window of a poll at 16:<until_minute>, as the owner's test produced it."""
    end = at(16, until_minute)
    return [row(stamp, water) for stamp, water in OWNER_TEST if end - 900 < stamp <= end]


def test_water_change_finds_the_minute_the_state_flipped() -> None:
    assert water_change(window(7), 9, at(16, 8)) == ("dry", at(16, 5), True)
    assert water_change(window(12), 9, at(16, 13)) == ("wet", at(16, 9), True)
    assert water_change(window(3), 9, at(16, 4)) == ("wet", at(16, 0), False)  # no change in sight
    assert water_change(window(7), 10, at(16, 8)) == (None, None, False)  # a level sensor has no wet/dry


def test_box_keeps_the_time_of_the_last_change_across_a_restart() -> None:
    box = Box("owner@example.org", "x", remember=True)
    box.reported = {"aux35Dev": 9}
    box.track_water(window(3), now=at(16, 4))
    assert (box.water_state, box.water_since) == ("wet", None)  # first look, no change visible: start unknown
    box.track_water(window(7), now=at(16, 8))
    assert (box.water_state, box.water_since) == ("dry", at(16, 5))
    box.sensors = scale_row(window(7)[-1])
    climate = box.climate_now()
    assert climate["water_state"] == "dry" and climate["water_since"] == at(16, 5)
    stored = json.loads((config.STATE_DIR / WATER_STATE_FILE).read_text(encoding="utf-8"))
    assert stored == {"state": "dry", "since": at(16, 5)}
    again = Box("owner@example.org", "x", remember=True)  # the server restarted while the tank was dry
    assert (again.water_state, again.water_since) == ("dry", at(16, 5))
    box.track_water(window(12), now=at(16, 13))
    assert (box.water_state, box.water_since) == ("wet", at(16, 9))
    box.sensors = scale_row(window(12)[-1])
    assert box.climate_now()["water_since"] == at(16, 9)
    assert Box("owner@example.org", "x", remember=False).water_state is None  # --check never reads it


# ---------- 3. the tank alarm ----------

class Recorder(alerts_mod.Alerts):
    def __init__(self) -> None:  # the real tank rule of alerts.json, pushes recorded instead of sent
        self._session = None
        self.config = {"remind_hours": 6, "rules": {"tank_empty": config.alert_rules()["rules"]["tank_empty"]}}
        self._state_path = config.STATE_DIR / "alerts-test.json"
        self.state = {}
        self.target = ("https://ntfy.example", "t")
        self.pushed: list[tuple[str, str]] = []

    async def push(self, title: str, message: str, *, priority: int = 3) -> bool:
        self.pushed.append((title, message))
        return True


def tank(state: str, *, age: int = 40, online: bool = True, since: float | None = None,
         run: float | None = None) -> dict[str, Any]:
    """A snapshot of the wet/dry sensor; run: seconds the readings have said `state` without a break."""
    return {"online": online, "sensors_age": age,
            "climate": {"water_kind": "wet_dry", "water_state": state, "water_since": since, "water_run_s": run}}


def test_tank_rule_is_three_dry_minutes_in_alerts_json() -> None:
    rule = config.alert_rules()["rules"]["tank_empty"]
    assert (rule["metric"], rule["is"], rule["for_minutes"]) == ("water_state", "dry", 3)
    assert rule["label"] == "Tank leer" and rule["label_en"] == "Tank empty"
    assert "23.10.2026" in rule["source"] and "Docht" in rule["source"]


def test_two_dry_minutes_then_wet_raise_nothing_three_raise_one_wet_clears(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clock = [10_000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: clock[0])
    a = Recorder()

    def poll(snapshot: dict[str, Any], seconds: float) -> list[str]:
        clock[0] += seconds
        return asyncio.run(a.check(snapshot))

    poll(tank("dry", run=0), 0)
    poll(tank("dry", run=60), 60)
    poll(tank("dry", run=120), 60)  # two minutes dry: a wobble, not yet an empty tank
    poll(tank("wet", run=0), 30)
    assert a.pushed == []
    poll(tank("dry", since=clock[0], run=0), 30)
    poll(tank("dry", since=clock[0] - 30, run=0), 600)  # ten minutes on the clock, but no new dry row
    poll(tank("dry", since=clock[0] - 30, run=90), 90)
    assert a.pushed == []
    poll(tank("dry", since=at(16, 5), run=180), 90)  # three minutes of dry readings in a row
    assert len(a.pushed) == 1
    title, message = a.pushed[0]
    assert title == "Tank leer"
    assert message.startswith("Tank leer. Der Wassersensor ist seit " + fmt_time(at(16, 5), "de") + " trocken.")
    assert "Mit dem Docht zieht die Pflanze ihr Wasser aus diesem Tank" in message
    poll(tank("dry", run=270), 90)
    poll(tank("dry", run=3870), 3600)
    assert len(a.pushed) == 1  # one push per event; the module's own reminder comes only after remind_hours
    poll(tank("wet"), 90)
    assert len(a.pushed) == 2 and a.pushed[1][0] == "Wieder ok" and "Wasser im Tank: vorhanden." in a.pushed[1][1]
    assert a.view()[0]["active"] is False


def test_the_percent_rule_never_holds_for_a_wet_dry_sensor_and_a_stuck_alarm_clears(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Live 23.10.2026: during the owner's refill the type-9 sensor read 0, the old code took that for "0 %" and raised
    water_low; afterwards the sensor reports no level at all, so the alarm never cleared and said "Water low" over a full
    tank. The percent rule is for level sensors only; for wet/dry it is never breached and a stuck alarm clears."""
    rule = config.alert_rules()["rules"]["water_low"]
    assert alerts_mod.breached(rule, tank("wet")) is False and alerts_mod.breached(rule, tank("dry")) is False
    level = {"online": True, "sensors_age": 40, "climate": {"water_kind": "level", "water_level": 20.0}}
    assert alerts_mod.breached(rule, level) is True  # the DWC kit in percent still raises it
    clock = [10_000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: clock[0])
    a = Recorder()
    a.config["rules"]["water_low"] = rule  # the Recorder carries only the tank rule; this test needs the percent rule
    a.state["water_low"] = {"since": 5_000.0, "active": True, "last_push": 5_300.0, "source": "water_level"}
    clock[0] += 90
    asyncio.run(a.check(tank("wet", run=3000)))
    assert a.state["water_low"]["active"] is False
    assert not any("niedrig" in title.lower() or "low" in title.lower() for title, _ in a.pushed)


def test_stale_offline_or_unknown_data_never_raise_or_clear_the_tank_alarm(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    rule = config.alert_rules()["rules"]["tank_empty"]
    assert alerts_mod.breached(rule, tank("dry")) is True and alerts_mod.breached(rule, tank("wet")) is False
    assert alerts_mod.breached(rule, tank("dry", age=400)) is None  # stale: an old "dry" proves nothing
    assert alerts_mod.breached(rule, tank("dry", online=False)) is None
    assert alerts_mod.breached(rule, tank("unknown")) is None
    assert alerts_mod.breached(rule, {"online": True, "sensors_age": 40, "climate": {"water_level": 75.0}}) is None
    clock = [10_000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: clock[0])
    a = Recorder()
    for _ in range(10):
        clock[0] += 90
        asyncio.run(a.check(tank("dry", age=600, run=3600)))
    assert a.pushed == []


def test_one_dry_row_and_polls_without_a_new_row_raise_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Review 23.10.2026: the debounce counts the readings, not the clock. One dry row, then four polls that fail or
    bring no new row (same sensors_at, still under the stale limit): no push. Three dry minutes of rows: one push."""
    clock = [at(16, 6)]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: clock[0])
    box = Box("owner@example.org", "x", remember=False)
    box.reported = {"aux35Dev": 9}
    rows = [row(at(16, m), 20000) for m in range(0, 5)] + [row(at(16, 5), 0)]
    box.track_water(rows, now=clock[0])
    box.sensors = scale_row(rows[-1])
    a = Recorder()

    def check(newest: float) -> None:
        asyncio.run(a.check({"online": True, "sensors_age": clock[0] - newest, "climate": box.climate_now()}))

    for _ in range(4):
        clock[0] += 60  # 16:07 to 16:10: the old row stays the newest, at most 300 s old
        check(at(16, 5))
    assert a.pushed == [] and box.climate_now()["water_run_s"] == 0
    rows += [row(at(16, m), 0) for m in range(6, 9)]
    clock[0] = at(16, 8) + 30
    box.track_water(rows, now=clock[0])
    box.sensors = scale_row(rows[-1])
    check(at(16, 8))
    assert len(a.pushed) == 1 and box.climate_now()["water_run_s"] == 180


def test_a_wet_row_or_a_gap_between_polls_starts_the_run_again() -> None:
    box = Box("owner@example.org", "x", remember=False)
    box.reported = {"aux35Dev": 9}
    box.track_water([row(at(16, m), 20000) for m in range(0, 3)] + [row(at(16, m), 0) for m in range(3, 6)],
                    now=at(16, 6))
    box.sensors = scale_row(row(at(16, 5), 0))
    assert (box.water_state, box.water_since, box.climate_now()["water_run_s"]) == ("dry", at(16, 3), 120)
    # dry, wet, dry between two polls (90 s apart): the run and the time of the last change start at 16:07
    flip = [row(at(16, m), 20000) for m in range(0, 3)] + [row(at(16, m), 0) for m in range(3, 6)] + \
           [row(at(16, 6), 20000), row(at(16, 7), 0)]
    box.track_water(flip, now=at(16, 7) + 30)
    box.sensors = scale_row(flip[-1])
    assert (box.water_state, box.water_since, box.climate_now()["water_run_s"]) == ("dry", at(16, 7), 0)
    # dry before a stalled upload and dry after it: the gap of 7 minutes breaks the run, it counts from 16:15
    gap = [row(at(16, m), 0) for m in range(7, 9)] + [row(at(16, m), 0) for m in range(15, 17)]
    box.track_water(gap, now=at(16, 17))
    box.sensors = scale_row(gap[-1])
    assert box.climate_now()["water_run_s"] == 60 and box.water_since == at(16, 7)


def test_aux_type_takes_only_ascii_digits() -> None:
    assert aux_type(9) == 9 and aux_type(" 10 ") == 10
    assert aux_type("\u00b2") is None and aux_type("9a") is None and aux_type(None) is None  # superscript two


def test_day_summaries_decode_the_water_by_the_sensor_type() -> None:
    from vgrow.measurements import MeasurementStore

    start = int(datetime(2026, 10, 22, tzinfo=UTC).timestamp())
    day = datetime(2026, 10, 22, tzinfo=UTC).date()
    store = MeasurementStore(tz=UTC, clock=lambda: start + 86400)
    store.add_rows([row(start + 60 * i + 5, 7500, waterT=2226) for i in range(10)], now=start + 86400)
    level = store.day_summary(day, now=start + 86400, aux=10)["metrics"]
    assert level["water_level"]["mean"] == 75.0 and level["water_temp"]["mean"] == 22.26  # the DWC kit (type 10)
    assert "water_level" not in store.day_summary(day, now=start + 86400, aux=9)["metrics"]  # wet/dry: no level
    assert "water_level" not in store.day_summary(day, now=start + 86400)["metrics"]  # no type: never a guess
    assert store.summary(1, now=start + 86400 - 60, aux=10)["days"][0]["metrics"]["water_level"]["n"] == 10


def test_tank_alarm_text_in_english() -> None:
    rule = alerts_mod.effective_rule({**config.alert_rules()["rules"]["tank_empty"], "label": "Tank empty"}, None)
    text = Recorder()._message(rule, tank("dry", since=at(16, 5)), reminder=False, lang="en")
    assert text == ("Tank empty. The water sensor has been dry since " + fmt_time(at(16, 5), "en") + ". With the wick "
                    "the plant draws its water from this tank: please refill it.")
    assert EM_DASH not in text


# ---------- 5. the pump hook ----------

def test_watering_is_allowed_only_with_water_at_the_sensor() -> None:
    assert watering_allowed(tank("wet")) is True
    assert watering_allowed(tank("dry")) is False
    assert watering_allowed(tank("unknown")) is False
    assert watering_allowed(tank("wet", age=400)) is False  # stale
    assert watering_allowed(tank("wet", online=False)) is False
    assert watering_allowed({"online": True, "sensors_age": 40, "climate": {}}) is False  # no sensor
    level = {"online": True, "sensors_age": 40, "climate": {"water_kind": "level", "water_level": 50.0}}
    assert watering_allowed(level) is True
    assert watering_allowed({**level, "climate": {"water_kind": "level", "water_level": 0.0}}) is False
    raw = {"online": True, "sensors_age": 40, "climate": {"water_kind": None, "water_raw": 7500}}
    assert watering_allowed(raw) is False  # an unknown type never lets the pump run


# ---------- 2. the cockpit's water tile (app.js waterTile, in node) ----------

def _js_function(src: str, name: str) -> str:
    """One top-level function of app.js: a one-liner, or up to the closing brace at the start of a line."""
    found = re.search(rf"^function {name}\(.*$", src, re.M)
    assert found, name
    if not re.sub(r"\s*//.*$", "", found.group(0)).endswith("{"):  # "function fmt(v) {  // a reading" opens one
        return found.group(0)
    end = src.index("\n}\n", found.start())
    return src[found.start():end + 3]


def _js_const(src: str, name: str) -> str:
    found = re.search(rf"^const {name} = .*?;(?=\s*(?://.*)?$)", src, re.M)  # a trailing comment stays out
    assert found, name
    return found.group(0)


TILE_HARNESS = """
const window = {};
%(i18n)s
let LANG = "de";
const DICT = window.I18N;
%(consts)s
%(functions)s
const today = new Date(); today.setHours(16, 5, 0, 0);
const since = today.getTime() / 1000;
const cases = {
  wet: { water_kind: "wet_dry", water_state: "wet", water_since: since },
  dry: { water_kind: "wet_dry", water_state: "dry", water_since: since },
  dry_no_time: { water_kind: "wet_dry", water_state: "dry", water_since: null },
  level: { water_kind: "level", water_level: 75, water_temp: 22.26 },
  odd: { water_kind: "wet_dry", water_state: "unknown", water_raw: 12345 },
  unknown_type: { water_kind: null, water_raw: 7500 },
  none: {},
};
const out = {};
for (const lang of ["de", "en"]) {
  LANG = lang;
  out[lang] = {};
  for (const [name, c] of Object.entries(cases)) out[lang][name] = waterTile(c);
}
console.log(JSON.stringify(out));
"""


def tile_texts() -> dict[str, dict[str, dict[str, Any]]]:
    src = (REPO / "web" / "app.js").read_text(encoding="utf-8")
    script = TILE_HARNESS % {
        "i18n": (REPO / "web" / "i18n.js").read_text(encoding="utf-8"),
        "consts": "\n".join(_js_const(src, n) for n in ("LOCALE", "HOUR", "MONTH", "DAY", "NUMBER")),
        "functions": "\n".join(_js_function(src, n) for n in ("t", "numberFormat", "fmt", "unitSuffix", "qty", "clock",
                                                              "stamp", "when", "waterTile")),
    }
    run = subprocess.run([NODE or "node", "-"], input=script, capture_output=True, text=True, encoding="utf-8",
                         timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_water_tile_says_wet_or_empty_with_the_time_in_german_and_english() -> None:
    out = tile_texts()
    de, en = out["de"], out["en"]
    assert (de["wet"]["value"], de["wet"]["sub"], de["wet"]["unit"]) == ("vorhanden", "seit 16:05", "")
    assert de["wet"]["title"] == "Wasser im Tank: vorhanden, seit 16:05" and de["wet"]["pill"] == ""
    assert de["wet"]["src"] == "Sensor nass/trocken" and de["wet"]["state"] and not de["wet"]["empty"]
    assert (de["dry"]["value"], de["dry"]["title"], de["dry"]["pill"]) == ("Tank leer", "Tank leer seit 16:05",
                                                                          "auffüllen")
    assert de["dry_no_time"]["title"] == "Tank leer" and de["dry_no_time"]["sub"] == ""
    assert (en["wet"]["value"], en["wet"]["sub"]) == ("present", "since 4:05 PM")
    assert en["wet"]["title"] == "Water in the tank: present, since 4:05 PM"
    assert (en["dry"]["value"], en["dry"]["title"], en["dry"]["pill"]) == ("Tank empty", "Tank empty since 4:05 PM",
                                                                          "refill")
    for lang in (de, en):
        for case in ("wet", "dry", "dry_no_time"):
            assert "gedeutet" not in lang[case]["sub"] and "decoded" not in lang[case]["sub"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_water_tile_keeps_the_percent_and_not_decoded_only_for_unknown() -> None:
    out = tile_texts()
    de, en = out["de"], out["en"]
    assert (de["level"]["value"], de["level"]["unit"]) == ("75", "%") and "22,3" in de["level"]["sub"]
    assert de["level"]["sub"].endswith("im Tank") and not de["level"]["state"]
    assert en["level"]["sub"].endswith("in the tank") and "22.3" in en["level"]["sub"]
    for case in ("odd", "unknown_type"):
        assert de[case]["value"] == "n. v." and de[case]["empty"] and "Pegel noch nicht gedeutet" in de[case]["sub"]
        assert en[case]["value"] == "n/a" and "Level not decoded yet" in en[case]["sub"]
    assert de["odd"]["title"] == "Sensor meldet 12345" and en["unknown_type"]["title"] == "Sensor reports 7500"
    assert de["none"]["show"] is False


def test_tile_texts_exist_in_both_languages() -> None:
    raw = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
    data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    assert data["de"]["tiles.tankWetLong"] == "Wasser im Tank: vorhanden" and data["de"]["tiles.tankDry"] == "Tank leer"
    assert data["en"]["tiles.tankWetLong"] == "Water in the tank: present" and data["en"]["tiles.tankDry"] == "Tank empty"
    keys = [k for k in data["de"] if k.startswith("tiles.tank")]
    assert len(keys) == 8 and all(k in data["en"] for k in keys)


def test_the_demo_shows_the_wet_dry_sensor_on_request(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert DemoBox().snapshot()["climate"]["water_level"] == 75.0  # unset: the DWC kit, as before
    monkeypatch.setenv("VGROW_DEMO_WATER", "dry")
    snap = DemoBox().snapshot()
    assert snap["aux_device"] == 9 and snap["climate"]["water_state"] == "dry"
    assert snap["climate"]["water_level"] is None and snap["climate"]["water_since"] is not None


# ---------- 4. Hank ----------

@pytest.fixture()
def hank_day(monkeypatch):  # type: ignore[no-untyped-def]
    fixtures.freeze(monkeypatch)
    texts_live.register()
    today.register()


def live_with(state: str | None, lang: str = "de", *, since: float | None = None, age: int | None = None,
              active: bool = False) -> dict[str, Any]:
    live = kit.live("F22", lang)
    climate = live["snapshot"].setdefault("climate", {})
    if state is not None:
        climate.update(water_kind="wet_dry", water_state=state, water_level=None, water_raw=None, water_since=since,
                       water_run_s=600)  # ten minutes of readings in that state: past the alarm's debounce
    if age is not None:
        live["snapshot"]["sensors_age"] = age
    if active:
        live["alerts_view"] = [*live.get("alerts_view", []), {"name": "tank_empty", "label": "Tank leer",
                                                              "metric": "water_state", "fallback": None, "op": None,
                                                              "limit": None, "for_minutes": 3, "active": True}]
    return live


def test_hank_digest_says_wet_or_empty_since(hank_day) -> None:  # type: ignore[no-untyped-def]
    time_de, time_en = fmt_time(at(16, 5), "de"), fmt_time(at(16, 5), "en")
    assert "Wasser im Tank: vorhanden." in collect(live_with("wet")).digest("de")
    assert f"Tank leer seit {time_de}." in collect(live_with("dry", since=at(16, 5))).digest("de")
    assert "Tank leer." in collect(live_with("dry")).digest("de")
    assert f"Tank empty since {time_en}." in collect(live_with("dry", "en", since=at(16, 5))).digest("en")
    assert "Water in the tank: present." in collect(live_with("wet", "en")).digest("en")
    level = collect(live_with(None)).digest("de")
    assert "Wasser 75 %" in level and "Tank" not in level  # the level sensor of the fixture keeps its line


def test_hank_water_fact_for_the_wet_dry_sensor(hank_day) -> None:  # type: ignore[no-untyped-def]
    def facts(live: dict[str, Any], question: str, lang: str = "de") -> list[str]:
        ctx = context.build(kit.route(question, lang), live, None, lang, question=question)
        return [f.text for f in ctx.facts.values()]

    wet = facts(live_with("wet"), "Wie viel Wasser ist im Tank?")
    assert any(f.startswith("Wasser im Tank: vorhanden. Der Sensor meldet nur nass oder trocken") for f in wet)
    assert not any(f.startswith("Wasserstand im Tank") for f in wet)
    dry = facts(live_with("dry", since=at(16, 5)), "Wie viel Wasser ist im Tank?")
    assert any(f.startswith(f"Tank leer seit {fmt_time(at(16, 5), 'de')}. Mit dem Docht") for f in dry)
    en = facts(live_with("dry", "en", since=at(16, 5)), "How much water is in the tank?", "en")
    assert any(f.startswith(f"Tank empty since {fmt_time(at(16, 5), 'en')}. With the wick") for f in en)


def test_hank_today_says_refill_while_the_tank_is_empty(hank_day) -> None:  # type: ignore[no-untyped-def]
    refill = "Tank auffüllen. Mit dem Docht zieht die Pflanze ihr Wasser aus diesem Tank."
    lines, _ = today.lines(live_with("dry", since=at(16, 5)), "de")
    assert refill in lines
    early = live_with("dry", since=at(16, 5))
    early["snapshot"]["climate"]["water_run_s"] = 60  # one dry minute: the same debounce as the push
    assert not any("Tank auff" in line for line in today.lines(early, "de")[0])
    assert refill not in today.lines(live_with("wet"), "de")[0]
    assert not any("Tank auff" in line for line in today.lines(live_with("dry", age=900), "de")[0])  # stale
    alarmed, _ = today.lines(live_with("dry", active=True), "de")
    said = [line for line in alarmed if "Tank auffüllen" in line]
    assert said == ["Jetzt handeln, Tank leer: " + refill]  # the alarm line carries it, said once
    en, _ = today.lines(live_with("dry", "en"), "en")
    assert "Refill the tank. With the wick the plant draws its water from this tank." in en
    assert today.check() == [] and texts_live.check() == []
