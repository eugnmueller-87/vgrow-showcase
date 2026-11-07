"""What Hank says while the VPD at the probe is above its band (23.10.2026).

Measured live that evening at 21:20: probe at leaf height 25.06 °C, 45.5 %, VPD 1.73 kPa against the early flower band of
1.0 to 1.4 kPa, lights on at 75 % without a light plan. The owner asked: "Wie kriege ich das runter?" Nothing reacted: the
humidifier rule held at 45.5 % (the card's minimum is 45 %), the autopilot had no vote for a VPD too high.

explain() gives, from the read-only moment only (livestate.collect) and in plain words:
1. the value against the band, and why: the air is too dry for this temperature, with the humidity the current
   temperature needs for the band's maximum (the box's Tetens formula, vgrow/automation.py saturation_kpa); when even the
   band's top humidity would not do, that it is mostly too warm;
2. what already runs: the humidifier on its port, the humidifier rule with the floor it switches on below (the
   automation's own humidity_floor, from its view), the exhaust step, the autopilot's mode;
3. what blocks the humidifier rule (rule off, stage only estimated, the humidifier not named on its port, a changed port
   assignment, the plausibility stop) and the autopilot (off, locked, stage, lamp distance, its exhaust status), each
   with its existing two-tap card (plant_stage, auto_humidifier on) or the place on the page;
4. why the light waits: more light makes it warmer and the air drier for the plant, the light rises only once the VPD
   is inside its band;
5. a light without a plan (it runs around the clock) once, with the stage card's own light-cycle note, no new number.

The autopilot off: the answer points to the Autopilot card and its "Beobachten" button (one tap there; Automatik takes
two), never straight to Automatik, and the answer's link is that card. Never a bare "humidifier on" card: the only
humidifier card is the rule's own switch (auto_humidifier, two taps), so every gate of the rule still holds. Hank still
switches nothing; vgrow/autopilot.py is never imported (tests/test_autopilot.py checks it).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from vgrow import automation, i18n, plant
from vgrow.i18n import fmt_qty, t

from . import pilot_report, proposals, verdicts
from .livestate import LiveState, collect, port_name, reading
from .types import Proposal

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
METRIC = "probe_vpd"
HUMIDITY = "probe_humidity"
_SOURCE_MARK = re.compile(r"\s*\((?:[SN]\d+)(?:\s*,\s*[SN]\d+)*\)")  # "(S1)" in a card's text
_LIGHT_CYCLE = ("lichtzyklus", "light cycle")  # the stage card's note on the light cycle starts with this

DE: dict[str, str] = {
    "hank.vpd.head": "Der VPD steht bei {value}, über dem {target}.",
    "hank.vpd.why": "Die Luft ist für {temp} zu trocken: bei dieser Temperatur braucht ein VPD bis {vmax} mindestens "
                    "{need} Luftfeuchte, am Fühler sind es {hum}.",
    "hank.vpd.why_warm": "Bei {temp} bräuchte ein VPD bis {vmax} mindestens {need} Luftfeuchte, mehr als die "
                         "Obergrenze von {hmax}: hier ist es vor allem zu warm.",
    "hank.vpd.why_plain": "Die Luft ist zu trocken oder zu warm.",
    "hank.vpd.runs": "Was schon läuft: {items}.",
    "hank.vpd.run_port": "{label} ({port}) {state}",
    "hank.vpd.on": "an",
    "hank.vpd.off": "aus",
    "hank.vpd.run_rule": "die Befeuchter-Regel schaltet unter {floor} ein (für {temp} berechnet)",
    "hank.vpd.run_rule_card": "die Befeuchter-Regel schaltet unter {floor} ein",
    "hank.vpd.run_dfan": "Abluft Stufe {step}",
    "hank.vpd.run_pilot_auto": "der Autopilot regelt",
    "hank.vpd.run_pilot_observe": "der Autopilot beobachtet nur",
    "hank.vpd.hum_off": "Die Befeuchter-Regel ist aus: einschalten mit zwei Tipps (Karte unten).",
    "hank.vpd.hum_stage": "Die Befeuchter-Regel schaltet erst mit bestätigter Phase: ich schätze {stage}, bestätigen "
                          "mit zwei Tipps (Karte unten).",
    "hank.vpd.hum_stage_none": "Die Befeuchter-Regel schaltet erst mit einer Phase, die du setzt: in der Pflanzenkarte.",
    "hank.vpd.hum_port": "Die Befeuchter-Regel schaltet {port} erst ein, wenn du dort den Befeuchter nennst: in der "
                         "Karte Autopilot unter Ausnahmen, „An {port} hängt: Befeuchter“.",
    "hank.vpd.hum_ports": "Die Belegung der Anschlüsse hat sich geändert: bis du sie in der Karte Autopilot bestätigst, "
                          "schaltet die Befeuchter-Regel nicht ein.",
    "hank.vpd.hum_implausible": "Die Befeuchter-Regel steht seit dem Plausibilitätsstopp: nenne in der Karte Autopilot "
                                "unter Ausnahmen neu, was an {port} hängt.",
    "hank.vpd.pilot_off": "Der Autopilot ist aus. Stell ihn in der Karte Autopilot auf „Beobachten“: dann zeigt er, was "
                          "er tun würde, und schaltet nichts. Automatik erst danach, mit zwei Tipps.",
    "hank.vpd.pilot_stage": "Auch der Autopilot regelt erst mit bestätigter Phase.",
    "hank.vpd.pilot_lamp": "Das Licht erhöht der Autopilot erst, wenn du in der Karte Autopilot den Lampenabstand "
                           "bestätigst.",
    "hank.vpd.pilot_dfan": "Autopilot, {status}.",
    "hank.vpd.light_waits": "Das Licht wartet: mehr Licht macht es wärmer und die Luft für die Pflanze trockener. Es "
                            "steigt erst, wenn der VPD in seinem Band ist.",
    "hank.vpd.no_plan": "Das Licht läuft ohne Lichtplan, also durchgehend. Die Phasenkarte sagt dazu: {note}",
}

EN: dict[str, str] = {
    "hank.vpd.head": "The VPD is {value}, above the {target}.",
    "hank.vpd.why": "The air is too dry for {temp}: at this temperature a VPD up to {vmax} needs at least {need} "
                    "humidity, and the probe reads {hum}.",
    "hank.vpd.why_warm": "At {temp} a VPD up to {vmax} would need at least {need} humidity, more than the top of "
                         "{hmax}: here it is mostly too warm.",
    "hank.vpd.why_plain": "The air is too dry or too warm.",
    "hank.vpd.runs": "Already running: {items}.",
    "hank.vpd.run_port": "{label} ({port}) {state}",
    "hank.vpd.on": "on",
    "hank.vpd.off": "off",
    "hank.vpd.run_rule": "the humidifier rule switches on below {floor} (computed for {temp})",
    "hank.vpd.run_rule_card": "the humidifier rule switches on below {floor}",
    "hank.vpd.run_dfan": "exhaust fan step {step}",
    "hank.vpd.run_pilot_auto": "the autopilot regulates",
    "hank.vpd.run_pilot_observe": "the autopilot only observes",
    "hank.vpd.hum_off": "The humidifier rule is off: switch it on with two taps (card below).",
    "hank.vpd.hum_stage": "The humidifier rule switches only with a confirmed stage: my estimate is {stage}, confirm it "
                          "with two taps (card below).",
    "hank.vpd.hum_stage_none": "The humidifier rule switches only with a stage you set: in the plant card.",
    "hank.vpd.hum_port": "The humidifier rule switches {port} on only once you name the humidifier there: in the "
                         "Autopilot card under exceptions, \"On {port}: humidifier\".",
    "hank.vpd.hum_ports": "The port assignment changed: until you confirm it in the Autopilot card, the humidifier rule "
                          "does not switch on.",
    "hank.vpd.hum_implausible": "The humidifier rule stands since the plausibility stop: name again in the Autopilot "
                                "card under exceptions what hangs on {port}.",
    "hank.vpd.pilot_off": "The autopilot is off. Set it to \"Observe\" in the Autopilot card: it then shows what it "
                          "would do, and switches nothing. Automatic only after that, with two taps.",
    "hank.vpd.pilot_stage": "The autopilot, too, regulates only with a confirmed stage.",
    "hank.vpd.pilot_lamp": "The autopilot raises the light only once you confirm the lamp distance in the Autopilot "
                           "card.",
    "hank.vpd.pilot_dfan": "Autopilot, {status}.",
    "hank.vpd.light_waits": "The light waits: more light makes it warmer and the air drier for the plant. It rises only "
                            "once the VPD is inside its band.",
    "hank.vpd.no_plan": "The light runs without a light plan, so around the clock. The stage card says: {note}",
}


def check() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, a {lang} placeholder, an em dash."""
    problems = [f"only in DE: {key}" for key in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {key}" for key in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        de, en = i18n.placeholders(DE[key]), i18n.placeholders(EN[key])
        if de != en:
            problems.append(f"placeholders differ: {key} {sorted(de)} {sorted(en)}")
        if "lang" in de | en:
            problems.append(f"placeholder lang: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    problems = check()
    if problems:
        raise ValueError("vpd: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


@dataclass
class Found:
    """The explanation, sentence groups in order: why (value against band and the reason), runs (what already runs),
    blocks (the humidifier rule, then the autopilot), light (why the light waits, the light without a plan)."""

    head: str  # the value against the band
    why: str | None = None  # the reason: too dry for this temperature (with the humidity it needs) or too warm
    runs: str | None = None
    # (sentence, about the stage): the humidifier rule's blocks, then the autopilot's; a sentence about the stage is
    # marked, so the today list can leave it to its own stage line
    blocks: list[tuple[str, bool]] = field(default_factory=list)
    light: list[str] = field(default_factory=list)
    cards: list[Proposal] = field(default_factory=list)
    link: dict[str, str] | None = None

    def lines(self, *, without_stage: bool = False, without_head: bool = False) -> list[str]:
        said = [line for line, stage in self.blocks if not (without_stage and stage)]
        first = " ".join(x for x in ((None if without_head else self.head), self.why) if x)
        return [*([first] if first else []), *([self.runs] if self.runs else []), *said, *self.light]

    def text(self) -> str:
        return "\n".join(self.lines())


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _row(state: LiveState, metric: str) -> dict[str, Any] | None:
    return next((r for r in state.checks if r.get("metric") == metric), None)


def high(live: LiveState | Mapping[str, Any]) -> bool:
    """Fresh readings and the probe VPD above its band (the plant check, the same one the traffic light reads)."""
    state = collect(live)
    row = _row(state, METRIC)
    return not state.stale and row is not None and row.get("status") == "high" and _number(row.get("actual"))


def _hum(value: float, lang: str) -> str:
    return fmt_qty(round(value, 1), "%", lang, 1)


def _head(row: dict[str, Any], lang: str) -> str:
    value = reading(row["actual"], METRIC, "kPa", lang, row.get("min"), row.get("max"))
    target = verdicts.target_text(row.get("min"), row.get("max"), "kPa", None, lang)
    return t("hank.vpd.head", lang, value=value, target=target)


def _why(state: LiveState, row: dict[str, Any], lang: str) -> str | None:
    """Why it is too high: the air too dry for the temperature, with the humidity the temperature needs for the band's
    maximum (Tetens, as the box computes its air VPD), or, when even the humidity band's top would not do, too warm.
    None without a temperature or a humidity: the card's advice then says it already."""
    temp, hum = state.climate("probe_temp"), state.climate(HUMIDITY)
    vmax = row.get("max")
    if temp is None or hum is None or not _number(vmax):
        return None
    need = 100 * (1 - float(vmax) / automation.saturation_kpa(temp))
    h_row = _row(state, HUMIDITY) or {}
    hmax = h_row.get("max")
    shown_t = reading(temp, "probe_temp", "°C", lang)
    shown_v = fmt_qty(vmax, "kPa", lang)
    if _number(hmax) and need > float(hmax):
        return t("hank.vpd.why_warm", lang, temp=shown_t, vmax=shown_v, need=_hum(need, lang),
                 hmax=fmt_qty(hmax, "%", lang))
    return t("hank.vpd.why", lang, temp=shown_t, vmax=shown_v, need=_hum(need, lang), hum=_hum(hum, lang))


def _rule_floor(auto: Mapping[str, Any], lang: str) -> str | None:
    floor = auto.get("floor") if isinstance(auto.get("floor"), Mapping) else {}
    value = floor.get("floor")
    if not _number(value):
        band = auto.get("band") if isinstance(auto.get("band"), list) else []
        value = band[0] if band and _number(band[0]) else None
        if value is None:
            return None
        return t("hank.vpd.run_rule_card", lang, floor=fmt_qty(value, "%", lang))
    if floor.get("reason") == "vpd" and _number(floor.get("temp")):
        return t("hank.vpd.run_rule", lang, floor=_hum(float(value), lang),
                 temp=reading(floor["temp"], "probe_temp", "°C", lang))
    return t("hank.vpd.run_rule_card", lang, floor=fmt_qty(value, "%", lang))


def _rule_blocks(state: LiveState, lang: str) -> tuple[list[tuple[str, bool]], list[dict[str, Any]], bool]:
    """Why the humidifier rule does not switch on now, the cards that clear it, and whether it regulates."""
    auto = state.automation_view
    port = str(auto.get("port") or automation.PORT)
    shown = port_name(port)
    out: list[tuple[str, bool]] = []
    bodies: list[dict[str, Any]] = []
    if not auto:
        return out, bodies, False
    if not auto.get("enabled"):
        out.append((t("hank.vpd.hum_off", lang), False))
        bodies.append({"action": "auto_humidifier", "on": True})
    if auto.get("needs_stage_confirmation"):
        estimate = auto.get("estimate") if isinstance(auto.get("estimate"), Mapping) else None
        if estimate and estimate.get("key"):
            stage = state.stage or {"key": estimate["key"], "label": estimate.get("label")}
            out.append((t("hank.vpd.hum_stage", lang, stage=verdicts.stage_name(stage, lang)), True))
            bodies.append({"action": "plant_stage", "key": str(estimate["key"])})
        elif not auto.get("profile_problem"):
            out.append((t("hank.vpd.hum_stage_none", lang), True))
    device = auto.get("port_device")
    if auto.get("implausible"):
        out.append((t("hank.vpd.hum_implausible", lang, port=shown), False))
    elif device != "humidifier":
        out.append((t("hank.vpd.hum_port", lang, port=shown), False))
    elif auto.get("port_hold") == "changed":
        out.append((t("hank.vpd.hum_ports", lang), False))
    return out, bodies, not out


def _pilot(state: LiveState, lang: str) -> tuple[list[tuple[str, bool]], str | None, bool]:
    """What blocks the autopilot (or its exhaust status while it runs), its run item, and whether the link fits."""
    found = pilot_report.brief(state)
    if not found:
        return [], None, False
    needs = found.get("needs") if isinstance(found.get("needs"), Mapping) else {}
    if found.get("forced_off"):
        return [(t("hank.pilot.forced_off", lang), False)], None, True
    if found.get("locked_out"):
        return [(t("hank.pilot.locked", lang), False)], None, True
    mode = found.get("mode")
    if mode not in ("auto", "observe"):
        return [(t("hank.vpd.pilot_off", lang), False)], None, True
    out: list[tuple[str, bool]] = []
    if needs.get("stage"):
        out.append((t("hank.vpd.pilot_stage", lang), True))
    else:
        info = (found.get("devices") or {}).get("dfan")
        if isinstance(info, Mapping) and pilot_report._lang_text(info.get("status"), lang):
            out.append((t("hank.vpd.pilot_dfan", lang, status=pilot_report.device_status(info, lang)), False))
    if needs.get("lamp"):
        out.append((t("hank.vpd.pilot_lamp", lang), False))
    run = t("hank.vpd.run_pilot_auto" if mode == "auto" else "hank.vpd.run_pilot_observe", lang)
    return out, run, True


def _runs(state: LiveState, rule_ok: bool, pilot_run: str | None, lang: str) -> str | None:
    auto = state.automation_view
    items: list[str] = []
    port = str(auto.get("port") or automation.PORT)
    on = proposals.port_on(state, port)
    if on is not None:
        items.append(t("hank.vpd.run_port", lang, label=state.port_label(port, lang), port=port_name(port),
                       state=t("hank.vpd.on" if on else "hank.vpd.off", lang)))
    if rule_ok:
        floor = _rule_floor(auto, lang)
        if floor:
            items.append(floor)
    step = proposals.fan_step(state, "dfan")
    if step is not None:
        items.append(t("hank.vpd.run_dfan", lang, step=step))
    if pilot_run:
        items.append(pilot_run)
    return t("hank.vpd.runs", lang, items=", ".join(items)) if items else None


def _light_cycle_note(lang: str) -> str | None:
    """The stage card's own note on the light cycle (no new numbers), without its source marks."""
    try:
        run = json.loads(plant.PLANT_FILE.read_text(encoding="utf-8"))
        name = run.get("stages_card") if isinstance(run, dict) else None
        card = json.loads((plant.LIBRARY / "stages" / f"{name}.json").read_text(encoding="utf-8")) if name else None
    except (OSError, ValueError):
        return None
    if not isinstance(card, dict):
        return None
    notes, notes_en = card.get("notes") or [], card.get("notes_en") or []
    for i, note in enumerate(notes):
        if isinstance(note, str) and note.strip().lower().startswith(_LIGHT_CYCLE):
            shown = note if lang == "de" else (notes_en[i] if i < len(notes_en) else None)
            if isinstance(shown, str) and shown.strip():
                return _SOURCE_MARK.sub("", shown).strip()
    return None


def _no_plan(state: LiveState) -> bool:
    """The light runs at a fixed level without a plan: on, manual mode, not in a plan (the box reports the flag)."""
    light = state.snapshot.get("light") if isinstance(state.snapshot.get("light"), dict) else {}
    level = light.get("level")
    if not _number(level) or level <= 0 or light.get("mode") not in (0, None):
        return False
    if light.get("in_plan_known") is False:
        return False
    return light.get("in_plan") is False and not state.snapshot.get("plan")


def _light(state: LiveState, lang: str) -> list[str]:
    out: list[str] = []
    preset = ((state.plant_view.get("stage") or {}).get("preset") or {}).get("light_percent")
    level = proposals.light_level(state)
    if _number(preset) and level is not None and 0 < level < preset:
        out.append(t("hank.vpd.light_waits", lang))
    if _no_plan(state):
        note = _light_cycle_note(lang)
        if note:
            out.append(t("hank.vpd.no_plan", lang, note=note))
    return out


def explain(live: LiveState | Mapping[str, Any], lang: str, *, now: float | None = None) -> Found | None:
    """The explanation while the probe VPD is above its band on fresh readings, else None."""
    state = collect(live)
    lang = lang if lang in ("de", "en") else "de"
    if not high(state):
        return None
    row = _row(state, METRIC) or {}
    hum_blocks, bodies, rule_ok = _rule_blocks(state, lang)
    pilot_blocks, pilot_run, pilot_link = _pilot(state, lang)
    cards: list[Proposal] = []
    for body in bodies:
        try:
            cards.append(proposals.make(body, state, lang, reason="stage" if body["action"] == "plant_stage"
                                        else f"{METRIC}:high", now=now))
        except proposals.Refused:
            continue
    why = _why(state, row, lang)
    return Found(head=_head(row, lang), why=why or t("hank.vpd.why_plain", lang), runs=_runs(state, rule_ok, pilot_run, lang),
                 blocks=hum_blocks + pilot_blocks, light=_light(state, lang), cards=cards,
                 link=pilot_report.link(lang) if pilot_link else None)


__all__ = ["DE", "EN", "Found", "check", "explain", "high", "register"]
