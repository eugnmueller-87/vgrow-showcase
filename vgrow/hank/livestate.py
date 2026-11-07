"""The live state as Hank reads it (spec 4.2, addendum N1): one compact line per group, S1 to S9.

The collector in wiring.py (WP8) hands over the objects it reads, never the box itself: box.snapshot(), the camera
status, plant.view, automation.view, alerts.view, feeding.view, vision.view (only busy), ports (config.port_rules()),
the ledger items, Hank's journal and the time. collect() takes them as one mapping, with the same names as
tests/hank_eval/fixtures.Live.sources().

The digest is stable: ages in whole minutes, numbers rounded as the page shows them, no seconds, so two questions
inside the same minute see the same text (spec 4.1, prompt prefix). The groups have fixed markers [S1] to [S9];
the validator counts them like facts (addendum N1).

Left out on purpose: the "online" flag of the snapshot (the device list's online flag is unreliable and only the
shadow counts, case live-05 forbids calling the box online or offline from the flag alone), the light schedule and
the history of readings (not in the code, spec 4.2). Views are expected in the answer language (plant.view and
feeding.view localize their texts); the collector builds them for it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from vgrow.automation import STALE_SECONDS
from vgrow.i18n import fmt_num, fmt_qty, fmt_time, localize, t

from . import text
from .types import Num

GROUPS = ("age", "climate", "light", "fans", "ports", "camera", "stage", "automation", "actions")
MARKERS = {key: f"S{i}" for i, key in enumerate(GROUPS, 1)}
RECENT = 3  # spec 4.2: the last three automation lines and the last three journal lines
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Group:
    """One line of the state digest. `id` is its marker S1 to S9, `numbers` what the validator allows for it."""

    id: str
    key: str
    text: str
    numbers: tuple[Num, ...]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _now(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.now()


def _epoch(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def minutes(seconds: Any) -> int | None:
    """Whole minutes, rounded down, never below 0; None for no age."""
    if seconds is None or isinstance(seconds, bool):
        return None
    try:
        return max(0, int(float(seconds) // 60))
    except (TypeError, ValueError):
        return None


def day_text(day: Any, lang: str) -> str:
    """A date as a short day: 22.10. in German, Oct 22 in English; the input as it is when it is no date."""
    try:
        when = datetime.fromisoformat(str(day))
    except ValueError:
        return str(day)
    return f"{when.day}.{when.month}." if lang == "de" else f"{_MONTHS_EN[when.month - 1]} {when.day}"


@dataclass
class LiveState:
    """The read-only objects of one moment. Nothing here switches or writes."""

    snapshot: dict[str, Any] = field(default_factory=dict)
    camera: dict[str, Any] = field(default_factory=dict)
    plant_view: dict[str, Any] = field(default_factory=dict)
    automation_view: dict[str, Any] = field(default_factory=dict)
    alerts_view: list[dict[str, Any]] = field(default_factory=list)
    feeding_view: dict[str, Any] = field(default_factory=dict)
    vision: dict[str, Any] = field(default_factory=dict)
    ports: dict[str, dict[str, Any]] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    journal: list[dict[str, Any]] = field(default_factory=list)
    now: datetime = field(default_factory=datetime.now)
    # v1.3: the stage group's bloom start and height (_g_stage). The prompt's last trimming step turns them off
    # (context._trim) before the sheet is called too long; the draft and every code answer keep them.
    stage_extras: bool = True

    # ---------- plain readings ----------

    @property
    def sensors_age(self) -> float | None:
        age = self.snapshot.get("sensors_age")
        return None if age is None or isinstance(age, bool) else float(age)

    @property
    def stale(self) -> bool:
        """Readings older than the automation's limit (600 s) or none at all: no verdicts from them (spec 4.3)."""
        age = self.sensors_age
        return age is None or age > STALE_SECONDS

    @property
    def stage(self) -> dict[str, Any] | None:
        stage = self.plant_view.get("stage")
        return stage if isinstance(stage, dict) and stage.get("key") else None

    @property
    def stage_key(self) -> str | None:
        return (self.stage or {}).get("key")

    @property
    def stage_set_by(self) -> str | None:
        return (self.stage or {}).get("set_by")

    @property
    def checks(self) -> list[dict[str, Any]]:
        """plant.evaluate() rows in the view's language."""
        rows = self.plant_view.get("checks")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def climate(self, key: str) -> float | None:
        value = _dict(self.snapshot.get("climate")).get(key)
        return None if value is None or isinstance(value, bool) else float(value)

    # ---------- the tank (type-9 wet/dry water sensor, vgrow/box.py water_view) ----------

    @property
    def tank_state(self) -> str | None:
        """"wet" or "dry" for a type-9 sensor, None for a level sensor, an unknown value or no sensor."""
        state = _dict(self.snapshot.get("climate")).get("water_state")
        return state if state in ("wet", "dry") else None

    @property
    def tank_since(self) -> float | None:
        """When the wet/dry sensor last changed (epoch seconds), None when not known."""
        return _epoch(_dict(self.snapshot.get("climate")).get("water_since")) if self.tank_state else None

    def tank_text(self, lang: str) -> str | None:
        """ "Wasser im Tank: vorhanden" or "Tank leer seit 16:05" (without the time when its change is not known);
        None without a wet/dry sensor, then the water level line applies."""
        state = self.tank_state
        if state is None:
            return None
        if state == "wet":
            return t("hank.digest.tank_wet", lang)
        since = self.tank_since
        return (t("hank.digest.tank_dry_since", lang, time=fmt_time(since, lang)) if since is not None
                else t("hank.digest.tank_dry", lang))

    def port_label(self, port: str, lang: str) -> str:
        rule = localize(_dict(self.ports.get(port)), lang)
        return str(rule.get("label") or port)

    # ---------- digest ----------

    def groups(self, lang: str) -> list[Group]:
        out = []
        for key in GROUPS:
            line = getattr(self, f"_g_{key}")(lang)
            out.append(Group(MARKERS[key], key, line, tuple(text.parse_numbers(line, lang))))
        return out

    def digest(self, lang: str, keep: set[str] | None = None) -> str:
        """The STAND/STATE block, one marked line per group; `keep` limits it to these group keys."""
        return "\n".join(f"[{g.id}] {g.text}" for g in self.groups(lang) if keep is None or g.key in keep)

    def _q(self, value: Any, unit: str, lang: str, digits: int | None = None) -> str:
        if value is None or isinstance(value, bool):
            return t("hank.digest.none", lang)
        return fmt_qty(value, unit, lang, digits)

    def _r(self, value: Any, metric: str, unit: str, lang: str) -> str:
        if value is None or isinstance(value, bool):
            return t("hank.digest.none", lang)
        lo, hi = self.band(metric)
        return reading(value, metric, unit, lang, lo, hi)

    def band(self, metric: str) -> tuple[Any, Any]:
        """The target band (min, max) of the check row that judges `metric` off target right now, or (None, None):
        a reading the code calls too high or too low is written with the decimals that show it (reading())."""
        for row in self.checks:
            if row.get("metric") == metric and row.get("status") in ("low", "high"):
                return row.get("min"), row.get("max")
        return None, None

    def _g_age(self, lang: str) -> str:
        parts = []
        age = minutes(self.sensors_age)
        parts.append(t("hank.digest.age_none", lang) if age is None else t("hank.digest.age", lang, minutes=age))
        shadow = minutes(self.snapshot.get("shadow_age"))
        if shadow is not None and shadow != age:
            parts.append(t("hank.digest.shadow", lang, minutes=shadow))
        if self.snapshot.get("mqtt") is False:
            parts.append(t("hank.digest.mqtt_off", lang))
        if self.snapshot.get("error"):
            parts.append(t("hank.digest.error", lang, error=str(self.snapshot["error"])))
        return _sentence(", ".join(parts))

    def _g_climate(self, lang: str) -> str:
        c = lambda key: self.climate(key)  # noqa: E731
        r = lambda key, unit: self._r(c(key), key, unit, lang)  # noqa: E731
        parts = [
            t("hank.digest.probe", lang, humidity=r("probe_humidity", "%"), temp=r("probe_temp", "°C"),
              vpd=r("probe_vpd", "kPa")),
            t("hank.digest.box", lang, humidity=r("humidity", "%"), temp=r("temp", "°C")),
            self.tank_text(lang) or t("hank.digest.water", lang, level=r("water_level", "%")),
        ]
        if c("room_humidity") is not None or c("room_temp") is not None:
            parts.append(t("hank.digest.room", lang, humidity=r("room_humidity", "%"), temp=r("room_temp", "°C")))
        return " ".join(_sentence(p) for p in parts)

    def _g_light(self, lang: str) -> str:
        light = _dict(self.snapshot.get("light"))
        spectrum = light.get("spectrum")
        name = t(f"hank.digest.spectrum.{spectrum}", lang) if spectrum in ("seedling", "veg", "bloom") else (
            str(spectrum) if spectrum else t("hank.digest.none", lang))
        line = t("hank.digest.light", lang, level=self._q(light.get("level"), "%", lang), spectrum=name)
        if light.get("in_plan"):
            line += ", " + t("hank.digest.light_plan", lang)
        return _sentence(line)

    def _fan(self, fan: dict[str, Any], lang: str) -> str:
        step = fan.get("step")
        out = t("hank.digest.none", lang) if step is None else t("hank.digest.fan_step", lang, step=step)
        if fan.get("natural_wind"):
            out += ", " + t("hank.digest.natural_wind", lang)
        return out

    def _g_fans(self, lang: str) -> str:
        return _sentence(t("hank.digest.fans", lang, cfan=self._fan(_dict(self.snapshot.get("cfan")), lang),
                           dfan=self._fan(_dict(self.snapshot.get("dfan")), lang)))

    def _g_ports(self, lang: str) -> str:
        items = []
        for port, report in sorted(_dict(self.snapshot.get("ports")).items()):
            report = _dict(report)
            on = report.get("on")
            state = [t("hank.digest.none", lang) if on is None else
                     t("hank.digest.port_on" if on else "hank.digest.port_off", lang)]
            if _dict(self.ports.get(port)).get("always_on"):
                state.append(t("hank.digest.port_locked", lang))
            if report.get("can_write") is False:
                state.append(t("hank.digest.port_app", lang))
            items.append(t("hank.digest.port", lang, port=port_name(port), label=self.port_label(port, lang),
                           state=", ".join(state)))
        return ". ".join(items) + "." if items else _sentence(t("hank.digest.none", lang))

    def _g_camera(self, lang: str) -> str:
        cam = self.camera
        if not cam or cam.get("configured") is False:
            state = t("hank.digest.camera_none", lang)
        elif cam.get("reachable") is False:
            off = minutes(cam.get("offline_seconds"))
            state = t("hank.digest.camera_down", lang) if off is None else t("hank.digest.camera_off", lang,
                                                                              minutes=off)
        else:
            state = t("hank.digest.camera_ok", lang)
            if cam.get("recording"):
                state += ", " + t("hank.digest.camera_rec", lang)
        return _sentence(t("hank.digest.camera", lang, state=state))

    def _g_stage(self, lang: str) -> str:
        """The stage, then (v1.2, owner 23.10.2026) the bloom start with where it comes from, when it says more than
        the stage line: a date the owner gave, or one that is not the stage's own start; the camera's estimate as an
        upper bound ("Blütebeginn spätestens 22.10."), so the model never counts "one day of flowering" from it.
        Then (v1.3, owner: "dass die KI sofort sieht, wo wir stehen") the newest height with its day, about ten
        tokens. The stage group is never trimmed, but these two extras are the prompt's last trimming step
        (stage_extras, context._trim)."""
        stage = self.stage
        bloom = self._bloom(lang, stage) if self.stage_extras else None
        extra = [bloom] if bloom else []
        height = _dict(self.plant_view.get("height")) if self.stage_extras else {}
        if height.get("cm") is not None and not isinstance(height.get("cm"), bool):
            extra.append(_sentence(t("hank.digest.height", lang, value=self._q(height["cm"], "cm", lang),
                                     date=day_text(height.get("date"), lang))))
        if not stage:
            return " ".join([_sentence(t("hank.digest.stage_none", lang)), *extra])
        set_by = stage.get("set_by")
        how = t(f"hank.digest.set_by.{set_by}", lang) if set_by in ("manual", "estimate", "days") else str(set_by)
        day = self.plant_view.get("day")
        line = _sentence(t("hank.digest.stage", lang, label=stage.get("label") or stage["key"],
                           day="?" if day is None else day,
                           since=day_text(stage.get("since"), lang) if stage.get("since") else "?", set_by=how))
        return " ".join([line, *extra])

    def _bloom(self, lang: str, stage: dict[str, Any] | None) -> str | None:
        bloom = _dict(self.plant_view.get("bloom"))
        by = bloom.get("by")
        if not bloom.get("date") or by not in ("owner", "plan", "stage", "estimate"):
            return None
        if by == "estimate":
            return _sentence(t("hank.digest.bloom_latest", lang, date=day_text(bloom["date"], lang)))
        if by == "stage" and stage and stage.get("since") == bloom["date"]:
            return None  # the camera's estimate on the stage's own day: the stage line says it already
        return _sentence(t("hank.digest.bloom", lang, date=day_text(bloom["date"], lang),
                           by=t(f"hank.digest.bloom_by.{by}", lang)))

    def _g_automation(self, lang: str) -> str:
        auto = self.automation_view
        if not auto.get("enabled"):
            return _sentence(t("hank.digest.automation", lang, state=t("hank.digest.auto_off", lang)))
        parts = [t("hank.digest.auto_on", lang)]
        band = auto.get("band") or [None, None]
        if isinstance(band, list | tuple) and len(band) == 2 and None not in band:
            parts.append(t("hank.digest.auto_band", lang, lo=fmt_num(band[0], lang), hi=fmt_qty(band[1], "%", lang)))
        paused = _epoch(auto.get("paused_until"))
        if paused:
            parts.append(t("hank.digest.auto_paused", lang, time=fmt_time(paused, lang)))
        if auto.get("needs_stage_confirmation"):
            parts.append(t("hank.digest.auto_stage", lang))
        if auto.get("stale"):
            parts.append(t("hank.digest.auto_stale", lang))
        if auto.get("missing"):
            parts.append(t("hank.digest.auto_missing", lang))
        return _sentence(t("hank.digest.automation", lang, state=", ".join(parts)))

    def _g_actions(self, lang: str) -> str:
        items: list[tuple[float, str]] = []
        for entry in list(self.automation_view.get("recent") or [])[:RECENT]:
            entry = _dict(entry)
            when = _epoch(entry.get("time"))
            action = entry.get("action")
            label = t(f"hank.digest.action.{action}", lang) if action in _AUTO_ACTIONS else str(action)
            if when is not None:
                items.append((when, f"{fmt_time(when, lang)} {label}"))
        for entry in list(self.journal or [])[-RECENT:]:
            entry = _dict(entry)
            when = _epoch(entry.get("time"))
            origin = entry.get("origin")
            who = t(f"hank.digest.origin.{origin}", lang) if origin in _ORIGINS else str(origin or "?")
            what = str(entry.get("summary") or entry.get("action") or "?")
            if when is not None:
                items.append((when, f"{fmt_time(when, lang)} {what} ({who})"))
        fed = _dict(self.feeding_view.get("last_fed"))
        if fed.get("time"):
            liters = fed.get("liters")
            items.append((_epoch(fed["time"]) or 0.0, t("hank.digest.fed", lang, date=day_text(fed["time"], lang),
                                                        liters="?" if liters is None else fmt_num(liters, lang))))
        if not items:
            return _sentence(t("hank.digest.actions_none", lang))
        items.sort(key=lambda item: item[0], reverse=True)
        return _sentence(t("hank.digest.actions", lang, items="; ".join(line for _, line in items)))


_AUTO_ACTIONS = ("humidifier_on", "humidifier_off", "paused_manual", "automation_on", "automation_off",
                 "failsafe_off", "stage_pause_off", "profile_pause_off")
_ORIGINS = ("human", "automation", "hank_accepted", "hank_declined")


def _frac(value: float | None) -> bool:
    return value is not None and not float(value).is_integer()


# Decimals of a reading as the cockpit's tiles show it (web/app.js TILES and the water tile), so Hank never says
# "54,39 %" next to a tile that shows 54 % (spec 4.1: numbers rounded as on the page; screens review 2, finding 7).
READING_DIGITS = {"probe_humidity": 0, "humidity": 0, "room_humidity": 0, "water_level": 0,
                  "probe_temp": 1, "temp": 1, "room_temp": 1, "water_temp": 1, "probe_vpd": 2, "vpd": 2}


BOUNDARY_EXTRA = 2  # at most this many decimals more than the tile, to show a reading just outside its band


def _bound(value: Any) -> float | None:
    return None if value is None or isinstance(value, bool) or not isinstance(value, int | float) else float(value)


def _outside(value: float, lo: Any, hi: Any) -> bool:
    top, bottom = _bound(hi), _bound(lo)
    return (top is not None and value > top) or (bottom is not None and value < bottom)


def reading(value: Any, metric: str, unit: str, lang: str, lo: Any = None, hi: Any = None) -> str:
    """A reading with its unit, rounded as the page shows it; a whole number stays bare.

    lo and hi: the target band, when the caller knows it. A reading outside the band whose rounded form would land
    on the bound or inside it ("60 %" for 60.4 with the target 45 to 60) gets one decimal more, and a second one if
    that is still not enough, so "zu hoch" never stands next to a number that looks on target (owner, 23.10.2026:
    "Humidity at the probe: 60%, too high (target 45 to 60%)")."""
    digits = READING_DIGITS.get(metric)
    if digits is None:
        return fmt_qty(value, unit, lang)
    number = float(value)
    rounded = round(number, digits)
    if _outside(number, lo, hi) and not _outside(rounded, lo, hi):
        for more in range(1, BOUNDARY_EXTRA + 1):
            finer = round(number, digits + more)
            if _outside(finer, lo, hi) or more == BOUNDARY_EXTRA:
                return fmt_qty(finer, unit, lang, digits + more)
    return fmt_qty(rounded, unit, lang, None if float(rounded).is_integer() else digits)


def _sentence(line: str) -> str:
    line = line.strip()
    if not line:
        return line
    line = line[0].upper() + line[1:]
    return line if line.endswith((".", "!", "?")) else line + "."


def port_name(port: str) -> str:
    """usb2 -> USB 2 (the page's name for a port key)."""
    if port.lower().startswith("usb") and port[3:].isdigit():
        return f"USB {port[3:]}"
    return port


def collect(sources: Mapping[str, Any] | LiveState) -> LiveState:
    """A LiveState from the collector's mapping (names as fixtures.Live.sources()); missing parts stay empty."""
    if isinstance(sources, LiveState):
        return sources

    def get(name: str, kind: type) -> Any:
        value = sources.get(name)
        return value if isinstance(value, kind) else kind()

    return LiveState(
        snapshot=get("snapshot", dict), camera=get("camera", dict), plant_view=get("plant_view", dict),
        automation_view=get("automation_view", dict), alerts_view=get("alerts_view", list),
        feeding_view=get("feeding_view", dict), vision=get("vision", dict), ports=get("ports", dict),
        ledger=get("ledger", list), journal=get("journal", list), now=_now(sources.get("now")),
    )
