"""Suggestion cards (spec 3.5, 5.5; addendum N5): what Hank offers instead of switching. Built only here, in code,
never by the model.

A card carries a checked copy of a body the cockpit itself sends to POST /api/cmd. The page sends that body (plus
"proposal": id) after the tap, so every guard of /api/cmd still applies; nothing here switches anything.

Rules, all from the cockpit (addendum N5):
- Allowlist: light, cfan, dfan, port, plant_stage, plant_apply, auto_humidifier, plant_start, feeding_setup. Anything
  else is refused.
- Two taps, as on the page: a port off, plant_stage, plant_apply, the humidifier automation on, plant_start and
  feeding_setup.
- feeding_setup (the fertilizer market, owner 23.10.2026: "ich nutze <Dünger>"): the page's own fertilizer switch,
  {"action": "feeding_setup", "setup": id}, only for an option the live feeding view offers and never for the one
  already in use. It stores the choice in the plant log under STATE_DIR through /api/cmd, exactly as the switch in the
  feeding card; amounts never carry over, every card still needs its own confirmation.
- plant_start (feeding v2, owner 23.10.2026: "meine Pflanze ist 5 Wochen alt"): the plant's start as the plant card
  stores it, exactly one of a date, an age in whole weeks (0 to 52) or in days (0 to plant.MAX_AGE_DAYS); never in
  the future, never more than plant.MAX_AGE_DAYS ago (the plant log's own bounds). It writes only the plant
  log under STATE_DIR, through /api/cmd, and never changes a dose.
- The humidifier automation is never suggested off (switching it off leaves the humidifier as it is).
- Light: 0 to 100; up by at most LIGHT_STEP points per card, from 0 the first card goes to LIGHT_FIRST (as
  server.py plant_apply does), and the next step up comes at the earliest LIGHT_WAIT_SECONDS after an accepted one.
- Fans: steps 0 to 10, one step up or down per card.
- A port off is never offered for a locked port (ports.json always_on), and for none at all while ports.json cannot be
  read (config.port_rules() is None: the cockpit then refuses every port off too).
- A card is valid for VALID_SECONDS (30 minutes).
- Acceptance 23.10.2026 (review 3): the 24 h light rule holds for plant_apply too (M6: the preset raises the light
  like a light card); stale readings give no card from a wish either (M7: the step would rest on an old level); and a
  card never goes against the direction the question asks for (L1: "Licht aus" gets no "Licht auf 85 %", "Mach das
  Licht an" at 60 % gets no card for 50 %).

from_request() reads an action wish (slots of WP4: verb, switch word, percent, step, devices); from_checks() turns
check rows off target into cards by the table in data/ledger_rules.json. Card.reason is "request" or
"<metric>:<status>", which the ledger uses to link a card to its entry and to keep a declined card from coming back.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Iterable, Mapping
from typing import Any

from datetime import date, datetime, timedelta

from vgrow import automation, config, plant
from vgrow.i18n import fmt_qty, localize, t

from . import pilot_report, text
from .livestate import LiveState, collect, port_name
from .types import Proposal, Verdict

_LOGGER = logging.getLogger(__name__)

KINDS = ("light", "cfan", "dfan", "port", "plant_stage", "plant_apply", "auto_humidifier", "plant_start",
         "feeding_setup")
START_KEYS = ("date", "age_weeks", "age_days")  # plant_start: exactly one of them, as the plant card sends
START_MAX_WEEKS = 52  # the plant card's bound: an age in whole weeks, 0 to 52
VALID_SECONDS = 30 * 60  # spec 5.5
LIGHT_MIN, LIGHT_MAX = 0, 100
LIGHT_STEP = 25  # server.py plant_apply: at most +25 points per tap (the cockpit's own safety rule, no source)
LIGHT_FIRST = 50  # server.py plant_apply: from 0 the first tap goes to 50
LIGHT_WAIT_SECONDS = 24 * 60 * 60  # addendum N5: the next light step at the earliest 24 h after an accepted one
FAN_MIN, FAN_MAX = 0, 10
FAN_STEP = 1  # addendum N5: one step per card
FANS = ("cfan", "dfan")
LIGHT_ACTIONS = ("light", "plant_apply")  # accepted cards that stepped the light up (journal origin hank_accepted)
_UNREAD = object()  # "read config.port_rules() now"

_CONFIRM_RE = re.compile(r"bestatig|confirm")
_PRESET_RE = re.compile(r"voreinstellung|preset")
_APPLY_RE = re.compile(r"ubernehm|ubernimm|anwend|\bapply\b|\buse\b")


class Refused(ValueError):
    """A body that is not a card Hank may offer."""


# ---------- reading the box ----------

def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def light_level(live: LiveState) -> int | None:
    return _int(_dict(live.snapshot.get("light")).get("level"))


def fan_step(live: LiveState, fan: str) -> int | None:
    return _int(_dict(live.snapshot.get(fan)).get("step"))


def port_on(live: LiveState, port: str) -> bool | None:
    on = _dict(_dict(live.snapshot.get("ports")).get(port)).get("on")
    return on if isinstance(on, bool) else None


def humidifier_port(live: LiveState) -> str:
    """The humidifier automation's port (automation.PORT unless the view names another)."""
    return str(live.automation_view.get("port") or automation.PORT)


def known_ports(live: LiveState, rules: Mapping[str, Any] | None) -> set[str]:
    return set(_dict(live.snapshot.get("ports"))) | set(rules or {}) | set(live.ports or {})


def stage_keys(live: LiveState) -> list[str]:
    return [str(s["key"]) for s in live.plant_view.get("stages") or [] if isinstance(s, dict) and s.get("key")]


def stage_label(live: LiveState, key: str) -> str:
    for stage in live.plant_view.get("stages") or []:
        if isinstance(stage, dict) and stage.get("key") == key:
            return str(stage.get("label") or key)
    return key


def light_step(current: int | None, target: int) -> int:
    """The level of the next card toward `target`: up by at most LIGHT_STEP, from 0 (or unknown) to at most
    LIGHT_FIRST; down straight to the target."""
    now = current or 0
    if target <= now:
        return max(LIGHT_MIN, target)
    return min(target, now + LIGHT_STEP) if now > 0 else min(target, LIGHT_FIRST)


def light_wait_until(live: LiveState) -> float | None:
    """When the next light step up may be offered, after the newest accepted card that raised the light or the
    autopilot's own newest increase (it comes through the snapshot, pilot_report.light_step_at; review 1, S10: one
    accepted card after the autopilot's +25 would have made +50 in a day); None when there is neither or the wait is
    over. A manual change by the owner still starts no wait for the cards."""
    newest = pilot_report.light_step_at(live)
    for entry in live.journal or []:
        entry = _dict(entry)
        if entry.get("origin") != "hank_accepted" or entry.get("action") not in LIGHT_ACTIONS:
            continue
        when = _epoch(entry.get("time"))
        if when is not None and (newest is None or when > newest):
            newest = when
    if newest is None:
        return None
    until = newest + LIGHT_WAIT_SECONDS
    return until if until > live.now.timestamp() else None


def light_wait_by_pilot(live: LiveState) -> bool:
    """Whether the running light wait comes from the autopilot's increase rather than an accepted card."""
    pilot = pilot_report.light_step_at(live)
    until = light_wait_until(live)
    return pilot is not None and until is not None and abs(until - (pilot + LIGHT_WAIT_SECONDS)) < 1


def _epoch(value: Any) -> float | None:
    from .stores import epoch_of

    return epoch_of(value)


# ---------- one card ----------

def check_body(body: Mapping[str, Any], live: LiveState | Mapping[str, Any], *,
               rules: Mapping[str, Any] | None | object = _UNREAD) -> dict[str, Any]:
    """A clean copy of a card body, or Refused. The same checks for a card built here and one handed in from
    outside: allowlist, types, ranges, the light step against the current level, the port lock."""
    state = collect(live)
    if not isinstance(body, Mapping):
        raise Refused("a card body is an object")
    action = body.get("action")
    if action not in KINDS:
        raise Refused(f"action {action!r} is not on the allowlist")
    if action == "light":
        level = _int(body.get("level"))
        if level is None or not LIGHT_MIN <= level <= LIGHT_MAX:
            raise Refused("light level must be 0 to 100")
        now = light_level(state) or 0
        top = now + LIGHT_STEP if now > 0 else LIGHT_FIRST
        if level > max(now, top):
            raise Refused(f"light {level} is more than one step up from {now}")
        return {"action": "light", "level": level}
    if action in FANS:
        step = _int(body.get("step"))
        if step is None or not FAN_MIN <= step <= FAN_MAX:
            raise Refused("fan step must be 0 to 10")
        now = fan_step(state, str(action))
        if now is not None and abs(step - now) > FAN_STEP:
            raise Refused(f"fan step {step} is more than one step from {now}")
        return {"action": action, "step": step}
    if action == "port":
        port, on = body.get("port"), body.get("on")
        if not isinstance(port, str) or not isinstance(on, bool):
            raise Refused("a port card needs port and on")
        table = config.port_rules() if rules is _UNREAD else rules
        if port not in known_ports(state, table if isinstance(table, Mapping) else None):
            raise Refused(f"unknown port {port!r}")
        if not on:
            if table is None:
                raise Refused("ports.json cannot be read: no port off")
            if _dict(_dict(table).get(port)).get("always_on"):
                raise Refused(f"{port} is locked on")
        elif isinstance(table, Mapping) and _dict(table.get(port)).get("device") == "pump":
            # A pump card would be one tap and run without end: no on card for a pump until a watering card with a
            # fixed duration exists (ports.json usb1 since 23.10.2026). Off stays a card, with two taps.
            raise Refused(f"{port} is a pump: no on card without a fixed duration")
        return {"action": "port", "port": port, "on": on}
    if action == "plant_stage":
        key = body.get("key")
        if not isinstance(key, str) or key not in stage_keys(state):
            raise Refused(f"unknown stage {key!r}")
        return {"action": "plant_stage", "key": key}
    if action == "plant_apply":
        preset = _dict(_dict(state.plant_view.get("stage")).get("preset"))
        if not preset:
            raise Refused("the stage has no preset")
        target = _int(preset.get("light_percent"))
        if target is not None and target > (light_level(state) or 0) and light_wait_until(state) is not None:
            raise Refused("the preset raises the light within 24 h of an accepted step")  # addendum N5 (review 3, M6)
        return {"action": "plant_apply"}
    if action == "plant_start":
        return _check_start(body, state)
    if action == "feeding_setup":
        if set(body) - {"action", "setup"}:
            raise Refused("a fertilizer card holds only its setup")
        setup = body.get("setup")
        options = [o for o in _dict(_dict(state.feeding_view).get("choice")).get("options") or [] if isinstance(o, dict)]
        found = next((o for o in options if o.get("id") == setup), None)
        if not isinstance(setup, str) or found is None:
            raise Refused(f"unknown fertilizer setup {setup!r}")
        if found.get("active"):
            raise Refused(f"{setup} is already the fertilizer in use")
        return {"action": "feeding_setup", "setup": setup}
    if action == "auto_humidifier":
        if body.get("on") is not True:
            raise Refused("the humidifier automation is only ever suggested on")
        return {"action": "auto_humidifier", "on": True}
    raise Refused(f"action {action!r} has no check")  # a kind in KINDS without its branch never falls through


def _today(state: LiveState) -> date:
    return state.now.date() if isinstance(state.now, datetime) else date.today()


def start_day(body: Mapping[str, Any], today: date) -> date | None:
    """The day a plant_start body stands for: its date, or today minus the weeks or days."""
    if "date" in body:
        return plant.parse_day(body.get("date"))
    for key, days in (("age_weeks", 7), ("age_days", 1)):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return today - timedelta(days=days * value)
    return None


def _check_start(body: Mapping[str, Any], state: LiveState) -> dict[str, Any]:
    """plant_start: only {"action", one of date / age_weeks / age_days}, in the bounds the plant card has."""
    keys = set(body) - {"action"}
    if len(keys) != 1 or not keys <= set(START_KEYS):
        raise Refused("a start card holds exactly one of date, age_weeks, age_days")
    (key,) = keys
    value = body[key]
    today = _today(state)
    if key == "date":
        if not isinstance(value, str) or plant.parse_day(value) is None:
            raise Refused(f"no date: {value!r}")
    else:
        limit = START_MAX_WEEKS if key == "age_weeks" else plant.MAX_AGE_DAYS
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= limit:
            raise Refused(f"{key} must be 0 to {limit}")
    day = start_day(body, today)
    if day is None or day > today:
        raise Refused("the start cannot lie in the future")
    if (today - day).days > plant.MAX_AGE_DAYS:
        raise Refused(f"the start lies more than {plant.MAX_AGE_DAYS} days back")
    return {"action": "plant_start", key: value}


def start_label(body: Mapping[str, Any], today: date, lang: str) -> str:
    """'Startdatum 18.09.2026 übernehmen (5 Wochen alt)', the date written as the page writes it."""
    day = start_day(body, today) or today
    info = plant.age_info(day, today)
    if not info or info["days"] == 0:
        return t("hank.proposal.start_today", lang, date=plant.date_text(day, lang))
    return t("hank.proposal.start", lang, date=plant.date_text(day, lang), age=plant.age_text(info, lang))


def two_taps(body: Mapping[str, Any]) -> bool:
    action = body.get("action")
    return (action == "port" and body.get("on") is False) or action in ("plant_stage", "plant_apply",
                                                                        "plant_start", "feeding_setup") or (
        action == "auto_humidifier" and body.get("on") is True)


def label(body: Mapping[str, Any], live: LiveState, lang: str) -> str:
    action = body.get("action")
    if action == "light":
        return t("hank.proposal.light", lang, value=fmt_qty(body["level"], "%", lang))
    if action in FANS:
        return t(f"hank.proposal.{action}", lang, step=body["step"])
    if action == "port":
        port = str(body["port"])
        name = str(localize(_dict(live.ports.get(port)), lang).get("label") or port_name(port))
        return t("hank.proposal.port_on" if body["on"] else "hank.proposal.port_off", lang, label=name,
                 port=port_name(port))
    if action == "plant_stage":
        return t("hank.proposal.stage", lang, stage=stage_label(live, str(body["key"])))
    if action == "plant_apply":
        stage = _dict(live.plant_view.get("stage"))
        return t("hank.proposal.apply", lang, stage=str(stage.get("label") or stage.get("key") or ""))
    if action == "plant_start":
        return start_label(body, _today(live), lang)
    if action == "feeding_setup":
        options = [o for o in _dict(_dict(live.feeding_view).get("choice")).get("options") or [] if isinstance(o, dict)]
        found = next((o for o in options if o.get("id") == body["setup"]), {})
        return t("hank.proposal.feeding_setup", lang, label=str(found.get("label") or body["setup"]))
    if action == "auto_humidifier":
        return t("hank.proposal.auto_on", lang)
    raise ValueError(f"no label for {action!r}")


def make(body: Mapping[str, Any], live: LiveState | Mapping[str, Any], lang: str = "de", *, reason: str = "request",
         now: float | None = None, rules: Mapping[str, Any] | None | object = _UNREAD) -> Proposal:
    """One card from a body: checked (Refused otherwise), labelled, valid for VALID_SECONDS."""
    state = collect(live)
    clean = check_body(body, state, rules=rules)
    start = state.now.timestamp() if now is None else now
    return Proposal(id="p-" + secrets.token_hex(4), kind=str(clean["action"]), body=clean,
                    label=label(clean, state, lang), reason=reason, expires=start + VALID_SECONDS,
                    two_taps=two_taps(clean))


def _try(body: Mapping[str, Any], live: LiveState, lang: str, reason: str, now: float | None,
         rules: Any) -> Proposal | None:
    try:
        return make(body, live, lang, reason=reason, now=now, rules=rules)
    except Refused as err:
        _LOGGER.info("no card for %s: %s", dict(body), err)
        return None


def _dedupe(cards: Iterable[Proposal | None]) -> list[Proposal]:
    out: list[Proposal] = []
    for card in cards:
        if card is not None and all(card.body != c.body for c in out):
            out.append(card)
    return out


# ---------- from an action wish (spec 3.5) ----------

def _named_ports(q: str, slots: Any, live: LiveState) -> list[str]:
    """Ports the question names: by the labels of ports.json (both languages), by "USB 2", or the humidifier by its
    name (the automation's port). Device names come from the labels, never from a card (spec 4.2)."""
    folded = text.fold(q)
    words = [w for w in re.findall(r"[^\W\d_]+", folded) if len(w) >= 5]
    out: list[str] = []
    for port in sorted(known_ports(live, live.ports)):
        names = [text.fold(port_name(port)), text.fold(port)]
        rule = _dict(live.ports.get(port))
        for key in ("label", "label_en"):
            if isinstance(rule.get(key), str):
                names.append(text.fold(rule[key]))
        hit = any(name and re.search(rf"(?<![^\W_]){re.escape(name)}(?![^\W_])", folded) for name in names)
        hit = hit or any(w in name for w in words for name in names[2:])
        if hit:
            out.append(port)
    devices = set(getattr(slots, "devices", None) or [])
    if "humidifier" in devices and humidifier_port(live) not in out:
        out.append(humidifier_port(live))
    for port in devices & {"usb1", "usb2"}:
        if port not in out:
            out.append(port)
    return out


def from_request(q: str, slots: Any, live: LiveState | Mapping[str, Any], *, lang: str = "de",
                 now: float | None = None, rules: Mapping[str, Any] | None | object = _UNREAD) -> list[Proposal]:
    """Cards for an action wish (intent "action", spec 3.5): "Dreh das Licht auf 100 Prozent hoch" gives the first
    step, at most +25 points (live-03). Nothing is switched; a wish the rules do not allow gives no card."""
    state = collect(live)
    if state.stale:
        return []  # review 3, M7: a step from old readings rests on an old level (as from_checks)
    folded = text.fold(q)
    devices = set(getattr(slots, "devices", None) or [])
    metrics = set(getattr(slots, "metrics", None) or [])
    switch = getattr(slots, "switch", None)
    percent = getattr(slots, "percent", None)
    step = getattr(slots, "step", None)
    cards: list[Proposal | None] = []

    if "light" in devices or "lamp" in devices or "light_percent" in metrics:
        current = light_level(state)
        target: int | None = None
        if isinstance(percent, int | float) and not isinstance(percent, bool):
            target = max(LIGHT_MIN, min(LIGHT_MAX, int(percent)))
        elif switch == "off":
            target = 0
        elif switch == "up":
            target = LIGHT_MAX
        elif switch == "on" and not current:
            target = LIGHT_FIRST  # "an" only means something while the light is off (review 3, L1)
        if target is not None and target != (current or 0):
            level = light_step(current, target)
            if level > (current or 0) and light_wait_until(state) is not None:
                level = None  # addendum N5: no second step up within 24 h of an accepted one
            if level is not None and level != (current or 0):
                cards.append(_try({"action": "light", "level": level}, state, lang, "request", now, rules))

    for fan in FANS:
        if fan not in devices and f"{fan}_step" not in metrics:
            continue
        current = fan_step(state, fan)
        if current is None:
            continue
        wish: int | None = None
        if isinstance(step, int | float) and not isinstance(step, bool):
            wish = int(step)
        elif switch in ("up", "on"):
            wish = current + FAN_STEP
        elif switch in ("down", "off"):
            wish = current - FAN_STEP
        if wish is None or wish == current:
            continue
        nxt = current + (FAN_STEP if wish > current else -FAN_STEP)
        if FAN_MIN <= nxt <= FAN_MAX:
            cards.append(_try({"action": fan, "step": nxt}, state, lang, "request", now, rules))

    if switch in ("on", "off"):
        if "automation" in devices:
            if switch == "on":  # "Automatik aus" is never suggested (addendum N5)
                cards.append(_try({"action": "auto_humidifier", "on": True}, state, lang, "request", now, rules))
        else:
            for port in _named_ports(q, slots, state):
                want = switch == "on"
                if port_on(state, port) is not want:
                    cards.append(_try({"action": "port", "port": port, "on": want}, state, lang, "request", now,
                                      rules))

    if _CONFIRM_RE.search(folded):
        key = getattr(slots, "stage", None) or _dict(state.automation_view.get("estimate")).get("key") or (
            state.stage_key if state.stage_set_by != "manual" else None)
        if key and not (key == state.stage_key and state.stage_set_by == "manual"):
            cards.append(_try({"action": "plant_stage", "key": str(key)}, state, lang, "request", now, rules))

    if _PRESET_RE.search(folded) and (_APPLY_RE.search(folded) or getattr(slots, "verb", False)):
        cards.append(_try({"action": "plant_apply"}, state, lang, "request", now, rules))
    return _dedupe(cards)


# ---------- the direction a question asks for (review 3, L1) ----------

def wishes(slots: Any, live: LiveState | Mapping[str, Any]) -> dict[str, str]:
    """The direction ("up" or "down") the question asks for, per light and fan action, from its switch word, percent
    or step; also without a switching verb ("Licht aus", "Abluft auf Stufe 0")."""
    state = collect(live)
    devices = set(getattr(slots, "devices", None) or [])
    metrics = set(getattr(slots, "metrics", None) or [])
    switch = getattr(slots, "switch", None)
    percent = getattr(slots, "percent", None)
    step = getattr(slots, "step", None)
    out: dict[str, str] = {}
    if "light" in devices or "lamp" in devices or "light_percent" in metrics:
        current = light_level(state) or 0
        if isinstance(percent, int | float) and not isinstance(percent, bool) and int(percent) != current:
            out["light"] = "up" if int(percent) > current else "down"
        elif switch in ("off", "down"):
            out["light"] = "down"
        elif switch == "up" or switch == "on" and not current:
            out["light"] = "up"
    for fan in FANS:
        if fan not in devices and f"{fan}_step" not in metrics:
            continue
        current = fan_step(state, fan)
        if isinstance(step, int | float) and not isinstance(step, bool) and current is not None and int(step) != current:
            out[fan] = "up" if int(step) > current else "down"
        elif switch in ("off", "down"):
            out[fan] = "down"
        elif switch in ("on", "up"):
            out[fan] = "up"
    if "light" in out:
        out["plant_apply"] = out["light"]
    return out


def direction(card: Proposal, live: LiveState | Mapping[str, Any]) -> str | None:
    """Which way a light or fan card moves the box, or None (a port, a stage, the automation)."""
    state = collect(live)
    body = card.body or {}
    if card.kind == "light":
        now = light_level(state) or 0
        level = _int(body.get("level"))
        return None if level is None or level == now else ("up" if level > now else "down")
    if card.kind in FANS:
        now = fan_step(state, card.kind)
        nxt = _int(body.get("step"))
        return None if now is None or nxt is None or nxt == now else ("up" if nxt > now else "down")
    if card.kind == "plant_apply":
        target = _int(_dict(_dict(state.plant_view.get("stage")).get("preset")).get("light_percent"))
        now = light_level(state) or 0
        return None if target is None or target == now else ("up" if target > now else "down")
    return None


def against(cards: Iterable[Proposal], wanted: Mapping[str, str],
            live: LiveState | Mapping[str, Any]) -> list[Proposal]:
    """The cards without those that go against the asked direction of their device."""
    out = []
    for card in cards:
        want = wanted.get(card.kind)
        if want is not None and direction(card, live) not in (None, want):
            continue
        out.append(card)
    return out


# ---------- from check rows (spec 5.5) ----------

def _rules_table() -> Mapping[str, Any]:
    from .ledger import load_rules

    return load_rules().get("actions") or {}


def _card_body(move: Mapping[str, Any], verdict: Verdict, live: LiveState) -> dict[str, Any] | None:
    """The body of one table move for a verdict, or None when it would change nothing."""
    action = move.get("action")
    if action in FANS:
        current = fan_step(live, str(action))
        if current is None:
            return None
        nxt = current + (FAN_STEP if move.get("dir") == "up" else -FAN_STEP)
        return {"action": action, "step": nxt} if FAN_MIN <= nxt <= FAN_MAX else None
    if action == "light":
        current = light_level(live)
        if move.get("dir") == "up":
            goal = verdict.lo if verdict.lo is not None else verdict.hi
            if goal is None or light_wait_until(live) is not None:
                return None
            level = light_step(current, int(goal))
            return {"action": "light", "level": level} if level > (current or 0) else None
        goal = verdict.hi if verdict.hi is not None else verdict.lo
        if goal is None or current is None or int(goal) >= current:
            return None
        return {"action": "light", "level": int(goal)}
    if action == "port":
        port = humidifier_port(live) if move.get("port") == "humidifier" else str(move.get("port"))
        want = bool(move.get("on"))
        return None if port_on(live, port) is want else {"action": "port", "port": port, "on": want}
    return None


def from_checks(verdicts: Iterable[Verdict] | Any, live: LiveState | Mapping[str, Any], *, lang: str = "de",
                now: float | None = None, rules: Mapping[str, Any] | None | object = _UNREAD,
                table: Mapping[str, Any] | None = None) -> list[Proposal]:
    """Cards for the verdicts off target (low, high), by the table of data/ledger_rules.json, in its order. Stale
    readings give no card. While the humidifier automation waits for a confirmed stage and a humidity verdict is
    shown, the estimated stage gets a confirm card (case live-04)."""
    state = collect(live)
    items = list(getattr(verdicts, "verdicts", verdicts) or [])
    if state.stale:
        return []
    moves = _rules_table() if table is None else table
    cards: list[Proposal | None] = []
    for verdict in items:
        if verdict.status not in ("low", "high"):
            continue
        for move in _dict(moves.get(verdict.metric)).get(verdict.status) or []:
            body = _card_body(_dict(move), verdict, state)
            if body is not None:
                cards.append(_try(body, state, lang, f"{verdict.metric}:{verdict.status}", now, rules))
    auto = state.automation_view
    estimate = _dict(auto.get("estimate")).get("key")
    if auto.get("enabled") and auto.get("needs_stage_confirmation") and estimate and any(
            v.metric == automation.METRIC for v in items):
        cards.append(_try({"action": "plant_stage", "key": str(estimate)}, state, lang, "stage", now, rules))
    return _dedupe(cards)


# ---------- issued cards (spec 5.5: valid 30 minutes) ----------

class Registry:
    """The cards handed out, by id, until they expire; the API looks a tapped or declined card up here. In memory on
    purpose: a card outlives no restart, and its 30 minutes are short."""

    def __init__(self, clock: Any = time.time) -> None:
        self.clock = clock
        self._cards: dict[str, Proposal] = {}

    def add(self, cards: Iterable[Proposal]) -> list[Proposal]:
        self.expire()
        out = list(cards)
        for card in out:
            self._cards[card.id] = card
        return out

    def get(self, card_id: str) -> Proposal | None:
        card = self._cards.get(card_id)
        if card is None or card.expires <= self.clock():
            return None
        return card

    def take(self, card_id: str) -> Proposal | None:
        """The card, once: a tapped or declined card is gone afterwards."""
        card = self.get(card_id)
        self._cards.pop(card_id, None)
        return card

    def open(self) -> list[Proposal]:
        """The cards still open, newest first, one per body (two answers may have handed out the same card): the
        today answer lists them (today.py)."""
        self.expire()
        out: list[Proposal] = []
        for card in reversed(list(self._cards.values())):
            if all(card.body != have.body for have in out):
                out.append(card)
        return out

    def expire(self) -> None:
        now = self.clock()
        for card_id in [k for k, c in self._cards.items() if c.expires <= now]:
            del self._cards[card_id]

    def __len__(self) -> int:
        self.expire()
        return len(self._cards)
