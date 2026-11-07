"""Local issue/action/result protocol. Observes reports and existing logs; never switches a device or calls a model.

HTTP acceptance, device confirmation and later movement of a reading are separate facts. Historical imports do
not invent missing confirmations. SQLite retains the history across restarts, including pauses and limits.
"""
from __future__ import annotations

import hashlib
import contextlib
import json
import math
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import config
from .box import CFAN_LEVEL_MAP, DFAN_LEVEL_MAP, SPECTRUMS
from .watering import TEXTS as WATERING_REASONS

RESPONSE_WINDOW = 30 * 60
CONFIRM_WINDOW = 20
EPSILON = {"probe_temp": .2, "probe_humidity": 1.0, "probe_vpd": .05, "light_schedule": 1, "light_percent": 1}
DIRECT = {"light", "dfan", "cfan", "port", "spectrum", "watering_start", "watering_stop"}
CONFIG = {"plant_stage", "plant_start", "light_schedule_auto", "light_schedule_config", "watering_config",
          "autopilot_mode", "autopilot_device", "auto_humidifier", "humidifier_target"}
ACTIONS = DIRECT | CONFIG


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def stamp(value):
    if number(value):
        return float(value)
    try:
        dt = datetime.fromisoformat(str(value))
        return (dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("Europe/Berlin"))).timestamp()
    except (ValueError, TypeError):
        return None


def tail(path: Path, count=500):
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 1024 * 1024))
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-count:]:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except ValueError:
            pass
    return rows


def device_value(snap, device):
    if device == "light":
        return (snap.get("light") or {}).get("level")
    if device == "spectrum":
        return (snap.get("light") or {}).get("spectrum")
    if device in ("cfan", "dfan"):
        return "nw" if device == "cfan" and (snap.get(device) or {}).get("natural_wind") else (snap.get(device) or {}).get("step")
    return ((snap.get("ports") or {}).get(device) or {}).get("on")


def command_target(body):
    action = body.get("action")
    if action in ("light", "cfan", "dfan"):
        return action, "nw" if action == "cfan" and body.get("natural_wind") else body.get("level" if action == "light" else "step")
    if action == "port":
        return body.get("port"), body.get("on")
    if action == "spectrum":
        return "spectrum", body.get("name")
    if action in ("watering_start", "watering_stop"):
        return "usb1", action == "watering_start"
    return None, None


def pending_for(snap, device, target):
    if device in ("dfan", "cfan"):
        field = "dFan.level" if device == "dfan" else "cFan.level"
        table = DFAN_LEVEL_MAP if device == "dfan" else CFAN_LEVEL_MAP
        raw = "nw" if target == "nw" else table[int(target)] if number(target) and 0 <= target <= 10 else None
    else:
        field = {"light": "light.level", "spectrum": "light.spec"}.get(device, "port." + str(device))
        raw = SPECTRUMS.get(target) if device == "spectrum" else target
    pending = (snap.get("pending") or {}).get(field) or {}
    return pending if pending.get("target") == raw else {}


def distance(value, lo, hi):
    if not all(number(v) for v in (value, lo, hi)):
        return None
    return max(lo - value, value - hi, 0)


def affected(metric, direction):
    if metric in ("light_percent", "light_schedule"):
        return ["light"]
    if metric == "humidifier_response":
        return ["usb2"]
    if metric == "pump_control":
        return ["usb1"]
    if metric == "probe_humidity" and direction == "low":
        return ["usb2"]
    if metric == "probe_vpd" and direction == "high":
        return ["usb2", "dfan"]
    return ["dfan", "usb2"] if metric in ("probe_humidity", "probe_vpd") else ["dfan"] if metric == "probe_temp" else []


def checks(data):
    rows = [dict(row) for row in (data.get("plant") or {}).get("checks", [])]
    auto, water = data.get("automation") or {}, data.get("watering") or {}
    if auto:
        failed = bool(auto.get("implausible"))
        rows.append({"metric": "humidifier_response", "actual": int(failed), "min": 0, "max": 0, "status": "high" if failed else "ok", "unit": ""})
    if water:
        unmanaged = water.get("reported_on") is True and water.get("status") == "unmanaged"
        rows.append({"metric": "pump_control", "actual": int(unmanaged), "min": 0, "max": 0, "status": "high" if unmanaged else "ok", "unit": ""})
    timer = data.get("light_schedule") or {}
    if (timer.get("settings") or {}).get("enabled"):
        rows = [r for r in rows if r.get("metric") != "light_percent"]
        expected, actual = timer.get("expected_level"), device_value(data["snapshot"], "light")
        if timer.get("status") != "manual" and number(expected) and number(actual):
            rows.append({"metric": "light_schedule", "unit": "%", "actual": actual, "min": expected, "max": expected,
                         "status": "ok" if actual == expected else "high" if actual > expected else "low"})
    return rows


def decision(row, data, now):
    snap, pilot = data["snapshot"], data.get("pilot") or {}
    ids = affected(row["metric"], row["status"])
    devices = {d["id"]: d for d in pilot.get("devices", [])}
    choices = []
    for device in ids:
        control = pilot.get("humidifier") or {} if device == "usb2" else devices.get(device) or {}
        state, code = control.get("control_state", "waiting"), control.get("reason_key", "unavailable")
        item = {"device": device, "state": state, "code": code, "text": control.get("status"),
                "text_de": control.get("status_de"), "until": control.get("paused_until")}
        if device == "usb2" and row["metric"] in ("probe_humidity", "probe_vpd") and (row["metric"] == "probe_humidity" and row["status"] == "high" or row["metric"] == "probe_vpd" and row["status"] == "low") and device_value(snap, device) is False:
            item.update(state="ready", code="already_off")
        if device == "usb1":
            item.update(state="needs_setup", code="pump_setup")
        if not snap.get("online") or not number(snap.get("sensors_age")) or snap["sensors_age"] > 600:
            item.update(state="waiting", code="connection")
        if device == "dfan" and item["state"] == "ready":
            bounds, value = (pilot.get("limits") or {}).get("dfan"), device_value(snap, device)
            increasing = row["metric"] == "probe_humidity" and row["status"] == "high" or row["metric"] == "probe_vpd" and row["status"] == "low" or row["metric"] == "probe_temp" and row["status"] == "high"
            if isinstance(bounds, list) and len(bounds) == 2 and number(value):
                limit = bounds[1] if increasing else bounds[0]
                if increasing and value >= limit or not increasing and value <= limit:
                    item.update(state="limited", code="limit", limit=limit, reported=value)
        choices.append(item)
    return choices


class Protocol:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.path = config.STATE_DIR / "boxes" / "box-1" / "hank-protocol.sqlite3"
        self.lock = threading.RLock()
        self.error = False
        self.latest = {"active": 0, "working": 0, "blocked": 0, "updated_at": None}

    @contextlib.contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
          CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,kind TEXT,at REAL,updated REAL,active INTEGER,data TEXT);
          CREATE INDEX IF NOT EXISTS record_kind_time ON records(kind,at DESC,id DESC);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,record_id TEXT,at REAL,kind TEXT,data TEXT);
          CREATE INDEX IF NOT EXISTS record_events ON events(record_id,id);
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY);
            """)
            with db:
                yield db
        finally:
            db.close()

    def rows(self, db, kind, active=None):
        sql = "SELECT data FROM records WHERE kind=?" + (" AND active=?" if active is not None else "")
        return [json.loads(r[0]) for r in db.execute(sql, (kind, active) if active is not None else (kind,))]

    def save(self, db, kind, item, active=1):
        db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?)",
                   (item["id"], kind, item["at"], self.clock(), active, json.dumps(item, ensure_ascii=False)))

    def event(self, db, item, kind, **data):
        db.execute("INSERT INTO events(record_id,at,kind,data) VALUES(?,?,?,?)",
                   (item["id"], self.clock(), kind, json.dumps(data, ensure_ascii=False)))

    def seen(self, db, key):
        return db.execute("INSERT OR IGNORE INTO seen VALUES(?)", (key,)).rowcount == 0

    def create_task(self, db, *, ident, at, origin, device, target, before=None, data=None, historical=False,
                    status="requested", reason=None, proposal=None):
        if db.execute("SELECT 1 FROM records WHERE id=?", (ident,)).fetchone():
            return None
        if not historical and status in ("requested", "confirmed"):
            for older in self.rows(db, "task", 1):
                if older["device"] == device:
                    if older["status"] == "requested":
                        older["status"] = "superseded"
                    older["response"] = "superseded"
                    older["monitoring"] = False
                    self.event(db, older, "superseded", by=ident)
                    self.save(db, "task", older, 0)
        issues = [] if historical else [i for i in self.rows(db, "issue", 1) if device in i["devices"]]
        actuals = {r["metric"]: r.get("actual") for r in checks(data)} if data else {}
        snap = (data or {}).get("snapshot") or {}
        fresh = snap.get("online") is True and number(snap.get("sensors_age")) and 0 <= snap["sensors_age"] <= 600
        baseline = [{"issue": i["id"], "metric": i["metric"], "unit": i.get("unit"), "value": actuals[i["metric"]],
                     "min": i["min"], "max": i["max"]} for i in issues if fresh and number(actuals.get(i["metric"]))]
        task = {"id": ident, "at": at, "origin": origin, "device": device, "before": before, "target": target,
                "status": status, "reason": reason, "proposal": proposal, "historical": historical,
                "context": (data or {}).get("context"),
                "issues": [i["id"] for i in issues], "baseline": [] if historical else baseline,
                "confirmed_at": None, "response": "unrecorded" if historical or status in ("refused", "observed", "unconfirmed") else "saved" if status == "saved" else "not_executed" if status in ("suggested", "skipped") else "waiting", "observations": [],
                "monitoring": not historical and status in ("requested", "confirmed")}
        self.save(db, "task", task, int(task["monitoring"]))
        self.event(db, task, "imported" if historical else status, target=target)
        for issue in issues:
            self.event(db, issue, "task", task=ident, origin=origin)
        return task

    def capture(self, body, http_status, before, after, origin="manual", error=None):
        """After the existing command handler. Failure to log must never change its result."""
        action = body.get("action")
        if action not in ACTIONS:
            return
        with self.lock:
            self.observe(before)  # the pre-command issue is durable even before the first background sample
            with self.connect() as db:
                device, target = command_target(body)
                task = self.create_task(db, ident="request-" + uuid.uuid4().hex, at=before.get("captured_at", self.clock()), origin=origin,
                    device=device or action, target=target if device else action, before=device_value(before["snapshot"], device) if device else None,
                    status="refused" if http_status >= 400 else "requested" if device else "saved", reason=error,
                    proposal=body.get("proposal"), data=before)
                if task and http_status < 400 and device:
                    self.settle_task(db, task, after, self.clock())

    def import_history(self, db, started):
        if db.execute("SELECT 1 FROM meta WHERE key='imported' ").fetchone():
            return
        ledger = {}
        try:
            ledger = json.loads((config.STATE_DIR / "hank" / "ledger.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        recommendations = {(i.get("recommendation") or {}).get("proposal_id"): i for i in ledger.get("items", []) if isinstance((i.get("recommendation") or {}).get("proposal_id"), str)}
        for row in tail(config.STATE_DIR / "hank" / "actions.jsonl"):
            at = stamp(row.get("time"))
            device, target = command_target(row.get("body") or {})
            if at is None or at >= started or not device:
                continue
            ident = "history-" + hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:24]
            record = recommendations.get(row.get("proposal")) or {}
            baseline = record.get("baseline") or {}
            task = self.create_task(db, ident=ident, at=at, origin="hank" if row.get("origin") == "hank_accepted" else "manual",
                device=device, target=target, before=baseline.get(device), historical=True,
                status="unrecorded" if row.get("ok") else "refused", proposal=row.get("proposal"))
            if task and number(baseline.get("value")):
                task["historical_baseline"] = {"metric": record.get("metric"), "value": baseline["value"]}
                self.save(db, "task", task, 0)
        db.execute("INSERT INTO meta VALUES('imported','true')")

    def repair_historical_links(self, db):
        """Early protocol installs linked imported actions to issues first detected at import time.

        That association has no evidence. Retain the original action and its own historical baseline only.
        """
        if db.execute("SELECT 1 FROM meta WHERE key='historical_links_v2'").fetchone():
            return
        historical = set()
        for task in self.rows(db, "task"):
            if task["historical"]:
                historical.add(task["id"])
                if task["issues"] or task["baseline"]:
                    task["issues"], task["baseline"] = [], []
                    self.save(db, "task", task, 0)
        for row in db.execute("SELECT id,data FROM events WHERE kind='task'").fetchall():
            if json.loads(row["data"]).get("task") in historical:
                db.execute("DELETE FROM events WHERE id=?", (row["id"],))
        db.execute("INSERT INTO meta VALUES('historical_links_v2','true')")

    def import_sources(self, db, data, started):
        now = self.clock()
        for entry in (data.get("pilot") or {}).get("log", []):
            kind, at = entry.get("kind"), stamp(entry.get("at"))
            if at is None or not entry.get("device") or kind not in ("step", "would", "suggest", "undo", "failed", "manual"):
                continue
            if kind == "manual" and entry.get("from") is None:
                continue  # cockpit commands have their own receipt; manufacturer-app changes carry a before value
            ident = "pilot-" + str(entry["id"])
            old = db.execute("SELECT data FROM records WHERE id=?", (ident,)).fetchone()
            status = {"ok": "confirmed", "failed": "unconfirmed", "cancelled": "superseded"}.get(entry.get("status"))
            if old:
                task = json.loads(old[0])
                if status and task["status"] != status:
                    task["status"] = status
                    task["confirmed_at"] = now if status == "confirmed" else None
                    if status != "confirmed":
                        task["response"], task["monitoring"] = "unrecorded", False
                    self.event(db, task, status)
                    self.save(db, "task", task, 0 if task["historical"] or status != "confirmed" else 1)
                continue
            task = self.create_task(db, ident=ident, at=at, origin="external" if kind == "manual" else "automatic",
                device=entry.get("device"), target=entry.get("to"), before=entry.get("from"), historical=at < started,
                status="observed" if kind == "manual" else "suggested" if kind in ("would", "suggest") else "unconfirmed" if kind == "failed" else status or "requested",
                reason={"en": entry.get("text"), "de": entry.get("text_de")}, data=data)
            if task and status == "confirmed":
                task["confirmed_at"] = None if at < started else now
                self.save(db, "task", task, 0 if at < started else 1)
        for entry in (data.get("automation") or {}).get("recent", []):
            action, at = entry.get("action", ""), stamp(entry.get("time"))
            if at is None or not (action.endswith("_on") or action.endswith("_off")):
                continue
            ident = "humidifier-" + str(at) + "-" + action
            self.create_task(db, ident=ident, at=at, origin="safety" if action not in ("humidifier_on", "humidifier_off") else "automatic",
                device="usb2", target=action.endswith("_on"), historical=at < started,
                status="unrecorded" if at < started else "requested", reason=action, data=data)
        for entry in (data.get("light_schedule") or {}).get("events", []):
            kind, at = entry.get("kind"), stamp(entry.get("at"))
            if kind not in ("requested", "confirmed", "unconfirmed") or at is None:
                continue
            if self.seen(db, "light-" + str(at) + "-" + kind):
                continue
            if kind == "requested":
                self.create_task(db, ident="light-timer-" + str(at), at=at, origin="schedule", device="light",
                                 target=entry.get("level"), historical=at < started,
                                 status="unrecorded" if at < started else "requested", data=data)
            else:
                row = db.execute("SELECT data FROM records WHERE kind='task' AND id LIKE 'light-timer-%' AND at<=? ORDER BY at DESC LIMIT 1", (at,)).fetchone()
                if row:
                    task = json.loads(row[0])
                    if task["target"] == entry.get("level") and task["status"] != kind:
                        task["status"], task["confirmed_at"] = kind, at if kind == "confirmed" else None
                        if kind == "unconfirmed":
                            task["response"], task["monitoring"] = "unrecorded", False
                        self.event(db, task, kind)
                        self.save(db, "task", task, 0 if task["historical"] or kind != "confirmed" else 1)
        for entry in (data.get("watering") or {}).get("events", []):
            kind, at = entry.get("kind"), stamp(entry.get("at"))
            if kind not in ("start", "stopped", "skipped") or at is None or self.seen(db, "watering-" + str(at) + "-" + kind):
                continue
            # A delayed OFF report belongs to the existing direct stop, not a second action.
            matching = db.execute("SELECT data FROM records WHERE kind='task' AND id LIKE 'request-%' AND at BETWEEN ? AND ? ORDER BY at DESC",
                                  (at - CONFIRM_WINDOW - 10, at + 1)).fetchall() if kind != "skipped" else []
            matching = next((t for row in matching if (t := json.loads(row[0]))["device"] == "usb1" and t["target"] is (kind == "start") and t["status"] != "refused"), None)
            if matching is not None:
                if kind == "stopped" and matching["status"] != "confirmed":
                    matching["status"], matching["confirmed_at"] = "confirmed", at
                    matching["response"], matching["monitoring"] = "waiting", True
                    self.event(db, matching, "confirmed")
                    self.save(db, "task", matching)
                continue
            reason = WATERING_REASONS.get(entry.get("reason"))
            task = self.create_task(db, ident="watering-" + str(at) + "-" + kind, at=at, origin="watering",
                device="usb1", target=None if kind == "skipped" else kind == "start", historical=at < started,
                status="skipped" if kind == "skipped" else "confirmed" if kind == "stopped" else "unrecorded" if at < started else "requested",
                reason=entry.get("detail") or ({"de": reason[0], "en": reason[1]} if reason else "watering"), data=data)
            if task:
                task["confirmed_at"] = at if kind == "stopped" else None
                task["response"] = "not_executed" if kind == "skipped" else task["response"]
                self.save(db, "task", task, int(not task["historical"] and kind != "skipped"))

    def settle_task(self, db, task, data, now):
        snap = data["snapshot"]
        sampled_at = data.get("captured_at", now)
        pending = pending_for(snap, task["device"], task["target"])
        fresh_device = snap.get("online") is True and snap.get("mqtt") is True and number(snap.get("shadow_age")) and 0 <= snap["shadow_age"] <= 300
        if task["status"] == "requested":
            previous = task["status"]
            if pending.get("status") in ("rejected", "timeout"):
                task["status"] = "refused" if pending["status"] == "rejected" else "unconfirmed"
            elif fresh_device and pending.get("status") != "pending" and (device_value(snap, task["device"]) == task["target"] or task["device"] == "cfan" and number(task["target"]) and (snap.get("cfan") or {}).get("step") == task["target"]) and (pending.get("status") == "ok" or sampled_at - snap["shadow_age"] >= task["at"]):
                task["status"], task["confirmed_at"] = "confirmed", now
            elif now - task["at"] >= CONFIRM_WINDOW:
                task["status"] = "unconfirmed"
            if task["status"] != previous:
                self.event(db, task, task["status"])
                if task["status"] in ("refused", "unconfirmed"):
                    task["response"] = "unrecorded"
        if task.get("context") and task["context"] != data.get("context"):
            if task["response"] != "superseded":
                self.event(db, task, "context_changed")
            task["response"] = "superseded"
            task["monitoring"] = False
            self.save(db, "task", task, 0)
            return
        if task["status"] == "confirmed" and not task["historical"]:
            fresh_sensor = snap.get("online") is True and number(snap.get("sensors_age")) and 0 <= snap["sensors_age"] <= 600
            observed_at = sampled_at - snap["sensors_age"] if fresh_sensor else None
            current = {r["metric"]: r for r in checks(data)}
            observations = []
            for base in task["baseline"]:
                row = current.get(base["metric"])
                reading_at = sampled_at - snap["shadow_age"] if base["metric"] in ("light_percent", "light_schedule") and fresh_device else observed_at
                if row and number(row.get("actual")) and reading_at is not None and reading_at >= (task["confirmed_at"] or now) and (base["metric"] in ("light_percent", "light_schedule") or reading_at > task["confirmed_at"]):
                    observations.append({**base, "current": row["actual"], "at": reading_at,
                                         "delta": round(row["actual"] - base["value"], 3)})
            old = task["response"]
            if observations:
                task["observations"] = observations
                distances = [(distance(o["value"], o["min"], o["max"]), distance(o["current"], o["min"], o["max"]), EPSILON.get(o["metric"], .1)) for o in observations if number(o["min"]) and number(o["max"])]
                overshot = any(o["value"] > o["max"] and o["current"] < o["min"] or o["value"] < o["min"] and o["current"] > o["max"] for o in observations if number(o["min"]) and number(o["max"]))
                if overshot:
                    task["response"] = "overshot"
                elif distances and all(after == 0 for _, after, _ in distances):
                    task["response"] = "in_range"
                elif any(after - before >= eps for before, after, eps in distances):
                    task["response"] = "worse"
                elif any(before - after >= eps for before, after, eps in distances):
                    task["response"] = "improving"
                else:
                    task["response"] = "not_improved" if now - task["confirmed_at"] >= RESPONSE_WINDOW else "watching"
            elif not task["baseline"]:
                task["response"] = "unrecorded" if task["issues"] else "no_issue"
            else:
                task["response"] = "waiting"
            if old != task["response"]:
                self.event(db, task, task["response"])
        active = not task["historical"] and task["status"] in ("requested", "confirmed") and task["response"] not in ("in_range", "no_issue") and now - task["at"] < 2 * 3600
        task["monitoring"] = bool(active)
        self.save(db, "task", task, int(active))

    def observe(self, data):
        now, snap = self.clock(), data["snapshot"]
        sampled_at = data.get("captured_at", now)
        with self.lock, self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='started'").fetchone()
            started = float(row[0]) if row else now
            if row is None:
                db.execute("INSERT INTO meta VALUES('started',?)", (str(started),))
            fresh = snap.get("online") is True and number(snap.get("sensors_age")) and 0 <= snap["sensors_age"] <= 600
            existing = self.rows(db, "issue", 1)
            current = {r["metric"]: r for r in checks(data)}
            context = data.get("context", "box-1")
            by_key = {(i["context"], i["metric"], i["direction"]): i for i in existing}
            for row in current.values():
                if not fresh or not number(row.get("actual")) or row.get("status") not in ("high", "low"):
                    continue
                key = (context, row["metric"], row["status"])
                issue = by_key.get(key)
                if issue is None:
                    issue = {"id": "issue-" + uuid.uuid4().hex, "at": now, "context": context, "metric": row["metric"],
                             "direction": row["status"], "unit": row.get("unit"), "baseline": row["actual"],
                             "min": row.get("min"), "max": row.get("max"), "devices": affected(row["metric"], row["status"])}
                    self.event(db, issue, "detected", value=row["actual"])
                previous = [(d["device"], d["code"], d.get("until")) for d in issue.get("decisions", [])]
                issue.update(current=row["actual"], observed_at=sampled_at - snap["sensors_age"], decisions=decision(row, data, now), state="active")
                signature = [(d["device"], d["code"], d.get("until")) for d in issue["decisions"]]
                if previous != signature:
                    self.event(db, issue, "decision", decisions=issue["decisions"])
                self.save(db, "issue", issue)
            for issue in existing:
                row = current.get(issue["metric"])
                why = "context_changed" if issue["context"] != context else "scheduled_dark" if issue["metric"] == "light_percent" and "light_percent" not in current else None
                if why or fresh and row and number(row.get("actual")) and row.get("status") != issue["direction"]:
                    issue.update(state="resolved" if not why and row["status"] == "ok" else "ended", closed_at=now)
                    if row and number(row.get("actual")):
                        issue["current"] = row["actual"]
                    self.event(db, issue, issue["state"], reason=why or "direction_changed" if issue["state"] == "ended" else "in_range")
                    self.save(db, "issue", issue, 0)
                elif not fresh or row is None or not number(row.get("actual")):
                    issue["decisions"] = [{"device": d, "state": "waiting", "code": "connection" if not fresh else "missing"} for d in issue["devices"]]
                    self.save(db, "issue", issue)
            self.import_history(db, started)
            self.import_sources(db, data, started)
            self.repair_historical_links(db)
            for task in self.rows(db, "task", 1):
                self.settle_task(db, task, data, now)
            db.execute("INSERT OR REPLACE INTO meta VALUES('updated',?)", (str(now),))
            db.execute("INSERT OR REPLACE INTO meta VALUES('fresh',?)", (str(bool(fresh)),))
            if fresh:
                db.execute("INSERT OR REPLACE INTO meta VALUES('sensor_at',?)", (str(sampled_at - snap["sensors_age"]),))
            self.error = False
        view = self.view(limit=1)
        self.latest = {**view["totals"], "updated_at": now, "started_at": started}

    def view(self, limit=50, before=None):
        if not 1 <= limit <= 100:
            raise ValueError("limit")
        with self.lock, self.connect() as db:
            issues = sorted(self.rows(db, "issue", 1), key=lambda i: i["at"], reverse=True)
            clauses, args = "", []
            if before:
                at, ident = before.split("|", 1)
                if not math.isfinite(float(at)) or len(ident) > 128:
                    raise ValueError("cursor")
                clauses, args = " AND (at<? OR (at=? AND id<?))", [float(at), float(at), ident]
            rows = db.execute("SELECT data FROM records WHERE kind='task'" + clauses + " ORDER BY at DESC,id DESC LIMIT ?", (*args, limit + 1)).fetchall()
            tasks = [json.loads(r[0]) for r in rows[:limit]]
            for item in issues + tasks:
                item["events"] = [{"at": r["at"], "kind": r["kind"], **json.loads(r["data"])} for r in db.execute("SELECT * FROM events WHERE record_id=? ORDER BY id DESC LIMIT 20", (item["id"],))][::-1]
            working = sum(1 for i in issues if any(i["id"] in t["issues"] and t["status"] in ("requested", "confirmed") and t["response"] in ("waiting", "watching", "improving") for t in self.rows(db, "task", 1)))
            blocked = sum(1 for i in issues if any(d["state"] not in ("ready", "pending") for d in i.get("decisions", [])))
            meta = {r[0]: r[1] for r in db.execute("SELECT key,value FROM meta")}
            return {"box": "box-1", "started_at": float(meta.get("started", self.clock())),
                    "updated_at": float(meta.get("updated", 0)), "issues": issues, "tasks": tasks,
                    "sensor_at": float(meta["sensor_at"]) if meta.get("sensor_at") else None,
                    "fresh": meta.get("fresh") == "True" and self.clock() - float(meta.get("sensor_at", 0)) <= 600,
                    "totals": {"active": len(issues), "working": working, "blocked": blocked},
                    "next": str(tasks[-1]["at"]) + "|" + tasks[-1]["id"] if len(rows) > limit else None}
