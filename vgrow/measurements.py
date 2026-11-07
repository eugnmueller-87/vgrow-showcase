"""The cockpit's own measurement store: every minute row of the VIVOSUN point log, kept on the Mac.

Why it exists: the charts read the point log over the cloud, and the cloud keeps about one day. A check on
23.10.2026 got the same 1,405 minute rows for 168 h as for 24 h (22.10. 09:03 to 23.10. 08:27). Every day without
a local copy is missing from the grow report for good.

Where it lives: STATE_DIR/measurements/measurements.sqlite3, with config.STATE_DIR read at every call, so the demo
(serve() points STATE_DIR at its own folder) and every test get their own file. Nothing is ever written into the
repository: the Mac deploys by git push into its working tree, and a changed tracked file would block the next deploy.

Why SQLite and not JSON lines per day (standard library either way):
- Deduplication is the primary key. The collector's windows overlap on purpose, and a poll snapshot is the same row
  the collector fetches a few minutes later; with JSON lines every write would first read the day's file back.
- A poll row is upgraded in place when the point log brings the same minute (one UPSERT); a line file would have to
  be rewritten.
- A write is one transaction: a crash or a power cut never leaves half a line behind.
- A report reads a time range through the primary key instead of parsing a year of files.
The timestamp is the INTEGER PRIMARY KEY (SQLite's rowid), so it is also the day index: a day is one range scan.

What a row holds: the raw point-log integers (hundredths for temperatures, humidity, VPD and the water readings,
upstream VIVOSUN-API-SPEC.md), so the store keeps exactly what the cloud sent and scaling stays one place
(box.scale_row). Light level and fan steps are not known point-log keys: they come from the poll's shadow snapshot,
attached only while the reading is fresh. Any other numeric key of a row is kept in `extra`, so a key not known
today (a port, a light value) is not lost.

Where a row came from (`source`): "pointlog" for every row of a point-log answer, "poll" for the poll's newest
reading as box.sensors holds it. The 90 s poll already asks for the last 15 minutes (box.poll_sensors) and keeps
only the newest row for itself; the store takes all of them (box.last_rows), so the poll alone covers every minute
and every upload up to 15 minutes late, at no extra cloud cost. The collector is the backup: one request every
15 minutes for the last two hours, for what the poll missed (a restart, a slow upload, a failed poll).

Light and fans: they are not in the point log. Each poll attaches the shadow's light level and fan steps to its
newest row only, so they sit on about two minutes in three (40 of 60 an hour), and only while the server ran.
Light hours, fan hours and the like must be computed from durations between changes, never from row counts.
USB reports are also recorded immediately in port_observations, separately from the sensor's time, by
vgrow/pump_history.py. This preserves short app-controlled pump runs between sensor polls. Event times are local
observation times, never proof of flow or a water quantity. Separate light and fan events are not recorded yet.

Which box: the file records the device it belongs to (table meta, device_id) on its first write. Rows of another
device (a second box, a replacement controller) go to measurements/<device_id>.sqlite3 instead, and a warning says
so; reads follow the device written last. The multi-box layout (docs/mehrere-pflanzen-boxen-entwurf.md 2.8, AP2)
can move these files without guessing whose they are.

The store never stops the server: every async entry point catches and logs its errors, a write that hangs is left
behind after a timeout (the next one is skipped while it still runs), and polling and the automation run on
whatever happens here. A damaged file is moved aside with its journal, never deleted, and a new one is started.
Retention: everything is kept; compacting old days is not done (yet).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from . import config
from .box import _SCALED, climate_view, row_time, scale_row
from .vivosun.const import SENSOR_UNAVAILABLE_SENTINEL

_LOGGER = logging.getLogger(__name__)

DIR_NAME = "measurements"
DB_NAME = "measurements.sqlite3"
STATE_NAME = "collector.json"  # the collector's last request, so a restart does not ask for a day again
SCHEMA_VERSION = 3  # 1: minute rows; 2: device identity; 3: reported USB states at their observation time

SOURCE_POINTLOG = "pointlog"
SOURCE_POLL = "poll"
_RANK = {SOURCE_POINTLOG: 1, SOURCE_POLL: 2}  # lower wins: the point log's row beats the poll's copy of it
_NAME = {v: k for k, v in _RANK.items()}

COLLECT_SECONDS = 15 * 60  # one point-log request every 15 minutes, never more
# Each asks for the last two hours (about 120 rows): a row the cloud gets up to 1 h 45 min after its own time is
# still caught. With 20 minutes, rows uploaded more than 5 minutes late were lost (94 % in the probe of 23.10.2026).
WINDOW_SECONDS = 2 * 3600
BACKFILL_SECONDS = 24 * 3600  # the first request without a recent one on file: the last day, whatever the cloud has
CATCH_UP_MARGIN = 5 * 60  # after failed requests the window reaches back to the last good one, plus this
FIRST_SECONDS = 30.0  # the backfill waits this long after the start, so it does not land on the boot poll
LOGIN_WAIT_SECONDS = 30.0  # before the first request the box must be logged in: checked this often,
LOGIN_WAIT_TRIES = 10  # at most this many times, then the request goes out (and fails, logged, if still not)
POLL_WRITE_TIMEOUT = 10.0  # a poll never waits longer than this on the disk
COLLECT_WRITE_TIMEOUT = 60.0
FRESH_SECONDS = 180  # light and fans from the shadow are attached to a poll row only this close to the reading
GAP_MINUTES = 5  # a missing stretch this long or longer is a gap; poll-only days have one-minute holes everywhere
MAX_GAPS = 50  # per day in the summary; the count says how many there were
MAX_EXTRA_KEYS = 24
MAX_SUMMARY_DAYS = 400

RAW_KEYS = tuple(_SCALED) + ("rssi",)  # the known point-log keys, kept as the integers the cloud sent
ACTUATORS = ("light", "cfan", "dfan")
_CLIMATE = ("inTemp", "inHumi", "bTemp", "bHumi", "pTemp", "pHumi")  # as box.newest_reading: a row needs one
COLUMNS = ("ts", "src", *RAW_KEYS, *ACTUATORS, "ports", "extra")
METRICS = ("temp", "humidity", "vpd", "probe_temp", "probe_humidity", "probe_vpd", "room_temp", "room_humidity",
           "water_level", "water_temp", "light", "cfan", "dfan")


def store_dir() -> Path:
    return config.STATE_DIR / DIR_NAME


class _SetAside(sqlite3.DatabaseError):
    """The file turned out damaged during a call and was moved aside: the call may be tried once more."""


def device_of(box: Any) -> str | None:
    """The box's device id (box.device.device_id), or None while it is not known (not logged in, the demo)."""
    value = getattr(getattr(box, "device", None), "device_id", None)
    return value if isinstance(value, str) and value else None


def _file_part(device_id: str) -> str:
    part = re.sub(r"[^A-Za-z0-9_-]", "_", device_id)[:64].strip("_")
    return part or "device"


def _consume(task: asyncio.Future[Any]) -> None:
    """A write left behind after its timeout still ends some day: its error must not be reported as never read."""
    if not task.cancelled():
        task.exception()


def _int(value: Any) -> int | None:
    """A cloud integer, or None for anything else (a bool, a float, the -6666 sentinel)."""
    return value if type(value) is int and value != SENSOR_UNAVAILABLE_SENTINEL else None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value if math.isfinite(value) else None


def raw_record(row: Mapping[str, Any], now: float) -> dict[str, Any] | None:
    """One point-log row as a store record: its own time, the known keys as integers, the rest in `extra`.
    None for a row without a plausible time or without a single climate value (a row of sentinels is no reading)."""
    stamp = row_time(dict(row), now)
    if stamp is None:
        return None
    record: dict[str, Any] = {"ts": int(stamp)}
    for key in RAW_KEYS:
        record[key] = _int(row.get(key))
    if record["rssi"] is not None and not -150 <= record["rssi"] <= 0:
        record["rssi"] = None
    if all(record[key] is None for key in _CLIMATE):
        return None
    extra = {}
    for key, value in row.items():
        if key == "time" or key in RAW_KEYS or not isinstance(key, str) or len(key) > 32:
            continue
        number = _number(value)
        if number is not None and len(extra) < MAX_EXTRA_KEYS:
            extra[key] = number
    record["extra"] = json.dumps(extra, sort_keys=True, separators=(",", ":")) if extra else None
    return record


def poll_record(sensors: Mapping[str, Any], sensors_at: float | None, snapshot: Mapping[str, Any] | None,
                now: float) -> dict[str, Any] | None:
    """The poll's newest reading (box.sensors, already scaled) back to cloud integers, plus light, fans and ports
    from the shadow snapshot while the reading is fresh. None without a reading."""
    if not sensors or sensors_at is None:
        return None
    raw: dict[str, Any] = {"time": sensors.get("time", sensors_at)}
    for key, divisor in _SCALED.items():
        value = sensors.get(key)
        if _number(value) is not None:
            raw[key] = int(round(float(value) * divisor))
    if type(sensors.get("waterLv_raw")) is int:
        raw["waterLv"] = sensors["waterLv_raw"]
    if type(sensors.get("rssi")) is int:
        raw["rssi"] = sensors["rssi"]
    record = raw_record(raw, now)
    if record is None:
        return None
    if snapshot and abs(now - sensors_at) <= FRESH_SECONDS:
        light = (snapshot.get("light") or {}).get("level")
        record["light"] = light if type(light) is int else None
        for name in ("cfan", "dfan"):
            step = (snapshot.get(name) or {}).get("step")
            record[name] = step if type(step) is int else None
        ports = {k: int(bool(v.get("on"))) for k, v in (snapshot.get("ports") or {}).items()
                 if isinstance(v, Mapping) and v.get("on") is not None}
        record["ports"] = json.dumps(ports, sort_keys=True, separators=(",", ":")) if ports else None
    return record


def _records(rows: Iterable[Any], now: float) -> list[dict[str, Any]]:
    return [r for r in (raw_record(row, now) for row in rows if isinstance(row, Mapping)) if r is not None]


def _schema() -> str:
    raw = ", ".join(f'"{key}" INTEGER' for key in (*RAW_KEYS, *ACTUATORS))
    return (f"CREATE TABLE IF NOT EXISTS m (ts INTEGER PRIMARY KEY, src INTEGER NOT NULL, {raw}, "
            "ports TEXT, extra TEXT)")


_META = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
_PORTS = ("CREATE TABLE IF NOT EXISTS port_observations ("
          "at REAL NOT NULL, port TEXT NOT NULL, state INTEGER, device TEXT, source TEXT NOT NULL, "
          "PRIMARY KEY (at, port, source))")


def _upsert() -> str:
    """Insert, or merge into the row of the same second. The better source (point log over poll) gives the
    climate values; a value the other one lacks is kept either way. Light, fans and ports keep the first known."""
    names = ", ".join(f'"{c}"' for c in COLUMNS)
    marks = ", ".join("?" for _ in COLUMNS)
    better = "excluded.src <= m.src"
    sets = ["src = MIN(m.src, excluded.src)"]
    for key in RAW_KEYS:
        sets.append(f'"{key}" = CASE WHEN {better} THEN COALESCE(excluded."{key}", m."{key}") '
                    f'ELSE COALESCE(m."{key}", excluded."{key}") END')
    for key in (*ACTUATORS, "ports"):
        sets.append(f'"{key}" = COALESCE(m."{key}", excluded."{key}")')
    sets.append(f'extra = CASE WHEN {better} THEN COALESCE(excluded.extra, m.extra) '
                'ELSE COALESCE(m.extra, excluded.extra) END')
    return f"INSERT INTO m ({names}) VALUES ({marks}) ON CONFLICT(ts) DO UPDATE SET {', '.join(sets)}"


_UPSERT = _upsert()


class MeasurementStore:
    """One SQLite file under STATE_DIR/measurements, opened per call (cheap, and safe from any thread).

    path: a fixed file for tests; None means config.STATE_DIR at the moment of each call. tz: the zone a day is
    counted in (None: the machine's, Europe/Berlin on the Mac)."""

    def __init__(self, path: Path | None = None, *, tz: tzinfo | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self.tz = tz
        self._clock = clock
        self._lock = threading.Lock()
        self._checked: set[Path] = set()  # files whose PRAGMA quick_check ran in this process
        self._homes: dict[Path, str] = {}  # main file -> the device it belongs to, once known
        self._warned: set[tuple[Path, str]] = set()
        self.device_id: str | None = None  # the device written last; reads follow it

    def path(self) -> Path:
        """The main file: the first device's."""
        return self._path if self._path is not None else store_dir() / DB_NAME

    def file_for(self, device_id: str | None = None) -> Path:
        """The file of a device (None: the one written last): the main file while it belongs to that device or
        to none yet, otherwise measurements/<device_id>.sqlite3 next to it."""
        main = self.path()
        device = device_id or self.device_id
        if not device:
            return main
        home = self._homes.get(main)
        if home is None:
            home = self._read_home(main)
            if home is None:
                return main
            self._homes[main] = home
        if home == device:
            return main
        if (main, device) not in self._warned:
            self._warned.add((main, device))
            _LOGGER.warning("Measurement store: %s belongs to device %s; the rows of device %s go to %s.sqlite3",
                            main.name, home, device, _file_part(device))
        return main.with_name(f"{_file_part(device)}.sqlite3")

    def _read_home(self, main: Path) -> str | None:
        if not main.exists():
            return None
        with self._db(main) as con:
            found = con.execute("SELECT value FROM meta WHERE key = 'device_id'").fetchone()
        return found[0] if found and isinstance(found[0], str) else None

    @contextlib.contextmanager
    def _db(self, path: Path) -> Any:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            con = self._open(path, check=path not in self._checked)
        except sqlite3.OperationalError:
            raise  # locked, disk I/O, no space: the file is fine, the next call tries again
        except sqlite3.DatabaseError as err:  # not a database, or malformed: keep it aside, never delete it
            self._set_aside(path, err)
            con = self._open(path)
        self._checked.add(path)
        try:
            with con:
                yield con
        except (sqlite3.OperationalError, _SetAside):
            raise
        except sqlite3.DatabaseError as err:  # damage the quick check did not see, found by this very call
            con.close()
            self._set_aside(path, err)
            raise _SetAside(str(err)) from err
        finally:
            con.close()

    def _set_aside(self, path: Path, err: BaseException) -> None:
        """Move a damaged file aside with its -journal, -wal and -shm files: a hot journal left behind would be
        played into the new file."""
        stamp = int(self._clock())
        aside = path.with_name(f"{path.name}.unreadable-{stamp}")
        n = 1
        while aside.exists():
            aside, n = path.with_name(f"{path.name}.unreadable-{stamp}-{n}"), n + 1
        _LOGGER.error("%s unreadable (%s): kept as %s, starting a new file", path.name, err, aside.name)
        path.replace(aside)
        for suffix in ("-journal", "-wal", "-shm"):
            side = path.with_name(path.name + suffix)
            if side.exists():
                side.replace(aside.with_name(aside.name + suffix))
        self._checked.discard(path)
        self._homes.pop(path, None)

    @staticmethod
    def _open(path: Path, *, check: bool = False) -> sqlite3.Connection:
        """Open, check once per process (PRAGMA quick_check: a damaged page opens fine and then fails every
        write), and create or upgrade the tables."""
        con = sqlite3.connect(path, timeout=5)
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if check:
                found = [str(r[0]) for r in con.execute("PRAGMA quick_check(5)")]
                if found != ["ok"]:
                    raise sqlite3.DatabaseError("quick_check: " + " ".join("; ".join(found).split()))
            if version < SCHEMA_VERSION:
                if version < 1:
                    con.execute(_schema())
                con.execute(_META)
                con.execute(_PORTS)
                con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                con.commit()
        except BaseException:
            con.close()
            raise
        return con

    # ---------- writing ----------

    def add_port_observations(self, records: Iterable[Mapping[str, Any]], device_id: str | None = None) -> int:
        """Reported USB states, never requested commands. Kept separately from the sensor's timestamp so a short
        app-controlled pump run survives between two sensor polls. Device is the owner-confirmed accessory then."""
        values = []
        for r in records:
            at, port, state = r.get("at"), r.get("port"), r.get("on")
            if (_number(at) is None or not isinstance(port, str) or not re.fullmatch(r"usb[1-9][0-9]*", port)
                    or (state is not None and type(state) is not bool) or r.get("source") not in ("shadow", "poll")):
                continue
            device = r.get("device") if r.get("device") in ("pump", "humidifier", "other") else None
            values.append((float(at), port, None if state is None else int(state), device, r["source"]))
        if not values:
            return 0
        with self._lock:
            for attempt in (1, 2):
                try:
                    target = self.file_for(device_id)
                    with self._db(target) as con:
                        before = con.total_changes
                        con.executemany("INSERT OR IGNORE INTO port_observations VALUES (?, ?, ?, ?, ?)", values)
                        added = con.total_changes - before
                        if device_id:
                            con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('device_id', ?)", (device_id,))
                    break
                except _SetAside:
                    if attempt == 2:
                        raise
            if device_id:
                self.device_id = device_id
                if target == self.path():
                    self._homes.setdefault(target, device_id)
        return added

    def port_observations(self, start: float, end: float) -> list[dict[str, Any]]:
        return self._query("SELECT at, port, state, device, source FROM port_observations "
                           "WHERE at >= ? AND at < ? ORDER BY at, port", (start, end))

    def legacy_port_observations(self, start: float, end: float) -> list[dict[str, Any]]:
        """Existing minute rows: their port identity was not recorded and their time is only approximate."""
        out = []
        for row in self._query("SELECT ts, ports FROM m WHERE ts >= ? AND ts < ? AND ports IS NOT NULL ORDER BY ts",
                               (start, end)):
            try:
                ports = json.loads(row["ports"])
            except (ValueError, TypeError):
                continue
            if not isinstance(ports, dict):
                continue
            for port, value in ports.items():
                if type(value) is int and value in (0, 1):
                    out.append({"at": row["ts"], "port": port, "state": value, "device": None, "source": "legacy"})
        return out

    def _write(self, batches: Iterable[tuple[Iterable[Mapping[str, Any]], str]], device_id: str | None) -> int:
        """Store batches of records (raw_record/poll_record shape), each with its source, in one transaction;
        returns how many seconds were new. Same second twice: merged, never doubled. A file found damaged during
        the write is moved aside and the write goes once more into a new file."""
        prepared = []
        for records, source in batches:
            rank = _RANK[source]
            values = [tuple([r["ts"], rank, *(r.get(k) for k in COLUMNS[2:])]) for r in records]
            if values:
                prepared.append(values)
        if not prepared:
            return 0
        low = min(v[0] for values in prepared for v in values)
        high = max(v[0] for values in prepared for v in values)
        span = "SELECT COUNT(*) FROM m WHERE ts BETWEEN ? AND ?"
        with self._lock:
            for attempt in (1, 2):
                try:
                    target = self.file_for(device_id)
                    with self._db(target) as con:
                        before = con.execute(span, (low, high)).fetchone()[0]
                        for values in prepared:
                            con.executemany(_UPSERT, values)
                        if device_id:
                            con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('device_id', ?)",
                                        (device_id,))
                        after = con.execute(span, (low, high)).fetchone()[0]
                    break
                except _SetAside:
                    if attempt == 2:
                        raise
            if device_id:
                self.device_id = device_id
                if target == self.path():
                    self._homes.setdefault(target, device_id)
        return after - before

    def add(self, records: Iterable[Mapping[str, Any]], source: str, device_id: str | None = None) -> int:
        """Store records of one source; returns how many seconds were new."""
        return self._write([(records, source)], device_id)

    def add_rows(self, rows: Iterable[Mapping[str, Any]], source: str = SOURCE_POINTLOG,
                 now: float | None = None, device_id: str | None = None) -> int:
        """Raw point-log rows as the cloud sent them. Rows without a time or a reading are skipped."""
        now = self._clock() if now is None else now
        return self.add(_records(rows, now), source, device_id)

    def add_poll(self, sensors: Mapping[str, Any], sensors_at: float | None,
                 snapshot: Mapping[str, Any] | None = None, now: float | None = None,
                 device_id: str | None = None) -> int:
        now = self._clock() if now is None else now
        record = poll_record(sensors, sensors_at, snapshot, now)
        return self.add([record], SOURCE_POLL, device_id) if record is not None else 0

    def add_from_poll(self, rows: Iterable[Mapping[str, Any]], sensors: Mapping[str, Any],
                      sensors_at: float | None, snapshot: Mapping[str, Any] | None = None,
                      now: float | None = None, device_id: str | None = None) -> int:
        """One poll in one transaction: every point-log row of its 15-minute window, then its newest reading with
        light, fans and ports (merged into the point-log row of the same second)."""
        now = self._clock() if now is None else now
        record = poll_record(sensors, sensors_at, snapshot, now) if sensors else None
        return self._write([(_records(rows, now), SOURCE_POINTLOG), ([record] if record else [], SOURCE_POLL)],
                           device_id)

    # ---------- reading ----------

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Rows of a read on the current device's file; [] without a file (a read never creates one). A file
        found damaged is moved aside and the read answers from the new, empty one."""
        for attempt in (1, 2):
            try:
                path = self.file_for()
                if not path.exists():
                    return []
                with self._db(path) as con:
                    con.row_factory = sqlite3.Row
                    return [dict(row) for row in con.execute(sql, params)]
            except _SetAside:
                if attempt == 2:
                    raise
        return []

    def _select(self, start: float, end: float) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM m WHERE ts >= ? AND ts < ? ORDER BY ts", (int(start), math.ceil(end)))

    @staticmethod
    def _view(record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored record in the shape of box.history() rows (scale_row), plus source, light, fans and ports."""
        raw = {"time": record["ts"], **{k: record[k] for k in RAW_KEYS}}
        row: dict[str, Any] = scale_row(raw)
        row["source"] = _NAME.get(record["src"], "unknown")
        for key in ACTUATORS:
            row[key] = record[key]
        row["ports"] = json.loads(record["ports"]) if record["ports"] else None
        row["extra"] = json.loads(record["extra"]) if record["extra"] else None
        return row

    def rows(self, start: float, end: float, step: int = 60) -> list[dict[str, Any]]:
        """Rows from start (inclusive) to end (exclusive). step 60 (or less): every stored row. A longer step:
        one row per step-wide bucket, the mean of each value, with n = the rows it holds and the time of the
        bucket's start. Buckets are counted from local midnight (the store's zone at `start`), so a step of a
        day is a calendar day and not 02:00 to 02:00 in Berlin summer time."""
        found = [self._view(r) for r in self._select(start, end)]
        if step <= 60:
            return found
        offset = self._utc_offset(start)
        buckets: dict[int, list[dict[str, Any]]] = {}
        for row in found:
            buckets.setdefault((int(row["time"]) + offset) // step * step - offset, []).append(row)
        out = []
        for stamp in sorted(buckets):
            group = buckets[stamp]
            merged: dict[str, Any] = {"time": stamp, "n": len(group)}
            for key in (*_SCALED, "rssi", *ACTUATORS):
                values = [r[key] for r in group if r.get(key) is not None]
                merged[key] = round(sum(values) / len(values), 2) if values else None
            sources = {r["source"] for r in group}
            merged["source"] = sources.pop() if len(sources) == 1 else "mixed"
            out.append(merged)
        return out

    def _utc_offset(self, at: float) -> int:
        """The store's zone at `at`: seconds east of UTC (7200 in Berlin summer time)."""
        moment = datetime.fromtimestamp(at, tz=self.tz) if self.tz else datetime.fromtimestamp(at).astimezone()
        delta = moment.utcoffset()
        return int(delta.total_seconds()) if delta is not None else 0

    def day_bounds(self, day: date) -> tuple[float, float]:
        """Start and end of a calendar day in the store's zone, as epoch seconds (23 or 25 hours on a DST day)."""
        zone = self.tz
        start = datetime(day.year, day.month, day.day, tzinfo=zone) if zone else datetime(
            day.year, day.month, day.day).astimezone()
        nxt = day + timedelta(days=1)
        end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=zone) if zone else datetime(
            nxt.year, nxt.month, nxt.day).astimezone()
        return start.timestamp(), end.timestamp()

    def today(self, now: float | None = None) -> date:
        now = self._clock() if now is None else now
        return datetime.fromtimestamp(now, tz=self.tz).date() if self.tz else datetime.fromtimestamp(now).date()

    @staticmethod
    def _gaps(minutes: list[int], first: int, last: int, min_minutes: int) -> list[tuple[int, int]]:
        """Missing stretches of at least min_minutes between minute numbers first (inclusive) and last (exclusive)."""
        out = []
        cursor = first
        for minute in [*minutes, last]:
            if minute - cursor >= min_minutes:
                out.append((cursor, minute))
            cursor = max(cursor, minute + 1)
        return out

    def gaps(self, start: float, end: float, min_minutes: int = GAP_MINUTES) -> list[dict[str, Any]]:
        """Stretches of at least min_minutes without a stored row: from, to (epoch seconds), minutes."""
        stamps = sorted({int(r["ts"]) // 60 for r in self._select(start, end)})
        found = self._gaps(stamps, math.ceil(start / 60), int(end // 60), min_minutes)
        return [{"from": a * 60, "to": b * 60, "minutes": b - a} for a, b in found]

    def day_summary(self, day: date, bands: Mapping[str, tuple[float, float]] | None = None,
                    now: float | None = None, aux: Any = None) -> dict[str, Any]:
        """One day: per metric min, max, mean and n; minutes inside each given band (low <= value <= high);
        coverage = minutes with a row out of the day's minutes so far; the sources; the gaps.
        Light and fans are only on the poll's minutes (about two in three, module docstring): their n counts rows,
        not minutes, and light hours must come from durations between changes, never from n.
        aux: the water sensor's accessory type (shadow aux35Dev, box.water_view). Only with it are water_level and
        water_temp decoded (type 10, the DWC kit); type 9 is wet/dry and has neither, an unknown type neither."""
        now = self._clock() if now is None else now
        start, end = self.day_bounds(day)
        until = min(end, now)
        expected = max(0, int(until // 60) - math.ceil(start / 60))
        records = self._select(start, until) if until > start else []
        minutes = sorted({int(r["ts"]) // 60 for r in records})
        views = [self._view(r) for r in records]
        metrics: dict[str, dict[str, Any]] = {}
        series: dict[str, list[float]] = {name: [] for name in METRICS}
        for row in views:
            climate = climate_view(row, aux)
            for name in METRICS:
                value = row.get(name) if name in ACTUATORS else climate.get(name)
                if value is not None:
                    series[name].append(float(value))
        for name, values in series.items():
            if values:
                metrics[name] = {"min": round(min(values), 2), "max": round(max(values), 2),
                                 "mean": round(sum(values) / len(values), 2), "n": len(values)}
        in_band = {}
        for name, (low, high) in (bands or {}).items():
            values = series.get(name, [])
            hits = sum(1 for v in values if low <= v <= high)
            in_band[name] = {"low": low, "high": high, "minutes": hits,
                             "percent_of_measured": round(100 * hits / len(values), 1) if values else None}
        sources = {name: sum(1 for r in records if r["src"] == rank) for name, rank in _RANK.items()}
        gaps = self._gaps(minutes, math.ceil(start / 60), int(until // 60), GAP_MINUTES) if expected else []
        return {
            "date": day.isoformat(),
            "complete": until >= end,
            "minutes_expected": expected,
            "minutes_stored": len(minutes),
            "coverage_percent": round(100 * len(minutes) / expected, 1) if expected else None,
            "sources": sources,
            "metrics": metrics,
            "bands": in_band,
            "gap_count": len(gaps),
            "gap_minutes": sum(b - a for a, b in gaps),
            "gaps": [{"from": a * 60, "to": b * 60, "minutes": b - a} for a, b in gaps[:MAX_GAPS]],
        }

    def summary(self, days: int = 7, bands: Mapping[str, tuple[float, float]] | None = None,
                now: float | None = None, aux: Any = None) -> dict[str, Any]:
        """The last `days` calendar days up to today, oldest first, and the store's first and last row. aux: as in
        day_summary."""
        now = self._clock() if now is None else now
        days = max(1, min(int(days), MAX_SUMMARY_DAYS))
        today = self.today(now)
        out = [self.day_summary(today - timedelta(days=n), bands, now, aux) for n in range(days - 1, -1, -1)]
        found = self._query("SELECT MIN(ts) AS first, MAX(ts) AS last, COUNT(*) AS total FROM m")
        first, last, total = (found[0]["first"], found[0]["last"], found[0]["total"]) if found else (None, None, 0)
        return {"days": out, "first": first, "last": last, "rows_total": total}


# ---------- feeding it: the poll's snapshot and the collector ----------

class _Quiet:
    """Logs a failure once, then again only when its text changes or an hour has passed: a broken disk must not
    write a line every 90 s."""

    def __init__(self, what: str) -> None:
        self.what, self.last, self.at = what, "", 0.0

    def failed(self, err: BaseException) -> None:
        text = f"{type(err).__name__}: {err}"
        now = time.monotonic()
        if text != self.last or now - self.at >= 3600:
            _LOGGER.warning("Measurement store, %s failed (the cockpit runs on): %s", self.what, text)
            self.last, self.at = text, now

    def ok(self) -> None:
        self.last = ""


class _Background:
    """A store write in a worker thread that the caller waits on for at most `timeout` seconds. A write that hangs
    (a stuck disk) is left running, and the next one is skipped until it ends: the event loop never waits on the
    disk, and a hung SQLite call cannot queue a worker thread per poll behind its lock."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.task: asyncio.Future[Any] | None = None

    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    async def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        if self.busy():
            raise TimeoutError("the previous write is still running; this one is skipped")
        self.task = task = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        task.add_done_callback(_consume)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.timeout)
        except TimeoutError:
            raise TimeoutError(f"the write took longer than {self.timeout:g} s and is left behind") from None


def poll_listener(store: MeasurementStore, *, timeout: float = POLL_WRITE_TIMEOUT) -> Callable[[Any], Any]:
    """For box.listeners: after every poll, every point-log row of the poll's window (box.last_rows) and its
    newest reading with light and fans. Never raises, and never waits on the disk longer than `timeout`."""
    quiet = _Quiet("the poll rows")
    writer = _Background(timeout)

    async def on_poll(box: Any) -> None:
        try:
            sensors, at = dict(getattr(box, "sensors", {}) or {}), getattr(box, "sensors_at", None)
            rows = getattr(box, "last_rows", None)
            rows = [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []
            if at is None:
                sensors = {}
            if not sensors and not rows:
                return
            snap = box.snapshot() if sensors else None
            await writer.run(store.add_from_poll, rows, sensors, at, snap, None, device_of(box))
            quiet.ok()
        except Exception as err:
            quiet.failed(err)
    return on_poll


class Collector:
    """Every COLLECT_SECONDS one point-log request for the last WINDOW_SECONDS, through the box's logged-in
    session (box.point_rows): no login of its own, and no request at all while the box is not logged in.
    The first request is the backfill (the last BACKFILL_SECONDS), unless collector.json says a request went
    out less than a day ago: then a restart (a deploy, launchd) waits for the next due time and asks only for
    the time since the last good request. After failed requests the next window reaches back to the last good
    one (at most a day), still one request per interval."""

    def __init__(self, box: Any, store: MeasurementStore, *, interval: float = COLLECT_SECONDS,
                 first: float = FIRST_SECONDS, sleep: Callable[[float], Any] = asyncio.sleep,
                 clock: Callable[[], float] = time.time, state_path: Path | None = None,
                 write_timeout: float = COLLECT_WRITE_TIMEOUT) -> None:
        self.box, self.store = box, store
        self.interval, self.first, self.sleep, self.clock = interval, first, sleep, clock
        self._state_path = state_path
        self.backfilled = False
        self.last_ok: float | None = None
        self.requests = 0
        self.task: asyncio.Task[None] | None = None
        self._quiet = _Quiet("the point-log collection")
        self._state_quiet = _Quiet("the collector's state file")
        self._writer = _Background(write_timeout)

    def state_path(self) -> Path:
        """STATE_DIR/measurements/collector.json, next to the store's main file."""
        return self._state_path if self._state_path is not None else self.store.path().parent / STATE_NAME

    def window(self, now: float) -> int:
        if not self.backfilled or self.last_ok is None:
            return BACKFILL_SECONDS
        return int(min(BACKFILL_SECONDS, max(WINDOW_SECONDS, now - self.last_ok + CATCH_UP_MARGIN)))

    # ---------- the state file ----------

    def load_state(self) -> dict[str, Any]:
        """collector.json as a dict; {} when missing or unreadable (then the collector starts with a backfill)."""
        try:
            data = json.loads(self.state_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as err:
            self._state_quiet.failed(err)
            return {}
        return data if isinstance(data, dict) else {}

    def save_state(self, last_request: float) -> None:
        """Atomic: a temporary file next to it, then os.replace. A crash never leaves half a file."""
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"last_request": last_request, "last_ok": self.last_ok, "device_id": device_of(self.box)}
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)

    async def _persist(self, last_request: float) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(self.save_state, last_request), 10)
            self._state_quiet.ok()
        except Exception as err:
            self._state_quiet.failed(err)

    @staticmethod
    def _seconds(value: Any) -> float | None:
        number = _number(value)
        return float(number) if number is not None else None

    def first_wait(self, saved: Mapping[str, Any], now: float) -> float:
        """The wait before the first request: FIRST_SECONDS, or until the saved last request is one interval old
        (never longer than one interval, whatever the clock did)."""
        last_request = self._seconds(saved.get("last_request"))
        if last_request is None:
            return self.first
        return min(self.interval, max(self.first, last_request + self.interval - now))

    def seed(self, saved: Mapping[str, Any], now: float) -> None:
        """Take the saved last good request when it is less than a day old and of the same device (or of an
        unknown one): then no backfill, the window reaches back to it."""
        last_ok = self._seconds(saved.get("last_ok"))
        saved_device, device = saved.get("device_id"), device_of(self.box)
        if last_ok is None or not 0 <= now - last_ok < BACKFILL_SECONDS:
            return
        if saved_device and device and saved_device != device:
            return
        self.last_ok, self.backfilled = last_ok, True

    def _box_ready(self) -> bool:
        """Logged in, as far as the box can say (Box._logged_in raises without tokens or a device)."""
        check = getattr(self.box, "_logged_in", None)
        if check is None:
            return True
        try:
            check()
        except Exception:
            return False
        return True

    # ---------- the requests ----------

    async def tick(self) -> int | None:
        """One request; the number of new rows, or None when it failed or was skipped (logged)."""
        now = self.clock()
        if self._writer.busy():  # the last answer is still being written: no request, the next window covers it
            self._quiet.failed(TimeoutError("the previous write is still running; this request is skipped"))
            return None
        seconds = self.window(now)
        try:
            self.requests += 1
            rows = await self.box.point_rows(seconds)
            added = await self._writer.run(self.store.add_rows, rows, SOURCE_POINTLOG, None, device_of(self.box))
        except Exception as err:
            self._quiet.failed(err)
            await self._persist(now)
            return None
        self._quiet.ok()
        if not self.backfilled:
            _LOGGER.info("Measurement store: backfill of %d h kept %d new rows of %d", seconds // 3600, added,
                         len(rows))
        self.backfilled, self.last_ok = True, now
        await self._persist(now)
        return added

    async def loop(self) -> None:
        saved = await asyncio.to_thread(self.load_state)
        await self.sleep(self.first_wait(saved, self.clock()))
        for _ in range(LOGIN_WAIT_TRIES):  # the login may take longer than the first wait: not a failure
            if self._box_ready():
                break
            await self.sleep(LOGIN_WAIT_SECONDS)
        self.seed(saved, self.clock())
        while True:
            await self.tick()
            await self.sleep(self.interval)

    def start(self) -> asyncio.Task[None]:
        self.task = asyncio.create_task(self.loop(), name="measurements_collect")
        self.task.add_done_callback(_forget)
        return self.task

    async def stop(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.task = None


def _forget(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        _LOGGER.error("Measurement collector stopped: %s", task.exception())


def setup(box: Any, demo: bool) -> tuple[MeasurementStore | None, Collector | None]:
    """For serve(), before box.start(): the store and its poll listener (so the boot poll is kept too), and the
    collector, started by the caller once the box is up. The demo stores nothing: its readings are made up."""
    if demo or not hasattr(box, "point_rows"):
        return None, None
    try:
        store = MeasurementStore()
        box.listeners.append(poll_listener(store))
        return store, Collector(box, store)
    except Exception:
        _LOGGER.exception("The measurement store could not be set up; the cockpit runs without it")
        return None, None


def parse_bands(values: Iterable[str]) -> dict[str, tuple[float, float]]:
    """?band=probe_vpd:0.8:1.2 (repeatable) into {"probe_vpd": (0.8, 1.2)}; ValueError for anything else."""
    bands = {}
    for value in values:
        parts = str(value).split(":")
        if len(parts) != 3 or parts[0] not in METRICS:
            raise ValueError(value)
        low, high = float(parts[1].replace(",", ".")), float(parts[2].replace(",", "."))
        if not (math.isfinite(low) and math.isfinite(high)) or low > high:
            raise ValueError(value)
        bands[parts[0]] = (low, high)
    return bands
