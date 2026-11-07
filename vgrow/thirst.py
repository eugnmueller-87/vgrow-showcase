"""The Durst-Index (thirst index): how much water the pot is missing, when it will be due by stage, and the need check
before every scheduled slot of the watering controller.

Ownership. vgrow/watering.py (the timed pump controller) keeps its settings, watchdog, stop and events. It asks gate()
before an automatic slot through one named hook (WateringController.need_gate). The gate can only skip a slot or
shorten its run time, never start or lengthen one, and a gate that fails or answers nothing skips the slot. Nothing in
this module sends a device command.

The metric (the reviewed design, sections 1, 3, 4). D is the litres the pot is below container capacity since the last
anchor; TI = 100 D / D_trig with D_trig = f_trig(stage) x a_w x V_pot. Between two events D grows by the modelled
evapotranspiration ET_h = K x S x shape (lights on: w Lf + (1 - w) VPD'/1.2; lights off: 0.10 of that at the last lit
level), integrated over epoch seconds from every poll. Water lowers D; what goes in beyond D is the overshoot X, and any
overshoot (or water in the saucer) starts the wet-hold. Every constant comes from kartei/water_model.json with its
source key or its assumption tag.

What counts as water: hand watering (thirst_manual), every feed of the feeding log (its litres, read only), and a run of
the pump port seen in the box's reports. A run counts only for the time after the owner named the pump on that port.
It counts with litres only when the owner confirmed in the watering settings that the port switches the water
(power_confirmed) and the flow is calibrated (two jug runs within 10 %): litres = the run's longest possible time x the
flow x 1.1, the side that never adds water. Power confirmed but no flow: an unknown amount, the pot counts as full and
the wet-hold starts. Power not confirmed: nothing is counted (USB power is not water flow), but the run makes the last
check stale and widens the band. Never minutes times a guessed flow.

The guardrails all live in blocks(), the only place that reads the tank: a full tank never triggers anything.
trigger() takes the ledger and the block list, never the tank.

The plan by stage: the trigger share and the caps per stage (water_model.json), the stage windows of the stage card,
the light of the light timer (else the measured level held), the measured VPD now and the middle of each later stage's
VPD band; forecast() says when the next watering is expected, about how much, and how sure that is.

State under STATE_DIR/boxes/<box id>/ (atomic writes, never the repository): thirst-pot.json, thirst-state.json,
thirst-log.jsonl. Nothing here imports Hank; Hank reads brief() through the server (server.HankBox).
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import box as box_mod
from . import config, i18n
from .i18n import UserError, fmt_num, fmt_qty, localize, t
from .plant import FLOWERING_STAGES, PlantLog, current_stage, load_profile, parse_day
from .portguard import PortGuard, configured_device

_LOGGER = logging.getLogger(__name__)

MODEL_FILE = config.REPO / "kartei" / "water_model.json"
POT_FILE = "thirst-pot.json"
STATE_FILE = "thirst-state.json"
LOG_FILE = "thirst-log.jsonl"
ZONE = ZoneInfo("Europe/Berlin")  # the watering controller's and the light timer's zone
PUMP = "pump"
KINDS = ("WAIT", "ASK", "PROPOSE", "BLOCKED", "ALERT")
STAGE_KEYS = ("seedling", "veg", "early_flower", "mid_flower", "late_flower", "harvest")
SERIES_STEP_S = 600  # the ET series keeps a point every 10 minutes ...
SERIES_KEEP_S = 30 * 86400  # ... for 30 days
FORECAST_STEP_S = 600
GAPS_KEEP = 200
EVENTS_SHOWN = 6
FINGER_ANSWERS = ("dry", "moist", "wet")
LIFT_ANSWERS = ("light", "medium", "heavy")
SENSOR_EVERY_S = 6 * 3600  # the soil sensor repeats an unchanged answer this often: a measured index holds 24 h
SENSOR_DEPTH_CM = 5.0  # the WH51's blade sits about at the finger's depth; it counts as a check at 5 cm
ARRIVED_ANSWERS = ("dry", "saucer", "no")  # Ja, Untersetzer trocken / Ja, Wasser im Untersetzer / Nein
MAX_BACK_S = 7 * 86400  # a watering entered afterwards: at most 7 days back
MIN_LITERS, MAX_LITERS = 0.05, 20.0
FLOW_SECONDS = (5, 3600)
JUG_MARGIN_S = 60.0  # a jug run starts within this of the owner's tap
FLOW_ML = (5, 5000)
DAY_PARTS = ((5, "morning"), (11, "midday"), (14, "afternoon"), (18, "evening"), (22, "night"))
POLL_EDGE_S = float(box_mod.POLL_SECONDS)


# ---------- the catalog (German first, English twin), registered into the server catalogs at import ----------

DE: dict[str, str] = {
    "thirst.decision.no_pot": "Topf einrichten: ohne Topfgröße rechnet das Cockpit keinen Durst-Index.",
    "thirst.decision.first_check": "Noch kein Bezugspunkt für den Durst-Index. Bitte kurz prüfen: Finger {depth} cm "
                                   "tief, trocken, feucht oder nass?",
    "thirst.decision.first_lift": "Noch kein Bezugspunkt für den Durst-Index. Bitte kurz prüfen: Topf anheben, leicht, "
                                  "mittel oder schwer?",
    "thirst.decision.wait": "Durst-Index {ti} %. Der Topf ist noch nicht dran. Ich gieße nicht und schaue {when} wieder.",
    "thirst.decision.wait_round": "Durst-Index {ti} %, gemessen. Gießen zur nächsten Runde, {when}.",
    "thirst.decision.ask": "Durst-Index {ti} %. Bald fällig. Bitte kurz prüfen: Finger {depth} cm tief, trocken, "
                           "feucht oder nass?",
    "thirst.decision.ask_lift": "Durst-Index {ti} %. Bald fällig. Bitte kurz prüfen: Topf anheben, leicht, mittel oder "
                                "schwer?",
    "thirst.decision.propose": "Durst-Index {ti} %, {measured}. Vorschlag: {litres} Liter, in {n} Gaben mit {soak} "
                               "Minuten Pause. Danach Von Hand gegossen tippen.",
    "thirst.decision.blocked": "Durst-Index {ti} %, gemessen. Ich schlage kein Gießen vor: {reasons}.",
    "thirst.decision.blocked_tank": "Ich gieße nicht: der Wassersensor meldet trocken. Auffüllen, dann Aufgefüllt tippen "
                                    "und die Liter wählen. Ein voller Tank ist für mich nie ein Grund zu gießen.",
    "thirst.decision.hold": "Zu nass seit {since}: kein Wasser, bis der Durst-Index wieder 100 erreicht und eine "
                            "Prüfung das bestätigt (die Waage, oder zweimal trocken in 5 cm, 12 h auseinander).",
    "thirst.decision.hold_check": "Zu nass seit {since}, der Durst-Index steht bei {ti} %. Bitte Finger 5 cm tief: "
                                  "trocken, feucht oder nass? Erst zwei trockene Antworten mit 12 h Abstand (oder die "
                                  "Waage) heben die Sperre auf.",
    "thirst.decision.hold_dry": "Trocken gemeldet, aber zu nass seit {since}: das hebt die Sperre nicht auf. Ab {when} "
                                "bitte noch einmal prüfen, oder wiegen.",
    "thirst.decision.wet": "Fingerprobe nass vor {h} h: 24 h kein Wasser, danach frage ich wieder.",
    "thirst.decision.weigh_or_wait": "Trocken in {depth} cm, aber laut Rechnung erst {ti} %: wiegen oder abwarten, "
                                     "nicht gießen.",
    "thirst.decision.alert_high": "Durst-Index {ti} %. Der Topf braucht dich. Auf Schätzung gieße ich nicht.",
    "thirst.decision.alert_blocked": "Seit {h} h fällig und gesperrt ({reasons}). Bitte selbst nachsehen.",
    "thirst.decision.alert_wet": "Der Topf ist {h} h nach dem Überschuss noch nass. Bitte wiegen und die Wurzeln "
                                 "ansehen; mit Gewalt trockne ich keinen Topf.",
    "thirst.decision.model": "Das Gießmodell ist nicht lesbar: kein Durst-Index, kein Gießvorschlag.",
    "thirst.measured.weigh": "gewogen",
    "thirst.measured.finger": "Fingerprobe trocken",
    "thirst.measured.lift": "Topf leicht",
    "thirst.when.today": "heute {time}",
    "thirst.when.tomorrow": "morgen {time}",
    "thirst.when.day": "am {date} {time}",
    "thirst.when.today_part": "heute, {part}",
    "thirst.when.tomorrow_part": "morgen, {part}",
    "thirst.when.day_part": "{weekday}, {date}, {part}",
    "thirst.part.morning": "morgens",
    "thirst.part.midday": "mittags",
    "thirst.part.afternoon": "nachmittags",
    "thirst.part.evening": "abends",
    "thirst.part.night": "nachts",
    "thirst.weekday.0": "Montag",
    "thirst.weekday.1": "Dienstag",
    "thirst.weekday.2": "Mittwoch",
    "thirst.weekday.3": "Donnerstag",
    "thirst.weekday.4": "Freitag",
    "thirst.weekday.5": "Samstag",
    "thirst.weekday.6": "Sonntag",
    "thirst.block.tank_dry": "der Wassersensor meldet trocken",
    "thirst.block.tank_settle": "der Tank ist erst seit {min} min wieder nass, nach 30 min geht es weiter",
    "thirst.block.tank_unknown": "der Tank meldet keinen frischen Wert",
    "thirst.block.stale": "die Messwerte sind älter als 10 min",
    "thirst.block.offline": "die Box meldet sich nicht",
    "thirst.block.gaps": "nur {pct} % der letzten 24 h sind beobachtet",
    "thirst.block.interval": "zuletzt gegossen vor {h} h, mindestens 20 h Abstand",
    "thirst.block.settle": "gerade gegossen, erst 2 h abtropfen lassen",
    "thirst.block.today": "heute schon gegossen",
    "thirst.block.week": "die Wochengrenze von {limit} ist erreicht",
    "thirst.block.caps": "die Grenze für 24 h ist erreicht",
    "thirst.block.pump_running": "{port} meldet Strom: die Pumpe läuft vielleicht",
    "thirst.block.app_plan": "an {port} läuft ein Plan oder eine Automatik der VIVOSUN-App",
    "thirst.block.ports": "die Belegung der Anschlüsse hat sich geändert und ist nicht bestätigt",
    "thirst.block.stage": "die Phase ist nicht bestätigt",
    "thirst.block.wet_hold": "zu nass seit {since}",
    "thirst.block.wet": "Fingerprobe nass, 24 h Pause",
    "thirst.why.measured_weigh": "Gewogen vor {h} h.",
    "thirst.why.measured_finger": "Fingerprobe vor {h} h: {answer}.",
    "thirst.why.model": "Nur gerechnet, letzte Prüfung vor {h} h.",
    "thirst.why.model_weigh": "Nur gerechnet, zuletzt gewogen vor {h} h.",
    "thirst.why.model_never": "Nur gerechnet, noch keine Prüfung.",
    "thirst.why.band": "Unsicherheit plus oder minus {band} Punkte ({confidence}).",
    "thirst.why.first_cycle": "Noch kein Bezugsgewicht: die erste Gabe ist höchstens die halbe Auslösemenge.",
    "thirst.why.half_dose": "Nach starkem Austrocknen nur die halbe Menge.",
    "thirst.why.saucer_less": "Beim letzten Gießen stand Wasser im Untersetzer: diesmal 25 % weniger.",
    "thirst.why.wick": "Mit Docht kommt ungezählt Wasser dazu: der Durst-Index ist unsicher.",
    "thirst.why.unknown_amount": "Die Pumpe lief mit unbekannter Menge: gerechnet wird, als wäre der Topf voll.",
    "thirst.why.unverified": "USB 1 hatte Strom, ob Wasser floss, ist nicht bestätigt: die Prüfung davor zählt nicht "
                             "mehr.",
    "thirst.why.stage_contested": "Phase strittig: bitte die Phase bestätigen.",
    "thirst.confidence.high": "hoch",
    "thirst.confidence.medium": "mittel",
    "thirst.confidence.low": "niedrig",
    "thirst.answer.dry": "trocken",
    "thirst.answer.moist": "feucht",
    "thirst.answer.wet": "nass",
    "thirst.answer.light": "leicht",
    "thirst.answer.medium": "mittel",
    "thirst.answer.heavy": "schwer",
    "thirst.check.finger": "Finger {depth} cm tief: trocken, feucht oder nass? Auf der Karte Durst-Index antippen.",
    "thirst.check.lift": "Topf anheben: leicht, mittel oder schwer? Auf der Karte Durst-Index antippen.",
    "thirst.check.hold": "Finger 5 cm tief: trocken, feucht oder nass? Auf der Karte Durst-Index antippen.",
    "thirst.check.pot": "Auf der Karte Durst-Index unter Automatik Topf einrichten tippen.",
    "thirst.origin.hand": "von Hand",
    "thirst.origin.feed": "mit Dünger",
    "thirst.origin.schedule": "Gießplan",
    "thirst.origin.once": "Einmal gießen",
    "thirst.origin.app": "App-Plan",
    "thirst.origin.other": "ohne Timer",
    "thirst.origin.owner": "du",
    "thirst.event.water": "{litres} gegossen, {origin}",
    "thirst.event.water_pump": "{port} lief etwa {minutes} min, {origin}: gezählt {litres}",
    "thirst.event.water_unknown": "{port} lief etwa {minutes} min, {origin}, unbekannte Menge",
    "thirst.event.arrived_no": "Wasser nicht angekommen",
    "thirst.event.check": "Fingerprobe {depth} cm: {answer}",
    "thirst.event.lift": "Topf angehoben: {answer}",
    "thirst.event.sensor": "Bodensensor: {answer}",
    "thirst.measured.sensor": "Bodensensor trocken",
    "thirst.event.weigh": "Gewogen: {kg}",
    "thirst.event.weigh_full": "Gewogen voll und abgetropft: {kg}",
    "thirst.event.saucer_water": "Wasser im Untersetzer",
    "thirst.event.saucer_dry": "Untersetzer trocken",
    "thirst.event.refill": "Tank aufgefüllt: {litres}",
    "thirst.event.refill_feed": "Tank aufgefüllt: {litres}, mit Dünger",
    "thirst.event.pot": "Topf eingerichtet: {litres}",
    "thirst.event.pump_seen_device": "{port} lief etwa {minutes} min, Pumpe nicht bestätigt: nicht gezählt",
    "thirst.event.pump_seen_power": "{port} lief etwa {minutes} min, Wasserfluss nicht bestätigt: nicht gezählt",
    "thirst.event.pump_seen_jug": "{port} lief etwa {minutes} min in den Messbecher: nicht gezählt",
    "thirst.event.flow": "Durchfluss gemessen: {ml} in {seconds} s",
    "thirst.event.flow_reset": "Durchflussmessungen verworfen",
    "thirst.push.title": "Durst-Index",
    "thirst.push.ask": "Durst-Index {ti} %. Seit {h} h keine Antwort: bitte prüfen, {check}",
    "thirst.push.high": "Durst-Index {ti} %. Der Topf braucht dich. Auf Schätzung gieße ich nicht.",
    "thirst.push.blocked": "Seit {h} h fällig und gesperrt: {reasons}.",
    "thirst.push.wet": "Der Topf ist {h} h nach dem Überschuss noch nass. Bitte wiegen und die Wurzeln ansehen.",
    "thirst.gate.no_pot": "Topf nicht eingerichtet: Bedarf unbekannt.",
    "thirst.gate.unknown": "Noch kein Durst-Index: erst prüfen.",
    "thirst.gate.unmeasured": "Durst-Index {ti} %, nur gerechnet: keine Prüfung in den letzten 24 h.",
    "thirst.gate.hold": "Zu nass seit {since}: Durst-Index {ti} %, kein Wasser.",
    "thirst.gate.not_due": "Topf noch nicht fällig: Durst-Index {ti} %.",
    "thirst.gate.blocked": "Gesperrt: {reasons}.",
    "thirst.gate.cap": "Die Grenze für 24 h oder die Woche ist erreicht.",
    "thirst.gate.too_short": "Die erlaubte Menge ist zu klein für einen Lauf: {litres}.",
    "thirst.gate.run_short": "Durst-Index {ti} %, gemessen: gekürzt auf {seconds} s, etwa {litres} (begrenzt durch "
                             "{limit}).",
    "thirst.gate.run_full": "Durst-Index {ti} %, gemessen: {seconds} s, etwa {litres}, unter der Grenze von {limit}.",
    "thirst.gate.no_flow": "Termin ausgelassen: erst den Durchfluss mit zwei Messbecher-Läufen kalibrieren.",
    "thirst.gate.no_power": "Termin ausgelassen: erst bestätigen, dass der USB-Port den Wasserfluss ein- und ausschaltet.",
    "thirst.gate.error": "Bedarfsprüfung nicht möglich: der Termin entfällt.",
    "thirst.gate.preview_run": "Nächster Gießtermin {time}: darf laufen, solange der Topf gemessen fällig bleibt.",
    "thirst.gate.preview_skip": "Nächster Gießtermin {time}: entfällt, wenn sich bis dahin nichts ändert. {reason}",
    "thirst.limit.deficit": "das Defizit im Topf",
    "thirst.limit.stage": "die Höchstmenge der Phase",
    "thirst.limit.aw": "die Höchstmenge für diesen Topf",
    "thirst.limit.first_cycle": "die erste Gabe ohne Bezugsgewicht",
    "thirst.limit.day": "die 24-h-Grenze",
    "thirst.limit.week": "die Wochengrenze",
    "thirst.limit.half": "die halbe Menge nach starkem Austrocknen",
    "thirst.manual.no_pot": "Kein Durst-Index ohne eingerichteten Topf: Einmal gießen gießt ohne Bedarfsprüfung.",
    "thirst.manual.unknown": "Noch kein Durst-Index: erst prüfen. Einmal gießen gießt ohne Bedarfsprüfung.",
    "thirst.manual.due": "Durst-Index {ti} %, gemessen: der Topf ist fällig.",
    "thirst.manual.unmeasured": "Durst-Index {ti} %, nur gerechnet: erst prüfen. Einmal gießen gießt ohne "
                                "Bedarfsprüfung.",
    "thirst.manual.not_due": "Durst-Index {ti} %: der Topf ist nicht fällig. Einmal gießen gießt trotzdem, ohne "
                             "Bedarfsprüfung.",
    "thirst.manual.hold": "Zu nass, Durst-Index {ti} %: bitte nicht gießen. Einmal gießen gießt trotzdem.",
    "thirst.forecast.due": "Nächstes Gießen voraussichtlich {when}, etwa {litres}.",
    "thirst.forecast.due_round": "Fällig voraussichtlich {due}. Gießen dann zur nächsten Runde, {when}, etwa {litres}.",
    "thirst.forecast.now": "Laut Rechnung ist der Topf jetzt fällig, etwa {litres}.",
    "thirst.forecast.measured": "Der Topf ist gemessen fällig: jetzt etwa {litres}.",
    "thirst.forecast.range": "Unsicher: frühestens {early}, spätestens {late}.",
    "thirst.forecast.range_open": "Unsicher: frühestens {early}, spätestens offen.",
    "thirst.forecast.beyond": "Nächstes Gießen nicht in den nächsten {days} Tagen: laut Rechnung verbraucht der Topf "
                              "gerade sehr wenig.",
    "thirst.forecast.hold": "Erst muss der Topf abtrocknen: der Durst-Index ab dem Überschuss erreicht 100 "
                            "voraussichtlich {when}.",
    "thirst.forecast.check": "Davor prüfen: {check}",
    "thirst.forecast.slot": "Passender Gießtermin: {slot}.",
    "thirst.forecast.hand": "Ohne Gießtermin: von Hand gießen.",
    "thirst.forecast.none": "Noch kein Bezugspunkt: erst prüfen, dann rechne ich voraus.",
    "thirst.forecast.basis": "Grundlage: {basis}.",
    "thirst.basis.pot": "Topf {litres} (eingetragen)",
    "thirst.basis.stage_set": "{stage}, von dir gesetzt",
    "thirst.basis.stage_estimated": "{stage}, geschätzt, nicht bestätigt",
    "thirst.basis.light_timer": "Lichttimer {on} bis {off}, {level} (eingestellt)",
    "thirst.basis.light_held": "Licht {level} rund um die Uhr (gemessen, kein Lichttimer)",
    "thirst.basis.light_unknown": "Licht unbekannt (geschätzt wie zuletzt gesehen)",
    "thirst.basis.vpd": "VPD {vpd} (Fühler, gemessen)",
    "thirst.basis.vpd_unknown": "VPD unbekannt (gerechnet mit 1,2 kPa)",
    "thirst.basis.height": "Pflanze {cm} (gemessen am {date})",
    "thirst.basis.height_estimated": "Pflanze {cm} (Maßband-Schätzung vom {date})",
    "thirst.basis.height_none": "Pflanzenhöhe unbekannt (gerechnet mit 61 cm)",
    "thirst.basis.aw": "Wasser, das die Erde hält: 30 % des Topfs (Annahme bis zur ersten Wägung)",
    "thirst.basis.k0": "Verbrauch: Startwert 20 ml je Stunde (Annahme bis zur ersten Wägung)",
    "thirst.basis.stages": "Phasenwechsel nach den Tagen der Phasenkarte ab {date} (geschätzt)",
    "thirst.basis.vpd_later": "VPD späterer Wochen: Mitte des Bands der Phasenkarte (geschätzt)",
    "thirst.basis.future": "Spätere Wochen rechnen mit der heutigen Höhe und dem heutigen Lichtplan. Wachstum und "
                            "geänderte Einstellungen können den Bedarf verändern; dies ist keine feste Gießanweisung.",
    "thirst.flow.unusual": "Ungewöhnlicher Durchfluss: Sekunden und Milliliter prüfen und die Messung erneut bestätigen.",
    "thirst.flow.confirm": "Das ergibt {rate}. Bitte Sekunden und Milliliter prüfen. Ist der gemessene Durchfluss "
                           "korrekt, dieselben Werte noch einmal bestätigen.",
    "thirst.flow.none": "Durchfluss nicht gemessen: Pumpenläufe zählen mit unbekannter Menge.",
    "thirst.flow.one": "Eine Durchflussmessung: {rate}. Für die Kalibrierung noch eine zweite.",
    "thirst.flow.far": "Die zwei Messungen weichen mehr als 10 % ab ({a} und {b}): noch einmal messen.",
    "thirst.flow.ok": "Durchfluss {rate}, gemessen am {date} (zwei Läufe, gültig bis {until}).",
    "thirst.flow.power_no": "Pumpenschalter nicht bestätigt (Gießplan-Einstellung): USB-Strom zählt nicht als Wasser.",
    "thirst.pump.on": "{port} meldet seit {h} h Strom. Strom ist kein Wasserfluss: gezählt wird {how}.",
    "thirst.pump.how_nothing": "nichts, solange der Pumpenschalter nicht bestätigt ist",
    "thirst.pump.how_unknown": "eine unbekannte Menge, bis der Durchfluss gemessen ist",
    "thirst.pump.how_litres": "die Laufzeit mal dem gemessenen Durchfluss",
    "thirst.error.no_pot": "Erst den Topf einrichten",
    "thirst.error.pot_size": "Topfvolumen zwischen 0,5 und 100 Litern, oder Durchmesser und Höhe zwischen 5 und 80 cm",
    "thirst.error.pot_mismatch": "Eingetragen {typed}, aus Durchmesser und Höhe etwa {geo}: mehr als 20 % auseinander. "
                                 "Stimmt das Volumen? Dann noch einmal tippen.",
    "thirst.error.pot_reset": "Ein neuer Topf setzt die Rechnung zurück. Noch einmal tippen.",
    "thirst.error.step": "Schritt der Waage zwischen 1 und 100 g",
    "thirst.error.kg": "Gewicht zwischen 0,2 und 100 kg",
    "thirst.error.answer": "Unbekannte Antwort {answer}",
    "thirst.error.liters": "Liter zwischen {min} und {max}",
    "thirst.error.liters_large": "{liters} sind viel für einen Topf: noch einmal tippen, wenn es stimmt",
    "thirst.error.time": "Zeit nicht lesbar oder in der Zukunft",
    "thirst.error.time_old": "Höchstens 7 Tage zurück, und nicht vor dem Einrichten des Topfs",
    "thirst.error.event": "Diesen Eintrag gibt es nicht",
    "thirst.error.refill": "Liter für den Tank zwischen 0,1 und 10",
    "thirst.error.flow": "Durchflusstest: Sekunden zwischen 5 und 3600, Milliliter zwischen 5 und 5000",
    "thirst.none": "Keine Durst-Index-Rechnung eingerichtet",
}

EN: dict[str, str] = {
    "thirst.decision.no_pot": "Set up the pot: without the pot size the cockpit computes no thirst index.",
    "thirst.decision.first_check": "No anchor for the thirst index yet. Please check: finger {depth} cm deep, dry, "
                                   "moist or wet?",
    "thirst.decision.first_lift": "No anchor for the thirst index yet. Please check: lift the pot, light, medium or "
                                  "heavy?",
    "thirst.decision.wait": "Thirst index {ti}%. The pot is not due yet. I will not water and will look again {when}.",
    "thirst.decision.wait_round": "Thirst index {ti}%, measured. Water at the next round, {when}.",
    "thirst.decision.ask": "Thirst index {ti}%. Due soon. Please check: finger {depth} cm deep, dry, moist or wet?",
    "thirst.decision.ask_lift": "Thirst index {ti}%. Due soon. Please check: lift the pot, light, medium or heavy?",
    "thirst.decision.propose": "Thirst index {ti}%, {measured}. Proposal: {litres} litres, in {n} portions with {soak} "
                               "minutes of pause. Then tap Watered by hand.",
    "thirst.decision.blocked": "Thirst index {ti}%, measured. I suggest no watering: {reasons}.",
    "thirst.decision.blocked_tank": "I will not water: the sensor reports dry. Refill, then tap Refilled and choose the "
                                    "litres. A full tank is never a reason for me to water.",
    "thirst.decision.hold": "Too wet since {since}: no water until the thirst index is back at 100 and a check confirms "
                            "it (the scale, or dry twice at 5 cm, 12 h apart).",
    "thirst.decision.hold_check": "Too wet since {since}, the thirst index stands at {ti}%. Please finger 5 cm deep: "
                                  "dry, moist or wet? Only two dry answers 12 h apart (or the scale) lift the hold.",
    "thirst.decision.hold_dry": "Dry reported, but too wet since {since}: that does not lift the hold. From {when} "
                                "please check once more, or weigh.",
    "thirst.decision.wet": "Finger test wet {h} h ago: no water for 24 h, then I will ask again.",
    "thirst.decision.weigh_or_wait": "Dry at {depth} cm, but by the model only {ti}%: weigh or wait, do not water.",
    "thirst.decision.alert_high": "Thirst index {ti}%. The pot needs you. I do not water on an estimate.",
    "thirst.decision.alert_blocked": "Due and blocked for {h} h ({reasons}). Please look yourself.",
    "thirst.decision.alert_wet": "The pot is still wet {h} h after the overshoot. Please weigh it and look at the "
                                 "roots; I never dry a pot by force.",
    "thirst.decision.model": "The watering model cannot be read: no thirst index, no watering proposal.",
    "thirst.measured.weigh": "weighed",
    "thirst.measured.finger": "finger test dry",
    "thirst.measured.lift": "pot light",
    "thirst.when.today": "today {time}",
    "thirst.when.tomorrow": "tomorrow {time}",
    "thirst.when.day": "on {date} {time}",
    "thirst.when.today_part": "today, {part}",
    "thirst.when.tomorrow_part": "tomorrow, {part}",
    "thirst.when.day_part": "{weekday}, {date}, {part}",
    "thirst.part.morning": "in the morning",
    "thirst.part.midday": "around midday",
    "thirst.part.afternoon": "in the afternoon",
    "thirst.part.evening": "in the evening",
    "thirst.part.night": "at night",
    "thirst.weekday.0": "Monday",
    "thirst.weekday.1": "Tuesday",
    "thirst.weekday.2": "Wednesday",
    "thirst.weekday.3": "Thursday",
    "thirst.weekday.4": "Friday",
    "thirst.weekday.5": "Saturday",
    "thirst.weekday.6": "Sunday",
    "thirst.block.tank_dry": "the water sensor reports dry",
    "thirst.block.tank_settle": "the tank has been wet again for only {min} min, it goes on after 30 min",
    "thirst.block.tank_unknown": "the tank reports no fresh value",
    "thirst.block.stale": "the readings are older than 10 min",
    "thirst.block.offline": "the box does not report",
    "thirst.block.gaps": "only {pct}% of the last 24 h are observed",
    "thirst.block.interval": "last watered {h} h ago, at least 20 h apart",
    "thirst.block.settle": "just watered, let it drain for 2 h first",
    "thirst.block.today": "already watered today",
    "thirst.block.week": "the weekly limit of {limit} is reached",
    "thirst.block.caps": "the limit for 24 h is reached",
    "thirst.block.pump_running": "{port} reports power: the pump may be running",
    "thirst.block.app_plan": "a plan or an automation of the VIVOSUN app runs on {port}",
    "thirst.block.ports": "the port assignment has changed and is not confirmed",
    "thirst.block.stage": "the stage is not confirmed",
    "thirst.block.wet_hold": "too wet since {since}",
    "thirst.block.wet": "finger test wet, 24 h pause",
    "thirst.why.measured_weigh": "Weighed {h} h ago.",
    "thirst.why.measured_finger": "Finger test {h} h ago: {answer}.",
    "thirst.why.model": "Only modelled, last check {h} h ago.",
    "thirst.why.model_weigh": "Only modelled, last weighed {h} h ago.",
    "thirst.why.model_never": "Only modelled, no check yet.",
    "thirst.why.band": "Uncertainty plus or minus {band} points ({confidence}).",
    "thirst.why.first_cycle": "No reference weight yet: the first dose is at most half the trigger amount.",
    "thirst.why.half_dose": "After a hard dry-down only half the dose.",
    "thirst.why.saucer_less": "Water stood in the saucer after the last watering: 25% less this time.",
    "thirst.why.wick": "With a wick, water comes in uncounted: the thirst index is uncertain.",
    "thirst.why.unknown_amount": "The pump ran with an unknown amount: the model counts the pot as full.",
    "thirst.why.unverified": "USB 1 had power, whether water flowed is not confirmed: the check before no longer counts.",
    "thirst.why.stage_contested": "Stage contested: please confirm the stage.",
    "thirst.confidence.high": "high",
    "thirst.confidence.medium": "medium",
    "thirst.confidence.low": "low",
    "thirst.answer.dry": "dry",
    "thirst.answer.moist": "moist",
    "thirst.answer.wet": "wet",
    "thirst.answer.light": "light",
    "thirst.answer.medium": "medium",
    "thirst.answer.heavy": "heavy",
    "thirst.check.finger": "Finger {depth} cm deep: dry, moist or wet? Tap it on the thirst index card.",
    "thirst.check.lift": "Lift the pot: light, medium or heavy? Tap it on the thirst index card.",
    "thirst.check.hold": "Finger 5 cm deep: dry, moist or wet? Tap it on the thirst index card.",
    "thirst.check.pot": "Tap Set up the pot on the thirst index card under Automation.",
    "thirst.origin.hand": "by hand",
    "thirst.origin.feed": "with feed",
    "thirst.origin.schedule": "watering schedule",
    "thirst.origin.once": "Water once",
    "thirst.origin.app": "app plan",
    "thirst.origin.other": "without a timer",
    "thirst.origin.owner": "you",
    "thirst.event.water": "{litres} watered, {origin}",
    "thirst.event.water_pump": "{port} ran about {minutes} min, {origin}: counted {litres}",
    "thirst.event.water_unknown": "{port} ran about {minutes} min, {origin}, unknown amount",
    "thirst.event.arrived_no": "Water did not arrive",
    "thirst.event.check": "Finger test {depth} cm: {answer}",
    "thirst.event.lift": "Pot lifted: {answer}",
    "thirst.event.sensor": "Soil sensor: {answer}",
    "thirst.measured.sensor": "soil sensor dry",
    "thirst.event.weigh": "Weighed: {kg}",
    "thirst.event.weigh_full": "Weighed full and drained: {kg}",
    "thirst.event.saucer_water": "Water in the saucer",
    "thirst.event.saucer_dry": "Saucer dry",
    "thirst.event.refill": "Tank refilled: {litres}",
    "thirst.event.refill_feed": "Tank refilled: {litres}, with feed",
    "thirst.event.pot": "Pot set up: {litres}",
    "thirst.event.pump_seen_device": "{port} ran about {minutes} min, pump not confirmed: not counted",
    "thirst.event.pump_seen_power": "{port} ran about {minutes} min, water flow not confirmed: not counted",
    "thirst.event.pump_seen_jug": "{port} ran about {minutes} min into the measuring jug: not counted",
    "thirst.event.flow": "Flow measured: {ml} in {seconds} s",
    "thirst.event.flow_reset": "Flow measurements discarded",
    "thirst.push.title": "Thirst index",
    "thirst.push.ask": "Thirst index {ti}%. No answer for {h} h: please check, {check}",
    "thirst.push.high": "Thirst index {ti}%. The pot needs you. I do not water on an estimate.",
    "thirst.push.blocked": "Due and blocked for {h} h: {reasons}.",
    "thirst.push.wet": "The pot is still wet {h} h after the overshoot. Please weigh it and look at the roots.",
    "thirst.gate.no_pot": "Pot not set up: need unknown.",
    "thirst.gate.unknown": "No thirst index yet: check first.",
    "thirst.gate.unmeasured": "Thirst index {ti}%, only modelled: no check in the last 24 h.",
    "thirst.gate.hold": "Too wet since {since}: thirst index {ti}%, no water.",
    "thirst.gate.not_due": "Pot not due yet: thirst index {ti}%.",
    "thirst.gate.blocked": "Blocked: {reasons}.",
    "thirst.gate.cap": "The limit for 24 h or for the week is reached.",
    "thirst.gate.too_short": "The allowed amount is too small for a run: {litres}.",
    "thirst.gate.run_short": "Thirst index {ti}%, measured: shortened to {seconds} s, about {litres} (limited by "
                             "{limit}).",
    "thirst.gate.run_full": "Thirst index {ti}%, measured: {seconds} s, about {litres}, under the limit of {limit}.",
    "thirst.gate.no_flow": "Slot skipped: first calibrate the flow with two measuring-jug runs.",
    "thirst.gate.no_power": "Slot skipped: first confirm that the USB port switches water flow on and off.",
    "thirst.gate.error": "Need check not possible: the slot is skipped.",
    "thirst.gate.preview_run": "Next watering slot {time}: may run while the pot stays measured due.",
    "thirst.gate.preview_skip": "Next watering slot {time}: skipped unless something changes by then. {reason}",
    "thirst.limit.deficit": "the pot's deficit",
    "thirst.limit.stage": "the stage maximum",
    "thirst.limit.aw": "the maximum for this pot",
    "thirst.limit.first_cycle": "the first dose without a reference weight",
    "thirst.limit.day": "the 24 h limit",
    "thirst.limit.week": "the weekly limit",
    "thirst.limit.half": "half the dose after a hard dry-down",
    "thirst.manual.no_pot": "No thirst index without a pot set up: Water once waters without the need check.",
    "thirst.manual.unknown": "No thirst index yet: check first. Water once waters without the need check.",
    "thirst.manual.due": "Thirst index {ti}%, measured: the pot is due.",
    "thirst.manual.unmeasured": "Thirst index {ti}%, only modelled: check first. Water once waters without the need "
                                "check.",
    "thirst.manual.not_due": "Thirst index {ti}%: the pot is not due. Water once waters anyway, without the need check.",
    "thirst.manual.hold": "Too wet, thirst index {ti}%: please do not water. Water once waters anyway.",
    "thirst.forecast.due": "Next watering expected {when}, about {litres}.",
    "thirst.forecast.due_round": "Due about {due}. Then water at the next round, {when}, about {litres}.",
    "thirst.forecast.now": "By the model the pot is due now, about {litres}.",
    "thirst.forecast.measured": "The pot is measured due: now about {litres}.",
    "thirst.forecast.range": "Uncertain: at the earliest {early}, at the latest {late}.",
    "thirst.forecast.range_open": "Uncertain: at the earliest {early}, the latest is open.",
    "thirst.forecast.beyond": "No watering in the next {days} days: by the model the pot uses very little right now.",
    "thirst.forecast.hold": "First the pot has to dry: the thirst index from the overshoot reaches 100 about {when}.",
    "thirst.forecast.check": "Check before: {check}",
    "thirst.forecast.slot": "Matching watering slot: {slot}.",
    "thirst.forecast.hand": "Without a watering slot: water by hand.",
    "thirst.forecast.none": "No anchor yet: check first, then I will forecast.",
    "thirst.forecast.basis": "Basis: {basis}.",
    "thirst.basis.pot": "pot {litres} (entered)",
    "thirst.basis.stage_set": "{stage}, set by you",
    "thirst.basis.stage_estimated": "{stage}, estimated, not confirmed",
    "thirst.basis.light_timer": "light timer {on} to {off}, {level} (set)",
    "thirst.basis.light_held": "light {level} around the clock (measured, no light timer)",
    "thirst.basis.light_unknown": "light unknown (estimated as last seen)",
    "thirst.basis.vpd": "VPD {vpd} (probe, measured)",
    "thirst.basis.vpd_unknown": "VPD unknown (computed with 1.2 kPa)",
    "thirst.basis.height": "plant {cm} (measured on {date})",
    "thirst.basis.height_estimated": "plant {cm} (ruler estimate of {date})",
    "thirst.basis.height_none": "plant height unknown (computed with 61 cm)",
    "thirst.basis.aw": "water the soil holds: 30% of the pot (assumption until the first weighing)",
    "thirst.basis.k0": "use: start value 20 ml per hour (assumption until the first weighing)",
    "thirst.basis.stages": "stage changes by the days of the stage card from {date} (estimated)",
    "thirst.basis.vpd_later": "VPD of later weeks: middle of the stage card's band (estimated)",
    "thirst.basis.future": "Later weeks assume today's height and light plan. Growth and changed settings can alter "
                            "demand; this is not a fixed watering instruction.",
    "thirst.flow.unusual": "Unusual flow: check the seconds and millilitres, then confirm the measurement again.",
    "thirst.flow.confirm": "This gives {rate}. Please check the seconds and millilitres. If the measured flow is "
                           "correct, confirm the same values again.",
    "thirst.flow.none": "Flow not measured: pump runs count with an unknown amount.",
    "thirst.flow.one": "One flow measurement: {rate}. One more for the calibration.",
    "thirst.flow.far": "The two measurements differ by more than 10% ({a} and {b}): measure again.",
    "thirst.flow.ok": "Flow {rate}, measured on {date} (two runs, valid until {until}).",
    "thirst.flow.power_no": "Pump switch not confirmed (watering schedule settings): USB power does not count as water.",
    "thirst.pump.on": "{port} has reported power for {h} h. Power is not water flow: counted is {how}.",
    "thirst.pump.how_nothing": "nothing, as long as the pump switch is not confirmed",
    "thirst.pump.how_unknown": "an unknown amount, until the flow is measured",
    "thirst.pump.how_litres": "the run time times the measured flow",
    "thirst.error.no_pot": "Set up the pot first",
    "thirst.error.pot_size": "Pot volume between 0.5 and 100 litres, or diameter and height between 5 and 80 cm",
    "thirst.error.pot_mismatch": "Entered {typed}, from diameter and height about {geo}: more than 20% apart. Is the "
                                 "volume right? Then tap once more.",
    "thirst.error.pot_reset": "A new pot resets the ledger. Tap once more.",
    "thirst.error.step": "Scale step between 1 and 100 g",
    "thirst.error.kg": "Weight between 0.2 and 100 kg",
    "thirst.error.answer": "Unknown answer {answer}",
    "thirst.error.liters": "Litres between {min} and {max}",
    "thirst.error.liters_large": "{liters} is a lot for a pot: tap once more if it is right",
    "thirst.error.time": "Time not readable or in the future",
    "thirst.error.time_old": "At most 7 days back, and not before the pot was set up",
    "thirst.error.event": "This entry does not exist",
    "thirst.error.refill": "Litres for the tank between 0.1 and 10",
    "thirst.error.flow": "Flow test: seconds between 5 and 3600, millilitres between 5 and 5000",
    "thirst.none": "No thirst index ledger set up",
}

EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself


def check_catalog() -> list[str]:
    """Problems of the two catalogs: missing keys, different placeholders, a {lang} placeholder, an em dash."""
    problems = [f"only in DE: {k}" for k in sorted(set(DE) - set(EN))]
    problems += [f"only in EN: {k}" for k in sorted(set(EN) - set(DE))]
    for key in sorted(set(DE) & set(EN)):
        if i18n.placeholders(DE[key]) != i18n.placeholders(EN[key]):
            problems.append(f"placeholders differ: {key}")
        if "lang" in i18n.placeholders(DE[key]):
            problems.append(f"placeholder lang: {key}")
        if EM_DASH in DE[key] + EN[key]:
            problems.append(f"em dash: {key}")
    return problems


def register() -> None:
    """Put the texts into the server catalogs (i18n.DE, i18n.EN). Safe to call more than once."""
    problems = check_catalog()
    if problems:
        raise ValueError("thirst: " + "; ".join(problems))
    i18n.DE.update(DE)
    i18n.EN.update(EN)


register()


# ---------- the model file ----------

class ModelError(RuntimeError):
    """kartei/water_model.json is missing or broken: no thirst index, every suggestion blocked, every slot skipped."""


class Model:
    """The constants of kartei/water_model.json by name; model("f_trig") is its value."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        constants = data.get("constants")
        if not isinstance(constants, Mapping) or not constants:
            raise ModelError("no constants")
        self.data = data
        self.constants = constants

    def __call__(self, name: str) -> Any:
        try:
            return self.constants[name]["value"]
        except (KeyError, TypeError) as err:
            raise ModelError(f"constant {name} missing") from err


_model: tuple[tuple[int, int] | None, Model | None] = (None, None)


def load_model(path: Path | None = None) -> Model:
    """The model, read again when the file changed (size and mtime). ModelError when it cannot be read."""
    global _model
    path = path or MODEL_FILE
    try:
        stat = path.stat()
    except OSError as err:
        raise ModelError(str(err)) from err
    key = (stat.st_size, stat.st_mtime_ns)
    if path == MODEL_FILE and _model[0] == key and _model[1] is not None:
        return _model[1]
    try:
        model = Model(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ModelError) as err:
        raise ModelError(str(err)) from err
    if path == MODEL_FILE:
        _model = (key, model)
    return model


# ---------- small helpers ----------

def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _parse_float(value: Any) -> float | None:
    """A form value as a number: empty means none, a decimal comma is fine. UserError for anything else."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise UserError(t("plant.not_a_number", value=value))
    if isinstance(value, int | float):
        number = float(value)
    else:
        try:
            number = float(str(value).strip().replace(",", "."))
        except ValueError:
            raise UserError(t("plant.not_a_number", value=value)) from None
    if not math.isfinite(number):
        raise UserError(t("plant.not_a_number", value=value))
    return number


def _flag(value: Any) -> bool | None:
    """yes/no/unknown from the page: True, False or None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "ja", "y", "j"):
        return True
    if text in ("0", "false", "no", "nein", "n"):
        return False
    return None


def litres_text(litres: float, lang: str | None = None) -> str:
    """0,35 l or 1,4 l: litres with up to two decimals."""
    digits = 2 if abs(litres) < 1 else 1
    return fmt_qty(round(litres, digits), "l", lang)


def hours_text(seconds: float, lang: str | None = None) -> str:
    """Whole hours, or one decimal below 10 h."""
    hours = max(0.0, seconds / 3600)
    return fmt_num(round(hours, 1) if hours < 10 else round(hours), lang)


def ti_text(ti: float, lang: str | None = None) -> str:
    return fmt_num(int(round(ti)), lang)


def local_dt(epoch: float, tz: tzinfo | None) -> datetime:
    return datetime.fromtimestamp(epoch, tz=tz or ZONE)


def local_day(epoch: float, tz: tzinfo | None = None) -> date:
    """The calendar day of an epoch second in the box's zone."""
    return local_dt(epoch, tz).date()


def at_local(day: date, hour: int, minute: int, tz: tzinfo | None = None) -> float:
    """Epoch seconds of a wall-clock time on a local day: 09:00 on 25.10.2026 is 08:00 UTC, on 24.10. 07:00 UTC.
    A time the spring change skips moves on with the clock; at the autumn change the first occurrence counts."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz or ZONE, fold=0).timestamp()


def clock_text(epoch: float, lang: str, tz: tzinfo | None = None) -> str:
    when = local_dt(epoch, tz)
    if lang == "de":
        return f"{when:%H:%M}"
    return f"{when.hour % 12 or 12}:{when:%M} {'PM' if when.hour >= 12 else 'AM'}"


MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day_text(day: date, lang: str) -> str:
    return f"{day.day}.{day.month}." if lang == "de" else f"{MONTHS_EN[day.month - 1]} {day.day}"


def when_text(epoch: float, now: float, lang: str | None = None, tz: tzinfo | None = None) -> str:
    """heute 09:00, morgen 09:00, am 26.10. 09:00."""
    lang = i18n.normalize(lang) or i18n.current_lang()
    clock = clock_text(epoch, lang, tz)
    day, today = local_day(epoch, tz), local_day(now, tz)
    if day == today:
        return t("thirst.when.today", lang, time=clock)
    if day == today + timedelta(days=1):
        return t("thirst.when.tomorrow", lang, time=clock)
    return t("thirst.when.day", lang, date=day_text(day, lang), time=clock)


def day_part(epoch: float, tz: tzinfo | None = None) -> str:
    hour = local_dt(epoch, tz).hour
    part = "night"
    for start, name in DAY_PARTS:
        if hour >= start:
            part = name
    return part


def part_text(epoch: float, now: float, lang: str | None = None, tz: tzinfo | None = None) -> str:
    """When a forecast lands, as a farmer says it: heute, nachmittags; Mittwoch, 28.10., morgens."""
    lang = i18n.normalize(lang) or i18n.current_lang()
    part = t(f"thirst.part.{day_part(epoch, tz)}", lang)
    day, today = local_day(epoch, tz), local_day(now, tz)
    if day == today:
        return t("thirst.when.today_part", lang, part=part)
    if day == today + timedelta(days=1):
        return t("thirst.when.tomorrow_part", lang, part=part)
    return t("thirst.when.day_part", lang, weekday=t(f"thirst.weekday.{day.weekday()}", lang),
             date=day_text(day, lang), part=part)


def both(fn: Callable[[str], str]) -> dict[str, str]:
    return {lang: fn(lang) for lang in ("de", "en")}


def port_name(port: str) -> str:
    return str(port).replace("usb", "USB ")


def pump_ports(snap: Mapping[str, Any], guard: PortGuard | None = None) -> list[str]:
    """The ports a pump hangs on: ports.json says "pump", or the owner named the pump there on the page."""
    ports = snap.get("ports") if isinstance(snap.get("ports"), Mapping) else {}
    owner = guard.devices() if guard is not None else {}
    return sorted(p for p in (ports or {}) if configured_device(p) == PUMP or owner.get(p) == PUMP)


# ---------- the pot ----------

@dataclass
class Pot:
    volume_l: float | None
    diameter_cm: float | None = None
    height_cm: float | None = None
    drain: bool | None = None
    saucer: bool | None = None
    feet: bool | None = None
    wick: bool | None = None
    lifts: bool | None = None
    scale: bool | None = None
    scale_step_g: float | None = None
    dryback: bool | None = None  # late flower: the owner's dry-back (f_trig_late_dryback)
    substrate: str = "soil"
    set_at: float = 0.0
    id: str = ""

    def geometry_l(self) -> float | None:
        """A cylinder of the entered diameter and height (a tapered pot holds less: an upper bound)."""
        if self.diameter_cm is None or self.height_cm is None:
            return None
        return math.pi * (self.diameter_cm / 2) ** 2 * self.height_cm / 1000

    def volume(self) -> float | None:
        return self.volume_l if self.volume_l is not None else self.geometry_l()

    def depth_cm(self, model: Model) -> float:
        depths = model("finger_depth_cm")
        if self.diameter_cm is None:
            return float(depths["unknown"])
        small = self.diameter_cm <= model("finger_depth_small_max_diameter_cm")
        return float(depths["small"] if small else depths["large"])

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Pot | None:
        try:
            pot = cls(**{k: data.get(k) for k in cls.__dataclass_fields__ if k in data})
        except TypeError:
            return None
        if _num(pot.set_at) is None or pot.volume() is None:
            return None
        return pot


# ---------- the ET series ----------

class Series:
    """The modelled evapotranspiration as a cumulative series: points [epoch, litres since the series began], at
    least SERIES_STEP_S apart, the head (the newest poll's value) kept apart, plus the gaps: stretches where the inputs
    were held longer than hold_input_s, or no poll came at all. between() integrates over epoch seconds, so a 25 h or a
    23 h day is simply 25 or 23 hours (the DST change of 25.10.2026 is no jump)."""

    def __init__(self, points: Iterable[Any] = (), gaps: Iterable[Any] = (), head: Any = None,
                 rate_l_s: float | None = None) -> None:
        self.points: list[tuple[float, float]] = sorted(
            (float(p[0]), float(p[1])) for p in points
            if isinstance(p, list | tuple) and len(p) == 2 and _num(p[0]) is not None and _num(p[1]) is not None)
        self.gaps: list[tuple[float, float]] = [
            (float(g[0]), float(g[1])) for g in gaps
            if isinstance(g, list | tuple) and len(g) == 2 and _num(g[0]) is not None and _num(g[1]) is not None
            and g[1] > g[0]]
        self.head: tuple[float, float] | None = None
        if isinstance(head, list | tuple) and len(head) == 2 and _num(head[0]) is not None and _num(head[1]) is not None:
            self.head = (float(head[0]), float(head[1]))
        self.rate = rate_l_s if rate_l_s is not None and rate_l_s >= 0 else None

    def latest(self) -> tuple[float, float] | None:
        last = self.points[-1] if self.points else None
        if self.head is not None and (last is None or self.head[0] >= last[0]):
            return self.head
        return last

    def _all(self) -> list[tuple[float, float]]:
        pts = list(self.points)
        if self.head is not None and (not pts or self.head[0] > pts[-1][0]):
            pts.append(self.head)
        return pts

    def start(self) -> float | None:
        pts = self._all()
        return pts[0][0] if pts else None

    def first_rate(self) -> float | None:
        pts = self._all()
        for (a, x), (b, y) in zip(pts, pts[1:], strict=False):
            if b > a:
                return max(0.0, (y - x) / (b - a))
        return self.rate

    def cum(self, at: float) -> float:
        """The cumulative litres at `at` (held at the first rate before the series, at the last rate after it)."""
        pts = self._all()
        if not pts:
            return 0.0
        if at <= pts[0][0]:
            return pts[0][1] - (self.first_rate() or 0.0) * (pts[0][0] - at)
        if at >= pts[-1][0]:
            rate = self.rate if self.rate is not None else (self.first_rate() or 0.0)
            return pts[-1][1] + rate * (at - pts[-1][0])
        lo, hi = 0, len(pts) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if pts[mid][0] <= at:
                lo = mid
            else:
                hi = mid
        (a, x), (b, y) = pts[lo], pts[hi]
        return x + (y - x) * (at - a) / (b - a)

    def between(self, start: float, end: float) -> tuple[float, float]:
        """Litres of modelled ET from start to end, and the seconds of that span that were gaps or extrapolated."""
        if end <= start:
            return 0.0, 0.0
        extra = 0.0
        pts = self._all()
        if pts:
            lo, hi = pts[0][0], pts[-1][0]
            extra += max(0.0, min(end, lo) - start) + max(0.0, end - max(start, hi))
        gap = sum(max(0.0, min(end, b) - max(start, a)) for a, b in self.gaps)
        return max(0.0, self.cum(end) - self.cum(start)), min(end - start, gap + extra)

    def coverage(self, now: float, span: float = 86400.0) -> float | None:
        """The observed share of the last `span` seconds (clipped to the series' life). None below one hour of life."""
        first = self.start()
        if first is None or now - first < 3600:
            return None
        window = min(span, now - first)
        _, gap = self.between(now - window, now)
        return max(0.0, 1.0 - gap / window)

    def add(self, at: float, litres: float, rate_l_s: float, gap_from: float | None = None) -> None:
        """One poll: the new cumulative value at `at`. A point is kept every SERIES_STEP_S; in between only the head
        moves. gap_from: the inputs were not fresh from then on (a gap up to `at`)."""
        last = self.latest()
        value = (last[1] if last else 0.0) + max(0.0, litres)
        self.rate = max(0.0, rate_l_s)
        if not self.points or at - self.points[-1][0] >= SERIES_STEP_S:
            self.points.append((at, value))
            self.head = None
        else:
            self.head = (at, value)
        if gap_from is not None and at > gap_from:
            if self.gaps and self.gaps[-1][1] >= gap_from - 1:
                self.gaps[-1] = (self.gaps[-1][0], max(self.gaps[-1][1], at))
            else:
                self.gaps.append((gap_from, at))
        cut = at - SERIES_KEEP_S
        if self.points and self.points[0][0] < cut:
            self.points = [p for p in self.points if p[0] >= cut] or self.points[-1:]
        self.gaps = [g for g in self.gaps if g[1] >= cut][-GAPS_KEEP:]

    def dump(self) -> dict[str, Any]:
        return {"points": [[round(a, 1), round(b, 6)] for a, b in self.points],
                "head": [round(self.head[0], 1), round(self.head[1], 6)] if self.head else None,
                "gaps": [[round(a, 1), round(b, 1)] for a, b in self.gaps],
                "rate_l_s": self.rate}


def constant_series(rate_l_s: float, start: float, now: float) -> Series:
    """A series at one constant rate from start to now (the demo's scenes and the eval fixture): made-up history."""
    series = Series()
    series.add(start, 0.0, rate_l_s)
    stamp = start
    while stamp + SERIES_STEP_S < now:
        stamp += SERIES_STEP_S
        series.add(stamp, rate_l_s * SERIES_STEP_S, rate_l_s)
    series.add(now, rate_l_s * (now - stamp), rate_l_s)
    return series


def tetens_vpd(temp: float, humidity: float) -> float:
    """Air VPD in kPa from temperature and relative humidity (FAO-56 es(T))."""
    return 0.6108 * math.exp(17.27 * temp / (temp + 237.3)) * (1 - humidity / 100)


def _et_constants(model: Model) -> tuple[float, ...]:
    """The ET model's constants once per model (the forecast steps it thousands of times)."""
    cached = getattr(model, "_et", None)
    if cached is None:
        s_lo, s_hi = model("height_factor_clip")
        cached = (float(model("light_weight_w")), float(model("vpd_norm_kpa")), float(model("vpd_min_kpa")),
                  float(model("vpd_max_kpa")), float(model("height_ref_cm")), float(s_lo), float(s_hi),
                  float(model("k0_ml_h")), float(model("night_factor")))
        model._et = cached  # type: ignore[attr-defined]
    return cached


def et_rate_ml_h(model: Model, *, light: float | None, vpd: float | None, height_cm: float | None,
                 k_ml_h: float | None = None, last_lit: float | None = None) -> float:
    """ET_h = K x S x shape. light: the lamp in percent (0 = off); vpd: the probe VPD in kPa, None when not known
    (then the normalising 1.2 kPa, the shape's own middle); height: the latest plant height; last_lit: the last lit
    level in percent, for the night term."""
    w, norm, lo, hi, ref, s_lo, s_hi, k0, night = _et_constants(model)
    v = norm if vpd is None else min(hi, max(lo, vpd))
    s = 1.0 if height_cm is None or height_cm <= 0 else min(s_hi, max(s_lo, height_cm / ref))
    k = k0 if k_ml_h is None else k_ml_h
    if light is not None and light > 0:
        shape = w * light / 100 + (1 - w) * v / norm
    else:
        lit = (last_lit if last_lit is not None and last_lit > 0 else 100.0) / 100
        shape = night * (w * lit + (1 - w) * v / norm)
    return k * s * shape


def _run_bounds(first_on: float, last_on: float, start_lo: float | None, off_at: float) -> dict[str, float]:
    """A run's length: at least from its first to its last report seen on, at most from the last report seen off before
    it to the first report seen off after it (one poll wide when no off report is known)."""
    lo = start_lo if start_lo is not None and start_lo <= first_on else first_on - POLL_EDGE_S
    least, most = max(0.0, last_on - first_on), max(0.0, off_at - lo)
    return {"least_s": least, "most_s": most, "minutes": round((least + most) / 2 / 60, 1),
            "minutes_lo": round(least / 60, 1), "minutes_hi": round(most / 60, 1)}


# ---------- the ledger: a fold over the inputs ----------

@dataclass
class Ledger:
    pot: Pot | None
    v_pot_l: float | None = None
    aw_l: float | None = None
    d_trig_l: float | None = None
    f_trig: float | None = None
    d_l: float | None = None
    ti: float | None = None
    anchor: dict[str, Any] | None = None
    measured: dict[str, Any] | None = None
    last_check: dict[str, Any] | None = None
    last_weigh: float | None = None  # when the pot was last weighed (full or not), for "zuletzt gewogen"
    band: float | None = None
    confidence: str = "low"
    w_ref: float | None = None
    overshoot: dict[str, Any] | None = None
    hold: dict[str, Any] | None = None
    moist_until: float | None = None
    wet_until: float | None = None
    dry_below: dict[str, Any] | None = None
    drains_without: int = 0
    saucer_less: bool = False
    hard_drydown: bool = False
    first_cycle: bool = True
    unknown_amount: bool = False
    unverified: dict[str, Any] | None = None
    waterings: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    gap_s: float = 0.0
    rate_l_day: float | None = None
    et_week_l: float | None = None
    model_error: str | None = None

    def ti_of(self, d: float | None) -> float | None:
        if d is None or not self.d_trig_l:
            return None
        return max(0.0, min(150.0, 100 * d / self.d_trig_l))


def f_trig_for(model: Model, stage_key: str | None, pot: Pot | None) -> float:
    if stage_key == "late_flower" and pot is not None and pot.dryback:
        return float(model("f_trig_late_dryback"))
    table = model("stage_f_trig")
    if isinstance(table, Mapping) and stage_key in table:
        return float(table[stage_key])
    return float(model("f_trig"))


def _step_down(model: Model, volume: float) -> float:
    """One pot size smaller: the largest step clearly below the entered litres. A step within pot_step_margin of them
    is the same pot (3 US gallons are 11.4 l, the step 11 l), so 11.4 l steps down to 7 l, not to 11 l."""
    limit = volume * (1 - float(model("pot_step_margin")))
    steps = [float(s) for s in model("pot_steps_l") if float(s) < limit]
    return steps[-1] if steps else volume


def fold(pot: Pot | None, events: Iterable[Mapping[str, Any]], series: Series, now: float, model: Model, *,
         stage_key: str | None = None) -> Ledger:
    """The ledger at `now` from the pot, the inputs (the log plus the feeding log's waterings) and the ET series.

    Pure: the same inputs give the same ledger, so a replay or a test can feed anything. Events before the pot was set
    up belong to an earlier pot and are skipped; events after `now` are not there yet."""
    ledger = Ledger(pot=pot)
    if pot is None:
        return ledger
    volume = pot.volume()
    if volume is None or volume <= 0:
        return ledger
    a_w = float(model("a_w"))
    f = f_trig_for(model, stage_key, pot)
    ledger.f_trig = f

    def set_pot(v: float) -> None:
        ledger.v_pot_l = v
        ledger.aw_l = a_w * v
        ledger.d_trig_l = f * ledger.aw_l

    set_pot(volume)
    day = float(model("measured_valid_s"))
    moist_cap = float(model("finger_moist_cap_ti")) / 100
    hold_depth = float(model("hold_depth_cm"))
    finger_band = float(model("band_finger_ti"))
    unknown_band = float(model("band_unknown_amount_ti"))
    rows = sorted((dict(e) for e in events if isinstance(e, Mapping) and _num(e.get("at")) is not None),
                  key=lambda e: (float(e["at"]), str(e.get("id") or "")))
    rows = [e for e in rows if pot.set_at <= float(e["at"]) <= now]
    # The arrival answers of pump runs and waterings belong to their event: "Nein" makes its water 0, "Wasser im
    # Untersetzer" is the saucer answer at the time it was given.
    arrived: dict[str, str] = {}
    for e in rows:
        if e.get("kind") == "arrived" and isinstance(e.get("event"), str) and e.get("answer") in ARRIVED_ANSWERS:
            arrived[e["event"]] = e["answer"]
    state: dict[str, Any] = {"t": pot.set_at, "d": None, "anchor_at": pot.set_at, "anchor_base": None,
                             "full_at": None, "n_water": 0}
    shown: list[dict[str, Any]] = []

    def advance(to: float) -> None:
        start = state["t"]
        if to <= start:
            return
        litres, gap = series.between(start, to)
        if state["d"] is not None:
            cap_until = ledger.moist_until
            if cap_until is not None and start < cap_until:
                mid = min(to, cap_until)
                first, _ = series.between(start, mid)
                state["d"] = min(state["d"] + first, moist_cap * (ledger.d_trig_l or 0))
                if to > mid:
                    rest, _ = series.between(mid, to)
                    state["d"] += rest
            else:
                state["d"] += litres
            ledger.gap_s += gap
        if ledger.hold is not None:
            ledger.hold["d_l"] += litres
        state["t"] = to

    def anchor(kind: str, at: float, base: float | None = None) -> None:
        ledger.anchor = {"kind": kind, "at": at}
        state["anchor_at"] = at
        state["anchor_base"] = base
        ledger.gap_s = 0.0
        ledger.unverified = None

    def start_hold(at: float) -> None:
        ledger.hold = {"since": at, "d_l": 0.0, "dry": [], "weighed": False, "wet_at": None}

    def lift_hold() -> None:
        hold = ledger.hold
        if hold is None or ledger.d_trig_l is None:
            return
        if hold["d_l"] < ledger.d_trig_l:
            return
        dry = sorted(hold["dry"])
        twice = len(dry) >= int(model("hold_dry_answers")) and dry[-1] - dry[0] >= float(model("hold_dry_apart_s"))
        if hold["weighed"] or twice:
            ledger.hold = None
            if twice and not hold["weighed"]:
                # the confirming answer is the measurement: the index is the hold's model value from now on
                ledger.measured = {"kind": "finger", "at": dry[-1], "answer": "dry"}
                state["d"] = max(state["d"] or 0.0, hold["d_l"])

    for e in rows:
        at = float(e["at"])
        kind = e.get("kind")
        advance(at)
        if kind == "water":
            litres = _num(e.get("litres"))
            answer = arrived.get(str(e.get("id"))) if e.get("id") else None
            if answer == "no":
                litres = 0.0
            entry = {"at": at, "litres": litres, "origin": e.get("origin") or "hand", "id": e.get("id"),
                     "pump": bool(e.get("port"))}
            ledger.waterings.append(entry)
            if litres is None or litres > 0:
                state["n_water"] += 1
                ledger.saucer_less = False  # the smaller dose after water in the saucer and the half dose after a
                ledger.hard_drydown = False  # hard dry-down were for this watering; the next one is judged anew
            if litres is None:  # a pump run of unknown amount: counted as full, the side that never adds water
                state["d"] = 0.0
                anchor("unknown_amount", at, unknown_band)
                ledger.overshoot = {"unknown": True, "at": at}
                ledger.unknown_amount = True
                ledger.measured = None
                start_hold(at)
            elif litres > 0:
                if state["d"] is not None:
                    excess = max(0.0, litres - state["d"])
                    state["d"] = max(0.0, state["d"] - litres)
                    if excess > 0.0005:
                        ledger.overshoot = {"ml": excess * 1000, "at": at}
                        start_hold(at)
                else:
                    # water on a pot without an anchor: counted as full, wide band (review: every event re-anchors)
                    state["d"] = 0.0
                    anchor("water_unanchored", at, unknown_band)
                ledger.measured = None
                ledger.unknown_amount = False
                if ledger.hold is not None and at > ledger.hold["since"]:
                    ledger.hold["d_l"] = max(0.0, ledger.hold["d_l"] - litres)
            ledger.drains_without += 1 if litres is None or litres > 0 else 0
            if answer == "saucer":
                _saucer(ledger, state, at, model, start_hold, anchor, set_pot)
            shown.append({**entry, "kind": "water", "arrived": answer, "minutes": e.get("minutes"),
                          "port": e.get("port")})
        elif kind == "saucer":
            if e.get("water") is True:
                _saucer(ledger, state, at, model, start_hold, anchor, set_pot)
            shown.append({"at": at, "kind": "saucer", "water": e.get("water") is True, "origin": "owner"})
        elif kind == "weigh":
            kg = _num(e.get("kg"))
            if kg is None:
                continue
            if e.get("full"):
                ledger.w_ref = kg
                state["d"] = 0.0
                state["full_at"] = at
                anchor("weigh_full", at, None)
                ledger.measured = {"kind": "weigh", "at": at}
            elif ledger.w_ref is not None:
                state["d"] = max(0.0, ledger.w_ref - kg)
                anchor("weigh", at, None)
                ledger.measured = {"kind": "weigh", "at": at}
                # review S2: a hard dry-down halves the next dose whichever way it was measured
                ledger.hard_drydown = (ledger.ti_of(state["d"]) or 0.0) > float(model("hard_drydown_ti"))
                if ledger.hold is not None and ledger.d_trig_l is not None and state["d"] >= ledger.d_trig_l:
                    ledger.hold["weighed"] = True  # (b) of the wet-hold; (a), the model from the overshoot, stays
            elif state["full_at"] is not None and state["d"] is not None:
                # the pot was full at full_at (a saucer answer): this weight plus the modelled loss since is the
                # reference weight ("a weighing tonight plus the modelled ET since 16:33 gives W_ref")
                ledger.w_ref = kg + state["d"]
            ledger.last_weigh = at
            shown.append({"at": at, "kind": "weigh", "kg": kg, "full": bool(e.get("full")), "origin": "owner"})
        elif kind == "check":
            answer = e.get("answer")
            # hook: the soil sensor's check (sensor_check) counts like a finger at its depth, only its name differs
            how = "sensor" if e.get("how") == "sensor" else "lift" if answer in LIFT_ANSWERS else "finger"
            depth = _num(e.get("depth_cm")) or pot.depth_cm(model)
            ledger.last_check = {"at": at, "answer": answer, "how": how, "depth_cm": depth}
            # the sensor repeats itself every few hours: the card's short list shows its answer only when it changes
            if how != "sensor" or state.get("sensor_answer") != answer:
                shown.append({"at": at, "kind": "check", "answer": answer, "how": how, "depth_cm": depth,
                              "origin": "sensor" if how == "sensor" else "owner"})
            if how == "sensor":
                state["sensor_answer"] = answer
            dry, moist, wet = answer in ("dry", "light"), answer in ("moist", "medium"), answer in ("wet", "heavy")
            if ledger.hold is not None:
                # in a hold a finger answer can lower the index or block, never lift it; two dry answers at 5 cm
                # (12 h apart) are part (b) of lifting the hold, and they count only when given near it: from the
                # hold's own model index of hold_dry_from_ti on (the index the card asks at)
                near = (ledger.ti_of(ledger.hold["d_l"]) or 0.0) >= float(model("hold_dry_from_ti"))
                if dry and near and (how == "lift" or depth >= hold_depth):
                    ledger.hold["dry"].append(at)
                if moist or wet:
                    ledger.hold["dry"] = []
                if moist:
                    cap = moist_cap * (ledger.d_trig_l or 0)
                    state["d"] = cap if state["d"] is None else min(state["d"], cap)
                    ledger.moist_until = at + float(model("moist_cap_s"))
                if wet:
                    ledger.wet_until = at + float(model("wet_block_s"))
                    ledger.hold["wet_at"] = at
            elif dry:
                current = ledger.ti_of(state["d"])
                if current is None or current >= float(model("finger_dry_from_ti")):
                    ledger.hard_drydown = current is not None and current > float(model("hard_drydown_ti"))
                    state["d"] = float(model("finger_dry_sets_ti")) / 100 * (ledger.d_trig_l or 0)
                    anchor("finger", at, finger_band)
                    ledger.measured = {"kind": how, "at": at, "answer": answer}
                    ledger.dry_below = None
                else:
                    ledger.dry_below = {"at": at, "ti": current, "depth_cm": depth}
            elif moist:
                cap = moist_cap * (ledger.d_trig_l or 0)
                state["d"] = cap if state["d"] is None else min(state["d"], cap)
                ledger.moist_until = at + float(model("moist_cap_s"))
                anchor("finger", at, finger_band)
                ledger.measured = {"kind": how, "at": at, "answer": answer}
                ledger.dry_below = None
            elif wet:
                ledger.wet_until = at + float(model("wet_block_s"))
                ledger.measured = None
                ledger.dry_below = None
        elif kind == "refill":
            shown.append({"at": at, "kind": "refill", "litres": _num(e.get("litres")), "feed": bool(e.get("feed")),
                          "origin": "owner"})
        elif kind == "pot":
            shown.append({"at": at, "kind": "pot", "litres": _num(e.get("litres")), "origin": "owner"})
        elif kind == "pump_seen":
            if e.get("why") == "power":
                # USB power without a confirmed water switch: no water counted, but the check before may be stale
                ledger.unverified = {"at": at, "minutes": e.get("minutes")}
                ledger.measured = None
            shown.append({"at": at, "kind": "pump_seen", "port": e.get("port"), "minutes": e.get("minutes"),
                          "why": e.get("why") or "device", "origin": e.get("origin") or "other"})
        elif kind in ("flow", "flow_reset"):
            shown.append({"at": at, "kind": kind, "ml": _num(e.get("ml")), "seconds": _num(e.get("seconds")),
                          "origin": "owner"})
        lift_hold()
    advance(now)
    lift_hold()
    ledger.first_cycle = ledger.w_ref is None
    ledger.d_l = state["d"]
    ledger.ti = ledger.ti_of(state["d"])
    if ledger.measured is not None and now - float(ledger.measured["at"]) > day:
        ledger.measured = None
    if ledger.measured is not None and ledger.measured.get("kind") != "weigh" and ledger.hold is not None:
        ledger.measured = None  # outside a wet-hold only; in a hold only the scale measures
    if ledger.dry_below is not None and now - float(ledger.dry_below["at"]) > day:
        ledger.dry_below = None
    if ledger.moist_until is not None and now >= ledger.moist_until:
        ledger.moist_until = None
    if ledger.wet_until is not None and now >= ledger.wet_until:
        ledger.wet_until = None
    if ledger.ti is not None:
        base = state["anchor_base"] if state["anchor_base"] is not None else float(model("band_base_ti"))
        days = max(0.0, now - state["anchor_at"]) / 86400 + ledger.gap_s / 86400
        ledger.band = round(base + float(model("band_per_day_ti")) * days, 1)
        if pot.wick:
            ledger.band = max(ledger.band, float(model("confidence_medium_band")) + 1)
        if ledger.unverified is not None:
            ledger.band = max(ledger.band, unknown_band + 1)
        ledger.confidence = ("high" if ledger.band <= float(model("confidence_high_band"))
                             else "medium" if ledger.band <= float(model("confidence_medium_band")) else "low")
    if series.latest() is not None:
        ledger.et_week_l = series.between(now - 7 * 86400, now)[0]
        ledger.rate_l_day = series.between(now - 86400, now)[0] if now - (series.start() or now) >= 86400 else None
    ledger.events = shown[-EVENTS_SHOWN:][::-1]
    return ledger


def _saucer(ledger: Ledger, state: dict[str, Any], at: float, model: Model, start_hold: Callable[[float], None],
            anchor: Callable[..., None], set_pot: Callable[[float], None]) -> None:
    """"Wasser im Untersetzer": the pot ran over, so it is full now. An overshoot whose amount the model did not see
    is unknown; the wet-hold starts (or goes on), and the next dose is 25 % smaller. A saucer that fills on the first
    watering of a pot without a reference weight lowers the pot one step."""
    if state["n_water"] == 1 and ledger.w_ref is None and ledger.v_pot_l is not None and not state.get("stepped"):
        state["stepped"] = True
        set_pot(_step_down(model, ledger.v_pot_l))
    state["d"] = 0.0
    state["full_at"] = at
    computed = ledger.overshoot is not None and not ledger.overshoot.get("unknown") and \
        at - float(ledger.overshoot.get("at") or 0) <= float(model("drain_settle_s")) * 3
    if not computed:
        ledger.overshoot = {"unknown": True, "at": at}
    if ledger.hold is None or not computed:
        start_hold(at)
    anchor("full", at, None)
    ledger.drains_without = 0
    ledger.saucer_less = True
    ledger.measured = None


# ---------- blocks: the guardrails, and the only place that reads the tank ----------

def blocks(snap: Mapping[str, Any], ledger: Ledger, *, now: float, model: Model, stage: Mapping[str, Any] | None,
           pump: list[str], coverage: float | None, ports_status: str = "ok",
           tz: tzinfo | None = None) -> list[dict[str, Any]]:
    """Every guardrail that holds now, each as {"key", "params"}. Each can only block; none can trigger.
    The tank is read here and nowhere else in this module: a full tank is never a reason to water."""
    out: list[dict[str, Any]] = []

    def add(key: str, **params: Any) -> None:
        out.append({"key": key, "params": params})

    climate = snap.get("climate") if isinstance(snap.get("climate"), Mapping) else {}
    # the dry tank: at once, through box.watering_allowed, not through the 3 min alarm debounce; lifting needs a
    # fresh wet reading and 30 min of them
    if not box_mod.watering_allowed(snap):
        if not snap.get("online"):
            add("offline")
        elif climate.get("water_state") == "dry":
            add("tank_dry")
        else:
            add("tank_unknown")
    else:
        run = _num(climate.get("water_run_s"))
        since = _num(climate.get("water_since"))
        settle = float(model("tank_wet_settle_s"))
        wet_for = run if run is not None else (now - since if since is not None else None)
        if climate.get("water_state") == "wet" and wet_for is not None and wet_for < settle:
            add("tank_settle", min=int(wet_for // 60))
    # stale data
    age = _num(snap.get("sensors_age"))
    if snap.get("online") and (age is None or age > float(model("probe_stale_s"))):
        add("stale")
    if coverage is not None and coverage < float(model("coverage_min")):
        add("gaps", pct=int(round(coverage * 100)))
    # spacing, in epoch seconds
    given = [w for w in ledger.waterings if w.get("litres") is None or (w.get("litres") or 0) > 0]
    if given:
        last = max(float(w["at"]) for w in given)
        if now - last < float(model("settle_after_event_s")):
            add("settle")
        elif now - last < float(model("min_interval_s")):
            add("interval", h=hours_text(now - last))
        elif local_day(last, tz) == local_day(now, tz):
            add("today")  # one event per local day; the two above already say it when they hold
    week = weekly_cap_l(ledger, model)
    if week is not None and weekly_used_l(ledger, now) >= week:
        add("week", limit=litres_text(week))
    # the pump port: reporting power now, or in an app plan or automation
    ports = snap.get("ports") if isinstance(snap.get("ports"), Mapping) else {}
    for port in pump:
        report = (ports or {}).get(port) or {}
        if report.get("on"):
            add("pump_running", port=port_name(port))
        if report.get("in_plan") or report.get("can_write") is False:
            add("app_plan", port=port_name(port))
    if ports_status == "changed":
        add("ports")
    # the stage is the owner's
    if not stage or stage.get("set_by") != "manual":
        add("stage")
    # the wet-hold, and a wet finger answer
    if ledger.hold is not None:
        add("wet_hold", since=float(ledger.hold["since"]))
    if ledger.wet_until is not None and now < ledger.wet_until:
        add("wet")
    return out


def weekly_cap_l(ledger: Ledger, model: Model) -> float | None:
    """1.5 x the modelled ET of the last 7 days (the series, held at its first rate where it is younger), at least
    5 l; None without a series."""
    if ledger.et_week_l is None:
        return None
    return max(float(model("weekly_cap_min_l")), float(model("weekly_cap_factor")) * ledger.et_week_l)


def weekly_used_l(ledger: Ledger, now: float) -> float:
    """The known litres of the last 7 days (rolling, in epoch seconds); a run of unknown amount counts nothing here,
    it blocks by the spacing rules instead."""
    return sum((w.get("litres") or 0) for w in ledger.waterings if now - float(w["at"]) < 7 * 86400)


def block_text(block: Mapping[str, Any], lang: str | None = None, tz: tzinfo | None = None) -> str:
    lang = i18n.normalize(lang) or i18n.current_lang()
    params = dict(block.get("params") or {})
    if "since" in params and _num(params["since"]) is not None:
        stamp = float(params["since"])
        params["since"] = f"{day_text(local_day(stamp, tz), lang)} {clock_text(stamp, lang, tz)}"
    return t(f"thirst.block.{block['key']}", lang, **params)


# ---------- the dose ----------

def stage_cap_l(model: Model, stage_key: str | None) -> float:
    caps = model("stage_caps_ml")
    return float(caps.get(stage_key or "", caps["early_flower"])) / 1000


def event_cap_l(ledger: Ledger, model: Model, stage_key: str | None) -> tuple[float, str]:
    """The cap of one watering: the stage's preset (MAN) and 0.6 of the available water (three quarters of the pot
    before the first reference weight), the smaller with its name."""
    aw_cap = float(model("cap_aw_factor")) * float(model("a_w")) * float(ledger.v_pot_l or 0)
    if ledger.w_ref is None:
        aw_cap *= float(model("caps_volume_factor_before_weighing"))
    stage = stage_cap_l(model, stage_key)
    return (stage, "stage") if stage <= aw_cap else (aw_cap, "aw")


def dose(ledger: Ledger, model: Model, *, now: float, stage_key: str | None) -> dict[str, Any] | None:
    """The dose: min(0.9 D, caps) with the caps per event and per rolling 24 h; the first cycle without a reference
    weight at most half the trigger amount; after a hard dry-down half; after water in the saucer 25 % less. In
    portions of hand_portion_ml with soak_min between them. None when nothing may be given."""
    if ledger.d_l is None or ledger.d_trig_l is None or ledger.v_pot_l is None:
        return None
    capped_by = "deficit"
    litres = float(model("dose_deficit_factor")) * ledger.d_l
    cap, name = event_cap_l(ledger, model, stage_key)
    caps = [(name, cap)]
    if ledger.first_cycle:
        caps.append(("first_cycle", float(model("first_dose_trig_factor")) * ledger.d_trig_l))
    for cap_name, value in caps:
        if value < litres:
            litres, capped_by = value, cap_name
    day = sum((w.get("litres") or 0) for w in ledger.waterings if now - float(w["at"]) < 86400)
    if day + litres > cap:
        litres, capped_by = max(0.0, cap - day), "day"
    week = weekly_cap_l(ledger, model)
    if week is not None and weekly_used_l(ledger, now) + litres > week:
        litres, capped_by = max(0.0, week - weekly_used_l(ledger, now)), "week"
    if ledger.hard_drydown:
        litres, capped_by = litres / 2, "half"
    if ledger.saucer_less:
        litres *= float(model("saucer_next_dose_factor"))
    litres = math.floor(litres * 20 + 1e-9) / 20  # down to 50 ml, never up
    if litres <= 0:
        return None
    portion = float(model("hand_portion_ml")) / 1000
    return {"litres": round(litres, 2), "portions": max(1, math.ceil(litres / portion - 1e-9)),
            "soak_min": int(model("soak_min")), "capped_by": capped_by}


# ---------- the decision ----------

@dataclass
class Decision:
    kind: str
    reason: str
    check: dict[str, Any] | None = None
    dose: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] = field(default_factory=list)
    next_look: float | None = None
    since: float | None = None  # blocked_long: when the block began, for the alert's hours


def next_round(now: float, model: Model, *, lights_edge: float | None, tz: tzinfo | None = None) -> float:
    """The start of the next daily round at or after `now`, or of the running one: lights-on plus 1 h when the lamp
    came on in the last 26 h, else 09:00 box-local time. Wall-clock times on a local day, so 09:00 stays 09:00 across
    the DST change; the 25 h day is just longer."""
    window = float(model("round_window_s"))
    if lights_edge is not None and 0 <= now - lights_edge < 26 * 3600:
        start = lights_edge + float(model("round_after_lights_on_s"))
        while start + window <= now:
            start += 86400
        return start
    hour = int(model("round_hour_local"))
    day = local_day(now, tz)
    for offset in (-1, 0, 1, 2):
        start = at_local(day + timedelta(days=offset), hour, 0, tz)
        if start + window > now:
            return start
    return at_local(day + timedelta(days=1), hour, 0, tz)


def check_of(ledger: Ledger, model: Model) -> dict[str, Any]:
    """The one-tap check: the finger at 2.5 or 5 cm by the pot's diameter (5 in a wet-hold), or lifting the pot when
    the owner said it lifts (and no hold asks for the finger)."""
    pot = ledger.pot
    if ledger.hold is not None:
        return {"how": "finger", "depth_cm": float(model("hold_depth_cm"))}
    if pot is not None and pot.lifts:
        return {"how": "lift", "depth_cm": None}
    return {"how": "finger", "depth_cm": pot.depth_cm(model) if pot is not None else 5.0}


def trigger(ledger: Ledger, block_list: list[dict[str, Any]], now: float, model: Model, *, stage_key: str | None,
            asked_since: float | None = None, blocked_since: float | None = None,
            lights_edge: float | None = None, tz: tzinfo | None = None) -> Decision:
    """WAIT, ASK, BLOCKED, ALERT, or PROPOSE: a watering suggestion at a measured index of 100 with every block clear.
    No tank argument: the tank reaches this only as a block. No device action anywhere."""
    if ledger.pot is None:
        return Decision("ASK", "no_pot", check={"how": "pot"})
    if ledger.model_error:
        return Decision("BLOCKED", "model")
    check = check_of(ledger, model)
    look = next_round(now, model, lights_edge=lights_edge, tz=tz)
    if look <= now:
        look = next_round(now + float(model("round_window_s")), model, lights_edge=lights_edge, tz=tz)
    wet = ledger.wet_until is not None and now < ledger.wet_until
    if ledger.ti is None:
        if wet:
            return Decision("WAIT", "wet", check=check, blocks=block_list, next_look=ledger.wet_until)
        return Decision("ASK", "first_check", check=check, blocks=block_list, next_look=look)
    ti = ledger.ti
    hold = ledger.hold
    if hold is not None and hold.get("wet_at") is not None and \
            float(hold["wet_at"]) - float(hold["since"]) >= float(model("wet_owner_after_s")):
        return Decision("ALERT", "wet_72h", check=check, blocks=block_list)
    measured_due = ledger.measured is not None and ti >= float(model("due_ti"))
    if measured_due:
        if block_list:
            if blocked_since is not None and now - blocked_since >= float(model("block_alert_after_s")):
                return Decision("ALERT", "blocked_long", check=check, blocks=block_list, since=blocked_since)
            tank = any(b["key"] == "tank_dry" for b in block_list)
            return Decision("BLOCKED", "tank" if tank else "blocked", check=check, blocks=block_list)
        start = next_round(now, model, lights_edge=lights_edge, tz=tz)
        if start > now:
            return Decision("WAIT", "round", check=check, blocks=block_list, next_look=start)
        found = dose(ledger, model, now=now, stage_key=stage_key)
        if found is None:
            return Decision("BLOCKED", "blocked", check=check, blocks=[{"key": "caps", "params": {}}])
        return Decision("PROPOSE", "hand", check=check, dose=found, blocks=block_list)
    # A model index of 120 or more after a check request nobody answered: the pot needs the owner. Never on a model
    # index alone: the first time it only asks.
    if ti >= float(model("alert_ti")) and asked_since is not None:
        return Decision("ALERT", "ti_high", check=check, blocks=block_list)
    if wet:
        return Decision("WAIT", "wet", check=check, blocks=block_list, next_look=ledger.wet_until)
    if hold is not None:
        said = ledger.last_check or {}
        apart = float(model("hold_dry_apart_s"))
        if said.get("answer") in ("dry", "light") and float(said.get("at") or 0) >= float(hold["since"]) and \
                now - float(said["at"]) < apart:
            # a dry answer in a wet-hold never lifts it; the second one is asked 12 h later (review: ask again)
            return Decision("BLOCKED", "hold_dry", check=check, blocks=block_list,
                            next_look=float(said["at"]) + apart)
        hold_ti = ledger.ti_of(float(hold["d_l"])) or 0.0
        if hold_ti >= float(model("hold_dry_from_ti")):  # from here on a dry answer counts toward lifting the hold
            return Decision("ASK", "hold_check", check=check, blocks=block_list, next_look=look)
        return Decision("WAIT", "hold", check=check, blocks=block_list, next_look=look)
    if ledger.dry_below is not None:
        return Decision("WAIT", "weigh_or_wait", check=check, blocks=block_list, next_look=look)
    ask_from = float(model("ask_from_ti"))
    band = ledger.band or 0.0
    if ti >= ask_from or (ledger.confidence == "low" and ti + band >= ask_from) or \
            (stage_key == "seedling" and ti + band >= float(model("finger_dry_from_ti"))):
        if ledger.measured is not None and ti < float(model("due_ti")) and ledger.confidence != "low":
            return Decision("WAIT", "not_due", check=check, blocks=block_list, next_look=look)
        return Decision("ASK", "check", check=check, blocks=block_list, next_look=look)
    return Decision("WAIT", "not_due", check=check, blocks=block_list, next_look=look)


# ---------- texts of a decision ----------

def decision_text(decision: Decision, ledger: Ledger, now: float, lang: str | None = None,
                  tz: tzinfo | None = None) -> str:
    lang = i18n.normalize(lang) or i18n.current_lang()
    ti = ti_text(ledger.ti, lang) if ledger.ti is not None else "?"
    check = decision.check or {}
    depth = fmt_num(check.get("depth_cm") or 5, lang)
    reason = decision.reason
    reasons = "; ".join(block_text(b, lang, tz) for b in decision.blocks)
    if reason == "no_pot":
        return t("thirst.decision.no_pot", lang)
    if reason == "model":
        return t("thirst.decision.model", lang)
    if reason == "first_check":
        return t("thirst.decision.first_lift" if check.get("how") == "lift" else "thirst.decision.first_check", lang,
                 depth=depth)
    if reason == "wet_72h":
        since = float((ledger.hold or {}).get("since") or now)
        return t("thirst.decision.alert_wet", lang, h=hours_text(now - since, lang))
    if reason == "ti_high":
        return t("thirst.decision.alert_high", lang, ti=ti)
    if reason == "blocked_long":
        return t("thirst.decision.alert_blocked", lang,
                 h=hours_text(now - float(decision.since if decision.since is not None
                                          else (ledger.measured or {}).get("at") or now), lang), reasons=reasons)
    if reason == "tank":
        return t("thirst.decision.blocked_tank", lang)
    if reason == "blocked":
        return t("thirst.decision.blocked", lang, ti=ti, reasons=reasons)
    if reason == "hand":
        found = decision.dose or {}
        measured = (ledger.measured or {}).get("kind")
        return t("thirst.decision.propose", lang, ti=ti,
                 measured=t("thirst.measured.weigh" if measured == "weigh" else "thirst.measured.lift"
                            if measured == "lift" else "thirst.measured.sensor" if measured == "sensor"
                            else "thirst.measured.finger", lang),
                 litres=fmt_num(found.get("litres"), lang), n=found.get("portions"), soak=found.get("soak_min"))
    if reason == "round":
        return t("thirst.decision.wait_round", lang, ti=ti, when=when_text(decision.next_look or now, now, lang, tz))
    if reason == "wet":
        at = float((ledger.last_check or {}).get("at") or now)
        return t("thirst.decision.wet", lang, h=hours_text(now - at, lang))
    if reason in ("hold", "hold_check", "hold_dry"):
        since = float((ledger.hold or {}).get("since") or now)
        since_text = f"{day_text(local_day(since, tz), lang)} {clock_text(since, lang, tz)}"
        if reason == "hold_dry":
            return t("thirst.decision.hold_dry", lang, since=since_text,
                     when=when_text(decision.next_look or now, now, lang, tz))
        if reason == "hold_check":
            return t("thirst.decision.hold_check", lang, since=since_text, ti=ti)
        return t("thirst.decision.hold", lang, since=since_text)
    if reason == "weigh_or_wait":
        found = ledger.dry_below or {}
        return t("thirst.decision.weigh_or_wait", lang, depth=fmt_num(found.get("depth_cm") or 5, lang),
                 ti=ti_text(found.get("ti") or 0, lang))
    if reason == "check":
        return t("thirst.decision.ask_lift" if check.get("how") == "lift" else "thirst.decision.ask", lang, ti=ti,
                 depth=depth)
    return t("thirst.decision.wait", lang, ti=ti, when=when_text(decision.next_look or now, now, lang, tz))


def check_text(decision: Decision, lang: str | None = None) -> str | None:
    """The one-tap check that goes with a decision, or None when nothing is asked."""
    check = decision.check or {}
    if decision.reason == "no_pot":
        return t("thirst.check.pot", lang)
    if decision.kind not in ("ASK", "ALERT") and decision.reason not in ("first_check", "hold_check", "hold_dry"):
        return None
    if decision.reason in ("hold_check", "hold_dry"):
        return t("thirst.check.hold", lang)
    if check.get("how") == "lift":
        return t("thirst.check.lift", lang)
    return t("thirst.check.finger", lang, depth=fmt_num(check.get("depth_cm") or 5, lang))


def why_lines(decision: Decision, ledger: Ledger, now: float, lang: str | None = None,
              stage: Mapping[str, Any] | None = None) -> list[str]:
    """Why the index stands where it stands: measured or modelled, the band, and what changes the dose."""
    lang = i18n.normalize(lang) or i18n.current_lang()
    out: list[str] = []
    if ledger.pot is None or ledger.ti is None:
        return out
    if ledger.measured is not None:
        age = hours_text(now - float(ledger.measured["at"]), lang)
        if ledger.measured.get("kind") == "weigh":
            out.append(t("thirst.why.measured_weigh", lang, h=age))
        else:
            out.append(t("thirst.why.measured_finger", lang, h=age,
                         answer=t(f"thirst.answer.{ledger.measured.get('answer') or 'dry'}", lang)))
    elif ledger.last_weigh is not None and (ledger.last_check is None
                                            or ledger.last_weigh >= float(ledger.last_check["at"])):
        out.append(t("thirst.why.model_weigh", lang, h=hours_text(now - ledger.last_weigh, lang)))
    elif ledger.last_check is not None:
        out.append(t("thirst.why.model", lang, h=hours_text(now - float(ledger.last_check["at"]), lang)))
    else:
        out.append(t("thirst.why.model_never", lang))
    if ledger.band is not None:
        out.append(t("thirst.why.band", lang, band=fmt_num(int(round(ledger.band)), lang),
                     confidence=t(f"thirst.confidence.{ledger.confidence}", lang)))
    if ledger.unknown_amount:
        out.append(t("thirst.why.unknown_amount", lang))
    if ledger.unverified is not None:
        out.append(t("thirst.why.unverified", lang))
    if ledger.pot.wick:
        out.append(t("thirst.why.wick", lang))
    if decision.kind == "PROPOSE":
        if ledger.first_cycle:
            out.append(t("thirst.why.first_cycle", lang))
        if ledger.hard_drydown:
            out.append(t("thirst.why.half_dose", lang))
        if ledger.saucer_less:
            out.append(t("thirst.why.saucer_less", lang))
    if not stage or stage.get("set_by") != "manual":
        out.append(t("thirst.why.stage_contested", lang))
    return out


def event_text(event: Mapping[str, Any], lang: str | None = None) -> str:
    lang = i18n.normalize(lang) or i18n.current_lang()
    kind = event.get("kind")
    port = port_name(str(event.get("port") or "usb1"))
    minutes = fmt_num(event.get("minutes") or 0, lang)
    if kind == "water":
        if event.get("arrived") == "no":
            return t("thirst.event.arrived_no", lang)
        origin = t(f"thirst.origin.{event.get('origin') or 'hand'}", lang)
        if event.get("litres") is None:
            return t("thirst.event.water_unknown", lang, port=port, minutes=minutes, origin=origin)
        if event.get("port"):
            return t("thirst.event.water_pump", lang, port=port, minutes=minutes, origin=origin,
                     litres=litres_text(float(event["litres"]), lang))
        return t("thirst.event.water", lang, litres=litres_text(float(event["litres"]), lang), origin=origin)
    if kind == "saucer":
        return t("thirst.event.saucer_water" if event.get("water") else "thirst.event.saucer_dry", lang)
    if kind == "weigh":
        return t("thirst.event.weigh_full" if event.get("full") else "thirst.event.weigh", lang,
                 kg=fmt_qty(event.get("kg"), "kg", lang))
    if kind == "check":
        answer = t(f"thirst.answer.{event.get('answer')}", lang)
        if event.get("how") == "lift":
            return t("thirst.event.lift", lang, answer=answer)
        if event.get("how") == "sensor":
            return t("thirst.event.sensor", lang, answer=answer)
        return t("thirst.event.check", lang, depth=fmt_num(event.get("depth_cm") or 5, lang), answer=answer)
    if kind == "refill":
        key = "thirst.event.refill_feed" if event.get("feed") else "thirst.event.refill"
        return t(key, lang, litres=litres_text(float(event.get("litres") or 0), lang))
    if kind == "pump_seen":
        key = {"power": "thirst.event.pump_seen_power", "jug": "thirst.event.pump_seen_jug"}.get(
            str(event.get("why")), "thirst.event.pump_seen_device")
        return t(key, lang, port=port, minutes=minutes)
    if kind == "pot":
        return t("thirst.event.pot", lang, litres=litres_text(float(event.get("litres") or 0), lang))
    if kind == "flow":
        return t("thirst.event.flow", lang, ml=fmt_qty(int(round(event.get("ml") or 0)), "ml", lang),
                 seconds=fmt_num(int(round(event.get("seconds") or 0)), lang))
    if kind == "flow_reset":
        return t("thirst.event.flow_reset", lang)
    return str(kind)


# ---------- light, stage windows and the forecast ----------

def light_edges(cfg: Mapping[str, Any], start: float, end: float, tz: tzinfo | None = None) -> list[tuple[float, bool]]:
    """The light timer's switch times from start to end as (epoch, on), as vgrow/light_schedule.py switches them: a
    doubled autumn time counts at its first occurrence, a time the spring change skips falls out."""
    zone = tz or ZONE
    out: list[tuple[float, bool]] = []
    day = local_day(start, zone) - timedelta(days=1)
    last = local_day(end, zone) + timedelta(days=1)
    while day <= last:
        for key, on in (("on_time", True), ("off_time", False)):
            hh, mm = (int(x) for x in str(cfg[key]).split(":"))
            local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone, fold=0)
            at = local.timestamp()
            if datetime.fromtimestamp(at, zone).replace(tzinfo=None) == local.replace(tzinfo=None):
                out.append((at, on))
        day += timedelta(days=1)
    return sorted(out)


class LightPlan:
    """The light the forecast and the plan count with: the light timer when it is on (its times and level), else the
    level the box reports now, held around the clock (or the last level seen, when the box reports none)."""

    def __init__(self, timer: Mapping[str, Any] | None, level: float | None, *, last_seen: float | None,
                 start: float, end: float, tz: tzinfo | None = None) -> None:
        valid = bool(timer and timer.get("enabled") and timer.get("on_time") and timer.get("off_time")
                     and _num(timer.get("level")) and timer.get("on_time") != timer.get("off_time"))
        self.kind = "timer" if valid else "held" if level is not None else "unknown"
        self.timer = dict(timer or {}) if valid else None
        self.level = float(timer["level"]) if valid and timer else level if level is not None else last_seen
        self.edges = light_edges(self.timer, start - 2 * 86400, end, tz) if self.timer else []
        self._times = [at for at, _ in self.edges]

    def at(self, epoch: float) -> float:
        """The light level at an epoch second: the timer's level between its on and off switch, else 0."""
        if self.kind != "timer":
            return float(self.level) if self.level is not None else 0.0
        if not self.edges:
            return 0.0
        i = bisect.bisect_right(self._times, epoch) - 1
        on = self.edges[i][1] if i >= 0 else not self.edges[0][1]
        return float(self.level or 0) if on else 0.0

    def lit_level(self) -> float | None:
        return self.level


def stage_band_mid(stage: Mapping[str, Any] | None, metric: str = "probe_vpd") -> float | None:
    for target in (stage or {}).get("targets") or []:
        if isinstance(target, Mapping) and target.get("metric") == metric and _num(target.get("min")) is not None \
                and _num(target.get("max")) is not None:
            return (float(target["min"]) + float(target["max"])) / 2
    return None


def stage_timeline(stages: list[Mapping[str, Any]], current: Mapping[str, Any] | None, today: date,
                   plant_start: date | None) -> list[dict[str, Any]]:
    """The stages from the current one on with their estimated dates: the current stage from its start (the bloom
    start for a flowering stage, else the day it was set, else the plant's start plus the card's from_day), then each
    following one by the card's day windows. The current stage lasts at least through today: only the owner moves on."""
    by_key = {s.get("key"): s for s in stages if isinstance(s, Mapping)}
    order = [s.get("key") for s in stages if isinstance(s, Mapping) and s.get("key") in STAGE_KEYS]
    key = (current or {}).get("key")
    if key not in by_key or key not in order:
        return []
    stage = by_key[key]
    length = max(1, int(stage.get("to_day", 0)) - int(stage.get("from_day", 0))) if key != "harvest" else 14
    flowering = [s for s in order if s in FLOWERING_STAGES and s != "harvest"]
    bloom = parse_day((current or {}).get("bloom_start"))
    since = parse_day((current or {}).get("since"))
    if key in flowering and bloom is not None and flowering:
        start = bloom + timedelta(days=int(stage.get("from_day", 0)) - int(by_key[flowering[0]].get("from_day", 0)))
    elif since is not None:
        start = since
    elif plant_start is not None:
        start = plant_start + timedelta(days=int(stage.get("from_day", 0)))
    else:
        start = today
    end = max(start + timedelta(days=length), today + timedelta(days=1))
    out = [{"key": key, "from": start, "until": end, "estimated": (current or {}).get("set_by") != "manual"}]
    for later in order[order.index(key) + 1:]:
        nxt = by_key[later]
        span = max(1, int(nxt.get("to_day", 0)) - int(nxt.get("from_day", 0))) if later != "harvest" else 14
        start, end = end, end + timedelta(days=span)
        out.append({"key": later, "from": start, "until": end, "estimated": True})
    return out


# ---------- the box's ledger ----------

class Thirst:
    """One box's Durst-Index: the ledger, the guards, the need gate, the forecast and the stage plan.

    box: read through snapshot() only. watering: the timed controller (vgrow/watering.py), read only: its settings
    (power_confirmed, the schedule), its running cycle and its slots. light: the light timer (vgrow/light_schedule.py),
    read only. alerts: the pushes. clock and tz replaceable for tests."""

    def __init__(self, box: Any = None, *, box_id: str = "box-1", alerts: Any = None,
                 plant_log: PlantLog | None = None, guard: PortGuard | None = None, demo: bool = False,
                 clock: Callable[[], float] = time.time, tz: tzinfo | None = None, model_path: Path | None = None,
                 stage_source: Callable[[], dict[str, Any] | None] | None = None, watering: Any = None,
                 light: Any = None) -> None:
        self.box = box
        self.box_id = box_id
        self.alerts = alerts
        self.plant_log = plant_log if plant_log is not None else PlantLog()
        self.guard = guard if guard is not None else PortGuard(box_id)
        self.demo = demo
        self.clock = clock
        self.tz = tz or ZONE
        self.model_path = model_path
        self._stage_source = stage_source
        self.watering = watering
        self.light = light
        self.state = self._load_state()
        self.series = Series(**self._series_args(self.state.get("series")))
        self._saved_at = 0.0
        self._saved_key = ""
        self._listening: Any = None
        self._read_cache: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
        self.jug_until: float | None = None  # the pump's next run goes into the owner's measuring jug (jug_run)

    def jug_run(self, seconds: float) -> None:
        """The next pump run goes into a measuring jug (vgrow/grow_plan.py): seen, never counted as water in the pot."""
        self.jug_until = self.clock() + seconds + JUG_MARGIN_S

    # ---------- files ----------

    def dir(self) -> Path:
        return config.STATE_DIR / "boxes" / self.box_id

    def pot_path(self) -> Path:
        return self.dir() / POT_FILE

    def state_path(self) -> Path:
        return self.dir() / STATE_FILE

    def log_path(self) -> Path:
        return self.dir() / LOG_FILE

    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _series_args(raw: Any) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        return {"points": raw.get("points") or [], "gaps": raw.get("gaps") or [], "head": raw.get("head"),
                "rate_l_s": _num(raw.get("rate_l_s"))}

    def save(self) -> None:
        self.state["series"] = self.series.dump()
        try:
            config.atomic_write(self.state_path(), json.dumps(self.state, ensure_ascii=False) + "\n")
        except OSError as err:
            _LOGGER.warning("Thirst state not saved: %s", err)
        self._saved_at = self.clock()
        self._saved_key = self._key()

    def pot(self) -> Pot | None:
        try:
            data = json.loads(self.pot_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Pot.from_dict(data) if isinstance(data, dict) else None

    def log(self, entry: dict[str, Any], at: float | None = None) -> dict[str, Any]:
        line = {"id": f"t-{uuid.uuid4().hex[:10]}", "at": self.clock() if at is None else at, "box": self.box_id,
                **entry}
        try:
            self.dir().mkdir(parents=True, exist_ok=True)
            with self.log_path().open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as err:
            _LOGGER.warning("Thirst log not written: %s", err)
        return line

    def _cached(self, name: str, path: Path) -> tuple[Any, list[dict[str, Any]] | None]:
        """A read file's parsed rows while its size and mtime stay the same (every page poll reads the ledger)."""
        try:
            stat = path.stat()
        except OSError:
            return None, []
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        found = self._read_cache.get(name)
        if found is not None and found[0] == key:
            return key, [dict(e) for e in found[1]]
        return key, None

    def entries(self) -> list[dict[str, Any]]:
        key, cached = self._cached("log", self.log_path())
        if cached is not None:
            return cached
        try:
            lines = self.log_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and _num(entry.get("at")) is not None:
                out.append(entry)
        self._read_cache["log"] = (key, out)
        return [dict(e) for e in out]

    def feed_waterings(self) -> list[dict[str, Any]]:
        """Every feed of the feeding log as a watering of its litres (feeding.py's log, read only)."""
        from . import feeding  # here: feeding is read, never changed, and only for its litres

        key, cached = self._cached("feed", feeding.log_path())
        if cached is not None:
            return cached
        try:
            lines = feeding.log_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                entry = json.loads(line)
                parsed = datetime.fromisoformat(str(entry.get("time")))
            except (ValueError, TypeError, AttributeError):
                continue
            stamp = (parsed if parsed.tzinfo else parsed.replace(tzinfo=self.tz)).timestamp()
            litres = _num(entry.get("liters")) if isinstance(entry, dict) else None
            if litres is not None and litres > 0:
                out.append({"id": f"feed-{int(stamp)}", "at": stamp, "kind": "water", "litres": litres,
                            "origin": "feed"})
        self._read_cache["feed"] = (key, out)
        return [dict(e) for e in out]

    # ---------- the moment ----------

    def profile(self) -> dict[str, Any] | None:
        try:
            return load_profile()
        except Exception:  # noqa: BLE001 (an unreadable plant.json costs the plan, never the ledger)
            _LOGGER.exception("Thirst: plant.json unreadable")
            return None

    def stage(self, profile: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        if self._stage_source is not None:
            return self._stage_source()
        profile = profile if profile is not None else self.profile()
        return current_stage(dict(profile), self.plant_log) if profile else None

    def height(self) -> dict[str, Any] | None:
        heights = self.plant_log.data.get("heights") or []
        last = heights[-1] if heights and isinstance(heights[-1], dict) else {}
        cm = _num(last.get("cm"))
        return {"cm": cm, "date": last.get("date"), "estimated": bool(last.get("estimated"))} if cm else None

    def height_cm(self) -> float | None:
        found = self.height()
        return found["cm"] if found else None

    def power_confirmed(self) -> bool:
        cfg = getattr(self.watering, "cfg", None)
        return isinstance(cfg, Mapping) and cfg.get("power_confirmed") is True

    def schedule(self) -> Mapping[str, Any] | None:
        cfg = getattr(self.watering, "cfg", None)
        return cfg if isinstance(cfg, Mapping) else None

    def next_slot(self, after: float, horizon: float = 40 * 86400) -> float | None:
        """The watering controller's first scheduled slot after `after`, or None (schedule off, or no controller)."""
        cfg = self.schedule()
        if not cfg or not cfg.get("enabled") or not hasattr(self.watering, "slots"):
            return None
        try:
            found = self.watering.slots(after, after + horizon)
        except Exception:  # noqa: BLE001 (a broken schedule costs the forecast line, never the ledger)
            return None
        return found[0][0] if found else None

    def flow(self, now: float | None = None) -> dict[str, Any]:
        """The flow calibration from the owner's jug runs: the last two after the last reset, both younger than
        flow_valid_s; calibrated when they agree within flow_tolerance. ml_s is their mean."""
        now = self.clock() if now is None else now
        try:
            model = load_model(self.model_path)
        except ModelError:
            return {"calibrated": False, "runs": []}
        runs: list[dict[str, Any]] = []
        for e in self.entries():
            if e.get("kind") == "flow_reset":
                runs = []
            elif e.get("kind") == "flow" and _num(e.get("ml")) and _num(e.get("seconds")):
                runs.append({"at": float(e["at"]), "ml_s": float(e["ml"]) / float(e["seconds"]),
                             "confirmed_outlier": e.get("confirmed_outlier") is True})
        valid = float(model("flow_valid_s"))
        runs = [r for r in runs if now - r["at"] <= valid][-int(model("flow_runs")):]
        out: dict[str, Any] = {"calibrated": False, "runs": runs, "ml_s": None, "at": None, "until": None}
        reference = float(model("flow_reference_ml_min")) / 60
        factor = float(model("flow_plausibility_factor"))
        if any(not r["confirmed_outlier"] and not reference / factor <= r["ml_s"] <= reference * factor for r in runs):
            out["unverified"] = True
            return out
        if len(runs) >= int(model("flow_runs")):
            rates = [r["ml_s"] for r in runs]
            spread = (max(rates) - min(rates)) / max(rates)
            if spread <= float(model("flow_tolerance")):
                out.update(calibrated=True, ml_s=sum(rates) / len(rates), at=max(r["at"] for r in runs),
                           until=min(r["at"] for r in runs) + valid)
            else:
                out["far"] = rates
        elif runs:
            out["one"] = runs[-1]["ml_s"]
        return out

    def ledger(self, now: float | None = None, stage: Mapping[str, Any] | None = None) -> Ledger:
        now = self.clock() if now is None else now
        pot = self.pot()
        try:
            model = load_model(self.model_path)
        except ModelError as err:
            ledger = Ledger(pot=pot)
            ledger.model_error = str(err)
            return ledger
        stage = stage if stage is not None else self.stage()
        events = [*self.entries(), *self.feed_waterings()]
        return fold(pot, events, self.series, now, model, stage_key=(stage or {}).get("key"))

    def assess(self, snap: Mapping[str, Any], now: float | None = None) -> tuple[Ledger, Decision, dict[str, Any]]:
        """The ledger, the decision and the context of this moment (stage, pump ports, the block list, the model)."""
        now = self.clock() if now is None else now
        profile = self.profile()
        stage = self.stage(profile)
        ledger = self.ledger(now, stage)
        pump = pump_ports(snap, self.guard)
        ctx: dict[str, Any] = {"stage": stage, "pump": pump, "profile": profile, "blocks": []}
        try:
            model = load_model(self.model_path)
        except ModelError:
            return ledger, Decision("BLOCKED", "model"), ctx
        ports_status = "ok"
        try:
            if self.guard.changed() is not None:
                ports_status = "changed"
        except Exception:  # noqa: BLE001
            ports_status = "changed"
        found = blocks(snap, ledger, now=now, model=model, stage=stage, pump=pump,
                       coverage=self.series.coverage(now), ports_status=ports_status, tz=self.tz)
        asked = (self.state.get("ask") or {}).get("since")
        blocked = (self.state.get("blocked") or {}).get("since")
        edge = (self.state.get("lights") or {}).get("edge_at")
        decision = trigger(ledger, found, now, model, stage_key=(stage or {}).get("key"), asked_since=asked,
                           blocked_since=blocked, lights_edge=edge, tz=self.tz)
        ctx.update(model=model, blocks=found)
        return ledger, decision, ctx

    # ---------- listening: every poll and every USB report ----------

    def start(self, box: Any) -> None:
        """Listen to the box's USB reports: each one tracks a pump run (box.port_listeners, when the box has them).
        The poll step, check(), runs inside the cockpit's own poll handler (server.poll_handler, parts["thirst"]), so
        the box keeps its one poll listener. Read only: nothing here switches."""
        if self._listening is not None:
            return
        self._listening = box
        if hasattr(box, "port_listeners"):
            box.port_listeners.append(self.observe)

    def stop(self) -> None:
        box, self._listening = self._listening, None
        if box is None:
            return
        if self.poll in box.listeners:
            box.listeners.remove(self.poll)
        if hasattr(box, "port_listeners") and self.observe in box.port_listeners:
            box.port_listeners.remove(self.observe)
        self.save()

    async def poll(self, box: Any) -> None:
        await self.check(box.snapshot())

    def observe(self, box: Any, ports: Mapping[str, Any], at: float | None, source: str = "shadow") -> None:
        """A reported USB fragment (MQTT): the port's state at its report time. A retained report of an offline box
        is no new run."""
        if at is None:
            return
        try:
            snap = box.snapshot()
            if not snap.get("mqtt") or snap.get("online") is False:
                return
            for port in pump_ports({"ports": {p: {} for p in ports}}, self.guard):
                value = ports.get(port)
                if type(value) is bool:
                    self._port_state(port, value, float(at), snap)
            self._maybe_save(self.clock())
        except Exception:  # noqa: BLE001 (an observer never breaks the box's message handling)
            _LOGGER.exception("Thirst: USB report not taken")

    async def check(self, snap: Mapping[str, Any]) -> Decision | None:
        """After every poll: the ET series, the lights, the pump port, the escalation."""
        now = self.clock()
        for step in (self._sample, self._lights, self._ports):
            try:
                step(snap, now)
            except Exception:  # noqa: BLE001 (one failing step never stops the others)
                _LOGGER.exception("Thirst: poll step %s failed", step.__name__)
        decision: Decision | None = None
        try:
            ledger, decision, _ctx = self.assess(snap, now)
            await self._escalate(ledger, decision, now)
        except Exception:  # noqa: BLE001 (a broken ledger must not stop the poll)
            _LOGGER.exception("Thirst: the ledger failed")
        self._maybe_save(now)
        return decision

    def _key(self) -> str:
        """What must reach the disk at once: everything but the held inputs and the series head."""
        return json.dumps({**{k: v for k, v in self.state.items() if k not in ("inputs", "series")},
                           "points": len(self.series.points), "gaps": len(self.series.gaps)},
                          sort_keys=True, default=str)

    def _maybe_save(self, now: float) -> None:
        """Save at once when anything but the held inputs changed (a run, a check request, a new series point every
        10 min), else at most every 10 min: the series is written about 144 times a day, not 960."""
        if self._key() == self._saved_key and now - self._saved_at < SERIES_STEP_S:
            return
        self.save()

    def _sample(self, snap: Mapping[str, Any], now: float) -> None:
        """One step of the ET series: the rate from this poll's light and climate, integrated from the last poll with
        the inputs held up to hold_input_s; a longer stretch, old readings or an unknown light level are a gap that
        widens the band. An unknown light keeps the last known level, never counts as night (review N3)."""
        model = load_model(self.model_path)
        hold = float(model("hold_input_s"))
        climate = snap.get("climate") if isinstance(snap.get("climate"), Mapping) else {}
        age = _num(snap.get("sensors_age"))
        fresh = bool(snap.get("online", True)) and age is not None and age <= hold
        inputs = self.state.get("inputs") if isinstance(self.state.get("inputs"), dict) else {}
        last_at = _num(inputs.get("at"))
        light = _num((snap.get("light") or {}).get("level")) if isinstance(snap.get("light"), Mapping) else None
        light_known = light is not None
        vpd = _num(climate.get("probe_vpd"))
        probe_h, box_h = _num(climate.get("probe_humidity")), _num(climate.get("humidity"))
        mist = probe_h is not None and box_h is not None and probe_h - box_h > float(model("mist_gap_points"))
        if mist:  # during mist, the last VPD without mist, else the box's own air (FAO)
            held = _num(inputs.get("vpd_clear"))
            box_t = _num(climate.get("temp"))
            vpd = held if held is not None else (tetens_vpd(box_t, box_h) if box_t is not None and box_h is not None
                                                 else None)
        elif vpd is not None:
            inputs["vpd_clear"] = vpd
        if not fresh:
            vpd = _num(inputs.get("vpd"))
        if light is None:
            light = _num(inputs.get("light"))
        if light is not None and light > 0:
            inputs["last_lit"] = light
        rate = et_rate_ml_h(model, light=light, vpd=vpd if vpd is not None else _num(inputs.get("vpd")),
                            height_cm=self.height_cm(), last_lit=_num(inputs.get("last_lit"))) / 1000 / 3600
        previous_rate = _num(inputs.get("rate_l_s"))
        if last_at is not None and now > last_at:
            dt = now - last_at
            litres = (previous_rate if previous_rate is not None else rate) * dt
            gap_from = last_at + hold if dt > hold else None
            if not fresh or not light_known:
                gap_from = last_at if gap_from is None else min(gap_from, last_at)
            self.series.add(now, litres, rate, gap_from=gap_from)
        elif last_at is None:
            self.series.add(now, 0.0, rate)
        if fresh and vpd is not None:
            inputs["vpd"] = vpd
        if light_known:
            inputs["light"] = light
        inputs.update(at=now, rate_l_s=rate)
        self.state["inputs"] = inputs

    def _lights(self, snap: Mapping[str, Any], now: float) -> None:
        level = _num((snap.get("light") or {}).get("level")) if isinstance(snap.get("light"), Mapping) else None
        if level is None:
            return
        lights = self.state.setdefault("lights", {})
        on = level > 0
        if lights.get("on") is False and on:
            lights["edge_at"] = now
        lights["on"] = on

    def _ports(self, snap: Mapping[str, Any], now: float) -> None:
        """The poll's view of the pump port, when the box is linked (a retained report of an offline box says
        nothing). A pump port that is no pump port any more forgets its open run."""
        if not snap.get("mqtt") or snap.get("online") is False:
            return
        ports = snap.get("ports") if isinstance(snap.get("ports"), Mapping) else {}
        pumps = pump_ports(snap, self.guard)
        for port in pumps:
            on = ((ports or {}).get(port) or {}).get("on")
            if type(on) is bool:
                self._port_state(port, on, now, snap)
        runs = self.state.get("runs") if isinstance(self.state.get("runs"), dict) else {}
        for port in [p for p in runs if p not in pumps]:
            runs.pop(port, None)

    def _origin_now(self, port: str, snap: Mapping[str, Any]) -> str:
        """Who switched the run on, as far as the cockpit knows: the watering controller's cycle (its schedule or Einmal
        gießen), the VIVOSUN app's plan, or a run without a timer (the app's switch, a button, a restart)."""
        active = getattr(self.watering, "active", None)
        if isinstance(active, Mapping) and active.get("phase") in ("starting", "running"):
            return "schedule" if active.get("automatic") else "once"
        report = ((snap.get("ports") or {}).get(port) or {}) if isinstance(snap.get("ports"), Mapping) else {}
        if report.get("in_plan") or report.get("can_write") is False:
            return "app"
        return "other"

    def _port_state(self, port: str, on: bool, at: float, snap: Mapping[str, Any]) -> None:
        runs = self.state.setdefault("runs", {})
        seen = self.state.setdefault("seen", {})
        run = runs.get(port)
        before = seen.get(port) if isinstance(seen.get(port), dict) else None
        if on:
            if run is None:
                prev_off = float(before["at"]) if before and before.get("on") is False else None
                runs[port] = {"first_on": at, "last_on": at, "prev_off": prev_off,
                              "origin": self._origin_now(port, snap)}
            else:
                run["last_on"] = max(float(run.get("last_on", at)), at)
        elif run is not None:
            runs.pop(port, None)
            self._close_run(port, run, at)
        seen[port] = {"at": at, "on": on}

    def _close_run(self, port: str, run: Mapping[str, Any], off_at: float) -> None:
        """A run seen on the port has ended. It counts only for the time after the owner named the pump on that port;
        with litres only with the confirmed pump switch and a calibrated flow, the longest possible run time times the
        flow plus its margin. Otherwise an unknown amount, or nothing (and a note that it was seen)."""
        model = load_model(self.model_path)
        first = float(run["first_on"])
        last = float(run.get("last_on", first))
        start_lo = _num(run.get("prev_off"))
        origin = str(run.get("origin") or "other")
        if self.jug_until is not None and first <= self.jug_until:  # the owner's measuring jug: this water never saw the pot
            self.jug_until = None
            done = _run_bounds(first, last, start_lo, off_at)
            self.log({"kind": "pump_seen", "port": port, "origin": origin, "minutes": done["minutes"], "why": "jug",
                      "counted": False}, at=off_at)
            return
        named = self.guard.owner_device(port) == PUMP
        since = self.guard.assigned_at(port)
        if not named or since is None or since > last:
            done = _run_bounds(first, last, start_lo, off_at)
            self.log({"kind": "pump_seen", "port": port, "origin": origin, "minutes": done["minutes"],
                      "why": "device", "counted": False}, at=off_at)
            return
        if start_lo is None or since > start_lo:
            start_lo = max(since, first - POLL_EDGE_S) if start_lo is None else since
            first = max(first, since)
        done = _run_bounds(first, last, start_lo, off_at)
        if not self.power_confirmed():
            self.log({"kind": "pump_seen", "port": port, "origin": origin, "minutes": done["minutes"],
                      "why": "power", "counted": False}, at=off_at)
            return
        flow = self.flow(off_at)
        base = {"kind": "water", "port": port, "origin": origin, "minutes": done["minutes"],
                "minutes_lo": done["minutes_lo"], "minutes_hi": done["minutes_hi"], "from": first, "until": off_at}
        if not flow["calibrated"]:
            self.log({**base, "litres": None, "counted": "unknown"}, at=off_at)
            return
        litres = done["most_s"] * float(flow["ml_s"]) * (1 + float(model("flow_margin"))) / 1000
        self.log({**base, "litres": round(litres, 3), "counted": "flow", "flow_ml_s": round(float(flow["ml_s"]), 3)},
                 at=off_at)

    # ---------- escalation ----------

    async def _escalate(self, ledger: Ledger, decision: Decision, now: float) -> None:
        """The pushes, each once per episode, never on a model-only index without a check request first."""
        model = load_model(self.model_path)
        ask = self.state.get("ask") if isinstance(self.state.get("ask"), dict) else None
        asked_before = ask is not None  # a check request of an earlier poll: only then may a model index push
        asking = decision.kind == "ASK" and decision.reason in ("check", "first_check", "hold_check")
        if asking and ask is None:
            ask = {"since": now, "pushed_ask": None}
            self.state["ask"] = ask
        elif not asking and decision.kind != "ALERT" and ask is not None:
            self.state["ask"] = ask = None
        ti = ledger.ti
        if ask is not None and ti is not None:
            waited = now - float(ask["since"])
            if ti >= float(model("due_ti")) and waited >= float(model("ask_push_after_s")) and not ask["pushed_ask"]:
                ask["pushed_ask"] = now
                await self._push("thirst.push.title", "thirst.push.ask", 3, ti=ti_text(ti), h=hours_text(waited),
                                 check=check_text(decision, i18n.server_lang()) or "")
        alerted = self.state.setdefault("alerted", {})
        if ti is not None and ti >= float(model("alert_ti")) and \
                ((asked_before and ask is not None) or ledger.measured is not None):
            if not alerted.get("pushed_high"):
                alerted["pushed_high"] = now
                await self._push("thirst.push.title", "thirst.push.high", 4, ti=ti_text(ti))
        elif ti is None or ti < float(model("alert_ti")) - 10:
            alerted.pop("pushed_high", None)  # well below again: the next time it is said again
        blocked = self.state.get("blocked") if isinstance(self.state.get("blocked"), dict) else None
        due = ledger.measured is not None and ti is not None and ti > float(model("due_ti"))
        if decision.kind in ("BLOCKED", "ALERT") and decision.blocks and due:
            if blocked is None:
                blocked = {"since": now, "pushed": None}
                self.state["blocked"] = blocked
            elif now - float(blocked["since"]) >= float(model("block_alert_after_s")) and not blocked["pushed"]:
                blocked["pushed"] = now
                await self._push("thirst.push.title", "thirst.push.blocked", 4,
                                 h=hours_text(now - float(blocked["since"])),
                                 reasons="; ".join(block_text(b, i18n.server_lang(), self.tz)
                                                   for b in decision.blocks))
        elif blocked is not None:
            self.state["blocked"] = None
        if decision.kind == "ALERT" and decision.reason == "wet_72h":
            since = (ledger.hold or {}).get("since")
            if alerted.get("wet") != since:
                alerted["wet"] = since
                await self._push("thirst.push.title", "thirst.push.wet", 4, h=hours_text(now - float(since or now)))

    async def _push(self, title_key: str, body_key: str, priority: int, **params: Any) -> None:
        if self.alerts is None:
            return
        lang = i18n.server_lang()
        try:
            await self.alerts.push(t(title_key, lang), t(body_key, lang, **params), priority=priority)
        except Exception:  # noqa: BLE001 (a failed push never stops the ledger)
            _LOGGER.exception("Thirst push failed")

    # ---------- the need gate (vgrow/watering.py asks it before every scheduled slot) ----------

    def gate(self, slot_at: float, duration_s: int) -> dict[str, Any]:
        """WateringController.need_gate: may the scheduled slot run, and how long at most? {"run": bool, "max_s": a
        shorter run time or None, "reason", "text": {"de", "en"}}. A slot runs only when the pot is due by a measured
        index (a check or a weighing within 24 h, outside a wet-hold) with every guardrail clear. With the confirmed
        pump switch and a calibrated flow the run is shortened to the dose (the stage cap at most); without either,
        the slot is skipped. It never lengthens a run and never starts one."""
        now = self.clock()
        try:
            snap = self.box.snapshot() if self.box is not None else {}
            ledger, decision, ctx = self.assess(snap, now)
            return self._verdict(ledger, ctx, now, int(duration_s))
        except Exception:  # noqa: BLE001 (fail closed: the controller skips the slot)
            _LOGGER.exception("Thirst: the need gate failed; the slot is skipped")
            return {"run": False, "max_s": None, "reason": "error", "text": both(lambda la: t("thirst.gate.error", la))}

    def _verdict(self, ledger: Ledger, ctx: Mapping[str, Any], now: float, duration_s: int) -> dict[str, Any]:
        model: Model | None = ctx.get("model")

        def skip(reason: str, key: str, **params: Any) -> dict[str, Any]:
            return {"run": False, "max_s": None, "reason": reason,
                    "text": both(lambda la: t(key, la, **{k: (v(la) if callable(v) else v) for k, v in params.items()}))}

        if model is None or ledger.model_error:
            return skip("model", "thirst.gate.error")
        if ledger.pot is None:
            return skip("no_pot", "thirst.gate.no_pot")
        if ledger.ti is None:
            return skip("unknown", "thirst.gate.unknown")
        ti = ledger.ti
        if ledger.hold is not None:
            since = float(ledger.hold["since"])
            return skip("hold", "thirst.gate.hold", ti=lambda la: ti_text(ti, la),
                        since=lambda la: f"{day_text(local_day(since, self.tz), la)} {clock_text(since, la, self.tz)}")
        if ledger.measured is None:
            return skip("unmeasured", "thirst.gate.unmeasured", ti=lambda la: ti_text(ti, la))
        if ti < float(model("due_ti")):
            return skip("not_due", "thirst.gate.not_due", ti=lambda la: ti_text(ti, la))
        held = [b for b in ctx.get("blocks") or [] if b["key"] not in ("pump_running",)]
        if held:
            return skip("blocked", "thirst.gate.blocked",
                        reasons=lambda la: "; ".join(block_text(b, la, self.tz) for b in held))
        stage_key = (ctx.get("stage") or {}).get("key")
        found = dose(ledger, model, now=now, stage_key=stage_key)
        if found is None:
            return skip("cap", "thirst.gate.cap")
        flow = self.flow(now)
        limit_name = found["capped_by"]
        if not self.power_confirmed():
            return skip("no_power", "thirst.gate.no_power")
        if not flow["calibrated"]:
            return skip("no_flow", "thirst.gate.no_flow")
        rate_l_s = float(flow["ml_s"]) * (1 + float(model("flow_margin"))) / 1000
        allowed = int(math.floor(found["litres"] / rate_l_s - float(model("stop_margin_s"))))
        if min(allowed, duration_s) < float(model("stop_margin_s")):
            return skip("too_short", "thirst.gate.too_short", litres=lambda la: litres_text(found["litres"], la))
        if allowed < duration_s:
            return {"run": True, "max_s": allowed, "reason": "shortened", "litres": found["litres"],
                    "text": both(lambda la: t("thirst.gate.run_short", la, ti=ti_text(ti, la),
                                              seconds=fmt_num(allowed, la), litres=litres_text(found["litres"], la),
                                              limit=t(f"thirst.limit.{limit_name}", la)))}
        litres = duration_s * rate_l_s
        return {"run": True, "max_s": None, "reason": "full", "litres": round(litres, 3),
                "text": both(lambda la: t("thirst.gate.run_full", la, ti=ti_text(ti, la),
                                          seconds=fmt_num(duration_s, la), litres=litres_text(litres, la),
                                          limit=litres_text(found["litres"], la)))}

    # ---------- the forecast and the plan ----------

    def _light_plan(self, snap: Mapping[str, Any], now: float, until: float) -> LightPlan:
        timer = getattr(self.light, "cfg", None)
        level = _num((snap.get("light") or {}).get("level")) if isinstance(snap.get("light"), Mapping) else None
        inputs = self.state.get("inputs") if isinstance(self.state.get("inputs"), dict) else {}
        return LightPlan(timer if isinstance(timer, Mapping) else None, level, last_seen=_num(inputs.get("light")),
                         start=now, end=until, tz=self.tz)

    def _vpd_now(self, snap: Mapping[str, Any]) -> float | None:
        climate = snap.get("climate") if isinstance(snap.get("climate"), Mapping) else {}
        inputs = self.state.get("inputs") if isinstance(self.state.get("inputs"), dict) else {}
        try:
            gap = float(load_model(self.model_path)("mist_gap_points"))
        except ModelError:
            gap = 20.0
        probe_h, box_h = _num(climate.get("probe_humidity")), _num(climate.get("humidity"))
        mist = probe_h is not None and box_h is not None and probe_h - box_h > gap
        vpd = None if mist else _num(climate.get("probe_vpd"))
        return vpd if vpd is not None else _num(inputs.get("vpd_clear")) or _num(inputs.get("vpd"))

    def _ahead(self, model: Model, light: LightPlan, start: float, target_l: float, *, vpd: float | None,
               band: float, d_trig: float, horizon: float) -> dict[str, float | None]:
        """Step the modelled ET forward from start: when the cumulative use reaches target_l (due), target_l minus the
        band (earliest) and target_l plus the band (latest), the band growing band_per_day_ti a day."""
        per_day = float(model("band_per_day_ti"))
        height = self.height_cm()
        lit = light.lit_level()
        cum, at = 0.0, start
        early = start if target_l - band / 100 * d_trig <= 0 else None
        due = start if target_l <= 0 else None
        late = None
        end = start + horizon
        while at < end and late is None:
            rate = et_rate_ml_h(model, light=light.at(at), vpd=vpd, height_cm=height, last_lit=lit) / 1000 / 3600
            nxt = at + FORECAST_STEP_S
            cum += rate * FORECAST_STEP_S
            width = (band + per_day * (nxt - start) / 86400) / 100 * d_trig
            if early is None and cum >= target_l - width:
                early = nxt
            if due is None and cum >= target_l:
                due = nxt
            if cum >= target_l + width:
                late = nxt
            at = nxt
        return {"early": early, "due": due, "late": late}

    def _water_at(self, model: Model, light: LightPlan, due: float) -> float:
        """When a pot due at `due` is watered: then, when that is inside a daily round, else at the start of the next
        round. The round is lights-on plus 1 h by the light timer when it runs (its own switch times ahead), else what
        trigger() uses now: the lamp's last switch-on, or 09:00 box-local time."""
        window = float(model("round_window_s"))
        start: float | None = None
        if light.kind == "timer":
            after = float(model("round_after_lights_on_s"))
            start = next((edge + after for edge, on in light.edges if on and edge + after + window > due), None)
        if start is None:
            lights = self.state.get("lights") if isinstance(self.state.get("lights"), dict) else {}
            start = next_round(due, model, lights_edge=_num(lights.get("edge_at")), tz=self.tz)
        return due if start <= due else start

    def day_use_l(self, model: Model, light: LightPlan, day_start: float, *, vpd: float | None) -> float:
        """The modelled use of one day from day_start, in litres, with the light plan."""
        height = self.height_cm()
        lit = light.lit_level()
        litres, at = 0.0, day_start
        while at < day_start + 86400:
            litres += et_rate_ml_h(model, light=light.at(at), vpd=vpd, height_cm=height,
                                   last_lit=lit) / 1000 / 3600 * FORECAST_STEP_S
            at += FORECAST_STEP_S
        return litres

    def forecast(self, ledger: Ledger, decision: Decision, ctx: Mapping[str, Any], snap: Mapping[str, Any], now: float,
                 lang: str) -> dict[str, Any] | None:
        """"Nächstes Gießen voraussichtlich <Tag, Tageszeit>, etwa <Liter>", with the earliest and the latest time the
        band allows, the check that comes first, and the watering slot that fits (when the schedule is on)."""
        model: Model | None = ctx.get("model")
        if model is None or ledger.pot is None:
            return None
        if ledger.ti is None or ledger.d_trig_l is None:
            return {"kind": "none", "text": t("thirst.forecast.none", lang)}
        horizon = float(model("forecast_horizon_s"))
        light = self._light_plan(snap, now, now + horizon)
        vpd = self._vpd_now(snap)
        stage_key = (ctx.get("stage") or {}).get("key")
        in_hold = ledger.hold is not None
        deficit = float(ledger.hold["d_l"]) if in_hold else float(ledger.d_l or 0)
        target = max(0.0, ledger.d_trig_l - deficit)
        ahead = self._ahead(model, light, now, target, vpd=vpd, band=float(ledger.band or 0), d_trig=ledger.d_trig_l,
                            horizon=horizon)
        planned = dose(replace(ledger, d_l=ledger.d_trig_l, hard_drydown=False), model,
                       now=float(ahead["due"] or now), stage_key=stage_key)
        litres = planned["litres"] if planned else None
        check = check_text(Decision("ASK", "check", check=decision.check or check_of(ledger, model)), lang)
        out: dict[str, Any] = {"due_at": ahead["due"], "early_at": ahead["early"], "late_at": ahead["late"],
                               "litres": litres, "hold": in_hold, "slot_at": None,
                               "light": light.kind, "vpd": vpd}
        lines: list[str] = []
        litres_said = litres_text(litres, lang) if litres else "?"
        if ahead["due"] is None:
            out["kind"] = "beyond"
            lines.append(t("thirst.forecast.beyond", lang, days=fmt_num(round(horizon / 86400), lang)))
        elif in_hold:
            out["kind"] = "hold"
            lines.append(t("thirst.forecast.hold", lang, when=part_text(float(ahead["due"]), now, lang, self.tz)))
        elif ahead["due"] <= now + 1 and ledger.measured is not None:
            out["kind"] = "measured"
            lines.append(t("thirst.forecast.measured", lang, litres=litres_said))
        elif ahead["due"] <= now + 1:
            out["kind"] = "now"
            lines.append(t("thirst.forecast.now", lang, litres=litres_said))
        else:
            out["kind"] = "due"
            due = float(ahead["due"])
            water_at = self._water_at(model, light, due)
            out["water_at"] = water_at
            due_said, water_said = part_text(due, now, lang, self.tz), part_text(water_at, now, lang, self.tz)
            if water_at - due > float(model("forecast_round_note_s")) and due_said != water_said:
                # due outside the daily round (at night, say): a farmer waters at the next round, not at 2 a.m.
                lines.append(t("thirst.forecast.due_round", lang, due=due_said, when=water_said, litres=litres_said))
            else:
                lines.append(t("thirst.forecast.due", lang, when=water_said, litres=litres_said))
        if out["kind"] in ("hold", "due"):
            early = part_text(float(ahead["early"] or now), now, lang, self.tz)
            if ahead["late"] is not None:
                lines.append(t("thirst.forecast.range", lang, early=early,
                               late=part_text(float(ahead["late"]), now, lang, self.tz)))
            else:
                lines.append(t("thirst.forecast.range_open", lang, early=early))
        if ahead["due"] is not None and not in_hold:
            slot = self.next_slot(max(now, float(ahead["due"]) - 1))
            if slot is not None:
                out["slot_at"] = slot
                lines.append(t("thirst.forecast.slot", lang, slot=when_text(slot, now, lang, self.tz)))
            elif self.schedule() is not None:
                lines.append(t("thirst.forecast.hand", lang))
        out["core"] = " ".join(lines)  # without the check: Hank says the check in its own line
        if ahead["due"] is not None and not in_hold and check and out["kind"] != "measured":
            lines.append(t("thirst.forecast.check", lang, check=check))
        out["lines"] = lines
        out["text"] = " ".join(lines)
        return out

    def plan(self, ledger: Ledger, ctx: Mapping[str, Any], snap: Mapping[str, Any], now: float,
             lang: str) -> dict[str, Any] | None:
        """The coming weeks by stage, like the feeding plan: per week the stage, the trigger (D_trig, the index's 100),
        the cap of one watering, the modelled use a day, how often that waters and the water of the week. What is
        measured and what is estimated is said in basis; every value of a later week is an estimate."""
        model: Model | None = ctx.get("model")
        profile = ctx.get("profile")
        stage = ctx.get("stage")
        if model is None or ledger.pot is None or ledger.v_pot_l is None or not profile:
            return None
        shown = localize(profile, lang)
        stages = [s for s in shown.get("stages") or [] if isinstance(s, Mapping)]
        today = local_day(now, self.tz)
        start = parse_day((shown.get("plant") or {}).get("start_date"))
        timeline = stage_timeline(stages, stage, today, start)
        if not timeline:
            return None
        by_key = {s.get("key"): s for s in stages}
        weeks_max = int(model("plan_weeks_max"))
        horizon = weeks_max * 7 * 86400
        light = self._light_plan(snap, now, now + horizon)
        vpd_now = self._vpd_now(snap)
        a_w = float(model("a_w"))
        weeks: list[dict[str, Any]] = []
        for index in range(weeks_max):
            first = today + timedelta(days=7 * index)
            entry = next((s for s in timeline if s["from"] <= first < s["until"]), None)
            if entry is None:
                break
            key = entry["key"]
            f = f_trig_for(model, key, ledger.pot)
            d_trig = f * a_w * ledger.v_pot_l
            cap, cap_by = event_cap_l(ledger, model, key)
            vpd = vpd_now if index == 0 else stage_band_mid(by_key.get(key)) or vpd_now
            day_start = max(now, at_local(first, 0, 0, self.tz))
            use = self.day_use_l(model, light, day_start, vpd=vpd)
            weeks.append({"from": first.isoformat(), "stage": key, "label": (by_key.get(key) or {}).get("label") or key,
                          "stage_estimated": bool(entry["estimated"]),
                          "f_trig": f, "d_trig_l": round(d_trig, 3), "cap_l": round(cap, 3), "cap_by": cap_by,
                          "vpd": round(vpd, 2) if vpd is not None else None,
                          "vpd_measured": index == 0 and vpd_now is not None, "use_l_day": round(use, 3),
                          "every_days": round(d_trig / use, 1) if use > 0 else None,
                          "week_l": round(use * 7, 2), "now": index == 0})
            if key == "harvest":
                break
        basis = self._basis(ledger, stage, light, vpd_now, timeline, lang)
        return {"weeks": weeks, "basis": basis, "stage_from": timeline[0]["from"].isoformat()}

    def _basis(self, ledger: Ledger, stage: Mapping[str, Any] | None, light: LightPlan, vpd: float | None,
               timeline: list[dict[str, Any]], lang: str) -> list[dict[str, Any]]:
        """What the forecast and the plan stand on, each marked measured (or entered) or estimated."""
        out: list[dict[str, Any]] = []

        def add(key: str, measured: bool, **params: Any) -> None:
            out.append({"text": t(key, lang, **params), "measured": measured})

        add("thirst.basis.pot", True, litres=litres_text(float(ledger.v_pot_l or 0), lang))
        label = str((localize(dict(stage), lang) if stage else {}).get("label") or (stage or {}).get("key") or "?")
        if stage and stage.get("set_by") == "manual":
            add("thirst.basis.stage_set", True, stage=label)
        else:
            add("thirst.basis.stage_estimated", False, stage=label)
        if light.kind == "timer" and light.timer:
            add("thirst.basis.light_timer", True, on=light.timer["on_time"], off=light.timer["off_time"],
                level=fmt_qty(int(light.timer["level"]), "%", lang))
        elif light.kind == "held":
            add("thirst.basis.light_held", True, level=fmt_qty(int(round(light.level or 0)), "%", lang))
        else:
            add("thirst.basis.light_unknown", False)
        if vpd is not None:
            add("thirst.basis.vpd", True, vpd=fmt_qty(round(vpd, 2), "kPa", lang))
        else:
            add("thirst.basis.vpd_unknown", False)
        height = self.height()
        if height and height.get("date"):
            day = parse_day(height["date"])
            add("thirst.basis.height_estimated" if height["estimated"] else "thirst.basis.height",
                not height["estimated"], cm=fmt_qty(int(round(height["cm"])), "cm", lang),
                date=day_text(day, lang) if day else str(height["date"]))
        else:
            add("thirst.basis.height_none", False)
        if ledger.w_ref is None:
            add("thirst.basis.aw", False)
        add("thirst.basis.k0", False)
        add("thirst.basis.future", False)
        if len(timeline) > 1:
            add("thirst.basis.stages", False, date=day_text(timeline[0]["until"], lang))
            add("thirst.basis.vpd_later", False)
        return out

    # ---------- the owner's inputs (/api/cmd thirst_*) ----------

    def set_pot(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """thirst_pot: the pot, its size (litres, or diameter and height), drainage, saucer, feet, wick, whether it
        lifts, the scale, the late-flower dry-back. Two taps when the typed litres and the geometry are more than 20 %
        apart, and when another pot exists already (a new pot resets the ledger). The same size with other flags keeps
        the pot and its ledger (review S4)."""
        model = load_model(self.model_path)
        volume = _parse_float(body.get("volume_l"))
        diameter = _parse_float(body.get("diameter_cm"))
        height = _parse_float(body.get("height_cm"))
        if volume is not None and not 0.5 <= volume <= 100:
            raise UserError(t("thirst.error.pot_size"))
        for value in (diameter, height):
            if value is not None and not 5 <= value <= 80:
                raise UserError(t("thirst.error.pot_size"))
        if volume is None and (diameter is None or height is None):
            raise UserError(t("thirst.error.pot_size"))
        step = _parse_float(body.get("scale_step_g"))
        if step is not None and not 1 <= step <= 100:
            raise UserError(t("thirst.error.step"))
        old = self.pot()
        same = old is not None and (old.volume_l, old.diameter_cm, old.height_cm) == (volume, diameter, height)
        pot = Pot(volume_l=volume, diameter_cm=diameter, height_cm=height, drain=_flag(body.get("drain")),
                  saucer=_flag(body.get("saucer")), feet=_flag(body.get("feet")), wick=_flag(body.get("wick")),
                  lifts=_flag(body.get("lifts")), scale=_flag(body.get("scale")), scale_step_g=step,
                  dryback=_flag(body.get("dryback")), substrate="soil",
                  set_at=old.set_at if same and old else self.clock(),
                  id=old.id if same and old else f"pot-{uuid.uuid4().hex[:8]}")
        confirmed = _flag(body.get("confirm")) is True
        geo = pot.geometry_l()
        if volume is not None and geo is not None and not confirmed and \
                abs(volume - geo) > float(model("pot_check_tolerance")) * max(volume, geo):
            return {"ok": False, "ask": "mismatch",
                    "text": t("thirst.error.pot_mismatch", typed=litres_text(volume), geo=litres_text(geo))}
        if old is not None and not same and not confirmed:
            return {"ok": False, "ask": "reset", "text": t("thirst.error.pot_reset")}
        config.atomic_write(self.pot_path(), json.dumps(pot.as_dict(), ensure_ascii=False, indent=2) + "\n")
        if not same:
            self.log({"kind": "pot", "pot": pot.id, "litres": pot.volume()}, at=pot.set_at)
            self.state["ask"] = None
            self.state["blocked"] = None
        self.save()
        return {"ok": True, "pot": pot.as_dict(), "kept": same}

    def _need_pot(self) -> Pot:
        pot = self.pot()
        if pot is None:
            raise UserError(t("thirst.error.no_pot"))
        return pot

    def _at(self, value: Any, pot: Pot) -> float:
        """A time from the page: epoch seconds, or a local ISO date-time; default now. Never in the future, at most
        7 days back, never before the pot was set up."""
        now = self.clock()
        if value in (None, ""):
            return now
        stamp = _num(value)
        if stamp is None:
            try:
                parsed = datetime.fromisoformat(str(value))
            except ValueError:
                raise UserError(t("thirst.error.time")) from None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=self.tz)
            stamp = parsed.timestamp()
        if stamp > now + 60:
            raise UserError(t("thirst.error.time"))
        if now - stamp > MAX_BACK_S or stamp < pot.set_at:
            raise UserError(t("thirst.error.time_old"))
        return min(stamp, now)

    def weigh(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """thirst_weigh: a weight in kg; full: the pot was watered with runoff and has drained 2 h (the reference)."""
        pot = self._need_pot()
        kg = _parse_float(body.get("kg"))
        if kg is None or not 0.2 <= kg <= 100:
            raise UserError(t("thirst.error.kg"))
        entry = self.log({"kind": "weigh", "kg": round(kg, 3), "full": _flag(body.get("full")) is True},
                         at=self._at(body.get("at"), pot))
        self.state["ask"] = None
        self.save()
        return {"ok": True, "entry": entry}

    def check_answer(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """thirst_check: the one-tap check, finger (trocken, feucht, nass) or lifting the pot (leicht, mittel,
        schwer)."""
        pot = self._need_pot()
        model = load_model(self.model_path)
        answer = str(body.get("answer") or "")
        if answer not in (*FINGER_ANSWERS, *LIFT_ANSWERS):
            raise UserError(t("thirst.error.answer", answer=answer or "?"))
        depth = _parse_float(body.get("depth_cm"))
        if depth is not None and depth not in (2.5, 5.0):
            depth = None
        entry = self.log({"kind": "check", "answer": answer,
                          "depth_cm": None if answer in LIFT_ANSWERS else depth or pot.depth_cm(model)},
                         at=self._at(body.get("at"), pot))
        self.state["ask"] = None
        self.save()
        return {"ok": True, "entry": entry}

    def sensor_check(self, view: Mapping[str, Any]) -> None:
        """Hook for the soil sensor (vgrow/soil.py, on_reading): a calibrated reading is a check like the finger, below
        the target dry, in the band moist, above it wet. Written once an answer has held for two readings, and again
        every SENSOR_EVERY_S. It only logs: what a check does to the index, the holds and the gate stays in the ledger."""
        answer = {"low": "dry", "ok": "moist", "high": "wet"}.get(view.get("state"))
        if answer is None or self.pot() is None:
            return
        now = self.clock()
        seen = self.state.get("sensor") or {}
        if answer != seen.get("seen"):
            self.state["sensor"] = {**seen, "seen": answer}
            return
        if answer == seen.get("answer") and now - float(seen.get("at") or 0) < SENSOR_EVERY_S:
            return
        self.log({"kind": "check", "answer": answer, "how": "sensor", "depth_cm": SENSOR_DEPTH_CM,
                  "percent": view.get("percent"), "relative": view.get("relative")}, at=now)
        self.state["sensor"] = {"seen": answer, "answer": answer, "at": now}
        self.state["ask"] = None
        self.save()

    def manual(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """thirst_manual: water given by hand, its litres; the time defaults to now. Above manual_confirm_above_l the
        page asks a second time (large)."""
        pot = self._need_pot()
        model = load_model(self.model_path)
        litres = _parse_float(body.get("litres"))
        if litres is None or not MIN_LITERS <= litres <= MAX_LITERS:
            raise UserError(t("thirst.error.liters", min=fmt_num(MIN_LITERS), max=fmt_num(MAX_LITERS)))
        if litres > float(model("manual_confirm_above_l")) and _flag(body.get("large")) is not True:
            return {"ok": False, "ask": "large", "text": t("thirst.error.liters_large", liters=litres_text(litres))}
        entry = self.log({"kind": "water", "litres": round(litres, 3), "origin": "hand"},
                         at=self._at(body.get("at"), pot))
        self.state["ask"] = None
        self.save()
        return {"ok": True, "entry": entry}

    def refilled(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """thirst_refill: the tank got its litres, with feed or without (logged; a tank meter reads this later)."""
        litres = _parse_float(body.get("litres"))
        if litres is None or not 0.1 <= litres <= 10:
            raise UserError(t("thirst.error.refill"))
        entry = self.log({"kind": "refill", "litres": round(litres, 2), "feed": _flag(body.get("feed")) is True})
        return {"ok": True, "entry": entry}

    def _water_ids(self) -> set[str]:
        return {str(e.get("id")) for e in self.entries() if e.get("kind") == "water" and e.get("id")}

    def saucer(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """thirst_saucer: water in the saucer (the pot ran over: overshoot, wet-hold) or the saucer dry. An event id,
        when given, must be a watering of the log (review N4)."""
        pot = self._need_pot()
        water = _flag(body.get("water"))
        if water is None:
            raise UserError(t("thirst.error.answer", answer=str(body.get("water"))))
        event = body.get("event")
        if event not in (None, "") and str(event) not in self._water_ids():
            raise UserError(t("thirst.error.event"))
        entry = self.log({"kind": "saucer", "water": water, "event": str(event) if event else None},
                         at=self._at(body.get("at"), pot))
        self.save()
        return {"ok": True, "entry": entry}

    def arrived(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """thirst_arrived: did the water of a watering or a pump run reach the pot? Ja, Untersetzer trocken / Ja,
        Wasser im Untersetzer / Nein (then that event's water is 0)."""
        self._need_pot()
        answer = str(body.get("answer") or "")
        if answer not in ARRIVED_ANSWERS:
            raise UserError(t("thirst.error.answer", answer=answer or "?"))
        event = str(body.get("event") or "")
        if event not in self._water_ids():
            raise UserError(t("thirst.error.event"))
        entry = self.log({"kind": "arrived", "event": event, "answer": answer})
        self.save()
        return {"ok": True, "entry": entry}

    def flow_test(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """thirst_flow: one jug run of the pump, the seconds it ran and the millilitres in the jug (the owner runs it,
        e.g. with Einmal gießen into a measuring jug); {"reset": true} discards the measurements (a new hose or pump)."""
        if _flag(body.get("reset")) is True:
            entry = self.log({"kind": "flow_reset"})
            return {"ok": True, "entry": entry}
        seconds = _parse_float(body.get("seconds"))
        ml = _parse_float(body.get("ml"))
        if seconds is None or ml is None or not FLOW_SECONDS[0] <= seconds <= FLOW_SECONDS[1] \
                or not FLOW_ML[0] <= ml <= FLOW_ML[1]:
            raise UserError(t("thirst.error.flow"))
        seconds, ml = round(seconds, 1), round(ml, 1)
        model = load_model(self.model_path)
        reference = float(model("flow_reference_ml_min"))
        factor = float(model("flow_plausibility_factor"))
        rate = ml / seconds * 60
        unusual = not reference / factor <= rate <= reference * factor
        if unusual and _flag(body.get("confirm")) is not True:
            return {"ok": False, "ask": "flow", "text": t("thirst.flow.confirm", rate=fmt_qty(round(rate, 1), "ml/min"))}
        entry = self.log({"kind": "flow", "seconds": seconds, "ml": ml, "confirmed_outlier": unusual})
        return {"ok": True, "entry": entry, "flow": self.flow()}

    # ---------- views ----------

    def _open_arrival(self, ledger: Ledger, now: float) -> dict[str, Any] | None:
        """The last watering of the last 24 h without an arrival or saucer answer, for the question under it."""
        answered = {e.get("event") for e in self.entries() if e.get("kind") in ("arrived", "saucer") and e.get("event")}
        for w in sorted(ledger.waterings, key=lambda w: -float(w["at"])):
            if now - float(w["at"]) > 86400 or w.get("origin") == "feed":
                return None
            if w.get("id") and w["id"] not in answered:
                later = [e for e in ledger.events if e.get("kind") == "saucer" and float(e["at"]) >= float(w["at"])]
                if later:
                    return None
                return {"event": w["id"], "at": w["at"], "pump": bool(w.get("pump"))}
            return None
        return None

    def _pump_view(self, snap: Mapping[str, Any], pump: list[str], now: float, lang: str) -> dict[str, Any]:
        ports = snap.get("ports") if isinstance(snap.get("ports"), Mapping) else {}
        port = pump[0] if pump else "usb1"
        report = (ports or {}).get(port) or {}
        run = (self.state.get("runs") or {}).get(port) if isinstance(self.state.get("runs"), dict) else None
        flow = self.flow(now)
        power = self.power_confirmed()
        counts = "litres" if power and flow["calibrated"] else "unknown" if power else "nothing"
        if flow["calibrated"]:
            rate = fmt_qty(round(float(flow["ml_s"]) * 60), "ml/min", lang)
            flow_text = t("thirst.flow.ok", lang, rate=rate, date=day_text(local_day(float(flow["at"]), self.tz), lang),
                          until=day_text(local_day(float(flow["until"]), self.tz), lang))
        elif flow.get("unverified"):
            flow_text = t("thirst.flow.unusual", lang)
        elif flow.get("far"):
            a, b = flow["far"][0], flow["far"][-1]
            flow_text = t("thirst.flow.far", lang, a=fmt_qty(round(a * 60), "ml/min", lang),
                          b=fmt_qty(round(b * 60), "ml/min", lang))
        elif flow.get("one"):
            flow_text = t("thirst.flow.one", lang, rate=fmt_qty(round(float(flow["one"]) * 60), "ml/min", lang))
        else:
            flow_text = t("thirst.flow.none", lang)
        on_text = None
        if report.get("on") is True and isinstance(run, Mapping):
            on_text = t("thirst.pump.on", lang, port=port_name(port),
                        h=hours_text(now - float(run.get("first_on") or now), lang),
                        how=t(f"thirst.pump.how_{counts}", lang))
        return {"port": port, "label": port_name(port), "named": self.guard.owner_device(port) == PUMP,
                "on": report.get("on"), "on_since": (run or {}).get("first_on") if isinstance(run, Mapping) else None,
                "power_confirmed": power, "flow": {k: v for k, v in flow.items() if k != "runs"},
                "flow_runs": len(flow.get("runs") or []), "counts": counts, "flow_text": flow_text,
                "power_text": None if power else t("thirst.flow.power_no", lang), "on_text": on_text}

    def _manual_note(self, ledger: Ledger, ctx: Mapping[str, Any], lang: str) -> dict[str, Any]:
        """The line next to Einmal gießen on the watering card: the index, and a warning when the pot is not due."""
        model: Model | None = ctx.get("model")
        if ledger.pot is None or model is None:
            return {"text": t("thirst.manual.no_pot", lang), "warn": True}
        if ledger.ti is None:
            return {"text": t("thirst.manual.unknown", lang), "warn": True}
        ti = ti_text(ledger.ti, lang)
        if ledger.hold is not None or (ledger.wet_until is not None and self.clock() < ledger.wet_until):
            return {"text": t("thirst.manual.hold", lang, ti=ti), "warn": True}
        if ledger.measured is None:
            return {"text": t("thirst.manual.unmeasured", lang, ti=ti), "warn": True}
        if ledger.ti < float(model("due_ti")):
            return {"text": t("thirst.manual.not_due", lang, ti=ti), "warn": True}
        return {"text": t("thirst.manual.due", lang, ti=ti), "warn": False}

    def _gate_preview(self, ledger: Ledger, ctx: Mapping[str, Any], now: float, lang: str) -> dict[str, Any] | None:
        """What the need check would say if the next scheduled slot came now (shown while the schedule is on)."""
        cfg = self.schedule()
        nxt = self.next_slot(now)
        if not cfg or not cfg.get("enabled") or nxt is None or not cfg.get("duration_s"):
            return None
        verdict = self._verdict(ledger, ctx, now, int(cfg["duration_s"]))
        reason = verdict["text"][lang] if lang in verdict["text"] else verdict["text"]["de"]
        key = "thirst.gate.preview_run" if verdict["run"] else "thirst.gate.preview_skip"
        return {"next_at": nxt, "run": verdict["run"], "reason": verdict["reason"],
                "text": t(key, lang, time=when_text(nxt, now, lang, self.tz), reason=reason), "detail": reason}

    def view(self, snap: Mapping[str, Any], lang: str | None = None) -> dict[str, Any]:
        """What the page's card shows (GET /api/thirst, /api/state "thirst"), texts in `lang`."""
        lang = i18n.normalize(lang) or i18n.current_lang()
        now = self.clock()
        ledger, decision, ctx = self.assess(snap, now)
        stage = ctx.get("stage")
        ask = self.state.get("ask") if isinstance(self.state.get("ask"), dict) else None
        model: Model | None = ctx.get("model")
        pot = ledger.pot
        try:
            forecast = self.forecast(ledger, decision, ctx, snap, now, lang) if pot else None
        except Exception:  # noqa: BLE001 (a broken forecast costs its line, never the card)
            _LOGGER.exception("Thirst: forecast failed")
            forecast = None
        try:
            plan = self.plan(ledger, ctx, snap, now, lang) if pot else None
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Thirst: plan failed")
            plan = None
        shown_stage = localize(dict(stage), lang) if stage else None
        week = [w for w in ledger.waterings if now - float(w["at"]) < 7 * 86400
                and (w.get("litres") is None or w["litres"] > 0)]
        return {
            "box": self.box_id,
            "pot": pot.as_dict() if pot else None,
            "pot_volume_l": ledger.v_pot_l,
            "depth_cm": pot.depth_cm(model) if pot and model else None,
            "ti": None if ledger.ti is None else round(ledger.ti, 1),
            "band": ledger.band,
            "confidence": ledger.confidence if ledger.ti is not None else None,
            "d_l": None if ledger.d_l is None else round(ledger.d_l, 3),
            "d_trig_l": None if ledger.d_trig_l is None else round(ledger.d_trig_l, 3),
            "aw_l": None if ledger.aw_l is None else round(ledger.aw_l, 3),
            "w_ref": ledger.w_ref,
            "measured": ledger.measured,
            "last_check": ledger.last_check,
            "last_weigh": ledger.last_weigh,
            "overshoot": ledger.overshoot,
            "hold": None if ledger.hold is None else {"since": ledger.hold["since"], "dry": len(ledger.hold["dry"])},
            "drains_without": ledger.drains_without,
            "rate_l_day": None if ledger.rate_l_day is None else round(ledger.rate_l_day, 3),
            "stage": {"key": (stage or {}).get("key"), "label": (shown_stage or {}).get("label"),
                      "contested": not stage or stage.get("set_by") != "manual", "f_trig": ledger.f_trig},
            "decision": {"kind": decision.kind, "reason": decision.reason,
                         "text": decision_text(decision, ledger, now, lang, self.tz),
                         "check": decision.check, "check_text": check_text(decision, lang),
                         "dose": decision.dose, "next_look": decision.next_look,
                         "why": why_lines(decision, ledger, now, lang, stage)},
            "blocks": [{"key": b["key"], "text": block_text(b, lang, self.tz)} for b in decision.blocks],
            "waiting_s": None if ask is None else max(0.0, now - float(ask["since"])),
            "pump": self._pump_view(snap, ctx.get("pump") or [], now, lang),
            "arrival": self._open_arrival(ledger, now),
            "events": [{"at": e["at"], "kind": e.get("kind"), "origin": e.get("origin"),
                        "text": event_text(e, lang)} for e in ledger.events],
            # hook: the water dashboard's tile (web/water.js); a watering of unknown amount counts once, without litres
            "given_week_l": round(sum((w.get("litres") or 0) for w in week), 3),
            "given_week_n": len(week),
            "forecast": forecast,
            "plan": plan,
            "gate": self._gate_preview(ledger, ctx, now, lang),
            "manual": self._manual_note(ledger, ctx, lang),
            "model_error": ledger.model_error,
            "manual_confirm_above_l": float(model("manual_confirm_above_l")) if model else None,
            "demo": self.demo,
        }

    @staticmethod
    def _check_line(ledger: Ledger, decision: Decision, model: Model | None, lang: str) -> str | None:
        """The one-tap check of the decision, or, when the decision asks nothing, the pot's usual check."""
        said = check_text(decision, lang)
        if said is not None or ledger.pot is None or model is None:
            return said
        return check_text(Decision("ASK", "check", check=decision.check or check_of(ledger, model)), lang)

    def brief(self, snap: Mapping[str, Any]) -> dict[str, Any]:
        """What Hank reads (server.HankBox puts it into his snapshot under "thirst"): the index, the decision, the
        forecast and the one-tap check, every text in both languages. Hank imports nothing of this module and switches
        nothing."""
        now = self.clock()
        ledger, decision, ctx = self.assess(snap, now)
        stage = ctx.get("stage")
        model: Model | None = ctx.get("model")
        forecasts: dict[str, Any] = {}
        for lang in ("de", "en"):
            try:
                forecasts[lang] = self.forecast(ledger, decision, ctx, snap, now, lang) if ledger.pot else None
            except Exception:  # noqa: BLE001
                forecasts[lang] = None
        planned = (forecasts.get("de") or {}).get("litres")
        cap = event_cap_l(ledger, model, (stage or {}).get("key"))[0] if model and ledger.v_pot_l else None
        gate = {lang: self._gate_preview(ledger, ctx, now, lang) for lang in ("de", "en")}
        flow = self.flow(now)
        return {
            "configured": ledger.pot is not None,
            "ti": None if ledger.ti is None else round(ledger.ti, 1),
            "band": ledger.band,
            "confidence": ledger.confidence if ledger.ti is not None else None,
            "measured": ledger.measured is not None,
            "kind": decision.kind,
            "reason": decision.reason,
            "text": {lang: decision_text(decision, ledger, now, lang, self.tz) for lang in ("de", "en")},
            "why": {lang: why_lines(decision, ledger, now, lang, stage) for lang in ("de", "en")},
            "check": {lang: self._check_line(ledger, decision, model, lang) for lang in ("de", "en")},
            "blocks": [{"key": b["key"], "text": {lang: block_text(b, lang, self.tz) for lang in ("de", "en")}}
                       for b in decision.blocks],
            "dose_l": (decision.dose or {}).get("litres"),
            "planned_l": planned,
            "cap_l": None if cap is None else round(cap, 3),
            "hold": ledger.hold is not None,
            "forecast": {lang: (forecasts[lang] or {}).get("core") or (forecasts[lang] or {}).get("text")
                         for lang in ("de", "en")},
            "schedule_note": {lang: (gate.get(lang) or {}).get("text") for lang in ("de", "en")},
            "pump_amount": "litres" if flow["calibrated"] and self.power_confirmed() else
                           "unknown" if self.power_confirmed() else "nothing",
            "stage_key": (stage or {}).get("key"),
        }


def closed_gate(slot_at: float, duration_s: int) -> dict[str, Any]:
    """The gate when the Durst-Index could not be built: every scheduled slot is skipped (fail closed)."""
    return {"run": False, "max_s": None, "reason": "error", "text": both(lambda la: t("thirst.gate.error", la))}


ACTIONS = {  # /api/cmd action -> Thirst method; none of them switches anything
    "thirst_pot": "set_pot", "thirst_weigh": "weigh", "thirst_check": "check_answer", "thirst_manual": "manual",
    "thirst_refill": "refilled", "thirst_saucer": "saucer", "thirst_arrived": "arrived", "thirst_flow": "flow_test",
}
