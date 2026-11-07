# Durst-Index: Gießplan nach Phase und Bedarfsprüfung

[Deutsch](#deutsch) · [English](#english) · [Betrieb](operations.md#durst-index-und-bedarfsprüfung) · [Architektur](architecture.md)

Code: [`vgrow/thirst.py`](../vgrow/thirst.py), Hank: [`vgrow/hank/thirst_report.py`](../vgrow/hank/thirst_report.py),
Zahlen: [`kartei/water_model.json`](../kartei/water_model.json), Karte: [`web/thirst.js`](../web/thirst.js).

## Deutsch

### Was der Index sagt

Der Durst-Index sagt, wie viel Wasser dem Topf fehlt. 0 heißt frisch gegossen und abgetropft, 100 heißt Zeit zum
Prüfen, über 100 ist überfällig. 100 ist erreicht, wenn die Hälfte des Wassers verbraucht ist, das die Erde für die
Pflanze hält (Quelle U1 in `water_model.json`).

- D ist die Wassermenge in Litern, die dem Topf seit dem letzten Bezugspunkt fehlt.
- D_trig = Auslöseanteil der Phase mal verfügbares Wasser je Liter Erde (0,3, Annahme bis zur ersten gewogenen
  Austrocknung, Quelle B05) mal Topfvolumen. Ein Topf mit 11,4 l (3 US-Gallonen) ist bei 0,5 mal 0,3 mal 11,4 l, also
  bei 1,71 l fällig.
- Index = 100 mal D durch D_trig.
- D wächst mit der gerechneten Verdunstung (ET, über Blätter und Erde) aus Licht, VPD und Pflanzenhöhe, nachts mit einem
  Zehntel davon. D sinkt mit jedem gezählten Gießen. Was über D hinaus gegossen wird, ist ein Überschuss: dann gilt die
  Nässesperre (kein Wasser, bis die Rechnung ab dem Überschuss wieder 100 erreicht und eine Waage oder zwei trockene
  Fingerproben in 5 cm Tiefe, 12 h auseinander, das bestätigen).

### Gemessen oder geschätzt

| Gemessen oder eingetragen | Geschätzt, bis eine Wägung es bestätigt |
|---|---|
| Topfvolumen (Liter, oder Durchmesser und Höhe) | Verdunstung zwischen zwei Prüfungen (Startwert 20 ml je Stunde, Annahme) |
| Fingerprobe (2,5 oder 5 cm) oder Anheben, Waage | verfügbares Wasser der Erde (30 % des Topfs) |
| Gießen von Hand, Liter im Block Düngen | Phasenwechsel späterer Wochen nach den Tagen der Phasenkarte |
| Licht (Lichttimer oder gemeldeter Wert), VPD des Fühlers, Pflanzenhöhe | VPD späterer Wochen: Mitte des Bands der Phasenkarte |

Ein Index nur aus der Rechnung fragt nach einer Prüfung, er gießt nie. Gemessen gilt der Index 24 h nach einer
Fingerprobe oder Wägung. Die Unsicherheit beginnt bei plus oder minus 5 Punkten nach einer Wägung und bei 25 nach einer
Fingerprobe und wächst um 10 je Tag ohne Prüfung und je Tag fehlender Daten.

### Gießplan nach Phase

Die Phase setzt die Person (eine Schätzung handelt nie). Je Phase gelten aus `water_model.json` der Auslöseanteil
(Keimling 0,4, sonst 0,5; späte Blüte 0,6 nur mit dem Schalter "späte Blüte trockener") und die Höchstmenge je
Gießen und je 24 h aus den Pumpenvoreinstellungen der Phasenkarte (Keimling 200 ml, Wachstum 1000 ml, Blüte 1400 ml),
höchstens 0,6 des verfügbaren Wassers. Die Karte zeigt die nächsten Wochen wie der Düngeplan: Phase, fällig bei,
höchstens je Gießen, Verbrauch am Tag, gießen etwa alle, Wasser je Woche. Ohne Zeichen: eingetragen, gemessen oder
Voreinstellung der Phasenkarte; mit ≈: geschätzt.

### Vorhersage

"Nächstes Gießen voraussichtlich <Tag, Tageszeit>, etwa <Liter>", dazu frühestens und spätestens aus der Unsicherheit.
Gerechnet wird mit dem Lichttimer, wenn er läuft, sonst mit dem gemeldeten Licht rund um die Uhr. Wird der Topf
außerhalb der täglichen Runde fällig (Licht an plus 1 h, ohne Lichtplan 09:00, jeweils 12 h lang), nennt die Vorhersage
beides: wann er fällig wird und die Runde, zu der gegossen wird. Läuft der Gießplan, nennt sie den passenden Termin.

### Bedarfsprüfung vor jedem geplanten Gießtermin

Der Gießplan (`vgrow/watering.py`) fragt vor jedem automatischen Termin über einen einzigen Haken (`need_gate`):

- Der Termin läuft nur, wenn der Topf gemessen fällig ist: Index 100 oder mehr, mit einer Prüfung oder Wägung in den
  letzten 24 h, ohne Nässesperre und ohne Sperre (trockener Tank, alte Messwerte, Lücken, Abstand unter 20 h, schon
  heute gegossen, Wochengrenze, App-Plan an der Pumpe, geänderte Belegung, unbestätigte Phase).
- Sonst entfällt er, mit dem Grund im Verlauf des Gießplans, zum Beispiel "Topf noch nicht fällig: Durst-Index 62 %".
  Ein ausgelassener Termin wird nie nachgeholt.
- Mit bestätigtem Pumpenschalter und gemessenem Durchfluss wird der Lauf auf die Gabe gekürzt, höchstens auf die
  Höchstmenge der Phase. Ohne beides entfällt der automatische Termin. Die manuelle Messbecher-Kalibrierung bleibt möglich.
- Die Prüfung kann einen Termin nur auslassen oder kürzen, nie starten oder verlängern. Ein Fehler lässt den Termin
  aus. "Einmal gießen" bleibt die eigene Handlung der Person: daneben stehen der Index und ein Hinweis, wenn der Topf
  nicht fällig ist.
- Ein voller Tank ist nie ein Grund zu gießen: der Tank wird nur in `blocks()` gelesen, und nur als Sperre (ein Test
  sucht das im Code).

### USB-Strom ist kein Wasserfluss

USB 1 kann Strom melden, ohne dass Wasser fließt (am 26.10.2026 meldete USB 1 über 23 h Strom, der Tank blieb nass).
Ein Pumpenlauf zählt deshalb nur so:

- ohne bestätigten Pumpenschalter (Einstellung des Gießplans): gar nicht; er macht die letzte Prüfung ungültig und
  verbreitert die Unsicherheit;
- mit Schalter, ohne Durchfluss: als unbekannte Menge; der Topf gilt dann als voll, die Nässesperre beginnt;
- mit Schalter und Durchfluss (zwei Messbecher-Läufe, höchstens 10 % auseinander, 30 Tage gültig): die längste mögliche
  Laufzeit mal Durchfluss mal 1,1, die Seite, die nie Wasser dazudichtet.

Nie Minuten mal einem geschätzten Durchfluss.

### Eingaben und Daten

Die Karte unter Automatik schickt nur diese Aktionen an `/api/cmd`, keine schaltet ein Gerät: `thirst_pot` (Topf
einrichten), `thirst_weigh`, `thirst_check` (Finger oder Anheben), `thirst_manual` (von Hand gegossen), `thirst_refill`
(mit oder ohne Dünger), `thirst_saucer`, `thirst_arrived`, `thirst_flow` (Messbecher). `GET /api/thirst` liefert die
Karte. Der Zustand liegt unter `STATE_DIR/boxes/<box>/` in `thirst-pot.json`, `thirst-state.json` und
`thirst-log.jsonl`, nie im Repository.

Der Bodensensor (`vgrow/soil.py`, Ecowitt WH51 am Gateway GW1200) zählt nach dem Einstellen wie eine Fingerprobe in 5 cm:
unter dem Zielwert trocken, im Band feucht, darüber nass. Er schreibt eine Antwort, sobald sie zwei Messungen hält, und
wiederholt sie alle 6 h, damit der Index gemessen bleibt. Was eine Prüfung bewirkt, entscheidet weiter allein das Hauptbuch:
Eine Nässesperre hebt auch er nur mit zwei trockenen Antworten, 12 h auseinander und ab dem Index der Rechnung. Der Zielwert
kommt aus `water_model.json`: Bei 0,55 Wasser im abgetropften Substrat (B05), 0,3 nutzbar und dem Auslöser 0,5 (U1) sind
es 73 % des Sensorwerts im abgetropften Topf. Dass der Sensor dazwischen linear ist, ist eine Annahme bis zur ersten mit der
Fingerprobe verglichenen Austrocknung. Sichtbar auf der Seite Wasser & Boden (`#water`).

### Hank

Hank beantwortet "Muss ich gießen?", "Wann gieße ich?", "Wie viel gieße ich?" und "Der Tank ist voll, soll ich
gießen?" aus Code, mit Index, Vorhersage, Warum und der Prüfung mit einem Tipp. "Wurde heute schon gegossen?" bleibt
beim Pumpenverlauf.

### Demo

`VGROW_DEMO_THIRST=<Szene>` füllt den Demo-Ordner: `nopot`, `pot`, `waiting`, `ask`, `due`, `wethold`, `skipped`,
`shortened`. Die Szenen nehmen den Topf, wie ihn der Eigentümer eingetragen hat (3 US-Gallonen, etwa 11,4 l); einen
voreingestellten Topf gibt es nicht.

### Noch offen

Die Liter-Eingabe meint das eingefüllte Substrat. Spätere Wochen halten die heutige Höhe und den heutigen Lichtplan
fest; Wachstum kann den Bedarf erhöhen. Die ET-Schätzung trennt Verdunstung der Erde und der Blätter nicht. Eine
Rechnung ersetzt keine Feuchtemessung. Die Karte zeigt diese Annahmen und auch beim Abtrocknen die Zeitspanne.

Durchfluss-Eingaben außerhalb des dreifachen bzw. eines Drittels des Plausibilitätswerts brauchen eine erneute
Bestätigung. Dieser Wert ist eine Eingabeprüfung, keine Behauptung über die Leistung einer Pumpe. Alte auffällige,
unbestätigte Messungen gelten nicht als Kalibrierung. Ein automatischer Lauf unterhalb des Bestätigungs- und
Stoppfensters entfällt.

Das Lernen der Verdunstung aus Wägungen, ein Tankzähler, die Haltung der Blätter aus dem Foto, ein Pumpenlauf mit
eigener Menge statt der Laufzeit des Gießplans.

## English

### What the index says

The thirst index says how much water the pot is missing. 0 means just watered and drained, 100 means time to check,
above 100 is overdue. 100 is reached when half of the water the soil holds for the plant is used (source U1 in
`water_model.json`).

- D is the water in litres the pot has lost since the last anchor.
- D_trig = the stage's trigger share times the available water per litre of soil (0.3, an assumption until the first
  weighed dry-down, source B05) times the pot volume. A pot of 11.4 l (3 US gallons) is due at 0.5 x 0.3 x 11.4 l, that
  is 1.71 l.
- Index = 100 x D / D_trig.
- D grows with the modelled evapotranspiration (ET, leaves and soil) from the light, the VPD and the plant height, at
  night with a tenth of it. D falls with every counted watering. Water beyond D is an overshoot: then the wet-hold holds
  (no water until the model from the overshoot is back at 100 and a scale or two dry finger tests at 5 cm, 12 h apart,
  confirm it).

### Measured or estimated

| Measured or entered | Estimated until a weighing confirms it |
|---|---|
| Pot volume (litres, or diameter and height) | Evapotranspiration between two checks (start value 20 ml an hour, an assumption) |
| Finger test (2.5 or 5 cm) or lifting, the scale | Available water of the soil (30 % of the pot) |
| Watering by hand, litres in the feeding block | Stage changes of later weeks by the days of the stage card |
| Light (light timer or reported level), probe VPD, plant height | VPD of later weeks: the middle of the stage card's band |

An index from the model alone asks for a check; it never waters. The index counts as measured for 24 h after a finger
test or a weighing. The uncertainty starts at plus or minus 5 points after a weighing and 25 after a finger test and
grows by 10 a day without a check and per day of missing data.

### Watering plan by stage

The stage is set by the person (an estimate never acts). Per stage `water_model.json` gives the trigger share
(seedling 0.4, otherwise 0.5; late flower 0.6 only with the switch "drier in late flower") and the cap per watering and
per 24 h from the stage card's pump presets (seedling 200 ml, vegetative 1000 ml, flower 1400 ml), at most 0.6 of the
available water. The card shows the coming weeks like the feeding plan: stage, due at, at most per watering, use per
day, water about every, water per week. Without a mark: entered, measured or a preset of the stage card; with ≈:
estimated.

### Forecast

"Next watering expected <day, time of day>, about <litres>", with the earliest and the latest time the uncertainty
allows. It counts with the light timer when it runs, otherwise with the reported light around the clock. When the pot
becomes due outside the daily round (lights on plus 1 h, 09:00 without a light plan, 12 h each), the forecast names
both: when it is due and the round at which it is watered. With the schedule on, it names the matching slot.

### The need check before every scheduled slot

The watering schedule (`vgrow/watering.py`) asks before every automatic slot through one hook (`need_gate`):

- The slot runs only when the pot is measured due: index 100 or more, with a check or a weighing in the last 24 h, no
  wet-hold and no guardrail (dry tank, stale readings, gaps, less than 20 h apart, already watered today, weekly limit,
  an app plan on the pump, a changed port assignment, an unconfirmed stage).
- Otherwise it is skipped, with the reason in the schedule's history, for example "Pot not due yet: thirst index 62%".
  A skipped slot is never caught up.
- With the pump switch confirmed and the flow measured, the run is shortened to the dose, at most to the stage's cap.
  Without both, the automatic slot is skipped. Manual measuring-jug calibration remains available.
- The check can only skip or shorten a slot, never start or lengthen one. An error skips the slot. "Water once" stays
  the person's own action: beside it the index shows, with a warning when the pot is not due.
- A full tank is never a reason to water: the tank is read only in `blocks()`, and only as a block (a test greps the
  code for it).

### USB power is not water flow

USB 1 can report power without water flowing (on 26.10.2026 USB 1 reported power for more than 23 h while the tank
stayed wet). A pump run therefore counts only like this:

- without the confirmed pump switch (watering schedule settings): not at all; it makes the last check stale and widens
  the uncertainty;
- with the switch, without a flow: as an unknown amount; the pot then counts as full and the wet-hold starts;
- with the switch and a flow (two measuring-jug runs at most 10 % apart, valid for 30 days): the longest possible run
  time times the flow times 1.1, the side that never adds water.

Never minutes times a guessed flow.

### Inputs and data

The card under Automation sends only these actions to `/api/cmd`, none of them switches a device: `thirst_pot` (set up
the pot), `thirst_weigh`, `thirst_check` (finger or lifting), `thirst_manual` (watered by hand), `thirst_refill` (with or
without feed), `thirst_saucer`, `thirst_arrived`, `thirst_flow` (measuring jug). `GET /api/thirst` returns the card.
The state lives under `STATE_DIR/boxes/<box>/` in `thirst-pot.json`, `thirst-state.json` and `thirst-log.jsonl`, never
in the repository.

Once set to the soil, the soil sensor (`vgrow/soil.py`, Ecowitt WH51 on a GW1200 gateway) counts like a finger check at
5 cm: below the target dry, in the band moist, above it wet. It writes an answer once two readings hold it and repeats it
every 6 h, so the index stays measured. What a check does stays with the ledger alone: it lifts a wet-hold only like anyone
else, with two dry answers 12 h apart and from the model's index on. The target comes from `water_model.json`: with 0.55
water in the drained substrate (B05), 0.3 available and the trigger 0.5 (U1) it is 73 % of the sensor's reading in the
drained pot. That the sensor is linear in between is an assumption until a first dry-down has been compared with a finger
check. Shown on the Water & soil page (`#water`).

### Hank

Hank answers "Do I need to water?", "When should I water?", "How much should I water?" and "The tank is full, should I
water?" from code, with the index, the forecast, why, and the one-tap check. "Wurde heute schon gegossen?" stays with
the pump history.

### Demo

`VGROW_DEMO_THIRST=<scene>` fills the demo folder: `nopot`, `pot`, `waiting`, `ask`, `due`, `wethold`, `skipped`,
`shortened`. The scenes use the pot as its owner entered it (3 US gallons, about 11.4 l); there is no default pot.

### Still open

The volume entry means filled substrate. Later weeks hold today's height and light plan fixed; growth can increase
demand. The ET estimate does not separate soil evaporation from leaf transpiration. An estimate does not replace
a moisture check. The card discloses these assumptions and shows a time range during a wet-hold too.

Flow entries beyond three times or below one third of the plausibility reference require confirmation again. That
reference checks typing errors; it does not claim an actual pump output. Old unusual, unconfirmed measurements do
not count as calibration. Automatic runs shorter than the confirmation and stop window are skipped.

Learning the evapotranspiration from weighings, a tank meter, the leaves' posture from the photo, a pump run with its
own amount instead of the schedule's run time.

## Integration checkpoint: 27 October 2026

The interrupted review pass is complete. Scheduled slots require a current verified flow calibration and confirmed
pump switching. Unusual calibration entries ask again; editing a value or letting the confirmation expire revokes
the confirmation. An owner-named pump can be stopped despite a conflicting readable `always_on` rule, using the
correct box's assignment. The manual measuring-jug run remains independent of the demand gate.

Wet-hold forecasts include their uncertainty range. Later-week projections disclose their fixed height/light
assumptions. Substrate volume and unconfirmed drainage/saucer choices are explicit. Model assumptions are separated
from contextual source references; a reference does not establish an assumed coefficient.

Validation on the combined branch: 2,605 passed and one existing XPASS; offline Hank evaluation 140 passed, nine
waiting for reference cards, zero failures; 96 demo browser captures across German/English, phone/desktop and
light/dark; nine calibration UI checks with zero actuator commands. Captures and the UI check script are in the
ignored `_shots/durst-review/` directory. No live schedules are enabled by this integration.

Remaining work is separate from this integration: learning consumption from weighings, a tank-volume meter,
camera posture observations, direct dose-based runs, an orphan-pump guard and coordination across controller
hosts. Review follow-ups also include light-aware integration across downtime, tighter report-gap accounting
and a note for weighings during the drain-settle window. The long-block alert counts its hours from the start of
the block (follow-up of 27 October 2026). Run only one
real controller host. Do not treat these unbuilt features as completed roadmap items.
