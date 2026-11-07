"""What Hank says about the autopilot (owner, 23.10.2026: "Wo ist der Button, damit Hank automatisch alles behebt?").

Hank never switches and never imports the regulator (vgrow/autopilot.py lives next to the humidifier rule, outside
this package; tests/test_autopilot.py checks both). The server adds the regulator's brief to the snapshot Hank's
collector reads (server.HankBox): live.snapshot["autopilot"] = {"boxes": [{mode, status, devices, recent,
light_step_at}]}, every text already in both languages. This module only reads that plain data:

- brief(live): the first box's brief, or None (no autopilot, an older server, a broken brief);
- regulated(live): the devices the autopilot itself regulates right now (mode "auto", the device on "auto", not run by
  a VIVOSUN plan or automation, not paused because the owner took over). For those the fix answer shows no card; it
  says "Das regelt der Autopilot" with the device's own status line instead (review 2, M4);
- answer(live, lang): the route "autopilot" ("Was hat der Autopilot gemacht?"): the mode with its status line and the
  last three steps from the step log, code only, never the model. Off or observing: where to switch it on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vgrow import i18n
from vgrow.i18n import t

from .livestate import LiveState, collect
from .types import Answer

EM_DASH = chr(0x2014)
DEVICES = ("light", "dfan", "cfan")
LINK = "#autopilot"

DE: dict[str, str] = {
    "hank.pilot.auto": "Der Autopilot steht auf Automatik und {status}.",
    "hank.pilot.observe": "Der Autopilot beobachtet nur: er rechnet und zeigt, was er tun würde, und schaltet nichts "
                          "({status}).",
    "hank.pilot.off": "Der Autopilot ist aus.",
    "hank.pilot.forced_off": "Der Autopilot ist über VGROW_AUTOPILOT=0 gesperrt.",
    "hank.pilot.locked": "Ein anderer Server regelt diese Box, dieser hier schaltet nichts.",
    "hank.pilot.recent": "Zuletzt:",
    "hank.pilot.none": "Im Protokoll steht noch kein Schritt.",
    "hank.pilot.switch_on": "Einschalten mit zwei Tipps in der Karte Autopilot. Er schaltet dann Licht, Abluft und "
                            "Umluft in festen Grenzen, und dein Handgriff geht immer vor.",
    "hank.pilot.device": "{device}: {status}.",
    "hank.pilot.link": "Zur Karte Autopilot",
    "hank.pilot.none_brief": "Zum Autopiloten habe ich gerade keine Daten.",
    "hank.fix.pilot": "Das regelt der Autopilot ({status}).",
    "hank.fix.close_pilot": "Im Chat schalte ich nichts selbst; was unter „Hank regelt“ läuft, regelt "
                            "der Autopilot in seinen Grenzen.",
    "hank.fix.pilot_off": "Soll das von selbst laufen: stell den Autopiloten in der Karte Autopilot zuerst auf "
                          "„Beobachten“, dann zeigt er, was er tun würde. Automatik erst danach.",
    "hank.fix.light_wait_pilot": "Den nächsten Lichtschritt biete ich erst 24 h nach dem letzten an: der Autopilot "
                                 "hat das Licht vor weniger als 24 h erhöht.",
}

EN: dict[str, str] = {
    "hank.pilot.auto": "The autopilot is on automatic and {status}.",
    "hank.pilot.observe": "The autopilot is only observing: it computes and shows what it would do, and switches "
                          "nothing ({status}).",
    "hank.pilot.off": "The autopilot is off.",
    "hank.pilot.forced_off": "The autopilot is locked by VGROW_AUTOPILOT=0.",
    "hank.pilot.locked": "Another server regulates this box; this one switches nothing.",
    "hank.pilot.recent": "Last steps:",
    "hank.pilot.none": "The log holds no step yet.",
    "hank.pilot.switch_on": "Switch it on with two taps in the Autopilot card. It then switches light, exhaust fan and "
                            "circulation fan within fixed limits, and your own change always wins.",
    "hank.pilot.device": "{device}: {status}.",
    "hank.pilot.link": "To the Autopilot card",
    "hank.pilot.none_brief": "I have no autopilot data right now.",
    "hank.fix.pilot": "The autopilot regulates this ({status}).",
    "hank.fix.close_pilot": "In the chat I switch nothing myself; what runs under \"Hank controls\" is regulated by "
                            "the autopilot within its limits.",
    "hank.fix.pilot_off": "If this should run by itself: set the autopilot to \"Observe\" in the Autopilot card "
                          "first, it then shows what it would do. Automatic only after that.",
    "hank.fix.light_wait_pilot": "I offer the next light step only 24 h after the last one: the autopilot raised the "
                                 "light less than 24 h ago.",
}


def check() -> list[str]:
    problems = [f"only in DE: {key}" for key in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {key}" for key in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        de, en = i18n.placeholders(DE[key]), i18n.placeholders(EN[key])
        if de != en:
            problems.append(f"placeholders differ: {key} {sorted(de)} {sorted(en)}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    problems = check()
    if problems:
        raise ValueError("pilot_report: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def _lang_text(value: Any, lang: str) -> str:
    if isinstance(value, Mapping):
        return str(value.get(lang) or value.get("de") or "")
    return str(value or "")


def brief(live: LiveState | Mapping[str, Any]) -> dict[str, Any] | None:
    state = collect(live)
    data = state.snapshot.get("autopilot")
    boxes = data.get("boxes") if isinstance(data, Mapping) else None
    first = boxes[0] if isinstance(boxes, list) and boxes and isinstance(boxes[0], Mapping) else None
    return dict(first) if first is not None else None


def regulated(live: LiveState | Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """{device: its brief} for every device the autopilot itself regulates right now."""
    found = brief(live)
    if not found or found.get("mode") != "auto" or found.get("locked_out") or found.get("forced_off"):
        return {}
    out = {}
    for device, info in (found.get("devices") or {}).items():
        if (device in DEVICES and isinstance(info, Mapping) and info.get("level") == "auto"
                and not info.get("app_mode") and not info.get("paused_until")):
            out[device] = dict(info)
    return out


def device_status(info: Mapping[str, Any], lang: str) -> str:
    name = _lang_text(info.get("name"), lang)
    status = _lang_text(info.get("status"), lang)
    return t("hank.pilot.device", lang, device=name, status=status).rstrip(".") if status else name


def mode(live: LiveState | Mapping[str, Any]) -> str | None:
    found = brief(live)
    return str(found.get("mode")) if found else None


def light_step_at(live: LiveState | Mapping[str, Any]) -> float | None:
    """When the autopilot itself last raised the light (any box), for the 24 h rule of the light cards."""
    state = collect(live)
    data = state.snapshot.get("autopilot")
    boxes = data.get("boxes") if isinstance(data, Mapping) else None
    times = [b.get("light_step_at") for b in boxes or [] if isinstance(b, Mapping)]
    times = [float(x) for x in times if isinstance(x, int | float) and not isinstance(x, bool)]
    return max(times) if times else None


def lines(live: LiveState | Mapping[str, Any], lang: str) -> list[str]:
    found = brief(live)
    if not found:
        return [t("hank.pilot.none_brief", lang)]
    status = _lang_text(found.get("status"), lang)
    out: list[str] = []
    if found.get("forced_off"):
        out.append(t("hank.pilot.forced_off", lang))
    elif found.get("locked_out"):
        out.append(t("hank.pilot.locked", lang))
    elif found.get("mode") == "auto":
        out.append(t("hank.pilot.auto", lang, status=status))
    elif found.get("mode") == "observe":
        out.append(t("hank.pilot.observe", lang, status=status))
    else:
        out.append(t("hank.pilot.off", lang))
    if found.get("mode") in ("auto", "observe"):
        for device in DEVICES:
            info = (found.get("devices") or {}).get(device)
            if isinstance(info, Mapping) and _lang_text(info.get("status"), lang):
                out.append(device_status(info, lang) + ".")
    recent = [r for r in found.get("recent") or [] if isinstance(r, Mapping) and _lang_text(r.get("text"), lang)]
    if recent:
        out.append(t("hank.pilot.recent", lang))
        out += [_lang_text(r.get("text"), lang) for r in recent[:3]]
    else:
        out.append(t("hank.pilot.none", lang))
    if found.get("mode") != "auto" and not found.get("forced_off"):
        out.append(t("hank.pilot.switch_on", lang))
    return out


def link(lang: str) -> dict[str, str]:
    return {"href": LINK, "label": t("hank.pilot.link", lang)}


def answer(live: LiveState | Mapping[str, Any], lang: str, *, answer_id: str) -> Answer:
    lang = lang if lang in ("de", "en") else "de"
    return Answer(id=answer_id, route="autopilot", lang=lang, text="\n".join(lines(live, lang)), sources=[],
                  proposals=[], clarify=None, chips=[], draft_only=False, note=None, timing={}, gap=False,
                  link=link(lang))


__all__ = ["DE", "EN", "answer", "brief", "check", "light_step_at", "lines", "link", "mode", "register",
           "regulated"]
