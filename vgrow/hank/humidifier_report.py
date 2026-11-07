"""Maintenance answers from the humidifier record, with no inference of a measured tank level."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from vgrow import i18n

from .livestate import collect
from .text import fold
from .types import Answer, SourceRef

DE = {
    "hank.hum.model": "Hinterlegt ist der {name} ({number}).",
    "hank.hum.spec": "Laut Hersteller: {min} bis {max} ml/h Nebelleistung; bis zu {continuous} h Dauernebel oder {intermittent} h Intervallnebel pro voller 750-ml-Füllung. Das ist keine Messung deines Verbrauchs.",
    "hank.hum.sensor": "Das Gerät hat eine eigene Abschaltung bei Wassermangel. Ihr Sensorzustand wird dem Cockpit nicht gemeldet; USB-Strom kann weiter an sein, obwohl kein Nebel mehr austritt.",
    "hank.hum.restart": "Laut Anleitung startet er bei wiederkehrendem USB-Strom im gemerkten Modus. Falls nicht: nach dem Nachfüllen den Docht sättigen lassen und den Netzschalter drei Sekunden halten. Das Wiederanlaufen deines Geräts ist damit noch nicht bestätigt.",
    "hank.hum.mode.unknown": "Der aktuell gewählte Nebelmodus ist noch unbekannt.",
    "hank.hum.mode.continuous": "Du hast Dauernebel als aktuellen Modus hinterlegt.",
    "hank.hum.mode.intermittent": "Du hast Intervallnebel als aktuellen Modus hinterlegt.",
    "hank.hum.uncalibrated": "Einzelne Sprühstöße und der tatsächliche Stundenverbrauch sind noch nicht gemessen. Die Herstellerangabe wird nicht als kalibrierter Verbrauch übernommen.",
    "hank.hum.start_button": "USB 2 meldet Strom an, aber der Nebelstart ist unbestätigt. Prüfe den seitlichen "
                             "Startknopf und sichtbaren Nebel. Bestätige den Start unter Befeuchter-Tank. "
                             "Ob er nach USB-Aus und -Ein automatisch wieder nebelt, ist noch nicht bestätigt.",
    "hank.hum.mist_runtime": "Geschätzte Laufzeit nach bestätigtem Nebelstart seit Nachfüllung: {hours}.",
    "hank.hum.no_response": "Bei der letzten Wirkungsprüfung stieg die Feuchte innerhalb von etwa "
                            "{minutes} Minuten zu wenig. Prüfe "
                            "den separaten Befeuchter-Tank, den seitlichen Startknopf und ob Nebel austritt. Ein leerer Tank, eine verstopfte "
                            "Düse oder starke Abluft sind mögliche Gründe; der Füllstand wird nicht gemessen.",
    "hank.hum.estimated_low": "Der Befeuchter-Tank könnte bald leer sein: geschätzt noch {remaining}. "
                              "Bitte Tank prüfen und bei Bedarf nachfüllen. Das ist eine Laufzeit-Schätzung, kein Sensorwert.",
    "hank.hum.capacity": "Für den separaten Befeuchter-Tank sind ungefähr {capacity} hinterlegt.",
    "hank.hum.start": "Bei der letzten bestätigten Nachfüllung waren danach {amount} im Tank.",
    "hank.hum.runtime": "Erfasste Einschaltzeit seit der letzten bestätigten Nachfüllung: ungefähr {hours}.",
    "hank.hum.total": "Bisher erfasste Einschaltzeit: ungefähr {hours}. Eine Füllmenge ist noch nicht protokolliert.",
    "hank.hum.rate": "Geschätzter Verbrauch je Einschaltstunde: {rate}.",
    "hank.hum.unknown": "Den Verbrauch pro Sprühstoß kenne ich ohne Gerätemodell oder Messung nicht. "
                        "Trage eine bekannte Nebelleistung in ml/h ein oder bestätige eine Füllmenge und "
                        "später einen leeren Tank. Daraus kann ich den Verbrauch je Einschaltstunde schätzen.",
    "hank.hum.remaining": "Geschätzt verbraucht: {used}; geschätzt übrig: {remaining}. Kein gemessener Füllstand.",
    "hank.hum.gaps": "Ohne bestätigte Füllmenge und lückenlose Laufzeit gibt es keine Restmengen-Schätzung.",
    "hank.hum.count": "Bestätigte Nachfüllungen: {count}. Das Nachfüllen bleibt Handarbeit.",
    "hank.hum.link": "Befeuchter-Tank und Nachfüllungen",
}
EN = {
    "hank.hum.model": "The recorded model is {name} ({number}).",
    "hank.hum.spec": "Manufacturer ratings: {min} to {max} ml/h mist output; up to {continuous} h continuous mist or {intermittent} h intermittent mist per full 750 ml tank. These are not measurements of your consumption.",
    "hank.hum.sensor": "The device has its own low-water shut-off. Its sensor state is not reported to the cockpit; USB power may remain on while no mist comes out.",
    "hank.hum.restart": "The manual says it resumes the remembered mode when USB power returns. If it does not, let the wick saturate after refilling and hold the power button for three seconds. This does not confirm that your unit has restarted.",
    "hank.hum.mode.unknown": "The currently selected mist mode is still unknown.",
    "hank.hum.mode.continuous": "You have recorded continuous mist as the current mode.",
    "hank.hum.mode.intermittent": "You have recorded intermittent mist as the current mode.",
    "hank.hum.uncalibrated": "Individual sprays and actual hourly consumption have not been measured yet. The manufacturer rating is kept separate from calibrated consumption.",
    "hank.hum.start_button": "USB 2 reports power on, but mist startup is unconfirmed. Check the side start "
                             "button and visible mist, then confirm the start under Humidifier tank. Automatic "
                             "mist restart after USB power is switched off and on has not been confirmed.",
    "hank.hum.mist_runtime": "Estimated runtime after confirmed mist startup since refill: {hours}.",
    "hank.hum.no_response": "The last response check found too little humidity rise within about "
                            "{minutes} minutes. Check the separate "
                            "humidifier tank, side start button and mist outlet. An empty tank, a blocked nozzle or strong "
                            "exhaust are possible causes; its water level is not measured.",
    "hank.hum.estimated_low": "The humidifier tank may be running low: an estimated {remaining} remains. "
                              "Please check the tank and refill if needed. This is a runtime estimate, not a sensor reading.",
    "hank.hum.capacity": "The separate humidifier tank is set to approximately {capacity}.",
    "hank.hum.start": "The last confirmed refill left {amount} in the tank.",
    "hank.hum.runtime": "Recorded power-on time since the last confirmed refill: approximately {hours}.",
    "hank.hum.total": "Power-on time recorded so far: approximately {hours}. No fill volume has been logged yet.",
    "hank.hum.rate": "Estimated consumption per powered-on hour: {rate}.",
    "hank.hum.unknown": "I do not know the amount per spray without a device model or measurement. Enter a known "
                        "mist output in ml/h, or confirm a fill volume and later an empty tank. I can use "
                        "that cycle to estimate consumption per powered-on hour.",
    "hank.hum.remaining": "Estimated used: {used}; estimated remaining: {remaining}. This is not a measured water level.",
    "hank.hum.gaps": "A remaining-volume estimate needs a confirmed fill volume and runtime without recording gaps.",
    "hank.hum.count": "Confirmed refills: {count}. Refilling is a manual task.",
    "hank.hum.link": "Humidifier tank and refills",
}
i18n.DE.update(DE)
i18n.EN.update(EN)


def asks(question: str) -> bool:
    q = fold(question)
    return bool(re.search(r"\b(befeuchter|luftbefeuchter|humidifier|usb ?2|aerostream|u075)\b", q) and re.search(
        r"tank|leer|empty|full|voll|nachfull|refill|verbrauch|consum|laufzeit|runtime|spray|spruh|nebel|mist|"
        r"wasser|water|wirkung|effect|nicht.*steig|not.*ris|nothing.*chang|model|kapazitat|capacity|restart", q))


def warning(auto: Mapping[str, Any], lang: str) -> str | None:
    tank = auto.get("tank") or {}
    if tank.get("warning") == "start_button":
        return i18n.t("hank.hum.start_button", lang)
    if tank.get("warning") == "no_response":
        return i18n.t("hank.hum.no_response", lang, minutes=tank.get("response_minutes", 20))
    if tank.get("warning") == "estimated_low":
        return i18n.t("hank.hum.estimated_low", lang,
                      remaining=i18n.fmt_qty(tank.get("remaining_ml_estimate"), "ml", lang, 0))
    return None


def lines(auto: Mapping[str, Any], lang: str) -> list[str]:
    tank = auto.get("tank") or {}
    out = [hint] if (hint := warning(auto, lang)) else []
    if not tank:
        return out + [i18n.t("hank.hum.unknown", lang)]
    model = tank.get("model")
    if isinstance(model, Mapping):
        out.append(i18n.t("hank.hum.model", lang, name=model["name"], number=model["model_number"]))
        out.append(i18n.t("hank.hum.spec", lang, min=model["output_min_ml_h"], max=model["output_max_ml_h"],
                          continuous=model["continuous_hours"], intermittent=model["intermittent_hours"]))
        out.append(i18n.t({"continuous": "hank.hum.mode.continuous", "intermittent": "hank.hum.mode.intermittent"}
                          .get(tank.get("mist_mode"), "hank.hum.mode.unknown"), lang))
        out.extend([i18n.t("hank.hum.sensor", lang), i18n.t("hank.hum.restart", lang)])
    out.append(i18n.t("hank.hum.capacity", lang, capacity=i18n.fmt_qty(tank["capacity_ml"], "ml", lang, 0)))
    cycle = tank.get("refilled_at") is not None
    if cycle:
        out.append(i18n.t("hank.hum.start", lang, amount=i18n.fmt_qty(tank.get("starting_ml"), "ml", lang, 0)))
    runtime_key = "hank.hum.mist_runtime" if cycle and tank.get("button_start") else "hank.hum.runtime" if cycle else "hank.hum.total"
    out.append(i18n.t(runtime_key, lang,
                      hours=i18n.fmt_qty(tank.get("cycle_runtime_s" if cycle else "runtime_s", 0) / 3600, "h", lang, 1)))
    rate = tank.get("rate_ml_h")
    out.append(i18n.t("hank.hum.rate", lang, rate=i18n.fmt_qty(rate, "ml/h", lang, 1)) if rate is not None
               else i18n.t("hank.hum.uncalibrated" if model else "hank.hum.unknown", lang))
    if tank.get("remaining_ml_estimate") is not None:
        out.append(i18n.t("hank.hum.remaining", lang, used=i18n.fmt_qty(tank["used_ml_estimate"], "ml", lang, 0),
                          remaining=i18n.fmt_qty(tank["remaining_ml_estimate"], "ml", lang, 0)))
    elif cycle:
        out.append(i18n.t("hank.hum.gaps", lang))
    out.append(i18n.t("hank.hum.count", lang, count=tank.get("refill_count", 0)))
    return out


def answer(live: Mapping[str, Any], lang: str, *, answer_id: str) -> Answer:
    auto = collect(live).automation_view
    model = (auto.get("tank") or {}).get("model")
    sources = [SourceRef("humidifier-manual", "VIVOSUN AeroStream U075", model["source"])] if isinstance(model, Mapping) else []
    return Answer(id=answer_id, route="humidifier_tank", lang=lang,
                  text="\n".join(lines(auto, lang)), sources=sources,
                  link={"href": "#automation", "label": i18n.t("hank.hum.link", lang)})
