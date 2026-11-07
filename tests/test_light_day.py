"""The measured day: light hours from the store, the two alarms on them, and the close camera's alarm.

The owner's case (measured 06.11.2026): the lamp ran without a dark period from 23. to 26.10. and from 04. to 06.11., and
the C920 was gone for three days, and nothing said so. One stored row in three carries no light value.
"""
import asyncio
import copy
from typing import Any

import pytest

from vgrow import alerts as alerts_mod
from vgrow import config
from vgrow.alerts import Alerts, breached
from vgrow.demo import DemoBox
from vgrow.light_schedule import stage_hours
from vgrow.light_view import DAY_REFRESH, LightDay, balance
from vgrow.server import poll_handler

HOUR = 3600
RULES = config.alert_rules()["rules"]


def rows_of(hours_on: float, hours: float = 24.0, start: int = 0) -> list[dict[str, Any]]:
    """A row a minute, light value in two of three, as the store holds them."""
    out = []
    for minute in range(int(hours * 60)):
        at = start + minute * 60
        level = 80 if minute < hours_on * 60 else 0
        out.append({"time": at, "light": None if minute % 3 == 2 else level})
    return out


@pytest.mark.parametrize(("hours_on", "dark"), [(24, 0), (20, 4), (18, 6), (12, 12), (0, 24)])
def test_the_rows_without_a_value_do_not_shorten_the_day(hours_on: float, dark: float) -> None:
    day = balance(rows_of(hours_on), 0, 24 * HOUR)
    assert day == {"lit_hours": hours_on, "dark_hours": dark, "measured_hours": 24}


def test_a_long_gap_counts_for_neither_side() -> None:
    rows = [{"time": 0, "light": 80}, {"time": 6 * HOUR, "light": 0}]
    assert balance(rows, 0, 24 * HOUR) == {"lit_hours": 0.1, "dark_hours": 0.1, "measured_hours": 0.1}


def test_invalid_values_are_neither_light_nor_dark() -> None:
    rows = [{"time": 0, "light": True}, {"time": 60, "light": -1}, {"time": 120, "light": 101}, {"time": 180}]
    assert balance(rows, 0, HOUR)["measured_hours"] == 0


def snap(lit: float, dark: float, measured: float | None = None, planned: list[int] | None = None) -> dict[str, Any]:
    return {"light_day": {"lit_hours": lit, "dark_hours": dark, "hours_planned": planned,
                          "measured_hours": lit + dark if measured is None else measured}}


@pytest.mark.parametrize(("lit", "dark", "expected"), [(24, 0, True), (23.5, 0.5, True), (20, 4, False), (18, 6, False)])
def test_no_dark_period_is_under_one_hour_of_dark(lit: float, dark: float, expected: bool) -> None:
    assert breached(RULES["dark_period"], snap(lit, dark)) is expected


def test_a_day_with_too_few_measured_hours_decides_nothing() -> None:
    assert breached(RULES["dark_period"], snap(10, 0)) is None
    assert breached(RULES["short_day"], snap(12, 7, planned=[18, 20])) is None
    assert breached(RULES["dark_period"], {}) is None


@pytest.mark.parametrize(("lit", "planned", "expected"), [(12, [18, 20], True), (11, [18], True), (13, [18], True),
                                                          (12, [12], False), (10.5, [18], False), (18, [18, 20], False),
                                                          (12, None, None)])
def test_twelve_hours_alarm_only_where_the_card_does_not_allow_them(lit: float, planned: list[int] | None,
                                                                    expected: bool | None) -> None:
    assert breached(RULES["short_day"], snap(lit, 24 - lit, planned=planned)) is expected


def test_the_stage_card_says_what_the_day_should_be() -> None:
    flower = {"key": "flower", "light_schedule": {"hours_on": 12, "sources": ["S1"]}}
    veg = {"key": "veg", "light_schedule": {"hours_on": 18, "alternative_hours_on": [20], "sources": ["L1"]}}
    assert stage_hours({"stages": []}, flower) == [12]
    assert stage_hours({"stages": []}, veg) == [18, 20]
    assert stage_hours({"light_schedule": {"hours_on": 20}}, {"key": "x"}) == [20]  # the autoflower's card
    assert stage_hours({"light_schedule": None}, {"key": "x"}) is None
    assert stage_hours(None, veg) is None and stage_hours({}, None) is None


@pytest.mark.parametrize(("cam", "expected"), [({"configured": False, "reachable": False}, None),
                                               ({"configured": True, "reachable": None}, None),
                                               ({"configured": True, "reachable": False}, True),
                                               ({"configured": True, "reachable": True}, False)])
def test_the_close_camera_alarm_never_guesses(cam: dict[str, Any], expected: bool | None) -> None:
    assert breached(RULES["usb_camera_offline"], {"usb_camera": cam}) is expected
    assert breached(RULES["usb_camera_offline"], {}) is None


class Pushes(Alerts):
    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[tuple[str, str]] = []

    async def push(self, title: str, message: str, *, priority: int = 3, click: str | None = None) -> bool:
        self.pushed.append((title, message))
        return True


def only(alerts: Alerts, name: str) -> None:
    alerts.config = copy.deepcopy(alerts.config)
    alerts.config["rules"] = {name: alerts.config["rules"][name]}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_000_000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: now[0])
    return now


GONE = {"configured": True, "reachable": False, "offline_seconds": 60, "error": "macOS sieht die Kamera nicht."}


def test_the_close_camera_pushes_after_its_hold_with_the_reason_and_once_a_day(clock: list[float]) -> None:
    alerts = Pushes()
    only(alerts, "usb_camera_offline")
    assert asyncio.run(alerts.check({"usb_camera": GONE})) == []  # the hold time first
    clock[0] += 31 * 60
    asyncio.run(alerts.check({"usb_camera": GONE}))
    assert len(alerts.pushed) == 1
    assert "31 min" in alerts.pushed[0][1] and "macOS sieht die Kamera nicht." in alerts.pushed[0][1]
    assert "GrowCam" in alerts.pushed[0][1]
    clock[0] += 7 * HOUR  # past the common six hours: this rule reminds once a day
    asyncio.run(alerts.check({"usb_camera": GONE}))
    assert len(alerts.pushed) == 1
    clock[0] += 18 * HOUR
    asyncio.run(alerts.check({"usb_camera": GONE}))
    assert len(alerts.pushed) == 2 and "25 Stunden" in alerts.pushed[1][1]


def test_the_time_counts_from_the_alarm_not_from_the_server_start(clock: list[float]) -> None:
    alerts = Pushes()
    only(alerts, "usb_camera_offline")
    asyncio.run(alerts.check({"usb_camera": GONE}))
    clock[0] += 3 * HOUR
    restarted = Pushes()  # the state file survives; the camera's own count starts again at 60 seconds
    only(restarted, "usb_camera_offline")
    asyncio.run(restarted.check({"usb_camera": GONE}))
    assert "3 Stunden" in restarted.pushed[0][1]


def test_a_camera_that_is_back_says_so(clock: list[float]) -> None:
    alerts = Pushes()
    only(alerts, "usb_camera_offline")
    asyncio.run(alerts.check({"usb_camera": GONE}))
    clock[0] += HOUR
    asyncio.run(alerts.check({"usb_camera": GONE}))
    asyncio.run(alerts.check({"usb_camera": {"configured": True, "reachable": True}}))
    assert "antwortet wieder" in alerts.pushed[-1][1] and "kein Bild" not in alerts.pushed[-1][1]


def test_the_growcam_counts_the_same_way_and_says_when_it_is_back(clock: list[float]) -> None:
    alerts = Pushes()
    only(alerts, "camera_offline")
    gone = {"configured": True, "reachable": False, "offline_seconds": 60}
    asyncio.run(alerts.check({"camera": gone}))
    clock[0] += 2 * HOUR
    asyncio.run(alerts.check({"camera": gone}))
    assert "120 min" in alerts.pushed[0][1]
    asyncio.run(alerts.check({"camera": {"configured": True, "reachable": True}}))
    assert "antwortet wieder" in alerts.pushed[-1][1]


def test_no_dark_period_pushes_the_hours_and_clears_with_a_dark_phase(clock: list[float]) -> None:
    alerts = Pushes()
    only(alerts, "dark_period")
    asyncio.run(alerts.check(snap(24, 0)))
    clock[0] += 61 * 60
    asyncio.run(alerts.check(snap(24, 0)))
    assert "24 Stunden Licht, 0 Stunden dunkel" in alerts.pushed[0][1] and "Lichtplan" in alerts.pushed[0][1]
    asyncio.run(alerts.check(snap(23, 1)))
    assert "wieder im Rahmen" in alerts.pushed[-1][1]


def test_twelve_hours_name_what_the_stage_wants(clock: list[float]) -> None:
    alerts = Pushes()
    only(alerts, "short_day")
    asyncio.run(alerts.check(snap(12, 12, planned=[18, 20])))
    clock[0] += 3 * HOUR  # a change of plan passes through 11 to 13 hours in at most 2.5 hours: no alarm for that
    asyncio.run(alerts.check(snap(12, 12, planned=[18, 20])))
    assert alerts.pushed == []
    clock[0] += 3 * HOUR + 60
    asyncio.run(alerts.check(snap(12, 12, planned=[18, 20])))
    assert "18 Stunden" in alerts.pushed[0][1]


class Store:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.data, self.reads = rows, 0

    def rows(self, start: float, end: float) -> list[dict[str, Any]]:
        self.reads += 1
        return self.data


def test_the_light_day_reads_the_store_seldom() -> None:
    now = [24.0 * HOUR]
    store = Store(rows_of(18))
    day = LightDay(store, clock=lambda: now[0])
    first = asyncio.run(day.read())
    assert first["lit_hours"] == 18 and first["dark_hours"] == 6
    now[0] += DAY_REFRESH - 1
    assert asyncio.run(day.read()) == first and store.reads == 1
    now[0] += 2
    asyncio.run(day.read())
    assert store.reads == 2


class Recorder:
    def __init__(self) -> None:
        self.snaps: list[dict[str, Any]] = []

    async def check(self, snapshot: dict[str, Any]) -> list[str]:
        self.snaps.append(snapshot)
        return []


def test_the_poll_hands_both_to_the_alarms() -> None:
    class Usb:
        def status(self) -> dict[str, Any]:
            return dict(GONE)

    class Day:
        async def read(self) -> dict[str, Any]:
            return {"lit_hours": 24, "dark_hours": 0, "measured_hours": 24}

    seen = Recorder()
    asyncio.run(poll_handler(seen, None, {"camera": None, "usb_camera": Usb(), "light_day": Day()})(DemoBox()))  # type: ignore[arg-type]
    assert seen.snaps[0]["usb_camera"]["reachable"] is False
    assert seen.snaps[0]["light_day"]["dark_hours"] == 0


def test_a_broken_store_stops_no_alarm_and_decides_nothing() -> None:
    class Broken:
        async def read(self) -> dict[str, Any]:
            raise OSError("disk")

    seen = Recorder()
    asyncio.run(poll_handler(seen, None, {"camera": None, "light_day": Broken()})(DemoBox()))  # type: ignore[arg-type]
    assert len(seen.snaps) == 1 and "light_day" not in seen.snaps[0]
    assert breached(RULES["dark_period"], seen.snaps[0]) is None
