"""Hank's window onto the autopilot: "Hank regelt" (owner, 23.10.2026: "Hank tells me the humidity dropped a few
percent. Instead of telling me, he should regulate it on autopilot. I cannot set Hank to auto.").

The switch "Hank regelt: Aus / Beobachten / Automatik" in Hank's panel IS the autopilot's mode: the page sends the
same /api/cmd action (autopilot_mode) and reads the same state (/api/state "autopilot"). This module adds no mode, no
store and no rule. It only reads the autopilot (vgrow/autopilot.py) and the humidifier rule (vgrow/automation.py) and
words, for the page, in the request language:

- blockers: what keeps Hank from regulating, each with the step that settles it where it already lives (the stage
  confirmation, what hangs on USB 2, the lamp distance, the humidifier rule's switch, a changed port assignment),
  and what it holds (everything, the humidifier, the light increase);
- regulates: what Hank regulates right now, and his last action with its time;
- about: for every problem key of Hank's warnings that a device can act on ("probe_humidity:low"), one sentence: Hank
  regulates it, or in observe what he would do, or which step blocks him (with that step's id for the one-tap link).

Which device acts on which problem (KEY_DEVICES) mirrors the votes of the autopilot's exhaust (_decide_dfan: too
warm, too cold, too humid, VPD too low), its light ramp (light below the preset) and the humidifier rule (too dry;
VPD too high only where the rule has its VPD floor, automation.humidity_floor). A change of those votes changes this
table too; tests/test_hank_regelt.py checks the reasons against the autopilot's own.

Hank (vgrow/hank/) imports nothing of this module, and this module nothing of Hank. Nothing here switches or writes.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from . import automation as automation_mod
from . import i18n
from .autopilot import DEVICES, LAMP_MIN_CM, LAMP_REGROW_CM, render
from .i18n import fmt_qty, fmt_time, t

EM_DASH = chr(0x2014)
HUMIDIFIER = "humidifier"
PORT_NAME = {"usb1": "USB 1", "usb2": "USB 2"}

# Which device acts on which problem key of Hank's warnings, in the order they are named.
BASE_KEY_DEVICES: dict[str, tuple[str, ...]] = {
    "probe_humidity:low": (HUMIDIFIER,),
    "probe_humidity:high": ("dfan",),
    "probe_temp:high": ("dfan",),
    "probe_temp:low": ("dfan",),
    "probe_vpd:low": ("dfan",),
    "light_percent:low": ("light",),
}
VPD_HIGH = "probe_vpd:high"

# What a blocker holds: everything, the autopilot's devices, the humidifier, the light increase.
HOLDS = {
    "profile": ("light", "dfan", "cfan", HUMIDIFIER),
    "ports": ("light", "dfan", "cfan", HUMIDIFIER),
    "stage": ("light", "dfan", "cfan", HUMIDIFIER),
    "forced": ("light", "dfan", "cfan"),
    "locked": ("light", "dfan", "cfan"),
    "hold": ("light", "dfan", "cfan"),
    "usb2": (HUMIDIFIER,),
    "usb2_other": (HUMIDIFIER,),
    "implausible": (HUMIDIFIER,),
    "hum_off": (HUMIDIFIER,),
    "lamp": ("light",),
}
HOLDS_TEXT = {"profile": "hp.holds.all", "ports": "hp.holds.all", "stage": "hp.holds.all", "forced": "hp.holds.pilot",
              "locked": "hp.holds.pilot", "hold": "hp.holds.pilot", "usb2": "hp.holds.humidifier",
              "usb2_other": "hp.holds.humidifier", "implausible": "hp.holds.humidifier",
              "hum_off": "hp.holds.humidifier", "lamp": "hp.holds.light"}
# The step on the page for each blocker (web/app.js pilotStep); None: nothing to tap, the line only says it.
STEP = {"stage": "stage", "usb2": "usb2", "usb2_other": "usb2", "implausible": "usb2", "lamp": "lamp", "hum_off": "hum",
        "ports": "ports", "profile": None, "forced": None, "locked": None, "hold": None}
# Holds of the autopilot that the list already names (or that are no hold): never a second "hold" line.
NAMED_HOLDS = ("ap.hold.off", "ap.hold.forced_off", "ap.hold.locked", "ap.hold.ports", "ap.hold.profile",
               "ap.hold.stage", "ap.hold.next_poll")
LAST_KINDS = ("step", "would", "undo", "failed")

DE: dict[str, str] = {
    "hp.dev.humidifier": "Befeuchter",
    "hp.and": "und",
    "hp.block.stage": "Phase nicht bestätigt (geschätzt: {stage})",
    "hp.block.stage_none": "Keine Phase gesetzt",
    "hp.block.usb2": "Nicht gesagt, was an {port} hängt",
    "hp.block.usb2_other": "An {port} hängt laut deiner Angabe kein Befeuchter",
    "hp.block.implausible": "Nach dem Einschalten stieg die Feuchte nicht (Befeuchter an {port} und sein Wasser prüfen)",
    "hp.block.lamp": "Lampenabstand nicht bestätigt (mindestens {cm})",
    "hp.block.hum_off": "Die Befeuchter-Regel ist aus",
    "hp.block.ports": "Die Belegung der Anschlüsse hat sich geändert",
    "hp.block.profile": "plant.json ist nicht lesbar",
    "hp.block.forced": "Über VGROW_AUTOPILOT=0 gesperrt",
    "hp.block.locked": "Ein anderer Server regelt diese Box",
    "hp.block.hold": "{status}",
    "hp.holds.all": "hält alles an",
    "hp.holds.pilot": "hält Licht und Lüfter an",
    "hp.holds.humidifier": "hält den Befeuchter an",
    "hp.holds.light": "hält den Lichtanstieg an",
    "hp.step.stage": "Phase bestätigen",
    "hp.step.stage_none": "Phase setzen",
    "hp.step.usb2": "{port} benennen",
    "hp.step.usb2_other": "{port} neu benennen",
    "hp.step.lamp": "Abstand bestätigen",
    "hp.step.hum": "Regel einschalten",
    "hp.step.ports": "Belegung bestätigen",
    "hp.step.mode_auto": "Auf Automatik stellen",
    "hp.step.mode_on": "Hank regelt einschalten",
    "hp.now.regulates": "Hank regelt jetzt: {devices}.",
    "hp.now.observes": "Hank beobachtet {devices} und schaltet sie nicht.",
    "hp.now.waits": "Hank wartet bei: {devices}.",
    "hp.now.nothing": "Hank regelt gerade nichts.",
    "hp.when.today": "{time}",
    "hp.when.yesterday": "gestern {time}",
    "hp.when.date": "{date} {time}",
    "hp.last.hum_on": "Befeuchter an bei {value}",
    "hp.last.hum_off": "Befeuchter aus bei {value}",
    "hp.last.hum_on_plain": "Befeuchter an",
    "hp.last.hum_off_plain": "Befeuchter aus",
    "hp.about.regulating": "Hank regelt das: {devices}.",
    "hp.about.would": "Hank beobachtet nur: auf Automatik würde er das über {devices} regeln.",
    "hp.about.waiting": "Hank wartet hier: {why}.",
    "hp.about.blocked": "Hank regelt das noch nicht: {why}.",
    "hp.about.paused": "Hank regelt das gerade nicht: du hast {device} bis {time} übernommen.",
    "hp.about.manual": "Hank regelt das nicht: {device} steht in den Ausnahmen auf Manuell.",
    "hp.about.app": "Hank regelt das nicht: {device} läuft über die VIVOSUN-App oder meldet seinen Modus nicht.",
    "hp.about.off": "Hank regelt das nicht: „Hank regelt“ steht auf Aus.",
}

EN: dict[str, str] = {
    "hp.dev.humidifier": "Humidifier",
    "hp.and": "and",
    "hp.block.stage": "Stage not confirmed (estimate: {stage})",
    "hp.block.stage_none": "No stage set",
    "hp.block.usb2": "Not said what hangs on {port}",
    "hp.block.usb2_other": "You said no humidifier hangs on {port}",
    "hp.block.implausible": "The humidity did not rise after switching on (check the humidifier on {port} and its water)",
    "hp.block.lamp": "Lamp distance not confirmed (at least {cm})",
    "hp.block.hum_off": "The humidifier rule is off",
    "hp.block.ports": "The port assignment has changed",
    "hp.block.profile": "plant.json cannot be read",
    "hp.block.forced": "Locked by VGROW_AUTOPILOT=0",
    "hp.block.locked": "Another server regulates this box",
    "hp.block.hold": "{status}",
    "hp.holds.all": "holds everything",
    "hp.holds.pilot": "holds light and fans",
    "hp.holds.humidifier": "holds the humidifier",
    "hp.holds.light": "holds the light increase",
    "hp.step.stage": "Confirm the stage",
    "hp.step.stage_none": "Set the stage",
    "hp.step.usb2": "Name {port}",
    "hp.step.usb2_other": "Name {port} again",
    "hp.step.lamp": "Confirm the distance",
    "hp.step.hum": "Switch the rule on",
    "hp.step.ports": "Confirm the ports",
    "hp.step.mode_auto": "Set to Auto",
    "hp.step.mode_on": "Switch Hank controls on",
    "hp.now.regulates": "Hank regulates now: {devices}.",
    "hp.now.observes": "Hank observes {devices} and switches nothing there.",
    "hp.now.waits": "Hank is waiting on: {devices}.",
    "hp.now.nothing": "Hank regulates nothing right now.",
    "hp.when.today": "{time}",
    "hp.when.yesterday": "yesterday {time}",
    "hp.when.date": "{date} {time}",
    "hp.last.hum_on": "Humidifier on at {value}",
    "hp.last.hum_off": "Humidifier off at {value}",
    "hp.last.hum_on_plain": "Humidifier on",
    "hp.last.hum_off_plain": "Humidifier off",
    "hp.about.regulating": "Hank regulates this: {devices}.",
    "hp.about.would": "Hank only observes: on Auto he would regulate this with {devices}.",
    "hp.about.waiting": "Hank is waiting here: {why}.",
    "hp.about.blocked": "Hank does not regulate this yet: {why}.",
    "hp.about.paused": "Hank does not regulate this right now: you took over {device} until {time}.",
    "hp.about.manual": "Hank does not regulate this: {device} is set to Manual in the exceptions.",
    "hp.about.app": "Hank does not regulate this: {device} runs on the VIVOSUN app or does not report its mode.",
    "hp.about.off": "Hank does not regulate this: “Hank controls” is set to Off.",
}


def check() -> list[str]:
    problems = [f"only in DE: {key}" for key in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {key}" for key in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        if i18n.placeholders(DE[key]) != i18n.placeholders(EN[key]):
            problems.append(f"placeholders differ: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    problems = check()
    if problems:
        raise ValueError("hankpilot: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


register()


def key_devices() -> dict[str, tuple[str, ...]]:
    """BASE_KEY_DEVICES, plus "VPD too high: the humidifier" where the humidifier rule has its VPD floor."""
    out = dict(BASE_KEY_DEVICES)
    if callable(getattr(automation_mod, "humidity_floor", None)):
        out[VPD_HIGH] = (HUMIDIFIER,)
    return out


def _join(names: list[str], lang: str) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} {t('hp.and', lang)} {names[-1]}"


def _port(port: str | None) -> str:
    return PORT_NAME.get(str(port or "usb2"), str(port or "usb2").upper())


def _when(at: float, lang: str, now: float) -> str:
    day, today = datetime.fromtimestamp(at).date(), datetime.fromtimestamp(now).date()
    clock = fmt_time(at, lang)
    if day == today:
        return t("hp.when.today", lang, time=clock)
    if (today - day).days == 1:
        return t("hp.when.yesterday", lang, time=clock)
    date = f"{day:%d.%m.}" if lang == "de" else f"{day.month}/{day.day}"
    return t("hp.when.date", lang, date=date, time=clock)


def _lamp_needed(pilot: Any, stage: Mapping[str, Any] | None, light_level: Any) -> bool:
    """The autopilot's own lamp rule (_decide_light), without the mode: the light is below the stage preset, the
    autopilot may touch it, and the distance was never confirmed, the owner changed the light since, or the plant grew
    more than LAMP_REGROW_CM since the confirmation."""
    timer = getattr(pilot, "light_schedule", None)
    if timer is not None and timer.cfg.get("enabled"):
        return False  # the light timer owns the brightness: the ramp this rule belongs to does not run
    preset = ((stage or {}).get("preset") or {}).get("light_percent")
    if not preset or pilot.state["devices"]["light"]["level"] == "manual":
        return False
    if isinstance(light_level, int | float) and not isinstance(light_level, bool) and light_level >= preset:
        return False
    lamp = pilot.state.get("lamp")
    if not lamp or pilot.state["light"].get("series_stopped"):
        return True
    heights = pilot.plant_log.data.get("heights") or []
    cm = heights[-1].get("cm") if heights and isinstance(heights[-1], dict) else None
    return cm is not None and lamp.get("height_cm") is not None and cm - lamp["height_cm"] > LAMP_REGROW_CM


def blockers(pilot: Any, view: Mapping[str, Any], auto: Mapping[str, Any] | None,
             lang: str) -> list[dict[str, Any]]:
    """What keeps Hank from regulating, most far-reaching first. Each: {id, text, holds (text), devices (held),
    step (the page's step id or None), label (the step's button text or None)}."""
    out: list[dict[str, Any]] = []

    def add(bid: str, text: str, step_key: str | None = None, **params: Any) -> None:
        step = STEP[bid]
        if bid == "stage" and step_key == "hp.step.stage_none":
            step = "stage_none"
        out.append({"id": bid, "text": text, "holds": t(HOLDS_TEXT[bid], lang), "devices": list(HOLDS[bid]),
                    "step": step, "label": t(step_key, lang, **params) if step_key else None})

    kind, stage = pilot.stage()
    needs = view.get("needs") or {}
    if view.get("forced_off"):
        add("forced", t("hp.block.forced", lang))
    if view.get("locked_out"):
        add("locked", t("hp.block.locked", lang))
    if kind == "profile" or (auto or {}).get("profile_problem"):
        add("profile", t("hp.block.profile", lang))
    if needs.get("ports"):
        add("ports", t("hp.block.ports", lang), "hp.step.ports")
    stage_open = kind == "stage" or bool((auto or {}).get("needs_stage_confirmation") and kind != "profile")
    if stage_open:
        estimate = needs.get("stage") or (auto or {}).get("estimate")
        if estimate:
            add("stage", t("hp.block.stage", lang, stage=str(estimate.get("label") or estimate.get("key"))),
                "hp.step.stage")
        else:
            add("stage", t("hp.block.stage_none", lang), "hp.step.stage_none")
    if view.get("mode") != "off" and not view.get("forced_off") and not view.get("locked_out"):
        status = getattr(pilot, "status", None)
        key = status.get("key") if isinstance(status, Mapping) else None
        if isinstance(key, str) and key.startswith("ap.hold.") and key not in NAMED_HOLDS:
            said = render(status, lang)  # the hold as the card says it ("wartet: ..."), one colon only
            add("hold", t("hp.block.hold", lang, status=said[:1].upper() + said[1:]))
    if auto is not None:
        port = _port(auto.get("port"))
        device = auto.get("port_device")
        if device is None:
            add("usb2", t("hp.block.usb2", lang, port=port), "hp.step.usb2", port=port)
        elif device != HUMIDIFIER:
            add("usb2_other", t("hp.block.usb2_other", lang, port=port), "hp.step.usb2_other", port=port)
        elif auto.get("implausible"):
            add("implausible", t("hp.block.implausible", lang, port=port), "hp.step.usb2_other", port=port)
        if not auto.get("enabled"):
            add("hum_off", t("hp.block.hum_off", lang), "hp.step.hum")
    light = next((d for d in view.get("devices") or [] if d.get("id") == "light"), {})
    if _lamp_needed(pilot, stage, light.get("value")):
        add("lamp", t("hp.block.lamp", lang, cm=fmt_qty(LAMP_MIN_CM, "cm", lang)), "hp.step.lamp")
    return out


def _device_name(device: str, view: Mapping[str, Any], lang: str) -> str:
    if device == HUMIDIFIER:
        return t("hp.dev.humidifier", lang)
    found = next((d for d in view.get("devices") or [] if d.get("id") == device), None)
    return str((found or {}).get("name") or t(f"ap.dev.{device}", lang))


def device_state(device: str, view: Mapping[str, Any], auto: Mapping[str, Any] | None,
                 blocks: list[dict[str, Any]]) -> tuple[str, Any]:
    """What one device does for Hank now: ("regulating" | "would" | "waiting" | "blocked" | "paused" | "manual" | "app" | "off"
    | "none", detail). detail: the blocker for "blocked", the pause end for "paused", the status line otherwise."""
    held = next((b for b in blocks if device in b["devices"]), None)
    if device == HUMIDIFIER:
        if auto is None:
            return "none", None
        if held is not None:
            return "blocked", held
        paused = auto.get("paused_until")
        if paused:
            return "paused", paused
        info = view.get("humidifier") or {}
        if info.get("control_state") == "waiting":
            return "waiting", info.get("status") or ""
        return "regulating", None  # its own rule: it switches in every mode of the autopilot
    info = next((d for d in view.get("devices") or [] if d.get("id") == device), None)
    if info is None:
        return "none", None
    if held is not None and held["id"] in ("forced", "locked"):
        return "blocked", held
    if view.get("mode") == "off":
        return "off", None
    if held is not None:
        return "blocked", held
    if info.get("level") == "manual":
        return "manual", None
    app = info.get("app_mode")
    if app in ("unknown", "wind") or (app in ("plan", "auto") and not info.get("app")):
        return "app", None
    if info.get("paused_until"):
        return "paused", info.get("paused_until")
    if view.get("mode") == "observe" or info.get("level") == "suggest":
        return "would", info.get("status") or ""
    if info.get("control_state") in ("waiting", "pending"):
        return "waiting", info.get("status") or ""
    return "regulating", info.get("status") or ""


RANK = ("regulating", "would", "waiting", "blocked", "paused", "manual", "app", "off", "none")


def about(key: str, view: Mapping[str, Any], auto: Mapping[str, Any] | None, blocks: list[dict[str, Any]],
          lang: str) -> dict[str, Any] | None:
    """One sentence on what Hank does about one problem key, with the step that unblocks him: {text, step, label}."""
    devices = key_devices().get(key)
    if not devices:
        return None
    states = [(d, *device_state(d, view, auto, blocks)) for d in devices]
    states = [s for s in states if s[1] != "none"]
    if not states:
        return None
    best = min(RANK.index(s[1]) for s in states)
    chosen = [s for s in states if RANK.index(s[1]) == best]
    kind = chosen[0][1]

    def names(with_status: bool) -> str:
        parts = []
        for device, _, detail in chosen:
            name = _device_name(device, view, lang)
            parts.append(f"{name} ({detail})" if with_status and isinstance(detail, str) and detail else name)
        return _join(parts, lang)

    step = label = None
    if kind == "regulating":
        text = t("hp.about.regulating", lang, devices=names(True))
    elif kind == "would":
        text = t("hp.about.would", lang, devices=names(False))
        step, label = "mode", t("hp.step.mode_auto", lang)
    elif kind == "waiting":
        text = t("hp.about.waiting", lang, why=str(chosen[0][2]).rstrip("."))
    elif kind == "blocked":
        block = chosen[0][2]
        text = t("hp.about.blocked", lang, why=block["text"])
        step, label = block["step"], block["label"]
    elif kind == "paused":
        text = t("hp.about.paused", lang, device=names(False), time=fmt_time(float(chosen[0][2]), lang))
    elif kind == "manual":
        text = t("hp.about.manual", lang, device=names(False))
    elif kind == "app":
        text = t("hp.about.app", lang, device=names(False))
    else:
        text = t("hp.about.off", lang)
        step, label = "mode", t("hp.step.mode_on", lang)
    return {"text": text, "step": step, "label": label}


def _last(pilot: Any, auto: Mapping[str, Any] | None, lang: str, now: float) -> dict[str, Any] | None:
    """Hank's newest action: a step of the autopilot (or a would-be step while observing) or a switch of the
    humidifier rule, with its time."""
    best: tuple[float, str] | None = None
    for entry in reversed(pilot.entries()):
        if entry.get("kind") in LAST_KINDS:
            best = (float(entry["at"]), render(entry.get("text"), lang))
            break
    for entry in (auto or {}).get("recent") or []:
        action, at = entry.get("action"), entry.get("time")
        if action not in ("humidifier_on", "humidifier_off") or not isinstance(at, int | float):
            continue
        if best is None or at > best[0]:
            short = "on" if action == "humidifier_on" else "off"
            value = entry.get("value")
            words = (t(f"hp.last.hum_{short}", lang, value=fmt_qty(value, "%", lang, digits=1))
                     if isinstance(value, int | float) and not isinstance(value, bool)
                     else t(f"hp.last.hum_{short}_plain", lang))
            best = (float(at), words)
        break  # recent is newest first
    if best is None or not best[1]:
        return None
    return {"at": best[0], "when": _when(best[0], lang, now), "text": best[1]}


def panel(pilot: Any, view: Mapping[str, Any], auto: Mapping[str, Any] | None, lang: str | None = None,
          now: float | None = None) -> dict[str, Any]:
    """Everything Hank's panel shows for one box, in one language: the autopilot's mode (the very same field the card
    shows), the blockers, what Hank regulates now, his last action, and the sentence for each problem key."""
    lang = i18n.normalize(lang) or i18n.current_lang()
    now = time.time() if now is None else now
    blocks = blockers(pilot, view, auto, lang)
    regulating, observing, waiting = [], [], []
    for device in (*DEVICES, HUMIDIFIER):
        kind, detail = device_state(device, view, auto, blocks)
        if kind == "regulating":
            regulating.append(_device_name(device, view, lang))
        elif kind == "would":
            observing.append(_device_name(device, view, lang))
        elif kind == "waiting":
            waiting.append(_device_name(device, view, lang))
    lines = []
    if regulating:
        lines.append(t("hp.now.regulates", lang, devices=_join(regulating, lang)))
    if observing:
        lines.append(t("hp.now.observes", lang, devices=_join(observing, lang)))
    if waiting:
        lines.append(t("hp.now.waits", lang, devices=_join(waiting, lang)))
    if not lines:
        lines.append(t("hp.now.nothing", lang))
    return {
        "mode": view.get("mode"), "forced_off": bool(view.get("forced_off")),
        "locked_out": bool(view.get("locked_out")), "blockers": blocks, "regulates": " ".join(lines),
        "last": _last(pilot, auto, lang, now),
        "about": {key: found for key in key_devices() if (found := about(key, view, auto, blocks, lang))},
    }


__all__ = ["BASE_KEY_DEVICES", "DE", "EN", "about", "blockers", "check", "device_state", "key_devices", "panel",
           "register"]
