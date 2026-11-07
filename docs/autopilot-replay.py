"""Hank Autopilot: open-loop replay of the proposed rules over recorded box history.

OPEN LOOP, NO PHYSICAL MODEL. The recorded climate does not react to the simulated commands. A step of the exhaust
fan changes nothing in the data that follows. The replay therefore answers only: "given this climate, how many
commands would the rules send, how often would they reverse, and what is the worst hour". It does not answer whether
the climate would have improved. For a sustained excursion it is an upper bound on commands as long as the fan has
no effect, and a step count that real feedback would usually shorten.

Reads (read-only):
  history-24h.json, history-168h.json, state.json   (this folder)
  <repo>/kartei/stages/autoflower.json               (stage card, targets and presets)
Writes:
  replay-ergebnis.json, replay-ergebnis.txt          (this folder)

Run:  python replay.py
"""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path("F:/01_PROJECTS/Coding - AI Agents und Tools/vgrow-control")
CARD = REPO / "kartei" / "stages" / "autoflower.json"

try:
    from zoneinfo import ZoneInfo
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # no tz database: 22./23.10.2026 is summer time, UTC+2 (ends 25.10.2026)
    BERLIN = timezone(timedelta(hours=2))

# ---------------------------------------------------------------- parameters
# "vorher": the naive first cut. "nachher": tuned until the replay shows no reversal within an hour.
CONFIGS = {
    "vorher": {
        "smooth_n": 1,            # readings in the median (1 = raw value)
        "persist_min": 0,         # condition must hold this many minutes before a step
        "out_margin": {"probe_temp": 0.0, "probe_humidity": 0.0, "probe_vpd": 0.0},   # beyond the band by
        "in_margin": {"probe_temp": 0.0, "probe_humidity": 0.0, "probe_vpd": 0.0},    # "settled" = inside by
        "min_gap_same": 5,        # minutes between two steps in the same direction
        "min_gap_reverse": 5,     # minutes before a step in the opposite direction
        "return_after": 5,        # minutes settled in band before one step back toward the base step
        "dfan_per_hour": 99, "dfan_per_day": 999,
        "global_per_hour": 99, "global_per_day": 999,
        "dfan_span": 3,           # base step +- span, clamped to 1..10
        "light_window": (60, 240),  # minutes after lights on in which a light step may happen
        "light_quiet": 0,         # minutes without any other autopilot step before a light step
        "fog_gap": None,          # probe minus box humidity above this: humidity and VPD votes are void
    },
    "nachher": {
        "smooth_n": 5,
        "persist_min": 10,
        "out_margin": {"probe_temp": 0.5, "probe_humidity": 2.0, "probe_vpd": 0.05},
        "in_margin": {"probe_temp": 0.5, "probe_humidity": 2.0, "probe_vpd": 0.05},
        "min_gap_same": 30,
        "min_gap_reverse": 90,
        "return_after": 90,
        "dfan_per_hour": 2, "dfan_per_day": 8,
        "global_per_hour": 6, "global_per_day": 30,
        "dfan_span": 3,
        "light_window": (60, 360),
        "light_quiet": 60,
        "fog_gap": 20.0,          # 22./23.10.: median 12.3, outside the humidity hump never above 18.4
    },
}
# "zeit": the tuned timing without the fog guard, to show what each change does on its own.
CONFIGS["zeit"] = {**CONFIGS["nachher"], "fog_gap": None, "light_quiet": 0, "light_window": (60, 240)}
HARD_TEMP = 2.0          # degrees beyond the band: temperature outranks everything
LIGHT_STEP = 25          # points per step (proposals.LIGHT_STEP, server.py plant_apply)
LIGHT_WAIT_H = 24        # hours between two light steps (proposals.py)
LIGHT_TEMP_HEADROOM = 1.0  # probe temp must stay this far below the day maximum for 30 min
HUM_MIN_SWITCH = 15      # automation.MIN_SWITCH_SECONDS / 60

# Light cycle: the history has no light channel. Lights on at 07:28 is inferred from the temperature rise on 23.10
# (probe +1.4 degC in 10 min). Hours on per day: VIVOSUN recommends 18/6 or 20/4 for autos (card note, S6).
LIGHT_ON_HHMM = (7, 28)
CYCLES = {"18/6": 18, "20/4": 20}

START = {"light": 50, "dfan": 5, "cfan": 10, "natural_wind": True, "humidifier_on": False}  # state.json 23.10


# ---------------------------------------------------------------- data
def load_rows(name: str) -> tuple[list[dict], str]:
    raw = (HERE / name).read_bytes()
    rows = json.loads(raw)["rows"]
    rows = [r for r in rows if r.get("pTemp") is not None]
    rows.sort(key=lambda r: r["time"])
    return rows, hashlib.sha256(raw).hexdigest()


def stage_targets(stage: dict) -> dict:
    out = {"preset": stage.get("preset") or {}}
    for tg in stage.get("targets", []):
        key = tg["metric"] + ("_" + tg["when"] if tg.get("when") else "")
        out[key] = (tg["min"], tg["max"])
    return out


def lights_on(ts: int, hours_on: int) -> tuple[bool, int]:
    """(on?, minutes since the last lights-on edge) under the assumed schedule."""
    local = datetime.fromtimestamp(ts, BERLIN)
    on_today = local.replace(hour=LIGHT_ON_HHMM[0], minute=LIGHT_ON_HHMM[1], second=0, microsecond=0)
    if local < on_today:
        on_today -= timedelta(days=1)
    since = int((local - on_today).total_seconds() // 60)
    return since < hours_on * 60, since


# ---------------------------------------------------------------- the rules
class Sim:
    def __init__(self, cfg: dict, tg: dict, hours_on: int, lamp_confirmed: bool = True):
        self.cfg, self.tg, self.hours_on, self.lamp_confirmed = cfg, tg, hours_on, lamp_confirmed
        self.light, self.dfan, self.cfan = START["light"], START["dfan"], START["cfan"]
        self.base = START["dfan"]
        self.lo_step = max(1, self.base - cfg["dfan_span"])
        self.hi_step = min(10, self.base + cfg["dfan_span"])
        self.hum_on, self.hum_last = START["humidifier_on"], None
        self.window = []                       # recent readings for the median
        self.since = defaultdict(lambda: None)  # metric:dir -> first minute the condition held
        self.settled_since = None
        self.last_dfan, self.last_dfan_dir = None, 0
        self.last_light = None
        self.temp_ok_since = None
        self.commands = []                     # (ts, device, from, to, reason)
        self.blocked = defaultdict(int)        # why a wanted step did not go out
        self.at_bound_min = 0
        self.conflict_holds = 0
        self.conflicts = []
        self.fog_min = 0

    # helpers
    def _recent(self, ts: int, device: str | None, minutes: int) -> int:
        return sum(1 for c in self.commands if ts - c[0] < minutes * 60 and (device is None or c[1] == device))

    def _today(self, ts: int, device: str | None) -> int:
        return sum(1 for c in self.commands if ts - c[0] < 86400 and (device is None or c[1] == device))

    def _global_ok(self, ts: int) -> bool:
        if self._recent(ts, None, 60) >= self.cfg["global_per_hour"]:
            self.blocked["global_hour"] += 1
            return False
        if self._today(ts, None) >= self.cfg["global_per_day"]:
            self.blocked["global_day"] += 1
            return False
        return True

    def _send(self, ts, device, old, new, reason):
        self.commands.append((ts, device, old, new, reason))

    def _smoothed(self, row: dict) -> dict:
        self.window.append(row)
        self.window = self.window[-self.cfg["smooth_n"]:]
        med = lambda k: statistics.median(r[k] for r in self.window)
        return {"probe_temp": med("pTemp"), "probe_humidity": med("pHumi"), "probe_vpd": med("pVpd"),
                "box_humidity": med("bHumi")}

    def _held(self, key: str, cond: bool, ts: int) -> bool:
        if not cond:
            self.since[key] = None
            return False
        if self.since[key] is None:
            self.since[key] = ts
        return (ts - self.since[key]) >= self.cfg["persist_min"] * 60

    def step(self, row: dict) -> None:
        ts = row["time"]
        v = self._smoothed(row)
        day, since_on = lights_on(ts, self.hours_on)
        t_band = self.tg.get("probe_temp_day" if day else "probe_temp_night") or self.tg.get("probe_temp_day")
        h_band = self.tg.get("probe_humidity")
        p_band = self.tg.get("probe_vpd")
        hum_band = h_band
        om, im = self.cfg["out_margin"], self.cfg["in_margin"]

        # ---- humidifier: the existing rule (automation.py), unchanged
        if hum_band:
            lo, hi = hum_band
            raw = row["pHumi"]  # the existing rule reads the raw value, no median (automation.py:246)
            want = False if raw > hi else True if raw < lo else None
            if want is not None and want != self.hum_on and (
                    self.hum_last is None or ts - self.hum_last >= HUM_MIN_SWITCH * 60) and self._global_ok(ts):
                self._send(ts, "befeuchter", self.hum_on, want, "Feuchte %.1f, Band %s-%s" % (raw, lo, hi))
                self.hum_on, self.hum_last = want, ts

        # ---- exhaust fan: votes, ranked
        votes = []  # (rank, direction, reason)
        settled = True
        fog = self.cfg["fog_gap"] is not None and v["probe_humidity"] - v["box_humidity"] > self.cfg["fog_gap"]
        if fog:
            self.fog_min += 1  # the probe sits in humidifier mist: its humidity says nothing about the box air
            h_band = p_band = None
            settled = False  # humidity unknown while misted: no step back toward the base either
            self.since["h+"] = self.since["p-"] = None  # a reading from before the mist does not count after it
        if t_band:
            lo, hi = t_band
            x = v["probe_temp"]
            if self._held("t+", x > hi + om["probe_temp"], ts):
                votes.append((1 if x > hi + HARD_TEMP else 3, +1, "Temperatur %.1f > %s" % (x, hi)))
            if self._held("t-", x < lo - om["probe_temp"], ts):
                votes.append((1 if x < lo - HARD_TEMP else 3, -1, "Temperatur %.1f < %s" % (x, lo)))
            settled &= lo + im["probe_temp"] <= x <= hi - im["probe_temp"]
        if h_band:
            lo, hi = h_band
            x = v["probe_humidity"]
            if self._held("h+", x > hi + om["probe_humidity"], ts):
                votes.append((2, +1, "Feuchte %.1f > %s" % (x, hi)))
            # humidity low: the humidifier owns it, the exhaust does not step down for it
            settled &= x <= hi - im["probe_humidity"]
        if p_band:
            lo, hi = p_band
            x = v["probe_vpd"]
            if self._held("p-", x < lo - om["probe_vpd"], ts):
                votes.append((4, +1, "VPD %.2f < %s" % (x, lo)))
            # VPD high: ambiguous for the exhaust (dryer and warmer pull opposite ways), no vote
            settled &= x >= lo + im["probe_vpd"]

        direction, reason = 0, ""
        if votes:
            ups = [r for r in votes if r[1] > 0]
            downs = [r for r in votes if r[1] < 0]
            best_up = min((r[0] for r in ups), default=99)
            best_dn = min((r[0] for r in downs), default=99)
            if ups and downs:
                self.conflicts.append((ts, [r[2] for r in votes]))
                if best_up == best_dn:
                    self.conflict_holds += 1
                    direction = 0
                else:
                    direction = +1 if best_up < best_dn else -1
            else:
                direction = +1 if ups else -1
            if direction:
                win = min((r for r in votes if r[1] == direction), key=lambda r: r[0])
                reason = win[2]

        target = self.dfan
        if direction:
            self.settled_since = None
            target = self.dfan + direction
            if not (self.lo_step <= target <= self.hi_step):
                self.at_bound_min += 1
                target = self.dfan
                self.blocked["dfan_bound"] += 1
        else:
            if settled:
                self.settled_since = self.settled_since or ts
                if self.dfan != self.base and ts - self.settled_since >= self.cfg["return_after"] * 60 and (
                        self.last_dfan is None or ts - self.last_dfan >= self.cfg["return_after"] * 60):
                    direction = -1 if self.dfan > self.base else +1
                    target = self.dfan + direction
                    reason = "zurueck zur Grundstufe %d" % self.base
            else:
                self.settled_since = None

        if target != self.dfan:
            gap = self.cfg["min_gap_reverse"] if (self.last_dfan_dir and direction != self.last_dfan_dir) \
                else self.cfg["min_gap_same"]
            if self.last_dfan is not None and ts - self.last_dfan < gap * 60:
                self.blocked["dfan_gap"] += 1
            elif self._recent(ts, "abluft", 60) >= self.cfg["dfan_per_hour"]:
                self.blocked["dfan_hour"] += 1
            elif self._today(ts, "abluft") >= self.cfg["dfan_per_day"]:
                self.blocked["dfan_day"] += 1
            elif self._global_ok(ts):
                self._send(ts, "abluft", self.dfan, target, reason)
                self.dfan, self.last_dfan, self.last_dfan_dir = target, ts, direction
                for k in list(self.since):
                    self.since[k] = None  # give the step time to act before the next one
                self.settled_since = None

        # ---- circulation fan: hold the preset step; natural wind on means hands off (box.py:752-755)
        cf = self.tg["preset"].get("cfan_step")
        if cf is not None and not START["natural_wind"] and self.cfan != cf and self._global_ok(ts):
            self._send(ts, "umluft", self.cfan, cf, "Voreinstellung")
            self.cfan = cf

        # ---- light ramp toward the preset
        preset = self.tg["preset"].get("light_percent")
        if t_band and day:
            hi_day = (self.tg.get("probe_temp_day") or t_band)[1]
            ok = v["probe_temp"] <= hi_day - LIGHT_TEMP_HEADROOM
            self.temp_ok_since = (self.temp_ok_since or ts) if ok else None
        else:
            self.temp_ok_since = None
        w0, w1 = self.cfg["light_window"]
        if (preset and self.light > 0 and self.light < preset and day and w0 <= since_on <= w1
                and self.lamp_confirmed
                and self._recent(ts, None, self.cfg["light_quiet"]) == 0
                and self.temp_ok_since is not None and ts - self.temp_ok_since >= 30 * 60
                and (self.last_light is None or ts - self.last_light >= LIGHT_WAIT_H * 3600)
                and self._global_ok(ts)):
            new = min(preset, self.light + LIGHT_STEP)
            self._send(ts, "licht", self.light, new, "Rampe zur Voreinstellung %s" % preset)
            self.light, self.last_light = new, ts
        elif preset is not None and self.light > preset and day:
            # above the preset: straight down (proposals.py: down goes directly to the target)
            if self._global_ok(ts):
                self._send(ts, "licht", self.light, preset, "ueber der Voreinstellung")
                self.light, self.last_light = preset, ts


# ---------------------------------------------------------------- measures
def reversals(cmds: list, device: str, within_min: int = 60) -> int:
    seq = [c for c in cmds if c[1] == device]
    n = 0
    for a, b in zip(seq, seq[1:]):
        da = (a[3] - a[2]) if not isinstance(a[3], bool) else (1 if a[3] else -1)
        db = (b[3] - b[2]) if not isinstance(b[3], bool) else (1 if b[3] else -1)
        if da * db < 0 and b[0] - a[0] <= within_min * 60:
            n += 1
    return n


def worst_hour(cmds: list) -> tuple[int, int | None]:
    best, at = 0, None
    times = sorted(c[0] for c in cmds)
    for i, t0 in enumerate(times):
        n = sum(1 for t in times[i:] if t - t0 < 3600)
        if n > best:
            best, at = n, t0
    return best, at


def hhmm(ts):
    return datetime.fromtimestamp(ts, BERLIN).strftime("%d.%m. %H:%M")


def run(rows, cfg_name, stage, cycle_name, lamp_confirmed=True):
    sim = Sim(CONFIGS[cfg_name], stage_targets(stage), CYCLES[cycle_name], lamp_confirmed)
    for r in rows:
        sim.step(r)
    hours = (rows[-1]["time"] - rows[0]["time"]) / 3600 + 1 / 60
    per_dev = defaultdict(int)
    for c in sim.commands:
        per_dev[c[1]] += 1
    wh, wh_at = worst_hour(sim.commands)
    return {
        "config": cfg_name, "stage": stage["key"], "cycle": cycle_name, "hours": round(hours, 2),
        "commands": dict(per_dev), "total": len(sim.commands),
        "per_24h": round(len(sim.commands) * 24 / hours, 1),
        "reversals_1h": {d: reversals(sim.commands, d) for d in ("abluft", "befeuchter", "licht", "umluft")},
        "worst_hour": wh, "worst_hour_from": hhmm(wh_at) if wh_at else None,
        "minutes_at_bound": sim.at_bound_min, "conflict_holds": sim.conflict_holds,
        "conflict_minutes": len(sim.conflicts), "fog_minutes": sim.fog_min,
        "blocked": dict(sim.blocked),
        "dfan_range": [sim.lo_step, sim.hi_step],
        "timeline": [[hhmm(c[0]), c[1], c[2], c[3], c[4]] for c in sim.commands],
    }


def main():
    card = json.loads(CARD.read_text(encoding="utf-8"))
    stages = {s["key"]: s for s in card["stages"]}
    out = {"_note": "Open loop, no physical model: the recorded climate does not react to simulated commands.",
           "files": {}, "runs": []}
    datasets = {}
    for name in ("history-24h.json", "history-168h.json"):
        rows, digest = load_rows(name)
        datasets[name] = rows
        out["files"][name] = {"rows": len(rows), "sha256": digest,
                              "from": hhmm(rows[0]["time"]), "to": hhmm(rows[-1]["time"]),
                              "max_gap_s": max(b["time"] - a["time"] for a, b in zip(rows, rows[1:]))}
    same = out["files"]["history-24h.json"]["sha256"] == out["files"]["history-168h.json"]["sha256"]
    out["identical_files"] = same

    lines = ["Hank Autopilot, Nachspiel (open loop, kein physikalisches Modell)", ""]
    for name, f in out["files"].items():
        lines.append("%s: %d Zeilen, %s bis %s, groesste Luecke %d s, sha256 %s..." % (
            name, f["rows"], f["from"], f["to"], f["max_gap_s"], f["sha256"][:12]))
    lines.append("Beide Dateien identisch: %s" % ("ja" if same else "nein"))
    lines.append("")

    flower = [k for k in ("seedling", "veg", "early_flower", "mid_flower", "late_flower") if k in stages]
    for name, rows in datasets.items():
        if same and name == "history-168h.json":
            continue  # identical bytes: same result, reported once
        for cfg in ("vorher", "zeit", "nachher"):
            for cyc in CYCLES:
                for st in flower:
                    out["runs"].append(run(rows, cfg, stages[st], cyc))
        out["runs"].append({**run(rows, "nachher", stages["early_flower"], "18/6", lamp_confirmed=False),
                            "lamp_confirmed": False})

    hdr = "%-7s %-5s %-12s %5s %6s %6s %6s %5s %5s | %-22s | %4s %-12s | %4s %4s" % (
        "Satz", "Zyklus", "Phase", "Abl.", "Befeu", "Licht", "Umlft", "Summe", "/24h",
        "Umkehr<1h Ab/Be/Li", "WH", "ab", "Ansch", "Konfl") + " Nebel"
    lines.append(hdr)
    for r in out["runs"]:
        c, rv = r["commands"], r["reversals_1h"]
        lines.append("%-7s %-5s %-12s %5d %6d %6d %6d %5d %5.1f | %-22s | %4d %-12s | %4d %4d" % (
            r["config"], r["cycle"], r["stage"], c.get("abluft", 0), c.get("befeuchter", 0), c.get("licht", 0),
            c.get("umluft", 0), r["total"], r["per_24h"], "%d/%d/%d" % (rv["abluft"], rv["befeuchter"], rv["licht"]),
            r["worst_hour"], r["worst_hour_from"] or "-", r["minutes_at_bound"], r["conflict_holds"],
            ) + " %5d" % r["fog_minutes"] + ("  (Lampenabstand nicht bestaetigt)" if r.get("lamp_confirmed") is False else ""))
    lines.append("")
    lines.append("Abl. = Abluft, Befeu = Befeuchter, WH = Befehle in der schlechtesten Stunde, Ansch = Minuten am")
    lines.append("Anschlag (wollte weiter, Grenze erreicht), Konfl = Minuten mit gleichrangigem Konflikt (gehalten), Nebel = Minuten mit Nebel-Sperre.")
    lines.append("")
    for r in out["runs"]:
        if r["config"] in ("vorher", "zeit", "nachher") and r["cycle"] == "18/6" and r["stage"] in ("early_flower", "late_flower") \
                and r.get("lamp_confirmed") is not False:
            lines.append("Zeitleiste %s, %s, %s (Abluft-Grenzen %s, gesperrt: %s):" % (
                r["config"], r["cycle"], r["stage"], r["dfan_range"], r["blocked"]))
            for e in r["timeline"][:40]:
                lines.append("  %s  %-10s %s -> %s  %s" % tuple(e))
            if len(r["timeline"]) > 40:
                lines.append("  ... %d weitere" % (len(r["timeline"]) - 40))
            lines.append("")
    (HERE / "replay-ergebnis.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "replay-ergebnis.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
