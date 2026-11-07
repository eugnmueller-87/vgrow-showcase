"""Pump history questions use reported device history, never retrieval or a model-generated recollection."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from vgrow import i18n

from . import thirst_report
from .livestate import LiveState, collect, port_name
from .text import fold
from .types import Answer

DE = {
    "hank.pump.yes": "Ja, die Pumpe war heute laut Box eingeschaltet.",
    "hank.pump.running": "Die Box meldet die Pumpe gerade eingeschaltet.",
    "hank.pump.last": "Zuletzt an gemeldet: {time} ({port}).",
    "hank.pump.off_at": "Danach aus gemeldet: {time}.",
    "hank.pump.off_now": "Aktuell meldet die Box die Pumpe aus.",
    "hank.pump.unseen": "Heute ist bisher kein Pumpenlauf erfasst.",
    "hank.pump.unavailable": "Für heute habe ich keinen lesbaren Pumpenverlauf.",
    "hank.pump.unassigned": "Ein USB-Anschluss wurde heute eingeschaltet gemeldet; die Zuordnung zur Pumpe war "
                            "damals noch nicht bestätigt.",
    "hank.pump.gaps": "Die Aufzeichnung ist nicht lückenlos; ein kurzer Lauf kann fehlen.",
    "hank.pump.amount": "Eine geförderte Wassermenge ist damit nicht gemessen.",
    "hank.pump.context": "Erfasster Pumpenverlauf ({date}): {summary}",
    "hank.pump.schedule_off": "Der automatische Gießplan ist aus.",
    "hank.pump.next": "Nächster geplanter Lauf: {time} (Europe/Berlin), für {seconds} Sekunden. Vor dem Start prüft der Controller Wasser und Verbindung.",
    "hank.pump.countdown": "Der Stoppbefehl für den zeitgesteuerten Lauf ist in {seconds} Sekunden geplant.",
    "hank.pump.stopping": "Der Controller stoppt die Pumpe; Aus ist noch nicht bestätigt.",
    "hank.pump.unmanaged": "USB 1 ist an, aber es läuft kein Timer dieses Controllers. Der bestehende Lauf wird nicht automatisch übernommen.",
}
EN = {
    "hank.pump.yes": "Yes. The box reported the pump on today.",
    "hank.pump.running": "The box currently reports the pump on.",
    "hank.pump.last": "Last reported on: {time} ({port}).",
    "hank.pump.off_at": "Reported off afterwards: {time}.",
    "hank.pump.off_now": "The box currently reports the pump off.",
    "hank.pump.unseen": "No pump run has been recorded today so far.",
    "hank.pump.unavailable": "I have no readable pump history for today.",
    "hank.pump.unassigned": "A USB port was reported on today, but it had not yet been confirmed as the pump.",
    "hank.pump.gaps": "The recording has gaps; a short run may be missing.",
    "hank.pump.amount": "This does not measure the amount of water delivered.",
    "hank.pump.context": "Recorded pump history ({date}): {summary}",
    "hank.pump.schedule_off": "The automatic watering schedule is off.",
    "hank.pump.next": "Next scheduled run: {time} (Europe/Berlin), for {seconds} seconds. The controller checks water and connection before starting.",
    "hank.pump.countdown": "The timed cycle's stop command is scheduled in {seconds} seconds.",
    "hank.pump.stopping": "The controller is stopping the pump; off is not confirmed yet.",
    "hank.pump.unmanaged": "USB 1 is on, but this controller has no timer running. The existing run is not taken over automatically.",
}


def register() -> None:
    i18n.DE.update(DE)
    i18n.EN.update(EN)


def asks(question: str) -> bool:
    q = fold(question)
    if re.match(r"^(ich|wir|i|we)\b", q) and "?" not in question:
        return False  # the owner's statement is not a request to infer a pump run
    if (re.search(r"\b(gestern|vorgestern|yesterday|tomorrow|last week|letzte woche)\b", q)
            or re.search(r"\bmorgen\b", q) and not re.search(r"\bheute morgen\b", q)):
        return False  # this report is explicitly today's; never answer another day with today's data
    if re.search(r"\b(soll|sollte|muss|kann|should|need|can|schalte|turn|switch|starte|start)\b", q):
        return False  # watering advice and pump commands keep their existing routes
    if (re.search(r"\b(pump|pumpe\w*|watering|bewasser\w*|giess\w*)\b", q)
            and re.search(r"\b(next|nachst\w*|schedule|zeitplan|cycle|zyklus|countdown|restzeit)\b", q)):
        return True
    if re.match(r"was (ist|macht|bedeutet|tut)\b", q):
        return False
    subject = re.search(r"\b((?:wasser)?pumpe\w*|pump|usb ?1|dripper|gegossen|gewassert|bewasser\w*|"
                        r"tropfbewasser\w*|irrigat\w*|water(?:ed|ing)?)\b", q)
    past = re.search(r"\b(war|lief|gelaufen|wurde|hat|hast|habe|schon|bereits|gegossen|gewassert|bewassert|"
                     r"was|did|has|have|already|ran|run|watered)\b", q)
    event = re.search(r"\b(an|aus|aktiv|lief|gelaufen|laufen|eingeschaltet|ausgeschaltet|gegossen|gewassert|"
                      r"bewassert|on|off|active|ran|run|water|watered|irrigated)\b", q)
    return bool(subject and past and event)


def _time(value: Any, lang: str) -> str | None:
    try:
        when = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if lang == "de":
        return f"{when:%H:%M:%S}"
    return f"{when.hour % 12 or 12}:{when:%M:%S} {'PM' if when.hour >= 12 else 'AM'}"


def answer(live: LiveState | Mapping[str, Any], lang: str, *, answer_id: str) -> Answer:
    state = collect(live)
    report = state.snapshot.get("pump_history")
    report = report if isinstance(report, Mapping) else {}
    runs = [r for r in report.get("runs") or [] if isinstance(r, Mapping)]
    if report.get("current_on") is True:
        lines = [i18n.t("hank.pump.running", lang)]
    elif report.get("observed_on"):
        lines = [i18n.t("hank.pump.yes", lang)]
    elif report.get("unidentified_on"):
        lines = [i18n.t("hank.pump.unassigned", lang)]
    else:
        lines = [i18n.t("hank.pump.unseen" if report.get("available") else "hank.pump.unavailable", lang)]
    if runs:
        last = max(runs, key=lambda r: r.get("last_on") or 0)
        at = _time(last.get("last_on_local"), lang)
        if at:
            lines.append(i18n.t("hank.pump.last", lang, time=at, port=port_name(str(last.get("port") or ""))))
        off = _time(last.get("off_seen_local"), lang)
        if off:
            lines.append(i18n.t("hank.pump.off_at", lang, time=off))
    if report.get("current_on") is False:
        lines.append(i18n.t("hank.pump.off_now", lang))
    if report.get("observed_on"):
        lines.append(i18n.t("hank.pump.amount", lang))
    elif not report.get("complete"):
        lines.append(i18n.t("hank.pump.gaps", lang))
    watering = state.snapshot.get("watering")
    if isinstance(watering, Mapping):
        if watering.get("status") == "stopping":
            lines.append(i18n.t("hank.pump.stopping", lang))
        elif watering.get("remaining_s") is not None:
            lines.append(i18n.t("hank.pump.countdown", lang, seconds=watering["remaining_s"]))
        elif watering.get("status") == "unmanaged":
            lines.append(i18n.t("hank.pump.unmanaged", lang))
        cfg = watering.get("settings") or {}
        if not cfg.get("enabled"):
            lines.append(i18n.t("hank.pump.schedule_off", lang))
        elif watering.get("next_at") is not None:
            moment = datetime.fromtimestamp(watering["next_at"], ZoneInfo("Europe/Berlin"))
            lines.append(i18n.t("hank.pump.next", lang, time=moment.strftime("%Y-%m-%d %H:%M"), seconds=cfg.get("duration_s")))
            lines += thirst_report.schedule_lines(state, lang)  # hook: what the need check says about that slot
    return Answer(id=answer_id, route="pump_history", lang=lang, text=" ".join(lines))
