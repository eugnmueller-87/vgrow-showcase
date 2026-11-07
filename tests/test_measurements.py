"""The local measurement store (vgrow/measurements.py): every minute row kept once, under STATE_DIR, never in the
way of the poll. Offline: no login, no network."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
import time
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, measurements
from vgrow.alerts import Alerts
from vgrow.box import Box, scale_row
from vgrow.demo import DemoBox
from vgrow.i18n import TextError
from vgrow.measurements import (
    BACKFILL_SECONDS,
    COLLECT_SECONDS,
    FIRST_SECONDS,
    SOURCE_POINTLOG,
    SOURCE_POLL,
    WINDOW_SECONDS,
    Collector,
    MeasurementStore,
)
from vgrow.server import COOKIE, make_app, poll_handler
from vgrow.vivosun.models import DeviceInfo

VGROW = DeviceInfo("dev1", "vivosun-VSCTLY18-acct-dev1", "vivosun/VS_COMMON/VSCTLY18/acct/dev1", "VGrow", True, 1,
                   "controller")
DAY = date(2026, 10, 22)
DAY_START = int(datetime(2026, 10, 22, tzinfo=UTC).timestamp())
DAY_END = DAY_START + 86400


def row(ts: int, temp: int = 2350, humi: int = 5500) -> dict[str, Any]:
    """A point-log row like the VGrow sends: built-in sensor, probe, sentinels for what is not fitted."""
    return {"time": ts, "bTemp": temp, "bHumi": humi, "bVpd": 130, "pTemp": 2280, "pHumi": 6010, "pVpd": 110,
            "outTemp": -6666, "outHumi": -6666, "outVpd": -6666, "inTemp": -6666, "inHumi": -6666, "inVpd": -6666,
            "waterLv": 20000, "waterT": -6666, "coreTemp": 3510, "rssi": -47}


def minute_rows(start: int, minutes: int, offset: int = 5) -> list[dict[str, Any]]:
    """One row per minute, `offset` seconds into the minute (the cloud's own stamps are not on the second)."""
    return [row(start + 60 * i + offset) for i in range(minutes)]


def utc_store(**kwargs: Any) -> MeasurementStore:
    return MeasurementStore(tz=UTC, clock=lambda: DAY_END + 3600, **kwargs)


def count(store: MeasurementStore) -> int:
    con = sqlite3.connect(store.path())
    try:
        return con.execute("SELECT COUNT(*) FROM m").fetchone()[0]
    finally:
        con.close()


SNAP = {"light": {"level": 75}, "cfan": {"step": 5}, "dfan": {"step": 4},
        "ports": {"usb1": {"on": True}, "usb2": {"on": False}}}


# ---------- where it lives ----------

def test_the_store_lives_under_state_dir_read_at_each_call(isolated_state, monkeypatch,  # type: ignore[no-untyped-def]
                                                          tmp_path) -> None:
    store = utc_store()
    assert store.add_rows(minute_rows(DAY_START, 3), now=DAY_END) == 3
    assert store.path() == isolated_state / "measurements" / "measurements.sqlite3" and store.path().exists()
    other = tmp_path / "other-state"
    monkeypatch.setattr(config, "STATE_DIR", other)  # as serve() does for the demo: the same object follows
    assert store.path() == other / "measurements" / "measurements.sqlite3"
    assert store.add_rows(minute_rows(DAY_START, 2), now=DAY_END) == 2
    assert count(store) == 2  # a fresh file in the new folder; the first one is untouched
    assert not (config.REPO / "measurements").exists() and not list(config.REPO.glob("*.sqlite3"))


def test_nothing_is_created_by_reading_an_empty_store(isolated_state) -> None:  # type: ignore[no-untyped-def]
    store = utc_store()
    summary = store.summary(2, now=DAY_END)
    assert summary["rows_total"] == 0 and summary["first"] is None
    assert [d["minutes_stored"] for d in summary["days"]] == [0, 0]
    assert store.rows(DAY_START, DAY_END) == [] and not (isolated_state / "measurements").exists()


# ---------- once per minute, whatever the source ----------

def test_the_same_rows_twice_are_stored_once() -> None:
    store = utc_store()
    rows = minute_rows(DAY_START, 20)
    assert store.add_rows(rows, now=DAY_END) == 20
    assert store.add_rows(rows, now=DAY_END) == 0
    assert count(store) == 20


def test_overlapping_windows_keep_each_minute_once() -> None:
    store = utc_store()
    assert store.add_rows(minute_rows(DAY_START, 20), now=DAY_END) == 20
    assert store.add_rows(minute_rows(DAY_START + 15 * 60, 20), now=DAY_END) == 15  # 5 minutes overlap
    assert count(store) == 35
    stamps = [r["time"] for r in store.rows(DAY_START, DAY_END)]
    assert stamps == sorted(set(stamps)) and len(stamps) == 35


def test_a_poll_row_is_upgraded_by_the_point_log_and_keeps_light_and_fans() -> None:
    store = utc_store()
    reading = row(DAY_START + 65)
    at = float(reading["time"])
    assert store.add_poll(scale_row(reading), at, SNAP, now=at + 40) == 1
    first = store.rows(DAY_START, DAY_END)[0]
    assert first["source"] == SOURCE_POLL and first["bTemp"] == 23.5 and first["light"] == 75
    assert first["cfan"] == 5 and first["dfan"] == 4 and first["ports"] == {"usb1": 1, "usb2": 0}
    assert store.add_rows([reading], now=at + 600) == 0  # the same second: merged, not a second row
    merged = store.rows(DAY_START, DAY_END)
    assert len(merged) == 1 and merged[0]["source"] == SOURCE_POINTLOG and merged[0]["light"] == 75
    assert merged[0]["extra"] is None  # no unknown keys in this row


def test_the_point_log_first_then_the_poll_stays_point_log_and_gains_light() -> None:
    store = utc_store()
    reading = row(DAY_START + 65)
    store.add_rows([reading], now=DAY_END)
    store.add_poll(scale_row(reading), float(reading["time"]), SNAP, now=reading["time"] + 30)
    kept = store.rows(DAY_START, DAY_END)
    assert len(kept) == 1 and kept[0]["source"] == SOURCE_POINTLOG and kept[0]["light"] == 75


def test_a_stale_poll_reading_gets_no_light_or_fans() -> None:
    store = utc_store()
    reading = row(DAY_START + 65)
    store.add_poll(scale_row(reading), float(reading["time"]), SNAP, now=reading["time"] + 840)  # upload stalled
    kept = store.rows(DAY_START, DAY_END)[0]
    assert kept["light"] is None and kept["cfan"] is None and kept["ports"] is None


def test_rows_without_a_time_or_a_reading_are_skipped_and_unknown_keys_kept() -> None:
    store = utc_store()
    now = DAY_START + 3600
    sentinels = {k: -6666 for k in ("bTemp", "bHumi", "pTemp", "pHumi", "inTemp", "inHumi")}
    rows = [
        {**row(DAY_START + 60), "lightLv": 75, "flag": True, "name": "x"},  # a number is kept, a bool or text not
        {**row(DAY_START + 120), "time": None},
        {"time": DAY_START + 180, **sentinels},  # sensor and probe gone: no reading
        row(now + 3600),  # an hour in the future
        {**row(DAY_START + 240), "bTemp": 23.5, "bHumi": True},  # only integers count, as the cloud sends them
        "not a row",
    ]
    assert store.add_rows(rows, now=now) == 2  # type: ignore[arg-type]
    first, second = store.rows(DAY_START, DAY_END)
    assert first["extra"] == {"lightLv": 75} and first["waterLv_raw"] == 20000  # kept raw, as box.scale_row does
    assert first["outTemp"] is None and first["coreTemp"] == 35.1 and first["rssi"] == -47
    assert second["time"] == DAY_START + 240 and second["bTemp"] is None and second["pTemp"] == 22.8


# ---------- coverage, gaps, summary ----------

def test_a_full_day_of_minute_rows_is_100_percent() -> None:
    store = utc_store()
    store.add_rows(minute_rows(DAY_START, 1440), now=DAY_END)
    day = store.day_summary(DAY, now=DAY_END + 60)
    assert day["minutes_expected"] == 1440 and day["minutes_stored"] == 1440
    assert day["coverage_percent"] == 100.0 and day["gap_count"] == 0 and day["complete"] is True
    assert day["sources"] == {"pointlog": 1440, "poll": 0}


def simulate_polls(store: MeasurementStore, start: int, end: int) -> None:
    """The real poll: every 90 s it asks for the last 15 minutes and keeps only the newest row (box.poll_sensors)."""
    readings = minute_rows(start, (end - start) // 60)
    t = start + 30
    while t < end:
        newest = max((r for r in readings if r["time"] <= t), key=lambda r: r["time"], default=None)
        if newest is not None:
            store.add_poll(scale_row(newest), float(newest["time"]), SNAP, now=t)
        t += 90


def test_poll_only_sees_about_two_minutes_in_three() -> None:
    store = utc_store()
    simulate_polls(store, DAY_START, DAY_END)
    day = store.day_summary(DAY, now=DAY_END + 60)
    assert 66.0 <= day["coverage_percent"] <= 67.5, day["coverage_percent"]
    assert day["sources"]["pointlog"] == 0 and day["sources"]["poll"] == day["minutes_stored"]
    assert day["gap_count"] == 0  # one-minute holes everywhere, but no stretch of 5 minutes
    store.add_rows(minute_rows(DAY_START, 1440), now=DAY_END)  # the collector fills the rest
    filled = store.day_summary(DAY, now=DAY_END + 60)
    assert filled["coverage_percent"] == 100.0 and filled["sources"]["poll"] == 0
    assert filled["metrics"]["light"]["n"] == day["minutes_stored"]  # light stays on the poll's minutes


def test_gaps_are_found_and_counted_in_the_summary() -> None:
    store = utc_store()
    rows = minute_rows(DAY_START, 1440)
    del rows[1400:]  # the last 40 minutes missing
    del rows[600:630]  # 10:00 to 10:30 missing
    store.add_rows(rows, now=DAY_END)
    gaps = store.gaps(DAY_START, DAY_END)
    assert [(g["from"], g["minutes"]) for g in gaps] == [(DAY_START + 600 * 60, 30), (DAY_START + 1400 * 60, 40)]
    assert store.gaps(DAY_START, DAY_END, min_minutes=31) == [gaps[1]]
    day = store.day_summary(DAY, now=DAY_END + 60)
    assert day["gap_count"] == 2 and day["gap_minutes"] == 70 and day["minutes_stored"] == 1370
    assert day["coverage_percent"] == round(100 * 1370 / 1440, 1)
    assert store.gaps(DAY_START - 600, DAY_START + 3600)[0] == {"from": DAY_START - 600, "to": DAY_START,
                                                                 "minutes": 10}


def test_today_counts_only_the_minutes_so_far() -> None:
    store = utc_store()
    store.add_rows(minute_rows(DAY_START, 360), now=DAY_START + 6 * 3600)
    day = store.day_summary(DAY, now=DAY_START + 6 * 3600 + 30)
    assert day["minutes_expected"] == 360 and day["coverage_percent"] == 100.0 and day["complete"] is False


def test_daily_summary_min_max_mean_and_minutes_in_a_band() -> None:
    store = utc_store()
    rows = [row(DAY_START + 60 * i + 5, temp=2000 + i, humi=5000) for i in range(100)]  # 20.00 to 20.99 C
    store.add_rows(rows, now=DAY_END)
    day = store.day_summary(DAY, {"temp": (20.5, 21.0), "probe_vpd": (0.8, 1.2), "light": (50, 100)},
                            now=DAY_END + 60)
    temp = day["metrics"]["temp"]
    assert temp == {"min": 20.0, "max": 20.99, "mean": 20.5, "n": 100}
    assert day["metrics"]["probe_vpd"]["mean"] == 1.1 and "room_temp" not in day["metrics"]
    assert day["bands"]["temp"] == {"low": 20.5, "high": 21.0, "minutes": 50, "percent_of_measured": 50.0}
    assert day["bands"]["probe_vpd"]["minutes"] == 100
    assert day["bands"]["light"] == {"low": 50, "high": 100, "minutes": 0, "percent_of_measured": None}
    assert day["coverage_percent"] == round(100 * 100 / 1440, 1)


def test_rows_with_a_step_are_bucket_means() -> None:
    store = utc_store()
    store.add_rows([row(DAY_START + 60 * i + 5, temp=2000 + 10 * i) for i in range(10)], now=DAY_END)
    buckets = store.rows(DAY_START, DAY_END, step=300)
    assert [(b["time"], b["n"], b["bTemp"]) for b in buckets] == [(DAY_START, 5, 20.2), (DAY_START + 300, 5, 20.7)]
    assert buckets[0]["source"] == SOURCE_POINTLOG
    assert len(store.rows(DAY_START, DAY_START + 120)) == 2  # end is exclusive, start inclusive


def test_summary_runs_over_local_days_oldest_first() -> None:
    store = utc_store()
    store.add_rows(minute_rows(DAY_START - 86400, 1440) + minute_rows(DAY_START, 1440), now=DAY_END)
    summary = store.summary(3, now=DAY_END + 60)  # the clock is on the 23rd: 21st, 22nd, 23rd
    assert [d["date"] for d in summary["days"]] == ["2026-10-21", "2026-10-22", "2026-10-23"]
    assert [d["coverage_percent"] for d in summary["days"]] == [100.0, 100.0, 0.0]
    assert summary["rows_total"] == 2880 and summary["first"] == DAY_START - 86400 + 5
    assert len(store.summary(10_000, now=DAY_END)["days"]) == measurements.MAX_SUMMARY_DAYS


def _local_zone_is_berlin() -> bool:
    """The machine's own zone switches like Berlin's in 2026 (winter +1 h, summer +2 h, the changes on 29.03. and
    25.10. at 01:00 UTC): then tz=None, the path the Mac takes, can be tested even without a zone database."""
    def offset(year: int, month: int, day: int, hour: int) -> float:
        moment = datetime(year, month, day, hour, tzinfo=UTC).astimezone()
        delta = moment.utcoffset()
        return delta.total_seconds() / 3600 if delta is not None else 0.0

    return (offset(2026, 1, 15, 12), offset(2026, 3, 29, 0), offset(2026, 3, 29, 2), offset(2026, 7, 15, 12),
            offset(2026, 10, 25, 0), offset(2026, 10, 25, 2)) == (1, 1, 2, 2, 2, 1)


def test_a_day_in_the_machines_zone_has_23_or_25_hours_on_a_dst_change() -> None:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    stores = []
    with contextlib.suppress(ZoneInfoNotFoundError):
        stores.append(MeasurementStore(tz=ZoneInfo("Europe/Berlin")))
    if _local_zone_is_berlin():
        stores.append(MeasurementStore())  # tz=None: the machine's zone, as serve() builds it
    if not stores:
        pytest.skip("neither a zone database nor a Berlin machine zone here; the Mac has both")
    for store in stores:
        start, end = store.day_bounds(date(2026, 10, 25))  # clocks go back: 25 hours
        assert end - start == 25 * 3600
        start, end = store.day_bounds(date(2026, 3, 29))
        assert end - start == 23 * 3600
        assert store.today(datetime(2026, 10, 24, 22, 30, tzinfo=UTC).timestamp()) == date(2026, 10, 25)


def test_day_buckets_start_at_local_midnight() -> None:
    summer = timezone(timedelta(hours=2))  # Berlin in summer time, without a zone database
    store = MeasurementStore(tz=summer, clock=lambda: DAY_END + 86400)
    local_midnight = DAY_START - 7200  # 22.10. 00:00 in +02:00
    store.add_rows(minute_rows(local_midnight, 2 * 1440), now=DAY_END + 86400)
    buckets = store.rows(local_midnight, local_midnight + 2 * 86400, step=86400)
    assert [(b["time"], b["n"]) for b in buckets] == [(local_midnight, 1440), (local_midnight + 86400, 1440)]
    hours = store.rows(local_midnight, local_midnight + 3 * 3600, step=3600)
    assert [b["time"] for b in hours] == [local_midnight + 3600 * i for i in range(3)]


def test_a_year_of_minutes_is_a_few_ten_megabytes_at_most() -> None:
    store = utc_store()
    store.add_rows(minute_rows(DAY_START, 1440), now=DAY_END)
    size = store.path().stat().st_size
    assert size < 120_000, size  # about 70 KB a day measured on 23.10.2026: some 25 MB a year


def test_an_unreadable_file_is_kept_aside_and_a_new_one_started(isolated_state) -> None:  # type: ignore[no-untyped-def]
    store = utc_store()
    store.path().parent.mkdir(parents=True)
    store.path().write_bytes(b"this is not a database at all, but it is somebody's data" * 100)
    assert store.add_rows(minute_rows(DAY_START, 2), now=DAY_END) == 2
    kept = [p.name for p in store.path().parent.iterdir() if ".unreadable-" in p.name]
    assert len(kept) == 1 and count(store) == 2


def damage(path: Path) -> None:
    """Overwrite every page from 8 KiB on: the header still reads, the tables do not."""
    size = path.stat().st_size
    with path.open("r+b") as handle:
        handle.seek(8192)
        handle.write(b"\xa5" * (size - 8192))


def test_a_damaged_file_that_still_opens_is_found_by_the_check_and_kept_aside() -> None:
    store = utc_store()
    store.add_rows(minute_rows(DAY_START, 1440), now=DAY_END)
    damage(store.path())
    restarted = utc_store()  # a new process: the quick check runs on the first open
    assert restarted.add_rows(minute_rows(DAY_START, 2), now=DAY_END) == 2
    names = sorted(p.name for p in store.path().parent.iterdir())
    assert names == ["measurements.sqlite3", f"measurements.sqlite3.unreadable-{DAY_END + 3600}"]
    assert count(restarted) == 2


def test_a_file_set_aside_takes_its_journal_with_it() -> None:
    """SQLite plays a hot journal into whatever file sits at its name: left behind, it would land in the new one."""
    store = utc_store()
    main = store.path()
    main.parent.mkdir(parents=True)
    for name, data in ((main.name, b"damaged"), (main.name + "-journal", b"journal"), (main.name + "-wal", b"wal")):
        main.with_name(name).write_bytes(data)
    (main.parent / "measurements.sqlite3.unreadable-1").write_bytes(b"an older one")
    store._set_aside(main, sqlite3.DatabaseError("database disk image is malformed"))
    aside = main.with_name(f"{main.name}.unreadable-{DAY_END + 3600}")
    assert aside.read_bytes() == b"damaged"
    assert aside.with_name(aside.name + "-journal").read_bytes() == b"journal"
    assert aside.with_name(aside.name + "-wal").read_bytes() == b"wal"
    assert not main.exists() and not main.with_name(main.name + "-journal").exists()
    main.write_bytes(b"damaged again")
    store._set_aside(main, sqlite3.DatabaseError("again, the same second"))
    assert main.with_name(f"{aside.name}-1").read_bytes() == b"damaged again"  # never overwrites the first


def test_damage_found_during_a_write_moves_the_file_aside_and_the_write_goes_through() -> None:
    store = utc_store()
    store.add_rows(minute_rows(DAY_START, 1440), now=DAY_END)  # checked once: no second check in this process
    damage(store.path())
    assert store.add_rows(minute_rows(DAY_END - 3600, 61), now=DAY_END + 60) == 61  # not stuck for good
    assert count(store) == 61 and store.add_rows(minute_rows(DAY_END + 60, 2), now=DAY_END + 600) == 2
    assert len([p for p in store.path().parent.iterdir() if ".unreadable-" in p.name]) == 1


def test_another_device_gets_its_own_file() -> None:
    store = utc_store()
    assert store.add_rows(minute_rows(DAY_START, 10), now=DAY_END, device_id="dev1") == 10
    assert store.add_rows(minute_rows(DAY_START, 20), now=DAY_END, device_id="new/box 2") == 20
    other = store.path().with_name("new_box_2.sqlite3")
    assert other.exists() and count(store) == 10
    assert len(store.rows(DAY_START, DAY_END)) == 20  # reads follow the device written last
    restarted = utc_store()  # a new process knows the main file's owner from the file
    assert restarted.add_rows(minute_rows(DAY_START + 3600, 5), now=DAY_END, device_id="dev1") == 5
    assert count(restarted) == 15 and len(restarted.rows(DAY_START, DAY_END)) == 15
    con = sqlite3.connect(store.path())
    try:
        assert con.execute("SELECT value FROM meta WHERE key = 'device_id'").fetchone() == ("dev1",)
    finally:
        con.close()


def test_a_file_of_the_first_version_gains_the_meta_table() -> None:
    store = utc_store()
    store.path().parent.mkdir(parents=True)
    con = sqlite3.connect(store.path())
    con.execute(measurements._schema())
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    assert store.add_rows(minute_rows(DAY_START, 3), now=DAY_END, device_id="dev1") == 3
    con = sqlite3.connect(store.path())
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == measurements.SCHEMA_VERSION
        assert con.execute("SELECT value FROM meta").fetchall() == [("dev1",)]
    finally:
        con.close()


# ---------- the collector: backfill, the window, failures ----------

class RowsBox:
    """The part of Box the collector uses: point_rows(seconds), counted."""

    def __init__(self, rows: list[dict[str, Any]], clock: list[float]) -> None:
        self.rows, self.clock, self.asked = rows, clock, []  # type: ignore[var-annotated]
        self.fail = False

    async def point_rows(self, seconds: int) -> list[dict[str, Any]]:
        self.asked.append(seconds)
        if self.fail:
            raise RuntimeError("cloud down")
        now = self.clock[0]
        return [r for r in self.rows if now - seconds <= r["time"] <= now]


def test_the_collector_backfills_once_then_asks_for_two_hours() -> None:
    store = utc_store()
    clock = [float(DAY_START + 12 * 3600)]
    box = RowsBox(minute_rows(DAY_START - 86400, 2 * 1440), clock)
    collector = Collector(box, store, clock=lambda: clock[0])
    assert asyncio.run(collector.tick()) == 1440  # the last 24 hours, whatever the cloud had
    clock[0] += COLLECT_SECONDS
    assert asyncio.run(collector.tick()) == 15
    assert box.asked == [BACKFILL_SECONDS, WINDOW_SECONDS]
    assert asyncio.run(collector.tick()) == 0  # the same minutes again: nothing new, nothing doubled
    assert count(store) == 1455


def test_a_failing_cloud_call_is_caught_and_the_next_window_catches_up() -> None:
    store = utc_store()
    clock = [float(DAY_START + 12 * 3600)]
    box = RowsBox(minute_rows(DAY_START - 86400, 2 * 1440), clock)
    collector = Collector(box, store, clock=lambda: clock[0])
    asyncio.run(collector.tick())
    box.fail = True
    for _ in range(4):  # an hour without the cloud: the two-hour window still covers it
        clock[0] += COLLECT_SECONDS
        assert asyncio.run(collector.tick()) is None
    assert box.asked[-1] == WINDOW_SECONDS
    for _ in range(8):  # three hours in all
        clock[0] += COLLECT_SECONDS
        assert asyncio.run(collector.tick()) is None
    box.fail = False
    clock[0] += COLLECT_SECONDS
    assert asyncio.run(collector.tick()) == 195  # the 195 minutes since the last good request, in one request
    assert box.asked[-1] == 195 * 60 + measurements.CATCH_UP_MARGIN and len(box.asked) == 14
    day = store.day_summary(DAY, now=clock[0])
    assert day["gap_count"] == 0 and day["coverage_percent"] == 100.0


def test_a_failed_backfill_is_tried_again_as_a_backfill() -> None:
    store = utc_store()
    clock = [float(DAY_START + 3600)]
    box = RowsBox(minute_rows(DAY_START - 86400, 1440 + 60), clock)
    box.fail = True
    collector = Collector(box, store, clock=lambda: clock[0])
    assert asyncio.run(collector.tick()) is None
    box.fail = False
    clock[0] += COLLECT_SECONDS
    assert asyncio.run(collector.tick()) is not None
    assert box.asked == [BACKFILL_SECONDS, BACKFILL_SECONDS]


def test_the_loop_sends_one_request_per_15_minutes() -> None:
    store = utc_store()
    clock = [float(DAY_START + 3600)]
    box = RowsBox(minute_rows(DAY_START, 60), clock)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds
        if len(waits) > 4:
            raise asyncio.CancelledError

    collector = Collector(box, store, sleep=sleep, clock=lambda: clock[0])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collector.loop())
    assert waits == [FIRST_SECONDS] + [COLLECT_SECONDS] * 4 and len(box.asked) == 4


def test_the_collector_never_logs_in() -> None:
    box = Box("x@example.test", "unused")  # not logged in: no tokens, no device
    calls: list[str] = []

    async def login() -> None:
        calls.append("login")

    box._login = login  # type: ignore[method-assign]
    with pytest.raises(TextError):
        asyncio.run(box.point_rows(1200))
    collector = Collector(box, utc_store())
    assert asyncio.run(collector.tick()) is None
    assert calls == [] and box._gate.recent() == [] and collector.backfilled is False


class PointApi:
    def __init__(self, rows: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.rows, self.fail, self.windows = rows or [], fail, []  # type: ignore[var-annotated]

    async def get_point_rows(self, tokens: Any, device: Any, *, start_time: int, end_time: int) -> list[dict[str, Any]]:
        self.windows.append(end_time - start_time)
        if self.fail:
            raise RuntimeError("cloud down")
        return self.rows


def logged_in_box(api: PointApi) -> Box:
    box = Box("x@example.test", "unused")
    box.device, box._tokens, box._api = VGROW, object(), api  # type: ignore[assignment]
    return box


def test_point_rows_goes_through_the_session_and_history_still_reads_through_it() -> None:
    now = int(time.time())
    api = PointApi([row(now - 120), row(now - 60)])
    box = logged_in_box(api)
    assert len(asyncio.run(box.point_rows(1200))) == 2
    assert asyncio.run(box.point_rows(10**9)) and asyncio.run(box.point_rows(1))
    history = asyncio.run(box.history(24))
    assert [r["bTemp"] for r in history] == [23.5, 23.5]
    assert api.windows == [1200, 168 * 3600, 60, 24 * 3600]


class Steps:
    """Stands in for the alerts and the automation in poll_handler: records that it was called."""

    def __init__(self, name: str, seen: list[str]) -> None:
        self.name, self.seen = name, seen

    async def check(self, snap: dict[str, Any]) -> None:
        self.seen.append(self.name)


def test_a_failing_store_or_cloud_never_stops_the_poll(isolated_state) -> None:  # type: ignore[no-untyped-def]
    now = int(time.time())
    box = logged_in_box(PointApi([row(now - 30)]))
    store = MeasurementStore()
    seen: list[str] = []
    box.listeners.append(measurements.poll_listener(store))  # first: a failing store must not keep the rest away
    box.listeners.append(poll_handler(Steps("alerts", seen), Steps("automation", seen),  # type: ignore[arg-type]
                                      {"camera": None}))

    def broken(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.MonkeyPatch.context() as patch:  # its own: undoing it must not undo the isolated state folder
        patch.setattr(store, "add_from_poll", broken)
        asyncio.run(box.poll_once())
    assert seen == ["alerts", "automation"] and box.sensors_at == now - 30 and box.last_error is None
    assert not store.path().exists()
    asyncio.run(box.poll_once())
    assert seen == ["alerts", "automation"] * 2 and count(store) == 1  # the row, with the snapshot's state
    kept = store.rows(0, now + 60)[0]
    assert store.path().is_relative_to(isolated_state) and kept["source"] == SOURCE_POINTLOG
    box._api = PointApi(fail=True)  # type: ignore[assignment]
    asyncio.run(box.poll_once())  # the cloud fails: the listeners still run, the store simply has nothing new
    assert seen == ["alerts", "automation"] * 3 and count(store) == 1 and "cloud down" in (box.last_error or "")
    assert box.last_rows == []
    collector = Collector(box, store)
    assert asyncio.run(collector.tick()) is None  # caught and logged, never raised


def test_a_hung_store_write_never_holds_up_the_next_poll() -> None:
    now = int(time.time())
    box = logged_in_box(PointApi([row(now - 30)]))
    store = MeasurementStore()
    seen: list[str] = []
    release = threading.Event()
    calls: list[int] = []

    def hung(*args: Any, **kwargs: Any) -> int:
        calls.append(1)
        release.wait(10)  # a stuck disk
        return 0

    box.listeners.append(measurements.poll_listener(store, timeout=0.2))
    box.listeners.append(poll_handler(Steps("alerts", seen), Steps("automation", seen),  # type: ignore[arg-type]
                                      {"camera": None}))

    async def run() -> float:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(store, "add_from_poll", hung)
            await box.poll_once()  # waits 0.2 s on the disk, then leaves the write behind
            started = time.monotonic()
            await box.poll_once()  # the write still hangs: skipped, no second thread behind it
            took = time.monotonic() - started
            release.set()
            await asyncio.sleep(0.05)
            return took

    try:
        took = asyncio.run(run())
    finally:
        release.set()
    assert took < 0.15 and len(calls) == 1
    assert seen == ["alerts", "automation"] * 2  # the alarms and the automation ran on both polls


def test_the_poll_stores_every_row_of_its_window() -> None:
    now = int(time.time())
    window = [row(now - 900 + 60 * i) for i in range(15)]  # what the poll's 15-minute request returns
    box = logged_in_box(PointApi(window))
    store = MeasurementStore()
    box.listeners.append(measurements.poll_listener(store))
    asyncio.run(box.poll_once())
    kept = store.rows(0, now + 60)
    assert len(kept) == 15 and {r["source"] for r in kept} == {SOURCE_POINTLOG}
    assert box.sensors_at == now - 60 and len(box.last_rows) == 15
    asyncio.run(box.poll_once())  # 90 s later the same window again: nothing doubled
    assert count(store) == 15


def test_late_uploads_up_to_an_hour_are_all_kept_by_the_collector() -> None:
    """The cloud gets some rows 10 to 60 minutes after their own time (a stalled upload): the two-hour window still
    finds every one of them, at one request per 15 minutes."""
    store = utc_store()
    rows = minute_rows(DAY_START, 1440)
    lag = {r["time"]: 600 + (i * 7919) % 3001 for i, r in enumerate(rows)}  # 10 to 60 minutes, spread

    class LateBox(RowsBox):
        async def point_rows(self, seconds: int) -> list[dict[str, Any]]:
            self.asked.append(seconds)
            now = self.clock[0]
            return [r for r in self.rows if now - seconds <= r["time"] and r["time"] + lag[r["time"]] <= now]

    clock = [float(DAY_START)]
    box = LateBox(rows, clock)
    collector = Collector(box, store, clock=lambda: clock[0])
    while clock[0] <= DAY_END + 3600 + COLLECT_SECONDS:
        asyncio.run(collector.tick())
        clock[0] += COLLECT_SECONDS
    day = store.day_summary(DAY, now=DAY_END + 7200)
    assert day["coverage_percent"] == 100.0 and day["gap_count"] == 0
    assert min(lag.values()) == 600 and max(lag.values()) >= 3500
    assert len(box.asked) == 102 and set(box.asked[1:]) == {WINDOW_SECONDS}  # 4 an hour, never more


def test_a_restart_right_after_a_request_waits_for_the_next_one() -> None:
    store = utc_store()
    clock = [float(DAY_START + 12 * 3600)]
    box = RowsBox(minute_rows(DAY_START - 86400, 2 * 1440), clock)
    first = Collector(box, store, clock=lambda: clock[0])
    assert asyncio.run(first.tick()) == 1440 and box.asked == [BACKFILL_SECONDS]
    saved = json.loads(first.state_path().read_text(encoding="utf-8"))
    assert saved["last_request"] == saved["last_ok"] == clock[0]
    assert first.state_path() == config.STATE_DIR / "measurements" / "collector.json"

    clock[0] += 40  # a deploy restarts the server 40 s later
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds
        if len(waits) > 2:
            raise asyncio.CancelledError

    second = Collector(box, store, sleep=sleep, clock=lambda: clock[0])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(second.loop())
    assert waits[0] == COLLECT_SECONDS - 40  # no request before the 15 minutes are up
    assert box.asked == [BACKFILL_SECONDS, WINDOW_SECONDS, WINDOW_SECONDS]  # and no second backfill


def test_an_old_or_broken_state_file_means_a_backfill() -> None:
    store = utc_store()
    clock = [float(DAY_START + 12 * 3600)]
    box = RowsBox([], clock)
    collector = Collector(box, store, clock=lambda: clock[0])
    collector.state_path().parent.mkdir(parents=True)
    collector.state_path().write_text("{not json", encoding="utf-8")
    assert collector.load_state() == {}
    old = {"last_request": clock[0] - 2 * 86400, "last_ok": clock[0] - 2 * 86400}
    collector.seed(old, clock[0])
    assert collector.backfilled is False and collector.window(clock[0]) == BACKFILL_SECONDS
    assert collector.first_wait(old, clock[0]) == FIRST_SECONDS
    assert collector.first_wait({"last_request": clock[0] + 10**6}, clock[0]) == COLLECT_SECONDS  # a clock jump
    other = {"last_request": clock[0] - 60, "last_ok": clock[0] - 60, "device_id": "another-box"}
    box.device = VGROW  # type: ignore[attr-defined]
    collector.seed(other, clock[0])
    assert collector.backfilled is False  # a different device: its last request says nothing about this one
    collector.seed({**other, "device_id": "dev1"}, clock[0])
    assert collector.backfilled is True and collector.window(clock[0]) == WINDOW_SECONDS


def test_the_first_request_waits_for_the_login_without_counting_a_failure() -> None:
    box = Box("x@example.test", "unused")  # the login is still running
    store = utc_store()
    api = PointApi([row(DAY_START + 65)])
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 4:  # logged in during the third wait
            box.device, box._tokens, box._api = VGROW, object(), api  # type: ignore[assignment]
        if len(waits) > 4:
            raise asyncio.CancelledError

    collector = Collector(box, store, sleep=sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collector.loop())
    assert waits == [FIRST_SECONDS] + [measurements.LOGIN_WAIT_SECONDS] * 3 + [COLLECT_SECONDS]
    assert api.windows == [BACKFILL_SECONDS] and collector.requests == 1 and collector.backfilled


# ---------- serve(), the demo, the endpoint ----------

def test_setup_wires_a_real_box_and_leaves_the_demo_alone() -> None:
    box = logged_in_box(PointApi())
    store, collector = measurements.setup(box, demo=False)
    assert isinstance(store, MeasurementStore) and isinstance(collector, Collector) and len(box.listeners) == 1
    demo = DemoBox()
    assert measurements.setup(demo, demo=True) == (None, None) and demo.listeners == []


def test_serve_in_the_demo_stores_nothing(isolated_state, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from vgrow import server

    socks: list[Any] = []
    real_bind = server.bind_socket

    def bind(host: str, port: int) -> Any:
        socks.append(real_bind(host, port))
        return socks[-1]

    monkeypatch.setattr(server, "bind_socket", bind)

    async def run() -> dict[str, Any]:
        import aiohttp

        task = asyncio.create_task(server.serve(DemoBox(), port=0, host="127.0.0.1"))
        try:
            for _ in range(200):
                if socks or task.done():
                    break
                await asyncio.sleep(0.02)
            assert socks and not task.done()
            base = f"http://127.0.0.1:{socks[0].getsockname()[1]}"
            key = config.access_key_path().read_text(encoding="utf-8")
            async with aiohttp.ClientSession(cookies={COOKIE: key}) as session:
                for _ in range(200):
                    with contextlib.suppress(aiohttp.ClientError):
                        async with session.get(f"{base}/api/measurements/summary") as resp:
                            if resp.status == 200:
                                return await resp.json()
                    await asyncio.sleep(0.05)
            raise AssertionError("no answer")
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    data = asyncio.run(run())
    assert data == {"store": False, "demo": True, "days": []}
    assert not list(isolated_state.rglob("measurements*"))
    # The issue/action/result protocol keeps its own disposable demo history, never sensor measurements.
    assert set(isolated_state.rglob("*.sqlite3")) <= {isolated_state / "demo" / "boxes" / "box-1" / "hank-protocol.sqlite3"}


async def summary_client(store: MeasurementStore | None) -> TestClient:
    client = TestClient(TestServer(make_app(DemoBox(), Alerts(), "k", measurements=store)))
    await client.start_server()
    return client


def test_the_summary_endpoint_is_behind_the_key_and_answers_the_days() -> None:
    store = MeasurementStore()
    now = int(time.time())
    store.add_rows(minute_rows(now - 3 * 3600, 170), now=now)

    async def run() -> None:
        client = await summary_client(store)
        try:
            assert (await client.get("/api/measurements/summary")).status == 403
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            resp = await client.get("/api/measurements/summary?days=2&band=probe_vpd:0.8:1.2")
            data = await resp.json()
            assert resp.status == 200 and data["store"] is True and data["demo"] is False
            assert len(data["days"]) == 2 and data["rows_total"] == 170
            assert sum(d["bands"]["probe_vpd"]["minutes"] for d in data["days"]) == 170
            for bad in ("days=many", "band=probe_vpd:1.2:0.8", "band=nope:1:2", "band=temp:1"):
                resp = await client.get(f"/api/measurements/summary?{bad}", headers={"X-Lang": "en"})
                assert resp.status == 400 and "Invalid request" in (await resp.json())["error"]
        finally:
            await client.close()

    asyncio.run(run())


def test_the_summary_endpoint_says_so_when_the_store_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = MeasurementStore()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "summary", broken)

    async def run() -> None:
        client = await summary_client(store)
        try:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            resp = await client.get("/api/measurements/summary")
            assert resp.status == 503
            assert (await resp.json())["error"] == "Der Messwertspeicher ist gerade nicht lesbar."
            assert (await client.get("/api/state")).status == 200  # the cockpit itself is untouched
        finally:
            await client.close()

    asyncio.run(run())


def test_the_module_text_is_clean() -> None:
    for path in (Path(measurements.__file__), Path(__file__)):
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf") and b"\r\n" not in data
        assert chr(0x2014) not in data.decode("utf-8")
    assert json.dumps(measurements.METRICS)
