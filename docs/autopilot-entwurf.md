# Hank Autopilot: Entwurf

Historisches Entwurfsdokument. Stand der Umsetzung vom 26.10.2026: Die hier vorgeschlagenen stündlichen und
täglichen Befehlsquoten wurden auf Wunsch des Nutzers vollständig entfernt. Aktuelles Verhalten: README.md.

Stand 23.10.2026. Nur Entwurf, nichts gebaut, das Repo `vgrow-control` ist unverändert.
Grundlage: Code-Karte vom 23.10., Messwerte `history-24h.json` und `history-168h.json`, `state.json` von 08:28,
Phasenkarte `kartei/stages/autoflower.json`, Roadmap (Abschnitte "hands-off", "Hanks Prinzip", "Leitplanken").

Eugens Wunsch (23.10. morgens): "Cool wäre, dass ich Hank auf Autopilot setze und dass er alles reguliert. Oder dass
ich manuell die Sachen auch regulieren kann."

## Kurzfassung

- **Ein Schalter "Autopilot"** in der bestehenden Karte. Standard aus. Nur Eugen schaltet ein (zwei Tipps).
- **Der Code regelt, nicht das Sprachmodell.** Hank meldet jeden Schritt mit festem Text und einem Knopf "Rückgängig".
- **Hand schlägt Automatik.** Jeder Handgriff pausiert das Gerät 60 Minuten. Danach regelt der Autopilot vom
  aktuellen Wert aus weiter. Er stellt nie einen gespeicherten alten Wert wieder her.
- **Nachspiel auf echten Messwerten** (23,4 Stunden, 1.405 Messpunkte): für die heutige Phase (frühe Blüte)
  **2 Befehle am Tag, keine Umkehr, schlechteste Stunde 1 Befehl.** Der erste Wurf der Regeln hätte 19 Befehle,
  6 Umkehrungen und 6 Befehle in der schlechtesten Stunde geschickt.
- **Wichtigster Fund:** der Fühler bekommt offenbar Nebel vom Befeuchter ab. Am 22.10. stand er 210 Minuten lang
  bei bis zu 83 %, während die Boxluft bei 46 bis 49 % blieb. Ohne Sperre hätte die Abluft gegen eine Feuchte
  angeregelt, die es in der Box gar nicht gab.
- **Grenzen:** nur ein Tag Daten (die 168-Stunden-Abfrage lieferte dieselben 1.405 Zeilen, Datei byte-gleich),
  kein physikalisches Modell der Box, der Lichtzyklus ist nur aus der Temperatur erschlossen.

Zwei Begriffe, die unten oft vorkommen:
- **Stellglied:** ein Gerät, das der Autopilot schalten darf. Hier Licht, Abluft, Umluft und Befeuchter.
- **Hysterese:** eine Lücke zwischen der Schwelle zum Handeln und der Schwelle zum Zurücknehmen. Ein Wert, der
  genau an der Grenze zittert, schaltet dann nicht dauernd hin und her.

---

## 1. Modi und Bedienung

### 1.1 Ein Schalter, Ausnahmen eingeklappt

Die Karte "Autopilot" (`web/index.html:260-275`) bekommt oben **einen Schalter**: Autopilot an oder aus.
Darunter eine Zeile mit dem Zustand ("regelt", "wartet auf Phase", "Pause bis 14:20" usw.).

Die Leiter Manuell, Vorschlag, Automatik gibt es schon als Legende (`index.html:262-266`). Sie wird pro Gerät
anklickbar, aber **eingeklappt** unter "Ausnahmen je Gerät" (Muster `<details class="feed-more">`, `index.html:217`):

| Gerät | Standard bei Autopilot an | Wahl in den Ausnahmen |
|---|---|---|
| Licht | Automatik | Automatik, Vorschlag, Manuell |
| Abluft | Automatik | Automatik, Vorschlag, Manuell |
| Umluft | Automatik | Automatik, Vorschlag, Manuell |
| Befeuchter | Automatik (die bestehende Regel) | Automatik, Manuell |
| KI-Tagesfoto | Vorschlag (bleibt so) | keine |
| Alarme | laufen immer | keine |

- **Automatik:** der Code schaltet in festen Grenzen.
- **Vorschlag:** dieselbe Regel rechnet, schaltet aber nicht. Hank zeigt eine Vorschlagskarte, Eugen bestätigt.
  Das ist die erste Stufe der Autonomie-Leiter, jetzt pro Gerät wählbar.
- **Manuell:** der Autopilot fasst das Gerät nicht an.

Der Schalter `#auto-hum` (heute einziger Schalter der Karte, `app.js:1425-1445`) wird zur Befeuchter-Zeile in den
Ausnahmen. Autopilot an schaltet die bestehende Befeuchter-Automatik mit ein, Autopilot aus schaltet sie mit aus.
Wer den Befeuchter allein automatisch will, stellt die anderen Geräte in den Ausnahmen auf Manuell.

Einschalten: zwei Tipps im 5-Sekunden-Fenster, wie heute (`autoArmUntil`, `app.js:1435-1440`).
Ausschalten: **ein Tipp**. Aus muss leichter sein als an.

### 1.2 Hank meldet, der Autopilot schaltet

Der Autopilot ist ein Regelwerk im Code. Hank ist nur der Sprecher. Grund: das Sprachmodell schaltet nie, im Chat
schaltet Hank nichts ohne dich (`hank/validator.py`, Text `hank.reply.no_self`: "Im Chat schalte ich nichts ohne
dich", EN "In the chat I switch nothing without you", seit 23.10.2026), und die Roadmap sagt "KI schaltet nie selbst, außer eine
Regel mit Sicherheitsgrenzen tut es".

Jede Meldung ist ein **fester Text aus dem Autopilot-Protokoll**, nie vom Modell formuliert. Satzgegenstand ist
"Autopilot", nicht "ich". So bleibt `no_self` wahr. Beispiel:

> Autopilot: Abluft 5 auf 6. Grund: VPD am Fühler 0,94 kPa, Ziel mindestens 1,0, seit 10 Minuten.
> [Rückgängig]

- **Rückgängig** schickt den alten Wert als normalen Handbefehl. Das zählt als Handgriff und pausiert das Gerät
  60 Minuten. Der Knopf gilt 30 Minuten (wie eine Vorschlagskarte, `proposals.py:46`), danach ist die Lage eine andere.
- **Hank-Modus** (`core.py:66-67`): "an" zeigt alle Schritte im Chat. "Nur Warnungen" zeigt nur Anschlag, Fehler und
  Pausen. "Aus" zeigt nichts im Chat, das Protokoll in der Karte bleibt. **Hank aus schaltet den Autopiloten nicht ab.**
  Der Autopilot speichert seinen Zustand getrennt von `hank_mode`.

### 1.3 Was die Ampel zeigt

Die Reihenfolge in `hank/nudges.py` (`assess`, :141-178) bleibt. Rot bleibt, wie es ist: Daten älter als 900 s oder
ein Alarm. Neu sind nur gelbe Gründe:

| Farbe | Grund | Was ein Tipp öffnet |
|---|---|---|
| grün | Autopilot an, alle Werte im Ziel | nichts nötig |
| gelb | Autopilot wartet: Phase nicht bestätigt, Lampenabstand nicht bestätigt, Daten alt | Hank mit dem einen Knopf, der fehlt |
| gelb | Nebel am Fühler (Abschnitt 2.2) | Hank mit dem Hinweis, Fühler oder Befeuchter umzustellen |
| gelb | Am Anschlag: Grenze erreicht, Wert 30 Minuten weiter außerhalb | Hank mit dem Rat aus der Phasenkarte (`advice_high` / `advice_low`) |
| gelb | Befehl nicht bestätigt | Hank mit Gerät, Wert und "noch einmal senden" |

Antippen der Ampel kommt ohnehin mit v1.1 (Roadmap, 23.10.).

### 1.4 Hand gewinnt immer

**Was als Handgriff zählt:**
- jeder Befehl über `/api/cmd`, der nicht vom Autopiloten stammt (Seite, angenommene Hank-Karte, Rückgängig),
- jede Änderung, die die Box meldet, ohne dass der Autopilot sie geschickt hat. Das fängt die VIVOSUN-App und die
  Tasten an der Box. Heute sieht das Cockpit solche Eingriffe nicht (Code-Karte Punkt 7).

**Was dann passiert:**
- Nur **dieses Gerät** pausiert **60 Minuten** (wie der Befeuchter, `MANUAL_PAUSE_SECONDS`, `automation.py:51`).
- Die Zeile zeigt "Pause bis 14:20, du hast übernommen".
- Nach der Pause regelt der Autopilot **vom dann gemeldeten Wert aus**. Bei der Abluft wird Eugens Stufe die neue
  Grundstufe. Der Autopilot kehrt nie zu einem gespeicherten Wert zurück. Ein gespeicherter Sollzustand ist nie die
  Autorität, wenn ein Mensch parallel schaltet.
- Wer länger will, stellt das Gerät in den Ausnahmen auf Manuell.

**Was der Autopilot nie übernimmt:**
- Licht nur bei `light.mode == 0` und `in_plan` falsch. Diese Sperre sitzt heute nur im Browser (`app.js:1268`) und
  muss in den Server.
- Abluft nur bei `dfan.mode == 0`. Ein Abluft-Befehl schreibt `mode: 0` (`shadow.py:399-401`). Er würde VIVOSUNs
  eigene Abluft-Automatik abschalten. Läuft sie, lässt der Autopilot die Abluft in Ruhe und sagt das.
- Umluft nicht, solange "Natürlicher Wind" an ist. Dann heißt "ok" nicht, was es scheint (`box.py:752-755`).
- Licht nie aus 0 einschalten. Der Code unterscheidet nicht zwischen Dunkelphase und von Hand aus (`plant.py:238-240`).

---

## 2. Regeln je Gerät

### 2.1 Gemeinsame Regeln

- **Messort:** die Klimaziele gelten am Fühler (Phasenkarte, `notes[0]`).
- **Glättung:** Median der letzten 5 Abfragen. Ein einzelner Ausreißer löst nichts aus.
- **Hysterese:** handeln erst, wenn der Wert um eine Marge **außerhalb** des Bandes liegt. Als "wieder gut" gilt
  er erst, wenn er um dieselbe Marge **innerhalb** liegt. Margen: Temperatur 0,5 °C, Feuchte 2 Punkte, VPD 0,05 kPa.
- **Dauer:** die Bedingung muss 10 Minuten am Stück gelten.
- **Tag und Nacht:** jedes Temperaturziel trägt `when: day` oder `when: night` (Phasenkarte), Auswahl über
  `lights_on = light.level > 0` (`plant.py:238-240`). Ob die Box im Dunkeln wirklich 0 meldet, ist nicht belegt
  (Abschnitt 5, Messung 1).
- **Nur mit bestätigter Phase:** `set_by == "manual"` wie beim Befeuchter (`automation.py:69-71`).

**Zielwerte aus der Phasenkarte** (`stages[].targets[].min/max`, `stages[].preset`):

| Phase | Temp. Licht an | Temp. Licht aus | Feuchte | VPD | `light_percent` | `cfan_step` |
|---|---|---|---|---|---|---|
| seedling | 20 bis 25 | 18 bis 22 | 65 bis 75 | 0,4 bis 0,8 | 60 | 1 |
| veg | 22 bis 28 | 18 bis 24 | 55 bis 70 | 0,8 bis 1,2 | 80 | keiner |
| early_flower | 20 bis 26 | 18 bis 22 | 45 bis 60 | 1,0 bis 1,4 | 100 | 10 |
| mid_flower | 20 bis 26 | 18 bis 22 | 40 bis 60 | 1,2 bis 1,6 | 100 | 10 |
| late_flower | 20 bis 24 | 16 bis 20 | 40 bis 50 | 1,2 bis 1,6 | 100 | 10 |
| harvest | 20 bis 24 | keiner | 40 bis 50 | keiner | kein Preset | keiner |

In der Ernte gibt es kein Preset (`plant_apply` antwortet 409). Dort regelt der Autopilot nur die Abluft.
Alle Klimaziele sind `verified: partial`, das Licht ist `supported`.

### 2.2 Abluft

Die Abluft ist das Hauptstellglied. Sie wirkt auf Temperatur und Feuchte zugleich.

**Grenzen.** Keine Phasenkarte hat `dfan_step` (Code-Karte Punkt 6). Also ohne belegte Zahl:
- **Grundstufe** = die Stufe beim Einschalten oder nach dem letzten Handgriff. Heute 5.
- **Erlaubter Bereich:** Grundstufe plus/minus 3, nie unter 1, nie über 10. Heute also 2 bis 8.
- Abluft ganz aus (0) schaltet der Autopilot nie. Bei Licht an staut sich sonst Wärme.
Diese Grenzen sind eigene Festlegung, keine Quelle.

**Wann eine Stufe hoch:**
- Temperatur über dem Maximum (Tag- oder Nachtband) plus 0,5 °C,
- Feuchte über dem Maximum plus 2 Punkte,
- VPD unter dem Minimum minus 0,05 kPa (zu niedriger VPD heißt: Luft zu feucht).

**Wann eine Stufe runter:**
- Temperatur unter dem Minimum minus 0,5 °C.

**Bewusst kein Zug:**
- Feuchte zu niedrig: das ist Sache des Befeuchters. Die Abluft geht dafür nicht runter.
- VPD zu hoch: mehrdeutig. Mehr Abluft macht die Luft trockener (VPD steigt) und kühler (VPD sinkt). Die
  Regeltabelle von Hank sagt hier auch nichts (`ledger_rules.json:22-39`).

**Takt:**
- 30 Minuten zwischen zwei Schritten in dieselbe Richtung,
- 90 Minuten, bevor es in die Gegenrichtung geht,
- höchstens 2 Schritte pro Stunde und 8 pro Tag.

**Zurück zur Grundstufe:** sind alle Werte 90 Minuten lang sicher im Band, geht die Abluft eine Stufe Richtung
Grundstufe. Danach frühestens wieder nach 90 Minuten. Sonst bliebe sie nach jeder Spitze oben stehen.

**Am Anschlag:** ist die Grenze erreicht und der Wert 30 Minuten weiter außerhalb, meldet Hank einmal den Rat aus
der Karte, z. B. "Zu kühl: ... oder den Raum wärmer" oder "In der App die Abluft-Automatik auf höchstens 60 %
stellen". Der Autopilot geht nicht über die Grenze.

**Nebel-Sperre (neu, aus den Daten):** liegt die Feuchte am Fühler **mehr als 20 Punkte** über der Boxluft
(`climate.probe_humidity` minus `climate.humidity`), zählen Feuchte und VPD für die Abluft nicht. Die Temperatur
zählt weiter. Hank sagt einmal: "Der Fühler bekommt wohl Nebel vom Befeuchter ab. Fühler oder Befeuchter umstellen."
- Am 22./23.10. lag der Abstand im Median bei 12,3 Punkten. Außerhalb des Buckels nie über 18,4.
- Im Buckel (22.10., 09:18 bis 12:48) lag er 210 Minuten über 20, höchstens bei 37,2.
- Dass es der Befeuchter war, ist erschlossen, nicht bewiesen: es gibt keine Port-Historie. Dafür spricht auch
  der Temperatursturz am Fühler (minus 2 °C in 10 Minuten ab 09:03), wie bei Verdunstungskälte.
- Die 20 Punkte stammen aus einem Tag. Sie werden am betreuten ersten Tag nachgemessen.

### 2.3 Umluft

- Der Autopilot setzt die Voreinstellung `preset.cfan_step` **einmal beim Phasenwechsel**: Keimling 1, Blüte 10.
  Wachstum hat keine, dort passiert nichts.
- Er regelt die Umluft **nicht** nach dem Klima. Keine Quelle sagt, was eine Umluftstufe am Fühler bewirkt, und
  Hanks Regeltabelle hat keine Umluft-Züge.
- Er stellt eine Handänderung nie zurück.
- Ist "Natürlicher Wind" an, fasst er die Umluft nicht an. Heute: Stufe 10, Naturwind an, also 0 Befehle.

### 2.4 Licht

**Ziel:** `preset.light_percent` der bestätigten Phase. Nie darüber.

**Rampe nach oben**, alle Bedingungen zugleich:
1. höchstens **+25 Punkte** pro Schritt (wie `proposals.py:47-49` und `plant_apply`, `server.py:477-479`),
2. frühestens **24 Stunden** nach der letzten Lichtänderung, **egal von wem**. Heute startet ein Handschritt die
   Wartezeit nicht (`test_hank_ledger.py:414-415`). Für den Autopiloten zählt jeder Schritt.
3. nur in der Lichtphase und nur **1 bis 6 Stunden nach Licht an**. So fällt die Wirkung in den Tag.
4. die Temperatur am Fühler liegt **30 Minuten lang mindestens 1 °C unter dem Tagesmaximum**. Sonst wartet das Licht.
5. in der letzten Stunde hat der Autopilot nichts anderes geschaltet. So lässt sich jede Wirkung einem Schritt zuordnen.
6. **Lampenabstand bestätigt.** Vor der ersten automatischen Erhöhung im Durchgang bestätigt Eugen einmal mit zwei
   Tipps: "Abstand Blattkrone zur Lampe gemessen, mindestens 35 cm." Die Zahl kommt aus der Karte (early_flower,
   `preset_note`: VIVOSUN nennt 35 bis 60 cm, Quelle S7). Gespeichert mit Datum und Pflanzenhöhe. Wächst die
   eingetragene Höhe um mehr als 10 cm, fragt der Autopilot erneut. Die 10 cm sind eigene Festlegung. Die Sortenkarte
   warnt, die Pflanze könne bis 120 cm werden (`ice-cream-cake-auto.json:8, 22-23`), heute steht sie bei 61 cm.

**Nach unten:** liegt das Licht über dem Preset, geht es direkt aufs Preset (wie `proposals.py:448-451`).
Ist es zu warm, hält das Licht nur an. Eine automatische Absenkung wegen Wärme gibt es in v1 nicht.

**Heute:** Licht 50, Preset der frühen Blüte 100. Mit bestätigter Phase und bestätigtem Abstand: Tag 1 auf 75,
Tag 2 auf 100.

### 2.5 Befeuchter

Bleibt genau, wie er ist (`vgrow/automation.py`): an unter Min, aus über Max, 15 Minuten zwischen Schaltungen,
60 Minuten Pause nach Handgriff, Ausschalten bei alten Daten (Fail-Safe). Neu ist nur: seine Befehle zählen in die
Befehlsgrenze (Abschnitt 3). Der Fail-Safe wird gezählt, aber nie gesperrt.

### 2.6 Vorrang, wenn Ziele gegeneinander ziehen

Die Abluft kann nur eine Richtung. Jede Regel gibt eine Stimme mit einem Rang ab. Der beste Rang gewinnt.

| Rang | Lage | Abluft |
|---|---|---|
| 1 | Temperatur mehr als 2 °C außerhalb des Bandes (nahe an den Alarmen unter 15 °C und über 30 °C, `alerts.json`) | Temperatur entscheidet |
| 2 | Feuchte zu hoch | hoch |
| 3 | Temperatur bis 2 °C außerhalb | hoch oder runter |
| 4 | VPD zu niedrig | hoch |

Gleicher Rang in beide Richtungen: die Abluft hält, Hank meldet es einmal.

**Beispiele:**
- **Zu kühl (1 °C) und zu feucht:** Feuchte gewinnt, Abluft hoch. In der Blüte ist dauerhafte Nässe der größere
  Schaden: die Karte nennt Ertragsverlust (early_flower, `consequence_high`, S17) und Schimmel (mid_flower, S33).
  1 °C zu kühl hält die Pflanze aus.
- **Zu kühl (3 °C) und zu feucht:** Temperatur gewinnt, Abluft runter. Hank sagt: "Zu kalt und zu feucht, die Abluft
  kann nur eins. Den Raum wärmer machen." (Rat aus `advice_low`.)
- **Zu kühl und VPD zu niedrig:** Temperatur gewinnt (Rang 3 vor 4), Abluft runter. Wärmere Luft mit gleich viel
  Wasser hat weniger relative Feuchte, der VPD steigt dann von selbst.
- **Zu warm und zu trocken:** Abluft hoch für die Temperatur. Gegen die Trockenheit arbeitet der Befeuchter.
  Abluft und Befeuchter ziehen damit nie in dieselbe Richtung gegeneinander: zu feucht heißt Befeuchter aus und
  Abluft hoch, zu trocken heißt Befeuchter an und die Abluft bleibt.

Im Nachspiel kam ein Gleichstand nie vor.

---

## 3. Sicherheit

**Alte oder fehlende Daten:**
- Messwerte älter als 600 s: kein neuer Schritt, für kein Gerät (wie `STALE_SECONDS`, `automation.py:53`).
- Älter als 900 s: der Befeuchter geht aus wie heute. Licht, Abluft und Umluft bleiben, wie sie sind. Ein Befehl
  ohne Messwert wäre ein Befehl ins Blaue. Die Abluft auf ihrer letzten Stufe ist die sichere Stellung.
- Keine MQTT-Verbindung oder kein gemeldeter Gerätezustand (`step` oder `level` fehlt): nichts.

**Unbestätigte Phase:** der Autopilot wartet. Die Zeile zeigt "wartet auf Phase" mit dem Knopf aus `stageButton`
(`app.js:593-630`). **Heute steht die Phase nur als Schätzung** (`set_by: estimate`, frühe Blüte seit 22.10.).
Mit dem heutigen Stand würde der Autopilot also warten.
Unlesbare `plant.json`: ebenfalls warten.

**Befehle, die nicht ankommen:**
- Pro Gerät ist höchstens **ein Befehl unterwegs**. Grund: das Cockpit merkt sich pro Feld nur einen offenen Befehl
  (`box.py:814`). Ein zweiter würde die Ablehnung des ersten verdecken.
- Erfolg heißt: die Box **meldet** den Zielwert (Vorbild `_reports_off`, `automation.py:382-392`).
- Abgelehnt oder nach 15 s ohne Meldung: **ein** neuer Versuch bei der nächsten Abfrage.
- Wieder nichts: das Gerät pausiert 60 Minuten, Hank warnt, Push Priorität 3.
- Drei Fehlschläge an einem Tag: das Gerät geht auf Manuell, bis Eugen es zurückstellt. Push Priorität 4.
- `CommandRefused` (keine Verbindung) ist kein Sendeversuch, sperrt aber den Schritt.

**Befehlsgrenze für das Konto:**
- Der Autopilot schickt zusammen **höchstens 6 Befehle pro Stunde und 30 pro Tag**. Wiederholungen zählen mit.
- Gezählt wird an der einzigen Engstelle `Box._send` (`box.py:809-814`), nach Herkunft getrennt. Handbefehle werden
  gezählt, aber nie gesperrt. Der Fail-Safe des Befeuchters wird gezählt, aber nie gesperrt.
- Ist die Grenze erreicht, wartet der Autopilot, bis die Stunde abgelaufen ist. Push einmal.
- VIVOSUN nennt keine Obergrenze (Code-Karte Punkt 2). 6 pro Stunde ist eigene Festlegung. Im Nachspiel lag die
  schlechteste Stunde bei 2 Befehlen, also dreifacher Puffer.

**Tageszusammenfassung:** einmal am Tag, 2 Stunden nach Licht an, als Chat-Nachricht (kein Push):
"Gestern: 2 Schritte (Licht 50 auf 75, Abluft 5 auf 6), 0 Rückgängig, 0 Pausen. Im Ziel: Temperatur 100 %,
Feuchte 84 %, VPD 66 % der Zeit." (Das sind die echten Werte vom 22./23.10. gegen die frühe Blüte, Zyklus 18/6.)
Dieselben Zahlen stehen im Protokoll. Sie sind der Anfang der **Trefferquote**: ein Rückgängig
oder ein Handgriff am selben Gerät innerhalb von 2 Stunden zählt als Fehlgriff. Erst eine gemessene Quote erlaubt
die nächste Stufe der Leiter.

**Notschalter, drei Wege:**
1. Der Schalter in der Karte. Ein Tipp, sofort aus.
2. `VGROW_AUTOPILOT=0` in `~/.vgrow.env` erzwingt aus beim Start. Für den Fall, dass Eugen nur per SSH an den Mac kommt.
3. Der Autopilot schaltet sich selbst aus, wenn drei Befehle hintereinander scheitern, über alle Geräte. Push Priorität 4.

Aus heißt: keine Befehle mehr, alle Geräte bleiben, wie sie sind (wie `set_enabled`, `automation.py:183-188`).
Alarme laufen unabhängig weiter.

**Nachts (Licht aus):**
- Licht: nie.
- Abluft: regelt weiter, mit dem Nachtband (`when: night`).
- Keine Push-Nachrichten außer Fehlern. Infos warten auf die Tageszusammenfassung.

**Nach einem Neustart:**
- Zustand in eigener Datei `autopilot-state.json`, atomar geschrieben (`config.atomic_write`, `config.py:64-80`).
  Unlesbar heißt aus, wie beim Befeuchter (`automation.py:122-132`).
- Pausen, Zeitpunkt des letzten Lichtschritts und Lampenabstand überleben den Neustart. Der Befehlszähler wird aus
  `autopilot-log.jsonl` der letzten 24 Stunden neu gezählt.
- Die ersten 2 Abfragen nach dem Start schickt er nichts. Der erste Listener-Aufruf kann vor dem ersten
  Gerätezustand kommen (`box.py:455-458`).
- Die Grundstufe der Abluft wird aus dem gemeldeten Zustand neu gelesen.
- Eine Sperrdatei mit Rechnername und Prozess verhindert, dass zwei Server gleichzeitig regeln. Heute verhindert der
  Code zwei Server nicht (Code-Karte Abschnitt 9).

---

## 4. Nachspiel auf echten Messwerten

### 4.1 Was das Skript tut, und was nicht

Skript `replay.py`, Ergebnis `replay-ergebnis.txt` und `replay-ergebnis.json` (alle in diesem Ordner).

- Es spielt die Regeln Minute für Minute über die aufgezeichneten Fühler- und Boxwerte.
- **Es gibt kein physikalisches Modell der Box.** Ein simulierter Abluft-Schritt ändert an den folgenden Messwerten
  nichts (open loop). Das Nachspiel zeigt nur: wie viele Befehle, wie viele Umkehrungen, welche schlechteste Stunde.
  Ob das Klima besser geworden wäre, zeigt es nicht. Bei einer langen Abweichung läuft die Abluft im Nachspiel bis
  zum Anschlag, weil sie nie wirkt. Eine echte Abluft würde den Wert meist früher zurückholen.
- **Daten:** 1.405 Punkte im Minutentakt, 22.10. 09:03 bis 23.10. 08:27, größte Lücke 60 s.
  `history-168h.json` ist **byte-gleich** mit `history-24h.json` (sha256 05ca78e02009...). Das ist **ein Tag, keine
  Woche**. Die Cloud liefert im Minutentakt offenbar nur einen Tag. Ob sie mehr kann, weiß ich noch nicht.
- **Annahmen:**
  - Licht an um 07:28, erschlossen aus dem Temperaturanstieg am 23.10. (Fühler plus 1,4 °C in 10 Minuten).
    Einen Licht-aus-Sprung zeigen die Daten nicht. Gerechnet mit 18/6 und 20/4 (Karte, `notes[2]`, S6).
  - Startwerte aus `state.json`: Licht 50, Abluft 5, Umluft 10 mit Naturwind an, Befeuchter aus.
  - Phase gilt als bestätigt (in Wirklichkeit nur geschätzt). Lampenabstand gilt als bestätigt, plus ein Lauf ohne.
  - Gerechnet für alle fünf Phasen mit Klimazielen. Dieselben echten Daten gegen strengere Ziele zeigen, wie die
    Regeln bei einer langen Abweichung reagieren (späte Blüte: Fühlerfeuchte den ganzen Tag über 50).

### 4.2 Drei Parametersätze

| | vorher (erster Wurf) | zeit (nur Takt) | nachher (Entwurf) |
|---|---|---|---|
| Glättung | Rohwert | Median 5 | Median 5 |
| Dauer vor dem Schritt | sofort | 10 min | 10 min |
| Hysterese-Marge | keine | 0,5 °C / 2 Punkte / 0,05 kPa | gleich |
| Abstand gleiche Richtung | 5 min | 30 min | 30 min |
| Abstand Gegenrichtung | 5 min | 90 min | 90 min |
| Zurück zur Grundstufe nach | 5 min im Band | 90 min | 90 min |
| Abluft pro Stunde / Tag | unbegrenzt | 2 / 8 | 2 / 8 |
| alle Geräte pro Stunde / Tag | unbegrenzt | 6 / 30 | 6 / 30 |
| Nebel-Sperre | nein | nein | ja, 20 Punkte |
| Licht wartet nach anderem Schritt | nein | nein | 60 min |

### 4.3 Ergebnis für heute (frühe Blüte, Zyklus 18/6)

| | Befehle (Abluft / Licht) | pro 24 h | Umkehr unter 1 h (Abluft) | schlechteste Stunde | Minuten am Anschlag |
|---|---|---|---|---|---|
| vorher | 19 (18 / 1) | 19,5 | 6 | 6 | 454 |
| zeit | 8 (7 / 1) | 8,2 | 0 | 3 | 133 |
| **nachher** | **2 (1 / 1)** | **2,0** | **0** | **1** | **0** |
| nachher, Abstand nicht bestätigt | 1 (1 / 0) | 1,0 | 0 | 1 | 0 |

Zyklus 20/4 ergibt für die frühe Blüte dieselben Zahlen.

**Zeitleiste "vorher"** (Auszug): Abluft 5 auf 6 um 09:03 (ein einzelner Messwert 63,8 %), 09:10 zurück auf 5,
09:17 wieder auf 6. Nachts dasselbe am VPD: 02:49 hoch, 02:55 runter, 03:03 hoch. Das ist das Hin und Her, das die
Regeln nicht haben dürfen.

**Zeitleiste "nachher":**
- 22.10. 09:33: Licht 50 auf 75 (Rampe zum Preset 100).
- 23.10. 06:47: Abluft 5 auf 6, VPD am Fühler 0,94 kPa, Ziel mindestens 1,0. Das fällt bei 18/6 in die Dunkelphase.

Während des Feuchte-Buckels (09:18 bis 12:48) schickt "nachher" keinen Abluft-Befehl, die Nebel-Sperre hält.

### 4.4 Alle Phasen, Satz "nachher"

| Phase | Zyklus | Abluft | Befeuchter | Licht | Summe pro 24 h | Umkehr unter 1 h (Abluft / Befeuchter) | schlechteste Stunde | Minuten am Anschlag |
|---|---|---|---|---|---|---|---|---|
| seedling | 18/6 | 0 | 3 | 1 | 4,1 | 0 / 1 | 2 | 0 |
| veg | 18/6 | 3 | 3 | 1 | 7,2 | 0 / 1 | 2 | 0 |
| veg | 20/4 | 5 | 3 | 1 | 9,2 | 0 / 1 | 2 | 71 |
| early_flower | beide | 1 | 0 | 1 | 2,0 | 0 / 0 | 1 | 0 |
| mid_flower | beide | 3 | 0 | 1 | 4,1 | 0 / 0 | 2 | 476 |
| late_flower | beide | 3 | 0 | 1 | 4,1 | 0 / 0 | 2 | 1.126 |

Umluft: in allen Läufen 0 Befehle (Naturwind an). Die Befehlsgrenze von 6 pro Stunde wurde nie erreicht.
Höchstens 9,2 Befehle am Tag, höchstens 2 in einer Stunde.

**Was das heißt:**
- **Die Abluft kehrt in keinem Lauf innerhalb einer Stunde um.** Ziel erreicht.
- **Der Befeuchter schaltet bei Keimling und Wachstum einmal innerhalb einer Stunde an und wieder aus**
  (Keimling: an 09:03 bei 63,8 %, aus 09:35 bei 76,3 %). Das ist seine bestehende Regel, die nicht geändert wird.
  Im echten Lauf ist das vermutlich genau der Buckel vom 22.10.: Befeuchter an, Nebel am Fühler, Fühler über Max,
  Befeuchter aus. Siehe Frage 3.
- **Späte Blüte: 1.126 Minuten am Anschlag.** Die Fühlerfeuchte lag den ganzen Tag bei 55 bis 59 %, das Ziel ist
  höchstens 50. Im Nachspiel wirkt die Abluft nie, also steht sie ab 13:31 auf 8 und meldet "am Anschlag". In der
  echten Box ist offen, ob Stufe 8 die Feuchte auf 50 bringt. Genau dafür gibt es die Anschlag-Meldung.

### 4.5 Was am Takt geändert wurde, und warum

1. **Median und 10 Minuten Dauer:** der Schritt um 09:03 hing an einem einzigen Messwert. Weg.
2. **Hysterese-Margen:** die Feuchte stand nach dem Buckel und am Morgen um 60 bis 60,5 %, genau an der Grenze.
   Ohne Marge reicht ein Zittern für einen Schritt. Weg.
3. **30 und 90 Minuten Abstand, zurück erst nach 90 Minuten im Band:** das Hoch-runter-hoch von 09:03/09:10/09:17
   und 02:49/02:55/03:03 kann nicht mehr entstehen. Umkehrungen in der frühen Blüte: 6 auf 0.
4. **Nebel-Sperre:** die übrigen drei Abluft-Schritte im Buckel (09:29, 09:59, 10:29 im Satz "zeit") regelten gegen
   Nebel am Fühler. Befehle in der frühen Blüte: 8 auf 2.
5. **Licht wartet eine Stunde nach jedem anderen Schritt:** damit jede Wirkung einem Schritt zuzuordnen ist.

### 4.6 Grenzen des Nachspiels

- Ein Tag. Eine Woche gibt die Cloud im Minutentakt nicht her (Abschnitt 5, Messung 9).
- Kein physikalisches Modell: die Zahl der Schritte bei einer echten Abweichung ist offen.
- Keine Historie von Licht, Lüftern und Ports. Startwerte aus einem einzigen Schnappschuss.
- Lichtzyklus erschlossen, nicht gemessen.

---

## 5. Bauplan

Reihenfolge so, dass jedes Paket allein grün ist und nichts schaltet, bevor die Sperren stehen.

| Paket | Inhalt | Dateien | Tests | Abnahme |
|---|---|---|---|---|
| AP1 Befehlsbudget | Zähler nach Herkunft in `_send`, Grenze 6/h und 30/Tag nur für Herkunft Autopilot, ein Befehl unterwegs je Feld | `vgrow/box.py`, `vgrow/demo.py` | neu `tests/test_command_budget.py`: 7. Autopilot-Befehl in der Stunde abgewiesen, Fail-Safe und Hand gehen durch und werden gezählt | alle bisherigen Tests grün (Stand 23.10.: 994) |
| AP2 Sperren im Server | Plan-Sperre fürs Licht im Server; gemeinsame Konstanten (`LIGHT_STEP`, 24 h) in ein neutrales Modul | neu `vgrow/limits.py`, `vgrow/server.py`, `hank/proposals.py` (nur Import) | `test_safety.py` erweitert: Licht im Plan abgewiesen | Hank-Tests unverändert grün, auch `test_hank_core.py:745-771` |
| AP3 Regelwerk | reine Funktion `decide(snap, stage, state, now)` ohne Ein- und Ausgabe; Klasse `Autopilot` mit `check(snap)` als dritter Schritt der Poll-Kette | neu `vgrow/autopilot.py`, `vgrow/server.py:115-131` | neu `tests/test_autopilot.py`: jede Regel einzeln, Vorrangtabelle, Nebel-Sperre, Nacht, Licht nie aus 0, nie bei `dfan.mode != 0` | Standard aus nach Installation und bei unlesbarer Datei |
| AP4 Simulationstest | aufgezeichnete Daten vom 22./23.10. als Fixture, Fake-Box, die Befehle annimmt und nach einer Abfrage zurückmeldet | neu `tests/fixtures/history-2026-10-22.json`, `tests/test_autopilot_replay.py` | frühe Blüte höchstens 4 Befehle pro 24 h, 0 Umkehrungen unter 1 h, schlechteste Stunde höchstens 2; eingebaute Lücke von 20 Minuten: 0 Befehle darin; Buckel: 0 Abluft-Befehle; Abstand nicht bestätigt: 0 Licht; Neustart mitten im Lauf: Pausen bleiben | läuft in der normalen Testsuite |
| AP5 Hand erkennen | Haken in `command()` für light, cfan, dfan, port, plant_apply; Fremdänderung aus dem gemeldeten Zustand | `vgrow/server.py:415-522`, `vgrow/autopilot.py` | Handbefehl pausiert genau ein Gerät 60 min; Änderung aus der App ebenso; nach der Pause neue Grundstufe | kein Zurückstellen auf alte Werte, per Test festgenagelt |
| AP6 Hank berichtet | neue Herkunft `autopilot` im Journal; Import des Autopilot-Protokolls; feste Texte; Rückgängig-Knopf | `hank/stores.py:47, 518-543`, `hank/texts_live.py`, `hank/livestate.py`, `wiring.py`, `web/hank.js` | Validator ersetzt weiter jede Schaltbehauptung; keine Schaltnamen im Hank-Paket | Hank aus lässt den Autopiloten laufen |
| AP7 Karte | Schalter, eingeklappte Ausnahmen, Lampenabstand bestätigen, Protokoll für alle Geräte, Ampelgründe | `web/index.html:260-275`, `web/app.js:1378-1566`, `web/style.css:403-448`, `web/i18n.js`, `hank/nudges.py` | `test_web_i18n.py`: Deutsch und Englisch, kein Gedankenstrich; `scripts/preview_mock.js` | Edge-Screenshot ohne Abschneiden vor jeder Freigabe; Anker `#autopilot` bleibt |
| AP8 Zusammenfassung, Notschalter | Tagesbericht, Trefferquote, `VGROW_AUTOPILOT=0`, Selbstabschaltung, Sperrdatei | `vgrow/autopilot.py`, `vgrow/config.py` | Umgebungsvariable erzwingt aus; zweiter Server regelt nicht | Bericht zeigt Schritte, Rückgängig und Zeit im Ziel |

**Betreuter erster Tag auf der echten Box.** Eugen ist dabei. Tag 1 nur die Abluft auf Automatik, Licht auf
Vorschlag, Umluft auf Manuell. Gemessen und ins Protokoll:

1. **Meldet das Licht im Dunkeln `level: 0`?** Davon hängt Tag und Nacht ab. Heute steht `mode: 0`, `in_plan: false`,
   `plan: null` bei Level 50, wo der Zyklus herkommt, ist offen.
2. **Wirkzeit der Abluft:** wie viele Minuten nach einem Schritt ändern sich Temperatur und Feuchte am Fühler, und
   um wie viel pro Stufe. Davon hängen die 30 Minuten ab.
3. Bleibt `dfan.mode` nach einem Befehl auf 0, und ändert sich sonst etwas?
4. Zeit bis zur Bestätigung je Befehl, Zahl der Ablehnungen.
5. Bewegt sich das Alter des Gerätezustands (`shadow_age`, heute 1.469 s) nach einem Befehl?
6. **Abstand Fühler zu Box-Feuchte** mit Befeuchter an und aus. Prüft die 20 Punkte der Nebel-Sperre.
7. Meldet die Cloud nach vielen Befehlen Fehler oder Warnungen? Zählen.
8. Lichtschritt 50 auf 75: Temperaturanstieg am Fühler über 2 Stunden, Lampenabstand vorher gemessen.
9. Eine echte Woche Historie holen (Seitenweise oder gröberer Takt als `ONE_MINUTE`) und das Nachspiel wiederholen.

---

## 6. Offene Fragen an Eugen

Nur Entscheidungen, die bei dir liegen.

1. **Name und Stimme.** Vorschlag: der Schalter heißt "Autopilot", Hank meldet nur ("Autopilot: Abluft 5 auf 6").
   Oder soll Hank in der Ich-Form schalten? Dann müssen `hank.hint`, `no_self` und der Validator geändert werden.
   **Entschieden 23.10.2026:** der Schalter heißt "Hank regelt" (EN "Hank controls") und regelt den Autopiloten
   unter Hanks Namen. Der flache Satz "Ich schalte nichts selbst" ist damit nicht mehr wahr: `no_self` sagt jetzt
   "Im Chat schalte ich nichts ohne dich", `hank.hint` sagt "Das Sprachmodell schaltet nie", und der Prüfer
   streicht den flachen Satz, wenn das Modell ihn schreibt (Grund `flat_rule`).
2. **Abluft: Handstufen oder VIVOSUNs eigene Abluft-Automatik?** Dieser Entwurf stellt Stufen und lässt die Abluft
   in Ruhe, sobald VIVOSUNs Automatik läuft. Die Alternative: deren Schwellen je Phase schreiben. Weniger Befehle,
   und die Box regelt bei Cloud-Ausfall weiter. Aber der Schreibweg ist nie beobachtet, die Skalen sind unbelegt.
3. **Fühler im Nebel.** Fühler oder Befeuchter umstellen? Und soll der Befeuchter selbst auch die Nebel-Sperre
   bekommen? Heute schaltet er aus, sobald der Nebel den Fühler trifft, vermutlich lange bevor die Boxluft feucht
   genug ist. Das ändert eine bestehende Regel.
4. **Pause nach einem Handgriff:** 60 Minuten wie beim Befeuchter, oder bis zum nächsten Licht an?
5. **Befehlsgrenze 6 pro Stunde und 30 pro Tag:** VIVOSUN nennt keine Zahl. Das Risiko ist eine Kontosperre.
   Reicht dir dieser Puffer, oder lieber enger?
6. **Phase:** heute ist die frühe Blüte nur geschätzt, der Autopilot würde warten. Darf eine KI-geschätzte Phase den
   Autopiloten je freigeben? Vorschlag: nein, bis Stufe 2 der Leiter (Einspruchsfrist) gemessen ist.
