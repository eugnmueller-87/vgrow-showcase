"""The grow plan: one plant, its stage and its height, and who does what for it today.

The owner, 06.11.2026: "set up the entire automation", a watering observer that checks "how much water the plant actually
needs", the light "as much as possible", the fertilizer "in 2 weeks", all of it "based on the requirements of the plant in
[its] specific state", and "one of the cameras is pointing exactly at the meter".

The parts run on their own already: the light timer (light_schedule.py), the autopilot's fans (autopilot.py), the humidifier
rule (automation.py), the E6's rule (fan_stick.py), the watering slots behind the need gate (watering.py, thirst.py) and the
soil sensor (soil.py). They all read the stage. This module adds what connects them to the plant:

- Light. The timer owns the brightness; this module tells it the level of the day. Once the owner set a goal it starts from
  the level running then and raises it by RAISE_STEP once per light period, up to the goal. It decides in the dark,
  so a new level starts with the next lights on. It holds when the last period ran too warm or too dry, when the lamp is too
  close, or when the owner set a level himself; it steps back when its own step made it too hot. Off stays the owner's: it
  never switches the timer on. Without a goal the stage card's preset stands.
- Spectrum. Once per stage the card's spectrum. A change by the owner after that stays.
- Height. Every few hours with the lamp on a photo from the GrowCam goes to ruler.py. The day's median goes into the plant log
  (the thirst index and the lamp distance read it) and onto the plant's record. An implausible jump counts only when the next
  reading agrees. A height the owner types wins on its day and sets the offset for the readings after it.
- Marks. The strip prints what to do at which height: trellis up to 20 cm, topping 20 to 25 (photo strains only), flowering 25
  to 30, lollipopping from 40. Each one pushes once when the plant reaches it. Switching to flower stays the owner's tap; the
  light then follows the stage card by itself. The strip's flowering field fits the rule of thumb: a plant about doubles when
  it flowers (S43), 92 cm growing height minus 35 cm to the lamp leaves 57 cm, half of that is 28 cm.
- Feeding. Plain water until the owner's first day with fertilizer (plant log feed_from, feeding.evaluate), one push on the day.
- Water watch. The need gate decides; this module sets it up: two slots a day that follow the light plan, a run time from the
  stage's cap and the measured flow (the gate shortens it to the dose). After every pump run it looks at the soil sensor: did
  the water arrive? USB power is no water flow (26.10.2026), so a run the sensor did not feel gets one push.
- view(): all of it on one card, with the few steps only the owner's hands can do.

It never starts the pump on its own: the only runs it starts are the owner's taps (the jug test, one litre). Automatic runs
stay the slots behind the need gate. It never sets the stage. Nothing here decides from a language model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import statistics
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import config, i18n, ruler, workspace
from .autopilot import DFAN_MAX, LAMP_MIN_CM, LIGHT_HEAT_HOLD, LIGHT_HEAT_MARGIN, bands
from .box import CommandRefused
from .budget import BUDGET, CommandBudget
from .i18n import fmt_num, fmt_qty, fmt_time, localize, t
from .light_schedule import boundaries
from .plant import (
    FLOWERING_STAGES,
    PRE_FLOWER_STAGES,
    PlantLog,
    current_stage,
    date_text,
    load_profile,
    parse_day,
    profile_problem,
)
from .portguard import PortGuard
from .thirst import ModelError, load_model, stage_cap_l
from .vivosun.const import LIGHT_MIN_BRIGHTNESS

_LOGGER = logging.getLogger(__name__)

ZONE = ZoneInfo("Europe/Berlin")
GROW_HEIGHT_CM = 92  # the VGrow's growing height (S1)

# ---- light ----
RAISE_ROOM = 0.3  # °C below the band's end before a step up: 60 to 40 % moved the probe by about 0.45 °C (06.11.2026), so a step of 10 fits
DRY_MARGIN = 0.3  # kPa over the stage's VPD band: too dry to add light to a young plant (own choice, no source)
MIN_SAMPLES = 20  # minutes of readings a light period needs before it can raise the level
HEAT_WATCH_S = 28 * 3600  # a step counts as the cause of heat this long: a day and the dark phase
RETRY_AFTER_HEAT_S = 48 * 3600
RAISE_STEP = LOWER_STEP = 10  # points, each way: 25 at once undid in one morning what the fans had just won (06.11.2026)
LOWER_FLOOR = 40  # where the temperature came into the band on 06.11.2026 with every fan up (own choice, no source for this lamp)
LOWER_AFTER_S = LIGHT_HEAT_HOLD  # this long too warm or too dry with the exhaust up: one step down ...
LOWER_AFTER_SLOW_S = 3 * LIGHT_HEAT_HOLD  # ... and this long while the autopilot can still raise the exhaust first
LOWER_GAP_S = 45 * 60  # a level takes this long to show at the probe: 40 to 80 % still climbed after 45 minutes (06.11.2026)
# ---- height ----
HEIGHT_EVERY_S = 3 * 3600
HEIGHT_LOOK_S = 60.0
RISE_BASE, RISE_PER_DAY, DROP_MAX = 3.0, 6.0, 6.0  # cm: more than this needs a second reading that agrees (own choice)
AGREE_CM = 2.0
UNREADABLE_PUSH_AFTER_S = 24 * 3600
PROBE_REGROW_CM = 15.0  # grown this far since the owner hung the probe: hang it again
# ---- water ----
PUMP_TEST_S = 60  # the jug run: about 50 ml at the manual's 1 l in 20 minutes (S1), which a kitchen jug can read; 10 s were a spoonful
WATCH_S = 3600  # how long a run has to show at the sensor
MIN_RISE = 2.0  # points: the sensor wobbles by one (soil.LOG_STEP)
MIN_RUN_S = 30  # a shorter run says nothing at the sensor
SOAK_LITRES = 1.0
FEED_PUSH_HOUR = 9
LOG_KEEP = 40

# What the strip prints (read off the owner's camera, 06.11.2026). photo: only for a photoperiod plant.
MARKS = (
    {"id": "trellis", "from": 15.0, "until": 20.0, "photo": False},
    {"id": "topping", "from": 20.0, "until": 25.0, "photo": True},
    {"id": "flowering", "from": 25.0, "until": 30.0, "photo": True},
    {"id": "lollipop", "from": 40.0, "until": None, "photo": False},
)

DE: dict[str, str] = {
    "gp.title": "Plan für diese Pflanze",
    "gp.sub": "Tag {day} in der Box · Phase {stage}",
    "gp.no_stage": "Phase noch nicht gesetzt: ohne sie regelt die Automatik nichts.",
    "gp.tile.height": "Höhe am Streifen",
    "gp.tile.light": "Licht",
    "gp.tile.soil": "Erde",
    "gp.tile.feed": "Dünger",
    "gp.unit.days": "Tage",
    "gp.height.sub": "Kamera {time} · {gap} bis zur Lampe",
    "gp.height.typed": "von dir eingetragen · {gap} bis zur Lampe",
    "gp.height.none": "noch keine Messung",
    "gp.height.no_strip": "Kamera sieht den Streifen nicht: sie muss die grünen Felder zeigen",
    "gp.height.no_plant": "Kamera sieht die Pflanze nicht",
    "gp.height.out_of_frame": "Spitze oben aus dem Bild: Kamera höher richten",
    "gp.height.no_picture": "Kein Bild von der Kamera",
    "gp.height.no_camera": "Keine Kamera eingerichtet",
    "gp.height.no_decoder": "Bild konnte nicht gelesen werden",
    "gp.light.goal": "Ziel {goal} · {hours} h am Tag",
    "gp.light.next_if": "Nächste Stufe {level} ab {time}, wenn die Hellphase bis dahin nicht zu warm läuft.",
    "gp.light.next_set": "Ab {time} gilt {level}.",
    "gp.light.at_goal": "Am Ziel: {level}.",
    "gp.light.hold.owner": "Bleibt bei {level}: du hast das so eingestellt.",
    "gp.light.hold.warm": "Bleibt bei {level}: 9 von 10 Werten der letzten Hellphase lagen bis {temp}, Grenze {limit}.",
    "gp.light.hold.heat": "Zurück auf {level}: zu warm nach der letzten Stufe. Neuer Versuch ab {date}.",
    "gp.light.hold.climate": "Licht {level}: mehr hält die Box gerade nicht. Neuer Versuch ab {date}, wenn es bis dahin passt.",
    "gp.light.hold.dry": "Bleibt bei {level}: Luft zu trocken in der letzten Hellphase (VPD {vpd}, Grenze {limit}).",
    "gp.light.hold.lamp": "Bleibt bei {level}: nur noch {gap} bis zur Lampe, mindestens {min} sind nötig.",
    "gp.light.hold.no_height": "Bleibt bei {level}: ohne Höhe kein Blick auf den Lampenabstand.",
    "gp.light.hold.no_data": "Bleibt bei {level}: zu wenig Messwerte aus der letzten Hellphase.",
    "gp.light.off": "Lichtplan ist aus. Er startet erst, wenn du ihn einschaltest.",
    "gp.light.schedule": "an {on}, aus {off}",
    "gp.soil.sub_raw": "Sensor, noch nicht auf deine Erde eingestellt",
    "gp.soil.sub_set": "vom vollen Topf · Ziel ab {low} %",
    "gp.soil.silent": "Sensor meldet sich nicht",
    "gp.soil.none": "kein Bodensensor",
    "gp.feed.until": "ab {date}, bis dahin nur Wasser",
    "gp.feed.now": "seit {date} laut Düngeplan",
    "gp.feed.none": "kein Termin gesetzt",
    "gp.row.what": "Was",
    "gp.row.who": "Wer regelt",
    "gp.row.now": "Jetzt",
    "gp.row.target": "Ziel in dieser Phase",
    "gp.row.light": "Licht",
    "gp.row.temp": "Temperatur",
    "gp.row.humidity": "Luftfeuchte",
    "gp.row.vpd": "VPD (Luft zu Blatt)",
    "gp.row.wind": "Wind",
    "gp.row.water": "Gießen",
    "gp.row.feed": "Dünger",
    "gp.who.light": "Lichtplan",
    "gp.who.exhaust": "Autopilot, Abluft",
    "gp.who.humidifier": "Befeuchter-Regel",
    "gp.who.both": "Befeuchter und Abluft",
    "gp.who.wind": "E6 folgt dem Licht",
    "gp.who.wind_off": "E6 von Hand",
    "gp.who.watch": "Gießwache",
    "gp.who.you": "Du, Mengen aus dem Düngeplan",
    "gp.wind.now": "Stufe {step}",
    "gp.wind.none": "E6 nicht gefunden",
    "gp.water.watch_on": "prüft um {times}, gegossen wird nur bei Bedarf",
    "gp.water.watch_off": "noch aus",
    "gp.todo.title": "Noch offen, nur deine Hände können das",
    "gp.todo.none": "Alles eingerichtet. Die Box läuft allein, du bekommst eine Nachricht, wenn etwas von dir gebraucht wird.",
    "gp.todo.probe": "Fühler auf Pflanzenhöhe hängen",
    "gp.todo.probe_text": "Temperatur und Feuchte gelten am Fühler: 5 bis 10 cm über der Spitze (Handbuch). Hängt er noch dort, wo er für die letzte Pflanze hing, regelt die Box für die Luft dort, nicht für diese Pflanze. Eine Minute.",
    "gp.todo.probe_btn": "Hängt richtig",
    "gp.todo.pump": "Pumpe messen",
    "gp.todo.pump_text": "Schlauchenden in einen Messbecher, Schalter an der Pumpe an. Dann zweimal: 60 Sekunden laufen lassen, Milliliter eintragen. Ohne diese Zahl weiß niemand, wie viel Wasser wirklich kommt. Kommt nichts, sag es: dann schaltet USB 1 die Pumpe nicht.",
    "gp.todo.pump_run": "60 Sekunden laufen lassen",
    "gp.todo.pump_ml": "Milliliter im Becher",
    "gp.todo.pump_save": "Eintragen",
    "gp.todo.pump_runs": "Messung {n} von 2",
    "gp.todo.soak": "Einmal durchgießen und Sensor einstellen",
    "gp.todo.soak_text": "Schläuche zurück in den Topf, einer nah am Stamm, einer nah am Sensor. Gießen, bis unten Wasser kommt (Hanfgarten: nach dem Eintopfen gleich gießen). Zwei Stunden abtropfen lassen, dann einstellen: dieser Wert ist ab dann 100 %.",
    "gp.todo.soak_btn": "1 Liter gießen",
    "gp.todo.soak_set": "Abgetropft: Sensor einstellen",
    "gp.todo.soak_again": "Nochmal tippen: dieser Wert wird 100 %",
    "gp.todo.watch": "Gießwache einschalten",
    "gp.todo.watch_text": "Ab dann prüft sie zweimal am Tag, passend zum Lichtplan. Gegossen wird nur, wenn die Erde es meldet, nie weil der Tank voll ist.",
    "gp.todo.watch_btn": "Gießwache einschalten",
    "gp.watch.off_btn": "Gießwache ausschalten",
    "gp.marks.title": "Was die Höhe als Nächstes auslöst",
    "gp.mark.done": "erledigt",
    "gp.mark.now": "jetzt",
    "gp.mark.next": "als Nächstes",
    "gp.mark.later": "später",
    "gp.mark.past": "vorbei",
    "gp.mark.done_btn": "Erledigt",
    "gp.mark.skip_btn": "Lasse ich",
    "gp.mark.range_until": "bis {cm}",
    "gp.mark.range": "{a} bis {b}",
    "gp.mark.range_from": "ab {cm}",
    "gp.mark.trellis": "Netz einhängen",
    "gp.mark.trellis_text": "Der Streifen markiert das Netz bis 20 cm.",
    "gp.mark.topping": "Toppen, wenn du willst",
    "gp.mark.topping_text": "Nur bei Photo-Sorten, diese ist eine. Spitze raus, die Pflanze wird breiter und bleibt niedriger.",
    "gp.mark.flowering": "Auf Blüte umstellen",
    "gp.mark.flowering_text": "Licht auf 12/12. In der Blüte verdoppelt sich die Höhe etwa, manche Pflanzen verdreifachen sie (growweedeasy). Bei 92 cm Wuchshöhe und 35 cm zur Lampe heißt das: bei etwa 28 cm umstellen. Ein Steckling braucht dafür Wurzeln.",
    "gp.mark.flowering_btn": "Auf Blüte umstellen",
    "gp.mark.flowering_again": "Nochmal tippen: Licht geht auf 12/12",
    "gp.mark.lollipop": "Unten ausputzen",
    "gp.mark.lollipop_text": "Der Streifen markiert das Lollipopping ab 40 cm.",
    "gp.log.title": "Was der Plan getan hat",
    "gp.log.start": "Plan gestartet: Licht {level}, Ziel {goal}.",
    "gp.log.raised": "Licht beim nächsten Einschalten {level}: letzte Hellphase bis {temp}, Grenze {limit}.",
    "gp.log.held_warm": "Licht bleibt bei {level}: letzte Hellphase bis {temp}, Grenze {limit}.",
    "gp.log.held_dry": "Licht bleibt bei {level}: VPD {vpd}, Grenze {limit}.",
    "gp.log.heat": "Zu warm ({temp}): Licht zurück auf {level}.",
    "gp.log.lowered": "Licht runter auf {level}: {temp} und VPD {vpd} am Fühler, mehr hält die Box gerade nicht.",
    "gp.log.owner": "Du hast das Licht auf {level} gestellt: der Plan hält das.",
    "gp.log.resume": "Licht fährt wieder hoch, Ziel {goal}.",
    "gp.log.spectrum": "Spektrum für die Phase {stage} gesetzt.",
    "gp.log.spectrum_failed": "Spektrum für die Phase {stage} nicht gesetzt: die Lampe läuft nicht im Handbetrieb.",
    "gp.log.height": "Höhe {cm} am Streifen.",
    "gp.log.height_offset": "Du hast {typed} eingetragen, der Streifen las {read}: künftige Lesungen um {offset} korrigiert.",
    "gp.log.height_suspect": "Lesung {cm} passt nicht zur letzten ({last}): wartet auf die nächste.",
    "gp.log.height_typed": "Echte Höhe eingetragen: {cm}.",
    "gp.log.mark": "Höhe {cm}: {mark}.",
    "gp.log.arrived": "Pumpenlauf kam an: Sensor von {before} auf {after}.",
    "gp.log.not_arrived": "Pumpenlauf nicht gespürt: Sensor {before}, danach höchstens {after}.",
    "gp.log.watch_on": "Gießwache an: prüft um {times}.",
    "gp.log.watch_off": "Gießwache aus.",
    "gp.log.watch_slots": "Gießwache prüft jetzt um {times}.",
    "gp.log.jug": "Pumpe {seconds} s in den Messbecher.",
    "gp.log.soak": "Du hast {litres} gegossen ({seconds} s).",
    "gp.push.trellis": "{plant} steht bei {cm}: Netz einhängen.",
    "gp.push.topping": "{plant} steht bei {cm}, im Feld Topping. Toppen wäre jetzt dran, wenn du willst.",
    "gp.push.flowering": "{plant} steht bei {cm}, im Feld Flowering. Im Plan auf Blüte umstellen: das Licht geht dann auf 12/12.",
    "gp.push.flowering_late": "{plant} steht bei {cm} und ist noch nicht auf Blüte. Je später, desto näher kommt sie der Lampe.",
    "gp.push.lollipop": "{plant} steht bei {cm}: unten ausputzen.",
    "gp.push.lamp": "{plant} steht bei {cm}: nur noch {gap} bis zur Lampe. Netz tiefer oder Licht runter.",
    "gp.push.tank": "Befeuchter fast leer (geschätzt noch {ml}): auffüllen und auf der Geräteseite eintragen, sonst fällt die Feuchte.",
    "gp.push.tank_stopped": "Der Befeuchter hat Strom, aber die Feuchte an der Pflanze fällt: Tank leer oder Düse zu? Bitte nachsehen.",
    "gp.push.feed": "Ab heute Dünger für {plant}: die Mengen stehen im Düngeplan.",
    "gp.push.not_arrived": "Die Pumpe lief {seconds} s, am Bodensensor kam nichts an ({before}, danach höchstens {after}). Schalter an der Pumpe, Schlauch und Tropfer prüfen.",
    "gp.push.camera": "Die Kamera liest seit einem Tag keine Höhe: {why}",
    "gp.push.title": "Plan",
    "gp.err.no_flow": "Erst die Pumpe messen: ohne Durchfluss zählt keine Menge.",
    "gp.err.watch_on": "Die Gießwache läuft: erst ausschalten, dann messen.",
    "gp.err.no_ports": "USB 1 meldet sich gerade nicht.",
    "gp.err.unknown_mark": "Diese Markierung gibt es nicht.",
    "gp.err.no_plan": "Kein Plan für diesen Durchgang.",
    "gp.err.goal": "Das Lichtziel liegt zwischen {low} und 100 %.",
    "gp.settings.title": "Einstellen",
    "gp.settings.goal": "Lichtziel in %",
    "gp.settings.feed": "Dünger ab",
    "gp.settings.save": "Speichern",
    "gp.pump.name": "Wasserpumpe",
    "gp.pump.running": "Gießt gerade",
    "gp.pump.unmanaged": "Läuft ohne Gießplan",
    "gp.pump.unmanaged_text": "USB 1 meldet Strom, aber der Gießplan hat keinen Lauf gestartet. Strom heißt nicht, dass Wasser fließt.",
    "gp.pump.watch": "Gießwache an",
    "gp.pump.watch_text": "Prüft um {times}, gegossen wird nur bei Bedarf.",
    "gp.pump.waiting": "Wartet",
    "gp.pump.measure": "Pumpe messen",
    "gp.pump.measure_text": "Die Gießwache bleibt aus, bis der Durchfluss gemessen ist: sie gießt erst, wenn sie weiß, wie viel Wasser kommt.",
    "gp.pump.watch_off": "Gießwache aus",
    "gp.pump.watch_off_text": "Die Pumpe ist gemessen. Gießwache einschalten, dann prüft sie zweimal am Tag.",
    "gp.btn.measure": "Jetzt messen",
    "gp.btn.resume": "Weiter hochfahren",
    "gp.btn.start_light": "Lichtplan starten",
    "gp.btn.water": "Zur Seite Wasser & Boden",
    "gp.btn.open": "Zum Plan",
    "gp.photo_alt": "Letztes Kamerabild mit der gelesenen Spitze der Pflanze",
    "gp.photo_note": "Die Linie zeigt die gelesene Spitze. Die Pflanze steht vor dem Streifen, also ein Schätzwert.",
    "gp.height.fix": "Linie falsch? Echte Höhe eintragen",
    "gp.height.fix_cm": "Höhe in cm",
    "gp.height.fix_save": "Eintragen",
}
EN: dict[str, str] = {
    "gp.title": "Plan for this plant",
    "gp.sub": "Day {day} in the box · stage {stage}",
    "gp.no_stage": "No stage set yet: without one the automation regulates nothing.",
    "gp.tile.height": "Height on the strip",
    "gp.tile.light": "Light",
    "gp.tile.soil": "Soil",
    "gp.tile.feed": "Fertilizer",
    "gp.unit.days": "days",
    "gp.height.sub": "camera {time} · {gap} to the lamp",
    "gp.height.typed": "entered by you · {gap} to the lamp",
    "gp.height.none": "no reading yet",
    "gp.height.no_strip": "The camera does not see the strip: it has to show the green fields",
    "gp.height.no_plant": "The camera does not see the plant",
    "gp.height.out_of_frame": "Tip above the picture: aim the camera higher",
    "gp.height.no_picture": "No picture from the camera",
    "gp.height.no_camera": "No camera set up",
    "gp.height.no_decoder": "The picture could not be read",
    "gp.light.goal": "goal {goal} · {hours} h a day",
    "gp.light.next_if": "Next step {level} from {time}, if the light period does not run too warm until then.",
    "gp.light.next_set": "From {time}: {level}.",
    "gp.light.at_goal": "At the goal: {level}.",
    "gp.light.hold.owner": "Stays at {level}: you set it.",
    "gp.light.hold.warm": "Stays at {level}: 9 of 10 readings of the last light period were up to {temp}, limit {limit}.",
    "gp.light.hold.heat": "Back to {level}: too warm after the last step. Next try from {date}.",
    "gp.light.hold.climate": "Light {level}: the box does not hold more right now. Next try from {date}, if the climate allows it by then.",
    "gp.light.hold.dry": "Stays at {level}: air too dry in the last light period (VPD {vpd}, limit {limit}).",
    "gp.light.hold.lamp": "Stays at {level}: only {gap} to the lamp, at least {min} are needed.",
    "gp.light.hold.no_height": "Stays at {level}: without a height no check of the lamp distance.",
    "gp.light.hold.no_data": "Stays at {level}: too few readings from the last light period.",
    "gp.light.off": "The light plan is off. It starts only when you switch it on.",
    "gp.light.schedule": "on {on}, off {off}",
    "gp.soil.sub_raw": "sensor, not yet set to your soil",
    "gp.soil.sub_set": "of the full pot · target from {low}%",
    "gp.soil.silent": "the sensor is silent",
    "gp.soil.none": "no soil sensor",
    "gp.feed.until": "from {date}, plain water until then",
    "gp.feed.now": "since {date} per the feeding plan",
    "gp.feed.none": "no date set",
    "gp.row.what": "What",
    "gp.row.who": "Who regulates",
    "gp.row.now": "Now",
    "gp.row.target": "Target in this stage",
    "gp.row.light": "Light",
    "gp.row.temp": "Temperature",
    "gp.row.humidity": "Humidity",
    "gp.row.vpd": "VPD (air to leaf)",
    "gp.row.wind": "Wind",
    "gp.row.water": "Watering",
    "gp.row.feed": "Fertilizer",
    "gp.who.light": "Light plan",
    "gp.who.exhaust": "Autopilot, exhaust fan",
    "gp.who.humidifier": "Humidifier rule",
    "gp.who.both": "Humidifier and exhaust fan",
    "gp.who.wind": "E6 follows the light",
    "gp.who.wind_off": "E6 by hand",
    "gp.who.watch": "Water watch",
    "gp.who.you": "You, amounts from the feeding plan",
    "gp.wind.now": "step {step}",
    "gp.wind.none": "E6 not found",
    "gp.water.watch_on": "checks at {times}, waters only on need",
    "gp.water.watch_off": "still off",
    "gp.todo.title": "Still open, only your hands can do this",
    "gp.todo.none": "All set up. The box runs by itself, you get a message when it needs you.",
    "gp.todo.probe": "Hang the probe at plant height",
    "gp.todo.probe_text": "Temperature and humidity apply at the probe: 5 to 10 cm above the tip (manual). If it still hangs where it hung for the last plant, the box regulates for the air there, not for this plant. One minute.",
    "gp.todo.probe_btn": "Hangs right",
    "gp.todo.pump": "Measure the pump",
    "gp.todo.pump_text": "Hose ends into a measuring jug, switch on the pump on. Then twice: let it run 60 seconds, enter the millilitres. Without this number nobody knows how much water really comes. If nothing comes, say so: then USB 1 does not switch the pump.",
    "gp.todo.pump_run": "Run 60 seconds",
    "gp.todo.pump_ml": "Millilitres in the jug",
    "gp.todo.pump_save": "Enter",
    "gp.todo.pump_runs": "Measurement {n} of 2",
    "gp.todo.soak": "Water through once and set the sensor",
    "gp.todo.soak_text": "Hoses back into the pot, one near the stem, one near the sensor. Water until water comes out below (Hanfgarten: water right after potting). Let it drain for two hours, then set it: this reading is 100% from then on.",
    "gp.todo.soak_btn": "Water 1 litre",
    "gp.todo.soak_set": "Drained: set the sensor",
    "gp.todo.soak_again": "Tap again: this reading becomes 100%",
    "gp.todo.watch": "Switch on the water watch",
    "gp.todo.watch_text": "From then on it checks twice a day, in step with the light plan. It waters only when the soil says so, never because the tank is full.",
    "gp.todo.watch_btn": "Switch on the water watch",
    "gp.watch.off_btn": "Switch off the water watch",
    "gp.marks.title": "What the height triggers next",
    "gp.mark.done": "done",
    "gp.mark.now": "now",
    "gp.mark.next": "next",
    "gp.mark.later": "later",
    "gp.mark.past": "past",
    "gp.mark.done_btn": "Done",
    "gp.mark.skip_btn": "Skip it",
    "gp.mark.range_until": "up to {cm}",
    "gp.mark.range": "{a} to {b}",
    "gp.mark.range_from": "from {cm}",
    "gp.mark.trellis": "Hang the net",
    "gp.mark.trellis_text": "The strip marks the net up to 20 cm.",
    "gp.mark.topping": "Top it, if you want",
    "gp.mark.topping_text": "Photo strains only, this is one. Tip off, the plant gets wider and stays lower.",
    "gp.mark.flowering": "Switch to flower",
    "gp.mark.flowering_text": "Light to 12/12. In flower the height about doubles, some plants triple (growweedeasy). With 92 cm growing height and 35 cm to the lamp that means: switch at about 28 cm. A cutting needs roots for this.",
    "gp.mark.flowering_btn": "Switch to flower",
    "gp.mark.flowering_again": "Tap again: the light goes to 12/12",
    "gp.mark.lollipop": "Clean up below",
    "gp.mark.lollipop_text": "The strip marks lollipopping from 40 cm.",
    "gp.log.title": "What the plan did",
    "gp.log.start": "Plan started: light {level}, goal {goal}.",
    "gp.log.raised": "Light at the next lights on {level}: last light period up to {temp}, limit {limit}.",
    "gp.log.held_warm": "Light stays at {level}: last light period up to {temp}, limit {limit}.",
    "gp.log.held_dry": "Light stays at {level}: VPD {vpd}, limit {limit}.",
    "gp.log.heat": "Too warm ({temp}): light back to {level}.",
    "gp.log.lowered": "Light down to {level}: {temp} and VPD {vpd} at the probe, the box does not hold more right now.",
    "gp.log.owner": "You set the light to {level}: the plan holds it.",
    "gp.log.resume": "Light goes up again, goal {goal}.",
    "gp.log.spectrum": "Spectrum set for the stage {stage}.",
    "gp.log.spectrum_failed": "Spectrum for the stage {stage} not set: the lamp is not in manual mode.",
    "gp.log.height": "Height {cm} on the strip.",
    "gp.log.height_offset": "You entered {typed}, the strip read {read}: later readings corrected by {offset}.",
    "gp.log.height_suspect": "Reading {cm} does not fit the last one ({last}): waiting for the next.",
    "gp.log.height_typed": "Real height entered: {cm}.",
    "gp.log.mark": "Height {cm}: {mark}.",
    "gp.log.arrived": "Pump run arrived: sensor from {before} to {after}.",
    "gp.log.not_arrived": "Pump run not felt: sensor {before}, afterwards at most {after}.",
    "gp.log.watch_on": "Water watch on: checks at {times}.",
    "gp.log.watch_off": "Water watch off.",
    "gp.log.watch_slots": "Water watch now checks at {times}.",
    "gp.log.jug": "Pump {seconds} s into the measuring jug.",
    "gp.log.soak": "You watered {litres} ({seconds} s).",
    "gp.push.trellis": "{plant} stands at {cm}: hang the net.",
    "gp.push.topping": "{plant} stands at {cm}, in the Topping field. Topping would be due now, if you want it.",
    "gp.push.flowering": "{plant} stands at {cm}, in the Flowering field. Switch to flower in the plan: the light then goes to 12/12.",
    "gp.push.flowering_late": "{plant} stands at {cm} and is not in flower yet. The later, the closer it gets to the lamp.",
    "gp.push.lollipop": "{plant} stands at {cm}: clean up below.",
    "gp.push.lamp": "{plant} stands at {cm}: only {gap} to the lamp. Net lower or light down.",
    "gp.push.tank": "Humidifier nearly empty (estimated {ml} left): fill it up and log it on the devices page, or the humidity falls.",
    "gp.push.tank_stopped": "The humidifier has power, but the humidity at the plant is falling: tank empty or nozzle blocked? Please check.",
    "gp.push.feed": "Fertilizer for {plant} from today: the amounts are in the feeding plan.",
    "gp.push.not_arrived": "The pump ran {seconds} s, nothing arrived at the soil sensor ({before}, afterwards at most {after}). Check the switch on the pump, the hose and the drippers.",
    "gp.push.camera": "The camera has read no height for a day: {why}",
    "gp.push.title": "Plan",
    "gp.err.no_flow": "Measure the pump first: without a flow no amount counts.",
    "gp.err.watch_on": "The water watch is running: switch it off first, then measure.",
    "gp.err.no_ports": "USB 1 is not reporting right now.",
    "gp.err.unknown_mark": "There is no such mark.",
    "gp.err.no_plan": "No plan for this run.",
    "gp.err.goal": "The light goal is between {low} and 100%.",
    "gp.settings.title": "Settings",
    "gp.settings.goal": "Light goal in %",
    "gp.settings.feed": "Fertilizer from",
    "gp.settings.save": "Save",
    "gp.pump.name": "Water pump",
    "gp.pump.running": "Watering now",
    "gp.pump.unmanaged": "Running without a schedule",
    "gp.pump.unmanaged_text": "USB 1 reports power, but the watering schedule started no run. Power does not mean water is flowing.",
    "gp.pump.watch": "Water watch on",
    "gp.pump.watch_text": "Checks at {times}, waters only on need.",
    "gp.pump.waiting": "Waiting",
    "gp.pump.measure": "Measure the pump",
    "gp.pump.measure_text": "The water watch stays off until the flow is measured: it waters only once it knows how much water comes.",
    "gp.pump.watch_off": "Water watch off",
    "gp.pump.watch_off_text": "The pump is measured. Switch on the water watch, then it checks twice a day.",
    "gp.btn.measure": "Measure now",
    "gp.btn.resume": "Keep going up",
    "gp.btn.start_light": "Start the light plan",
    "gp.btn.water": "To the page Water & soil",
    "gp.btn.open": "To the plan",
    "gp.photo_alt": "Latest camera picture with the plant's tip as read",
    "gp.photo_note": "The line shows the tip as read. The plant stands in front of the strip, so this is an estimate.",
    "gp.height.fix": "Line off? Enter the real height",
    "gp.height.fix_cm": "Height in cm",
    "gp.height.fix_save": "Enter",
}
i18n.DE.update(DE)
i18n.EN.update(EN)


def minutes_of(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def hours_on(cfg: Mapping[str, Any]) -> float | None:
    if not cfg.get("on_time") or not cfg.get("off_time") or cfg["on_time"] == cfg["off_time"]:
        return None
    return ((minutes_of(cfg["off_time"]) - minutes_of(cfg["on_time"])) % 1440) / 60


def water_slots(cfg: Mapping[str, Any]) -> list[str]:
    """The water watch's two checks: an hour after lights on (S1), and again later in the light period, at least three hours
    before lights off, so a late watering does not sit in the dark. Local times, sorted."""
    hours = hours_on(cfg)
    if hours is None:
        return ["09:00"]
    start = minutes_of(cfg["on_time"])
    after = [1.0]
    later = min(13.0, hours - 3.0)
    if later > 1.0:
        after.append(later)
    return sorted({f"{int(start + h * 60) % 1440 // 60:02d}:{int(start + h * 60) % 1440 % 60:02d}" for h in after})


def percentile_90(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[int(0.9 * (len(ordered) - 1))]


class GrowPlan:
    """One box's plan. timer: light_schedule.LightSchedule; watering: watering.WateringController; thirst: thirst.Thirst; soil:
    soil.SoilSensor; measurements: measurements.MeasurementStore; camera_of: the GrowCam as it is now (it can come later);
    link: a page link for a push. Everything except the box and the plant log is optional: what is missing is left out."""

    def __init__(self, box: Any, plant_log: PlantLog, *, alerts: Any = None, budget: CommandBudget = BUDGET,
                 guard: PortGuard | None = None, timer: Any = None, watering: Any = None, thirst: Any = None, soil: Any = None,
                 measurements: Any = None, camera_of: Callable[[], Any] | None = None,
                 link: Callable[[str], str | None] | None = None, clock: Callable[[], float] = time.time, tank: Any = None) -> None:
        self.box, self.plant_log, self.alerts, self.budget = box, plant_log, alerts, budget
        self.guard = guard or PortGuard("box-1")
        self.timer, self.watering, self.thirst, self.soil, self.tank = timer, watering, thirst, soil, tank
        self.measurements = measurements
        self.camera_of = camera_of or (lambda: None)
        self.link = link or (lambda _fragment: None)
        self.clock = clock
        self.state: dict[str, Any] = self._load()
        self.task: asyncio.Task[None] | None = None
        self._hot_since: float | None = None
        self._off_since: float | None = None
        self._measuring = False

    # ---------- files ----------

    @staticmethod
    def folder() -> Path:
        return config.STATE_DIR / "boxes" / "box-1"

    def path(self) -> Path:
        return self.folder() / "grow-plan.json"

    def photo_path(self) -> Path:
        return self.folder() / "height-latest.jpg"

    def readings_path(self) -> Path:
        return self.folder() / "height-readings.jsonl"

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as err:
            _LOGGER.warning("Grow plan unreadable, starting over: %s", err)
            return {}

    def _save(self) -> None:
        try:
            config.atomic_write(self.path(), json.dumps(self.state, ensure_ascii=False) + "\n")
        except OSError:
            _LOGGER.exception("Grow plan could not be saved")

    def _note(self, key: str, **params: Any) -> None:
        """A line of the plan's log. Numbers stay numbers with their unit, so it reads right in either language."""
        self.state["log"] = [*self.state.get("log", [])[-(LOG_KEEP - 1):], {"at": self.clock(), "key": key, "params": params}]

    async def _push(self, key: str, *, priority: int = 3, **params: Any) -> None:
        if self.alerts is None:
            return
        lang = i18n.server_lang()
        try:
            await self.alerts.push(t("gp.push.title", lang), t(key, lang, **_shown(params, lang)), priority=priority,
                                   click=self.link("automation"))
        except Exception:  # a failed push never stops the plan
            _LOGGER.exception("Grow plan push failed")

    # ---------- the run ----------

    def _ensure_run(self, profile: Mapping[str, Any] | None) -> None:
        """A new plant (plant_run.py, or a new start date in plant.json) starts a new plan."""
        plant = (profile or {}).get("plant") or {}
        run = plant.get("record") or plant.get("start_date")
        if self.state.get("run") != run:
            self.state = {"run": run, "log": [], "marks": {}, "water": {}}

    def settings(self, body: Mapping[str, Any], snap: Mapping[str, Any]) -> None:
        """The owner's two settings of this run: how far the light climbs, and the first day with fertilizer."""
        self._ensure_run(load_profile())
        if "light_goal" in body:
            self._set_goal(body["light_goal"], snap)
        if "feed_from" in body:
            self.plant_log.set_feed_from(body["feed_from"])
            self.state.pop("feed_pushed", None)
        self._save()

    def _set_goal(self, goal: Any, snap: Mapping[str, Any]) -> None:
        if goal in (None, ""):
            self.state["light"] = None  # the stage card's preset stands again
            return
        if type(goal) is not int or not LIGHT_MIN_BRIGHTNESS <= goal <= 100:
            raise CommandRefused(t("gp.err.goal", low=LIGHT_MIN_BRIGHTNESS))
        light = self.state.get("light")
        if light:
            light.update(goal=goal, level=min(int(light["level"]), goal), hold=None if light.get("hold") != "owner" else "owner")
            return
        level = (snap.get("light") or {}).get("level")
        if type(level) is not int or level <= 0:
            level = (self.timer.cfg.get("level") if self.timer is not None else None) or goal
        self.state["light"] = {"level": min(int(level), goal), "goal": goal, "at": self.clock(), "from": None, "hold": None,
                               "decided": None, "stats": None}
        self._note("gp.log.start", level=_pct(min(int(level), goal)), goal=_pct(goal))

    def light_stage(self, profile: Mapping[str, Any] | None, stage: dict[str, Any] | None) -> dict[str, Any] | None:
        """The stage as the light timer reads it: the plan's level of the day instead of the card's preset. Without a goal
        the card's preset stands, as before."""
        self._ensure_run(profile)
        light = self.state.get("light")
        preset = (stage or {}).get("preset")
        if not light or not isinstance(preset, dict) or "light_percent" not in preset:
            return stage
        return {**stage, "preset": {**preset, "light_percent": int(light["level"])}}  # type: ignore[dict-item]

    def _today(self, now: float) -> date:
        return datetime.fromtimestamp(now, ZONE).date()

    def height_now(self) -> dict[str, Any] | None:
        heights = self.plant_log.data.get("heights") or []
        return heights[-1] if heights else None

    # ---------- every poll ----------

    async def check(self, snap: Mapping[str, Any]) -> None:
        now = self.clock()
        profile = load_profile()
        if not profile or profile_problem():
            return
        self._ensure_run(profile)
        stage = current_stage(profile, self.plant_log)
        before = json.dumps(self.state, sort_keys=True)
        await self._stage_steps(stage, snap)
        self._owner_light(snap, now)
        await self._light_step(stage, snap, now)
        await self._feed_push(profile, now)
        await self._tank_push(now)
        await self._watch_pump(now)
        await self._align_watering(stage)
        if json.dumps(self.state, sort_keys=True) != before:
            self._save()

    async def _stage_steps(self, stage: dict[str, Any] | None, snap: Mapping[str, Any]) -> None:
        """Once per stage the owner set: the card's spectrum, and the stage on the plant's record."""
        if not stage or stage.get("set_by") != "manual":
            return
        mark = f"{stage['key']}:{stage.get('since')}"
        seen = self.state.get("stage_seen")
        if not isinstance(seen, dict) or seen.get("mark") != mark:
            seen = {"mark": mark, "spectrum_done": False, "tries": 0, "record": False}
            self.state["stage_seen"] = seen
        if not seen.get("record"):  # the record can come after the stage: until then every poll looks again
            try:
                seen["record"] = await asyncio.to_thread(
                    workspace.sync_stage, stage.get("label") or stage["key"],
                    stage.get("label_en") or stage.get("label") or stage["key"], stage.get("since"))
            except (OSError, ValueError, workspace.sqlite3.Error):
                _LOGGER.exception("Stage not written to the plant's record")
        if seen.get("spectrum_done"):
            return
        wanted = (stage.get("preset") or {}).get("spectrum")
        light = snap.get("light") or {}
        label = {"de": stage.get("label") or stage["key"], "en": stage.get("label_en") or stage.get("label") or stage["key"]}
        if not wanted or light.get("spectrum") == wanted or light.get("spectrum_writable") is False:
            seen["spectrum_done"] = True
            return
        seen["tries"] = int(seen.get("tries") or 0) + 1
        try:
            await self.budget.send("grow_plan", "light.spec", lambda: self.box.set_spectrum(str(wanted)))
        except Exception as err:  # noqa: BLE001 (refused, or no answer: the next poll looks again, a few times)
            _LOGGER.info("Spectrum not set yet: %s", err)
            if seen["tries"] >= 5:
                seen["spectrum_done"] = True
                self._note("gp.log.spectrum_failed", stage=label)
            return
        seen["spectrum_done"] = True
        self._note("gp.log.spectrum", stage=label)

    # ---------- light ----------

    def _owner_light(self, snap: Mapping[str, Any], now: float) -> None:
        """A level the owner set (cockpit, app, buttons; the timer calls it manual) becomes the plan's level, and stays."""
        light = self.state.get("light")
        timer = self.timer
        if not light or timer is None or not timer.cfg.get("enabled"):
            return
        level = (snap.get("light") or {}).get("level")
        until = timer.override_until
        if until and now < until and type(level) is int and level > 0 and level != light["level"]:
            light.update(level=level, **{"from": None}, hold="owner", at=now)
            self._note("gp.log.owner", level=_pct(level))

    async def _light_step(self, stage: dict[str, Any] | None, snap: Mapping[str, Any], now: float) -> None:
        light = self.state.get("light")
        timer = self.timer
        if not light or timer is None or not timer.cfg.get("enabled") or not timer.cfg.get("from_requirements"):
            self._hot_since = None
            return
        edge, _ = boundaries(timer.cfg, now)
        tg = bands(stage)
        day = tg.get("probe_temp_day") or tg.get("probe_temp")
        if edge["on"]:
            await self._heat_guard(day, snap, now, light)
            await self._climate_guard(tg, day, snap, now, light)
            return
        self._hot_since = self._off_since = None
        if light.get("decided") == edge["at"]:
            return
        light["decided"] = edge["at"]
        await self._decide(tg, day, edge["at"], now, light)

    async def _heat_guard(self, day: Mapping[str, Any] | None, snap: Mapping[str, Any], now: float,
                          light: dict[str, Any]) -> None:
        """Its own step made it too hot, for 20 minutes: back to the level before, at once (the autopilot's rule for its own
        ramp, which does not run while the timer owns the brightness)."""
        temp = (snap.get("climate") or {}).get("probe_temp")
        back = light.get("from")
        if not day or type(temp) not in (int, float) or not back or now - float(light.get("at") or 0) > HEAT_WATCH_S:
            self._hot_since = None
            return
        if temp <= float(day["max"]) + LIGHT_HEAT_MARGIN:
            self._hot_since = None
            return
        self._hot_since = self._hot_since or now
        if now - self._hot_since >= LIGHT_HEAT_HOLD:
            self._hot_since = None
            light.update(level=int(back), **{"from": None}, hold="heat", at=now, retry_at=now + RETRY_AFTER_HEAT_S)
            self._note("gp.log.heat", temp=_deg(temp), level=_pct(back))
            await self._push("gp.log.heat", priority=4, temp=_deg(temp), level=_pct(back))

    async def _climate_guard(self, tg: Mapping[str, Any], day: Mapping[str, Any] | None, snap: Mapping[str, Any], now: float,
                             light: dict[str, Any]) -> None:
        """As much light as the climate holds (owner, 06.11.2026: "fix all numbers ... and keep it that way"). The fans and the
        humidifier come first. If the probe stays over the temperature band, or over the VPD line that holds back a raise, the
        level comes down one step and waits to show, never below LOWER_FLOOR and never over the owner's own level. In the dark
        _decide brings it back up, not before RETRY_AFTER_HEAT_S."""
        climate = snap.get("climate") or {}
        temp, vpd = climate.get("probe_temp"), climate.get("probe_vpd")
        vpd_band = tg.get("probe_vpd")
        hot = bool(day) and type(temp) in (int, float) and temp > float(day["max"])
        dry = bool(vpd_band) and type(vpd) in (int, float) and vpd > float(vpd_band["max"]) + DRY_MARGIN
        level = int(light["level"])
        if not (hot or dry) or light.get("hold") == "owner" or level <= LOWER_FLOOR:
            self._off_since = None
            return
        self._off_since = self._off_since or now
        exhaust = (snap.get("dfan") or {}).get("step")
        wait = LOWER_AFTER_S if type(exhaust) is int and exhaust >= DFAN_MAX else LOWER_AFTER_SLOW_S
        if now - self._off_since < wait or now - float(light.get("lowered_at") or 0) < LOWER_GAP_S:
            return
        new = max(LOWER_FLOOR, level - LOWER_STEP)
        self._off_since = None
        light.update(level=new, **{"from": None}, hold="climate", at=now, lowered_at=now, retry_at=now + RETRY_AFTER_HEAT_S)
        self._note("gp.log.lowered", level=_pct(new), temp=_deg(temp), vpd=_kpa(vpd))

    def _stats(self, start: float, end: float) -> dict[str, Any] | None:
        """The probe's readings of a light period: 9 of 10 temperatures at or below temp, the median VPD."""
        if self.measurements is None or end <= start:
            return None
        rows = self.measurements.rows(start, end)
        temps = [r["pTemp"] if r.get("pTemp") is not None else r.get("bTemp") for r in rows]
        temps = [float(v) for v in temps if v is not None]
        if len(temps) < MIN_SAMPLES:
            return None
        vpds = [float(r["pVpd"]) for r in rows if r.get("pVpd") is not None]
        return {"temp": round(percentile_90(temps), 1), "vpd": round(statistics.median(vpds), 2) if vpds else None, "n": len(temps)}

    async def _decide(self, tg: Mapping[str, Any], day: Mapping[str, Any] | None, off_at: float, now: float,
                      light: dict[str, Any]) -> None:
        """In the dark, once per period: does the next light period get a step up? Only the time at this level counts."""
        if light.get("hold") == "owner" or int(light["level"]) >= int(light["goal"]):
            return
        if light.get("hold") in ("heat", "climate") and now < float(light.get("retry_at") or 0):
            return
        hours = hours_on(self.timer.cfg) or 0
        stats = await asyncio.to_thread(self._stats, max(off_at - hours * 3600, float(light.get("at") or 0)), off_at)
        light["stats"] = stats
        level = int(light["level"])
        vpd_band = tg.get("probe_vpd")
        if stats is None or not day:
            light["hold"] = "no_data"
            return
        limit = float(day["max"]) - RAISE_ROOM
        if stats["temp"] > limit:
            light["hold"] = "warm"
            self._note("gp.log.held_warm", level=_pct(level), temp=_deg(stats["temp"]), limit=_deg(limit))
            return
        if vpd_band and stats.get("vpd") is not None and stats["vpd"] > float(vpd_band["max"]) + DRY_MARGIN:
            light["hold"] = "dry"
            self._note("gp.log.held_dry", level=_pct(level), vpd=_kpa(stats["vpd"]), limit=_kpa(float(vpd_band["max"]) + DRY_MARGIN))
            return
        height = self.height_now()
        if height is None:
            light["hold"] = "no_height"
            return
        if GROW_HEIGHT_CM - float(height["cm"]) < LAMP_MIN_CM:
            light["hold"] = "lamp"
            return
        new = min(int(light["goal"]), level + RAISE_STEP)
        light.update(level=new, **{"from": level}, hold=None, at=now)
        self._note("gp.log.raised", level=_pct(new), temp=_deg(stats["temp"]), limit=_deg(limit))

    def resume_light(self) -> None:
        """The owner's tap: the plan may raise the light again from where it stands."""
        light = self.state.get("light")
        if not light:
            raise CommandRefused(t("gp.err.no_plan"))
        light.update(hold=None, at=self.clock())
        light.pop("retry_at", None)
        self._note("gp.log.resume", goal=_pct(light["goal"]))
        self._save()

    # ---------- height ----------

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop(), name="grow_plan_height")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.height_tick()
            except Exception:  # a bad picture costs one reading, never the loop
                _LOGGER.exception("Height reading failed")
            await asyncio.sleep(HEIGHT_LOOK_S)

    async def height_tick(self) -> None:
        now = self.clock()
        level = (self.box.snapshot().get("light") or {}).get("level")
        if not level or now - float(self.state.get("height_try") or 0) < HEIGHT_EVERY_S:
            return
        camera = self.camera_of()
        if camera is None or not getattr(camera, "configured", False) or not getattr(camera, "reachable", False):
            return
        await self.measure()

    async def measure(self) -> dict[str, Any]:
        """One reading now: a photo, the strip, the plant log. Also the page's button."""
        if self._measuring:
            return {"ok": False, "why": "busy"}
        self._measuring = True
        try:
            now = self.clock()
            profile = load_profile()
            self._ensure_run(profile)
            self.state["height_try"] = now
            camera = self.camera_of()
            if camera is None or not hasattr(camera, "photo"):
                result: dict[str, Any] = {"ok": False, "why": "no_camera"}
                jpeg = None
            else:
                try:
                    jpeg = await camera.photo()
                except Exception as err:  # noqa: BLE001 (TextError, RuntimeError, a timeout)
                    _LOGGER.warning("No picture for the height reading: %s", err)
                    jpeg, result = None, {"ok": False, "why": "no_picture"}
                else:
                    result = await asyncio.to_thread(ruler.read_jpeg, jpeg)
            await self._took(result, jpeg, profile, now)
            self._save()
            return result
        finally:
            self._measuring = False

    async def _took(self, result: dict[str, Any], jpeg: bytes | None, profile: Mapping[str, Any] | None, now: float) -> None:
        await asyncio.to_thread(self._log_reading, result, now)
        if not result.get("ok"):
            problem = self.state.get("height_problem")
            if not isinstance(problem, dict) or problem.get("why") != result.get("why"):
                problem = {"why": result.get("why"), "since": now, "pushed": False}
                self.state["height_problem"] = problem
            if not problem["pushed"] and now - float(problem["since"]) >= UNREADABLE_PUSH_AFTER_S:
                problem["pushed"] = True
                lang = i18n.server_lang()
                await self._push("gp.push.camera", priority=3, why=t(f"gp.height.{result.get('why')}", lang))
            return
        self.state.pop("height_problem", None)
        day = self._today(now).isoformat()
        raw = float(result["cm"])
        self._typed_offset(day, raw)
        offset = float(self.state.get("offset") or 0)
        value = round(raw + offset, 1)
        last = self.state.get("height")
        if isinstance(last, dict) and not self._plausible(value, last, now):
            suspect = self.state.get("suspect")
            if not isinstance(suspect, dict) or abs(float(suspect["cm"]) - value) > AGREE_CM:
                self.state["suspect"] = {"cm": value, "at": now}
                self._note("gp.log.height_suspect", cm=_cm(value), last=_cm(last["cm"]))
                return
            self.state["today"] = {"day": day, "raw": []}  # it really moved: topped, tied down, or the camera was aimed anew
        self.state.pop("suspect", None)
        today = self.state.get("today")
        if not isinstance(today, dict) or today.get("day") != day:
            today = {"day": day, "raw": []}
        today["raw"] = [*today["raw"], raw]
        self.state["today"] = today
        value = round(statistics.median(today["raw"]) + offset, 1)
        self.state["height"] = {"cm": value, "at": now, "day": day, "top_y": result.get("top_y"), "top_x": result.get("top_x"),
                                "raw": raw}
        if jpeg:
            await asyncio.to_thread(self._keep_photo, jpeg)
        if value >= 1:
            self._write_height(value, day)
            try:
                await asyncio.to_thread(workspace.camera_height, day, value)
            except (OSError, ValueError, TypeError, workspace.sqlite3.Error):
                _LOGGER.exception("Height not written to the plant's record")
        await self._mark_pushes(profile, value)

    def _plausible(self, value: float, last: Mapping[str, Any], now: float) -> bool:
        days = max(0.0, (now - float(last.get("at") or now)) / 86400)
        was = float(last["cm"])
        return was - DROP_MAX <= value <= was + RISE_BASE + RISE_PER_DAY * days

    def _typed_offset(self, day: str, raw: float) -> None:
        """A height the owner entered today, against what the strip reads now: the later readings carry the difference."""
        last = self.height_now()
        if not last or last.get("date") != day or last.get("method") == "camera_strip" or self.state.get("offset_day") == day:
            return
        offset = round(float(last["cm"]) - raw, 1)
        self.state.update(offset=offset, offset_day=day)
        self._note("gp.log.height_offset", typed=_cm(last["cm"]), read=_cm(raw), offset=_cm(offset))

    async def correct_height(self, cm: float | None) -> None:
        """The owner's real height, for the rare day the line on the picture is off. It wins on its day, and the reading right
        after it sets the offset for the later ones. Typed twice in a day, the second one counts."""
        day = self._today(self.clock()).isoformat()
        self.plant_log.add_height(cm, day)  # outside 1 to 300 cm it refuses, and nothing has changed yet
        value = round(float(cm or 0), 1)
        try:
            await asyncio.to_thread(workspace.owner_height, day, value)
        except (OSError, ValueError, TypeError, workspace.sqlite3.Error):
            _LOGGER.exception("Height not written to the plant's record")
        for key in ("offset_day", "suspect", "today"):  # the day's median starts over from the reading it is compared with
            self.state.pop(key, None)
        last = self.state.get("height")
        if isinstance(last, dict):
            last["cm"] = value  # the owner's number is the one a jump is measured against from now on
        self._note("gp.log.height_typed", cm=_cm(value))
        await self.measure()

    def _write_height(self, value: float, day: str) -> None:
        last = self.height_now()
        if last and last.get("date") == day and last.get("method") != "camera_strip":
            return  # the owner's entry of today stays
        self.plant_log.add_height(value, day, estimated=True, camera="growcam", method="camera_strip")

    def _log_reading(self, result: Mapping[str, Any], now: float) -> None:
        try:
            self.readings_path().parent.mkdir(parents=True, exist_ok=True)
            with self.readings_path().open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"at": now, **result}) + "\n")
        except OSError:
            _LOGGER.exception("Height reading not logged")

    def _keep_photo(self, jpeg: bytes) -> None:
        path = self.photo_path()
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(jpeg)
            tmp.replace(path)
        except OSError:
            _LOGGER.exception("Height photo not kept")

    # ---------- the strip's marks ----------

    def _marks_of(self, profile: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        photo = (profile or {}).get("stages_variant") == "photoperiod"
        return [m for m in MARKS if photo or not m["photo"]]

    async def _mark_pushes(self, profile: Mapping[str, Any] | None, cm: float) -> None:
        stage = current_stage(profile or {}, self.plant_log) or {}
        plant = ((profile or {}).get("plant") or {}).get("strain") or ""
        marks = self.state.setdefault("marks", {})
        for mark in self._marks_of(profile):
            entry = marks.setdefault(mark["id"], {})
            if cm < mark["from"] or entry.get("pushed"):
                continue
            entry["pushed"] = self.clock()
            if mark["id"] == "flowering" and stage.get("key") in FLOWERING_STAGES:
                continue
            self._note("gp.log.mark", cm=_cm(cm), mark={"de": DE[f"gp.mark.{mark['id']}"], "en": EN[f"gp.mark.{mark['id']}"]})
            await self._push(f"gp.push.{mark['id']}", priority=4 if mark["id"] == "flowering" else 3, plant=plant, cm=_cm(cm))
        # past the flowering field and still not switched: said once more
        if (self._marks_of(profile) and any(m["id"] == "flowering" for m in self._marks_of(profile))
                and cm >= 30.0 and stage.get("key") in PRE_FLOWER_STAGES and not marks.setdefault("flowering_late", {}).get("pushed")):
            marks["flowering_late"]["pushed"] = self.clock()
            await self._push("gp.push.flowering_late", priority=4, plant=plant, cm=_cm(cm))
        gap = GROW_HEIGHT_CM - cm
        if gap < LAMP_MIN_CM and not marks.setdefault("lamp", {}).get("pushed"):
            marks["lamp"]["pushed"] = self.clock()
            await self._push("gp.push.lamp", priority=4, plant=plant, cm=_cm(cm), gap=_cm(gap))

    def mark(self, ident: Any, done: bool = True) -> None:
        if ident not in {m["id"] for m in MARKS}:
            raise CommandRefused(t("gp.err.unknown_mark"))
        entry = self.state.setdefault("marks", {}).setdefault(str(ident), {})
        entry["done"] = bool(done)
        self._save()

    def probe_hung(self) -> None:
        height = self.height_now()
        self.state["probe"] = {"at": self.clock(), "cm": float(height["cm"]) if height else 0.0}
        self._save()

    # ---------- feeding ----------

    async def _feed_push(self, profile: Mapping[str, Any], now: float) -> None:
        day = parse_day(self.plant_log.data.get("feed_from"))
        if day is None or self.state.get("feed_pushed"):
            return
        local = datetime.fromtimestamp(now, ZONE)
        if local.date() < day or (local.date() == day and local.hour < FEED_PUSH_HOUR):
            return
        self.state["feed_pushed"] = now
        await self._push("gp.push.feed", priority=3, plant=(profile.get("plant") or {}).get("strain") or "")

    # ---------- water ----------

    async def _tank_push(self, now: float) -> None:
        """The humidifier runs most of the day for a plant this young, and its tank lasts 15 to 25 hours (U075's data). Nothing
        measures the level: the estimate says when, one message per fill, and the vapour at the probe says it has stopped."""
        if self.tank is None:
            return
        tank = self.tank.view(now=now)
        said = self.state.setdefault("tank_said", {})
        warning = tank.get("warning")
        if warning == "no_response":
            if not said.get("stopped"):
                said["stopped"] = True
                await self._push("gp.push.tank_stopped", priority=4)
            return
        said["stopped"] = False
        if warning == "estimated_low" and said.get("low") != tank.get("refilled_at"):
            said["low"] = tank.get("refilled_at")
            await self._push("gp.push.tank", priority=4, ml={"q": [tank.get("remaining_ml_estimate"), "ml", 0]})

    async def _watch_pump(self, now: float) -> None:
        """Every run of at least MIN_RUN_S gets a look at the soil sensor an hour later: did it feel the water?"""
        if self.watering is None or self.soil is None:
            return
        water = self.state.setdefault("water", {})
        seen = float(water.get("seen_at") or 0)
        started = None
        pending = list(water.get("pending") or [])
        for event in self.watering.events:
            if event.get("kind") == "start":
                started = float(event["at"])
            elif event.get("kind") == "stopped" and float(event["at"]) > seen:
                seen = float(event["at"])
                if started is not None and seen - started >= MIN_RUN_S:
                    pending.append({"start": started, "stop": seen})
                started = None
        water["seen_at"] = seen
        due = [p for p in pending if now - float(p["stop"]) >= WATCH_S]
        water["pending"] = [p for p in pending if p not in due]
        for run in due:
            await self._judge_run(run)

    async def _judge_run(self, run: Mapping[str, Any]) -> None:
        hours = (self.clock() - float(run["start"])) / 3600 + 2
        rows = await asyncio.to_thread(self.soil.history, hours)
        before = [r["percent"] for r in rows if r["at"] <= run["start"]]
        after = [r["percent"] for r in rows if run["start"] < r["at"]]
        if not before or not after:
            return  # the sensor was silent: nothing to say about the run
        was, most = float(before[-1]), max(float(v) for v in after)
        if most - was >= MIN_RISE:
            self._note("gp.log.arrived", before=_pct(was), after=_pct(most))
            return
        self._note("gp.log.not_arrived", before=_pct(was), after=_pct(most))
        await self._push("gp.push.not_arrived", priority=4, seconds=int(float(run["stop"]) - float(run["start"])),
                         before=_pct(was), after=_pct(most))

    def _run_seconds(self, stage: Mapping[str, Any] | None) -> int | None:
        """The watering slot's run time: what the stage's cap takes at the measured flow. The gate shortens it to the dose."""
        if self.thirst is None:
            return None
        flow = self.thirst.flow(self.clock())
        if not flow.get("calibrated") or not flow.get("ml_s"):
            return None
        try:
            cap = stage_cap_l(load_model(self.thirst.model_path), (stage or {}).get("key"))
        except ModelError:
            return None
        return max(MIN_RUN_S, min(3600, math.ceil(cap * 1000 / float(flow["ml_s"]))))

    async def _align_watering(self, stage: Mapping[str, Any] | None) -> None:
        """While the water watch is the plan's: its checks follow the light plan, its run time the stage. A schedule the owner
        changed on the watering card is his from then on."""
        water = self.state.setdefault("water", {})
        if not water.get("auto") or self.watering is None or self.timer is None:
            return
        cfg = self.watering.cfg
        if not cfg.get("enabled") or cfg.get("times") != water.get("times"):
            water["auto"] = False
            return
        times, seconds = water_slots(self.timer.cfg), self._run_seconds(stage)
        if times == cfg.get("times") and (seconds is None or seconds == cfg.get("duration_s")):
            return
        try:
            await self.watering.configure({**cfg, "times": times, "duration_s": seconds or cfg.get("duration_s")})
        except (CommandRefused, ValueError) as err:
            _LOGGER.info("Water watch not aligned yet: %s", err)
            return
        if times != water.get("times"):
            self._note("gp.log.watch_slots", times=", ".join(times))
        water["times"] = times

    async def pump_test(self) -> None:
        """The owner's tap with the hoses in the jug: names USB 1 the pump, confirms its switch, runs PUMP_TEST_S. The run is
        told to the thirst index as a jug run, so it never counts as water in the pot."""
        if self.watering is None or self.thirst is None:
            raise CommandRefused(t("gp.err.no_plan"))
        if self.watering.cfg.get("enabled"):
            raise CommandRefused(t("gp.err.watch_on"))
        snap = self.box.snapshot()
        if self.guard.owner_device("usb1") != "pump" and not self.guard.assign("usb1", "pump", snap):
            raise CommandRefused(t("gp.err.no_ports"))
        await self.watering.configure({**self.watering.cfg, "duration_s": PUMP_TEST_S, "power_confirmed": True, "enabled": False})
        self.thirst.jug_run(PUMP_TEST_S)
        await self.watering.start()
        self._note("gp.log.jug", seconds=PUMP_TEST_S)
        self._save()

    async def water_now(self, litres: float = SOAK_LITRES) -> None:
        """The owner's tap: this many litres at the measured flow. It counts in the thirst index by that flow."""
        if self.watering is None or self.thirst is None:
            raise CommandRefused(t("gp.err.no_plan"))
        flow = self.thirst.flow(self.clock())
        if not flow.get("calibrated") or not flow.get("ml_s"):
            raise CommandRefused(t("gp.err.no_flow"))
        seconds = max(1, min(3600, math.ceil(litres * 1000 / float(flow["ml_s"]))))
        await self.watering.configure({**self.watering.cfg, "duration_s": seconds})
        await self.watering.start()
        self._note("gp.log.soak", litres={"q": [litres, "l", None]}, seconds=seconds)
        self._save()

    async def watch_on(self, stage: Mapping[str, Any] | None) -> None:
        if self.watering is None or self.thirst is None or self.timer is None:
            raise CommandRefused(t("gp.err.no_plan"))
        seconds = self._run_seconds(stage)
        if seconds is None:
            raise CommandRefused(t("gp.err.no_flow"))
        times = water_slots(self.timer.cfg)
        await self.watering.configure({**self.watering.cfg, "enabled": True, "duration_s": seconds, "times": times, "every_days": 1,
                                       "first_date": self._today(self.clock()).isoformat(), "power_confirmed": True})
        self.state.setdefault("water", {}).update(auto=True, times=times)
        self._note("gp.log.watch_on", times=", ".join(times))
        self._save()

    async def watch_off(self) -> None:
        if self.watering is None:
            raise CommandRefused(t("gp.err.no_plan"))
        await self.watering.configure({**self.watering.cfg, "enabled": False})
        self.state.setdefault("water", {})["auto"] = False
        self._note("gp.log.watch_off")
        self._save()

    # ---------- the card ----------

    def view(self, snap: Mapping[str, Any], lang: str | None = None) -> dict[str, Any]:
        lang = i18n.normalize(lang) or i18n.current_lang()
        profile = load_profile()
        if not profile:
            return {"configured": False}
        self._ensure_run(profile)
        now = self.clock()
        shown = localize(profile, lang)
        stage = current_stage(shown, self.plant_log)
        run = shown.get("plant") or {}
        started = parse_day(run.get("start_date"))
        sub = t("gp.sub", lang, day=fmt_num((self._today(now) - started).days if started else 0, lang),
                stage=(stage or {}).get("label") or "?")
        confirmed = bool(stage and stage.get("set_by") == "manual")
        marks = self._marks_view(profile, stage, lang)
        todo = self._todo(snap, lang)
        return {
            "configured": True,
            "title": run.get("strain") or t("gp.title", lang),
            "heading": t("gp.title", lang),
            "sub": sub,
            "stage": {"key": (stage or {}).get("key"), "confirmed": confirmed},
            "warning": None if confirmed else t("gp.no_stage", lang),
            "tiles": [self._height_tile(lang), self._light_tile(snap, lang), self._soil_tile(snap, lang),
                      self._feed_tile(now, lang)],
            "light": self._light_view(snap, now, lang),
            "pump": self._pump_view(snap, lang),
            "todo": todo,
            "todo_title": t("gp.todo.title", lang),
            "todo_none": t("gp.todo.none", lang),
            "marks": marks,
            "marks_title": t("gp.marks.title", lang),
            "rows": self._rows(stage, snap, lang),
            "row_heads": [t(f"gp.row.{k}", lang) for k in ("what", "who", "now", "target")],
            "photo": self._photo_view(lang),
            "log": [{"at": e["at"], "text": t(e["key"], lang, **_shown(e.get("params") or {}, lang))}
                    for e in reversed(self.state.get("log") or [])][:8],
            "log_title": t("gp.log.title", lang),
            "settings": {"light_goal": (self.state.get("light") or {}).get("goal"), "feed_from": self.plant_log.data.get("feed_from")},
            "labels": {k: t(f"gp.{k}", lang) for k in ("btn.measure", "btn.resume", "btn.start_light", "btn.water", "btn.open",
                                                        "watch.off_btn", "mark.flowering_again", "todo.soak_again", "photo_note", "height.fix", "height.fix_cm", "height.fix_save",
                                                        "settings.title", "settings.goal", "settings.feed", "settings.save")},
        }

    def _height_tile(self, lang: str) -> dict[str, Any]:
        height = self.height_now()
        problem = self.state.get("height_problem")
        out: dict[str, Any] = {"id": "height", "label": t("gp.tile.height", lang), "unit": "cm", "state": None}
        out["value"] = ("≈" if (height or {}).get("estimated") else "") + fmt_num((height or {}).get("cm"), lang, 0) if height else "–"
        gap = fmt_qty(round(GROW_HEIGHT_CM - float(height["cm"])), "cm", lang) if height else ""
        if isinstance(problem, dict):
            out.update(sub=t(f"gp.height.{problem.get('why')}", lang), state="warn")
        elif height and height.get("method") == "camera_strip":
            read = (self.state.get("height") or {}).get("at")
            out["sub"] = t("gp.height.sub", lang, time=fmt_time(read, lang) if read else "", gap=gap)
        elif height:
            out["sub"] = t("gp.height.typed", lang, gap=gap)
        else:
            out["sub"] = t("gp.height.none", lang)
        return out

    def _light_tile(self, snap: Mapping[str, Any], lang: str) -> dict[str, Any]:
        reported = (snap.get("light") or {}).get("level")
        light = self.state.get("light") or {}
        timer = self.timer.cfg if self.timer is not None else {}
        hours = hours_on(timer)
        sub = t("gp.light.goal", lang, goal=fmt_qty(light["goal"], "%", lang), hours=fmt_num(hours, lang, 0)) if light and hours else ""
        return {"id": "light", "label": t("gp.tile.light", lang), "value": fmt_num(reported, lang, 0) if reported is not None else "–",
                "unit": "%", "sub": sub, "state": None if timer.get("enabled") else "warn"}

    def _soil_tile(self, snap: Mapping[str, Any], lang: str) -> dict[str, Any]:
        soil = snap.get("soil")
        out: dict[str, Any] = {"id": "soil", "label": t("gp.tile.soil", lang), "value": "–", "unit": "%", "state": None}
        if not isinstance(soil, Mapping):
            out["sub"] = t("gp.soil.none", lang)
        elif soil.get("percent") is None:
            out.update(sub=t("gp.soil.silent", lang), state="warn")
        elif soil.get("calibrated") and soil.get("relative") is not None:
            out.update(value=fmt_num(soil["relative"], lang, 0), sub=t("gp.soil.sub_set", lang, low=fmt_num(soil.get("trigger"), lang, 0)),
                       state={"low": "warn", "high": "bad", "ok": "ok"}.get(soil.get("state")))
        else:
            out.update(value=fmt_num(soil["percent"], lang, 0), sub=t("gp.soil.sub_raw", lang), state="warn")
        return out

    def _feed_tile(self, now: float, lang: str) -> dict[str, Any]:
        day = parse_day(self.plant_log.data.get("feed_from"))
        out: dict[str, Any] = {"id": "feed", "label": t("gp.tile.feed", lang), "value": "–", "unit": "", "state": None}
        if day is None:
            out["sub"] = t("gp.feed.none", lang)
            return out
        left = (day - self._today(now)).days
        if left > 0:
            out.update(value=fmt_num(left, lang), unit=t("gp.unit.days", lang), sub=t("gp.feed.until", lang, date=date_text(day, lang)))
        else:
            out.update(value="✓", sub=t("gp.feed.now", lang, date=date_text(day, lang)), state="ok")
        return out

    def _light_view(self, snap: Mapping[str, Any], now: float, lang: str) -> dict[str, Any]:
        """The line under the tiles: what happens to the light next, and why it does not when it does not."""
        light = self.state.get("light")
        timer = self.timer
        if timer is None:
            return {"text": None}
        view = snap.get("light_schedule") or timer.view(dict(snap), lang)
        auto = view.get("automatic") or {}
        if not timer.cfg.get("enabled"):
            return {"text": t("gp.light.off", lang), "start": (auto.get("plan") or {}).get("fingerprint") if auto.get("available") else None}
        if not light or not timer.cfg.get("from_requirements"):
            return {"text": view.get("message")}
        level = int(light["level"])
        pct = fmt_qty(level, "%", lang)
        upcoming = view.get("next_switch") or {}
        on_at = upcoming.get("at") if upcoming.get("on") else None
        when = fmt_time(on_at, lang) if on_at else _clock(timer.cfg.get("on_time"), lang)
        hold = light.get("hold")
        stats = light.get("stats") or {}
        tg = bands(current_stage(load_profile() or {}, self.plant_log))
        day = tg.get("probe_temp_day") or tg.get("probe_temp") or {}
        out: dict[str, Any] = {"resume": hold in ("owner", "heat", "climate")}
        if hold == "owner":
            out["text"] = t("gp.light.hold.owner", lang, level=pct)
        elif hold == "heat":
            retry = float(light.get("retry_at") or now)
            out["text"] = t("gp.light.hold.heat", lang, level=pct, date=date_text(self._today(retry), lang))
        elif hold == "climate":
            retry = float(light.get("retry_at") or now)
            out["text"] = t("gp.light.hold.climate", lang, level=pct, date=date_text(self._today(retry), lang))
        elif hold == "warm" and stats.get("temp") is not None and day:
            out["text"] = t("gp.light.hold.warm", lang, level=pct, temp=fmt_qty(stats["temp"], "°C", lang),
                            limit=fmt_qty(float(day["max"]) - RAISE_ROOM, "°C", lang))
        elif hold == "dry" and stats.get("vpd") is not None and tg.get("probe_vpd"):
            out["text"] = t("gp.light.hold.dry", lang, level=pct, vpd=fmt_qty(stats["vpd"], "kPa", lang),
                            limit=fmt_qty(float(tg["probe_vpd"]["max"]) + DRY_MARGIN, "kPa", lang))
        elif hold == "lamp":
            height = self.height_now()
            gap = GROW_HEIGHT_CM - float((height or {}).get("cm") or 0)
            out["text"] = t("gp.light.hold.lamp", lang, level=pct, gap=fmt_qty(round(gap), "cm", lang), min=fmt_qty(LAMP_MIN_CM, "cm", lang))
        elif hold in ("no_height", "no_data"):
            out["text"] = t(f"gp.light.hold.{hold}", lang, level=pct)
        elif level >= int(light["goal"]):
            out["text"] = t("gp.light.at_goal", lang, level=pct)
        elif (snap.get("light") or {}).get("level") != level:
            out["text"] = t("gp.light.next_set", lang, time=when, level=pct)
        else:
            out["text"] = t("gp.light.next_if", lang, level=fmt_qty(min(int(light["goal"]), level + RAISE_STEP), "%", lang), time=when)
        return out

    def _pump_view(self, snap: Mapping[str, Any], lang: str) -> dict[str, Any] | None:
        """The water pump as one more device beside light, fans and humidifier (the overview's glance, the automation page).
        control_state in the autopilot's words, short: what its state means here, status: the sentence behind it."""
        port = (snap.get("ports") or {}).get("usb1")
        watering = snap.get("watering")
        if not isinstance(port, Mapping) or not isinstance(watering, Mapping):
            return None
        settings = watering.get("settings") or {}
        thirst = snap.get("thirst") or {}
        pump = thirst.get("pump") or {}
        measured = bool(pump.get("power_confirmed") and (pump.get("flow") or {}).get("calibrated"))
        blocked = watering.get("blocked")
        decision = (thirst.get("decision") or {}).get("text")
        if watering.get("active"):
            state, short, status = "ready", "gp.pump.running", None
        elif port.get("on"):
            state, short, status = "needs_setup", "gp.pump.unmanaged", t("gp.pump.unmanaged_text", lang)
        elif settings.get("enabled") and blocked:
            state, short, status = "waiting", "gp.pump.waiting", t(f"watering.reason.{blocked}", lang)
        elif settings.get("enabled"):
            times = ", ".join(_clock(hhmm, lang) for hhmm in settings.get("times") or [])
            state, short = "ready", "gp.pump.watch"
            status = " ".join(x for x in (t("gp.pump.watch_text", lang, times=times), decision) if x)
        elif measured:
            state, short, status = "needs_setup", "gp.pump.watch_off", t("gp.pump.watch_off_text", lang)
        else:
            state, short, status = "needs_setup", "gp.pump.measure", t("gp.pump.measure_text", lang)
        return {"id": "pump", "name": t("gp.pump.name", lang), "port": "usb1", "control_state": state, "short": t(short, lang), "status": status}

    def _photo_view(self, lang: str) -> dict[str, Any] | None:
        height = self.state.get("height")
        if not isinstance(height, dict) or not self.photo_path().exists() or height.get("top_y") is None:
            return None
        return {"url": f"/api/grow_plan/photo?t={int(float(height.get('at') or 0))}", "top_y": height["top_y"],
                "top_x": height.get("top_x"), "label": "≈" + fmt_qty(height.get("cm"), "cm", lang, digits=0), "alt": t("gp.photo_alt", lang)}

    def _marks_view(self, profile: Mapping[str, Any], stage: Mapping[str, Any] | None, lang: str) -> list[dict[str, Any]]:
        height = self.height_now()
        cm = float(height["cm"]) if height else None
        in_flower = (stage or {}).get("key") in FLOWERING_STAGES
        entries = self.state.get("marks") or {}
        out = []
        next_found = False
        for mark in self._marks_of(profile):
            entry = entries.get(mark["id"]) or {}
            done = bool(entry.get("done")) or (mark["id"] == "flowering" and in_flower)
            reached = cm is not None and cm >= mark["from"]
            state = "done" if done else "now" if reached else "next" if not next_found else "later"
            if mark["id"] == "topping" and in_flower and not done:
                state = "past"
            next_found = next_found or state == "next"
            until, start = mark["until"], mark["from"]
            if until is None:
                span = t("gp.mark.range_from", lang, cm=fmt_qty(start, "cm", lang, 0))
            elif mark["id"] == "trellis":
                span = t("gp.mark.range_until", lang, cm=fmt_qty(until, "cm", lang, 0))
            else:
                span = t("gp.mark.range", lang, a=fmt_num(start, lang, 0), b=fmt_qty(until, "cm", lang, 0))
            item: dict[str, Any] = {"id": mark["id"], "title": t(f"gp.mark.{mark['id']}", lang), "text": t(f"gp.mark.{mark['id']}_text", lang),
                                    "state": state, "state_text": t(f"gp.mark.{state}", lang), "span": span, "buttons": []}
            if state == "now":
                if mark["id"] == "flowering":
                    item["buttons"].append({"label": t("gp.mark.flowering_btn", lang), "action": "plant_stage", "key": "early_flower", "armed": True})
                else:
                    item["buttons"].append({"label": t("gp.mark.done_btn", lang), "action": "grow_plan_mark", "id": mark["id"]})
                    if mark["id"] == "topping":
                        item["buttons"].append({"label": t("gp.mark.skip_btn", lang), "action": "grow_plan_mark", "id": mark["id"]})
            out.append(item)
        return out

    def _rows(self, stage: Mapping[str, Any] | None, snap: Mapping[str, Any], lang: str) -> list[dict[str, Any]]:
        tg = bands(stage)
        climate = snap.get("climate") or {}
        lights_on = bool((snap.get("light") or {}).get("level"))
        temp = (tg.get("probe_temp_day") or tg.get("probe_temp")) if lights_on else (tg.get("probe_temp_night") or tg.get("probe_temp"))
        hum, vpd = tg.get("probe_humidity"), tg.get("probe_vpd")
        pilot = ((snap.get("autopilot") or {}).get("boxes") or [{}])[0]
        device = {d.get("id"): d for d in pilot.get("devices") or []}
        humidifier = pilot.get("humidifier") or {}
        timer = self.timer.cfg if self.timer is not None else {}
        light = snap.get("light") or {}
        rows: list[dict[str, Any]] = []

        def band(target: Mapping[str, Any] | None, unit: str) -> str:
            if not target:
                return "–"
            return t("gp.mark.range", lang, a=fmt_num(target["min"], lang), b=fmt_qty(target["max"], unit, lang))

        def status(value: Any, target: Mapping[str, Any] | None) -> str | None:
            if not target or type(value) not in (int, float):
                return None
            return "warn" if value < target["min"] or value > target["max"] else "ok"

        schedule = t("gp.light.schedule", lang, on=_clock(timer.get("on_time"), lang), off=_clock(timer.get("off_time"), lang)) if timer.get("enabled") else t("gp.water.watch_off", lang)
        rows.append({"what": t("gp.row.light", lang), "who": t("gp.who.light", lang), "now": fmt_qty(light.get("level"), "%", lang),
                     "target": schedule, "state": None, "hint": None})
        rows.append({"what": t("gp.row.temp", lang), "who": t("gp.who.exhaust", lang), "now": fmt_qty(climate.get("probe_temp"), "°C", lang),
                     "target": band(temp, "°C"), "state": status(climate.get("probe_temp"), temp), "hint": (device.get("dfan") or {}).get("status")})
        rows.append({"what": t("gp.row.humidity", lang), "who": t("gp.who.humidifier", lang), "now": fmt_qty(climate.get("probe_humidity"), "%", lang),
                     "target": band(hum, "%"), "state": status(climate.get("probe_humidity"), hum), "hint": humidifier.get("status")})
        rows.append({"what": t("gp.row.vpd", lang), "who": t("gp.who.both", lang), "now": fmt_qty(climate.get("probe_vpd"), "kPa", lang),
                     "target": band(vpd, "kPa"), "state": status(climate.get("probe_vpd"), vpd), "hint": None})
        fan = snap.get("fan_stick")
        if isinstance(fan, Mapping) and fan.get("found"):
            rows.append({"what": t("gp.row.wind", lang), "who": t("gp.who.wind" if fan.get("auto") else "gp.who.wind_off", lang),
                         "now": t("gp.wind.now", lang, step=fan.get("step")) if fan.get("fan_seen") else t("gp.wind.none", lang),
                         "target": "–", "state": None, "hint": None})
        watering = snap.get("watering") or {}
        thirst = snap.get("thirst") or {}
        settings = watering.get("settings") or {}
        rows.append({"what": t("gp.row.water", lang), "who": t("gp.who.watch", lang),
                     "now": t("gp.water.watch_on", lang, times=", ".join(settings.get("times") or [])) if settings.get("enabled") else t("gp.water.watch_off", lang),
                     "target": "–", "state": None, "hint": (thirst.get("decision") or {}).get("text"), "off": bool(settings.get("enabled"))})
        day = parse_day(self.plant_log.data.get("feed_from"))
        rows.append({"what": t("gp.row.feed", lang), "who": t("gp.who.you", lang),
                     "now": t("gp.feed.until", lang, date=date_text(day, lang)) if day and day > self._today(self.clock()) else t("gp.feed.now", lang, date=date_text(day, lang)) if day else t("gp.feed.none", lang),
                     "target": "–", "state": None, "hint": None})
        return rows

    def _todo(self, snap: Mapping[str, Any], lang: str) -> list[dict[str, Any]]:
        """The steps only hands can do, in the order they depend on each other. Each one reads its own truth from the box."""
        out: list[dict[str, Any]] = []
        height = self.height_now()
        probe = self.state.get("probe")
        grown = bool(isinstance(probe, dict) and height and float(height["cm"]) - float(probe.get("cm") or 0) >= PROBE_REGROW_CM)
        out.append({"id": "probe", "title": t("gp.todo.probe", lang), "text": t("gp.todo.probe_text", lang), "done": isinstance(probe, dict) and not grown,
                    "kind": "button", "buttons": [{"label": t("gp.todo.probe_btn", lang), "action": "grow_plan_probe"}]})
        thirst = snap.get("thirst") or {}
        pump = thirst.get("pump") or {}
        flow = pump.get("flow") or {}
        measured = bool(pump.get("power_confirmed") and flow.get("calibrated"))
        runs = int(pump.get("flow_runs") or 0)
        running = (snap.get("watering") or {}).get("active") is not None
        out.append({"id": "pump", "title": t("gp.todo.pump", lang), "text": t("gp.todo.pump_text", lang), "done": measured, "kind": "pump",
                    "run_label": t("gp.todo.pump_run", lang), "ml_label": t("gp.todo.pump_ml", lang), "save_label": t("gp.todo.pump_save", lang),
                    "runs": t("gp.todo.pump_runs", lang, n=fmt_num(min(runs + 1, 2), lang)), "seconds": PUMP_TEST_S, "running": running,
                    "done_text": fmt_qty(round(float(flow["ml_s"]) * 60), "ml/min", lang) if measured and flow.get("ml_s") else None})
        soil = snap.get("soil") if isinstance(snap.get("soil"), Mapping) else {}
        out.append({"id": "soak", "title": t("gp.todo.soak", lang), "text": t("gp.todo.soak_text", lang), "done": bool(soil.get("calibrated")), "kind": "soak",
                    "water_label": t("gp.todo.soak_btn", lang), "set_label": t("gp.todo.soak_set", lang), "can_water": measured and not running,
                    "can_set": soil.get("percent") is not None, "litres": SOAK_LITRES})
        settings = (snap.get("watering") or {}).get("settings") or {}
        out.append({"id": "watch", "title": t("gp.todo.watch", lang), "text": t("gp.todo.watch_text", lang), "done": bool(settings.get("enabled")), "kind": "button",
                    "enabled": measured, "buttons": [{"label": t("gp.todo.watch_btn", lang), "action": "grow_plan_watch_on"}]})
        return out


def _clock(hhmm: str | None, lang: str) -> str:
    """A saved HH:MM as the page writes a time: 20:00 in German, 8:00 PM in English."""
    if not hhmm:
        return "–"
    return fmt_time(datetime(2026, 1, 1, int(hhmm[:2]), int(hhmm[3:])).timestamp(), lang)


def _pct(value: Any) -> dict[str, Any]:
    return {"q": [value, "%", None]}


def _deg(value: Any) -> dict[str, Any]:
    return {"q": [value, "°C", None]}


def _kpa(value: Any) -> dict[str, Any]:
    return {"q": [value, "kPa", None]}


def _cm(value: Any) -> dict[str, Any]:
    return {"q": [round(float(value)), "cm", 0]}


def _shown(params: Mapping[str, Any], lang: str) -> dict[str, Any]:
    """A log line's values in the reader's language: {"q": [number, unit, digits]} as a quantity, {"de", "en"} as a text."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, Mapping) and "q" in value:
            number, unit, digits = value["q"]
            out[key] = fmt_qty(number, unit, lang, digits=digits)
        elif isinstance(value, Mapping) and lang in value:
            out[key] = value[lang]
        else:
            out[key] = value
    return out


def start_grow_plan(box: Any, plant_log: PlantLog, demo: bool, **kwargs: Any) -> GrowPlan | None:
    """The plan for serve(), or None when it cannot be built: logged, and the cockpit runs without it. No camera reading in the
    demo: there is no strip in a placeholder."""
    try:
        plan = GrowPlan(box, plant_log, **kwargs)
        if not demo:
            plan.start()
        return plan
    except Exception:
        _LOGGER.exception("The grow plan could not be started; the cockpit runs without it")
        return None
