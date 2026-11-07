# Grow plan: one plant, its stage and its height, and who does what today

[Deutsch](#deutsch) · [English](#english)

Code: [`vgrow/grow_plan.py`](../vgrow/grow_plan.py), [`vgrow/ruler.py`](../vgrow/ruler.py), card: [`web/plan.js`](../web/plan.js).
The run itself comes from the owner's choice of a plant: [`vgrow/plant_run.py`](../vgrow/plant_run.py).

## Deutsch

### Wofür

Licht, Abluft, Umluft, Befeuchter, E6, Gießplan und Bodensensor liefen jeder für sich und lasen die Phase aus `plant.json`. Der Plan
verbindet sie mit der Pflanze: mit ihrer Phase und mit ihrer Höhe. Er steht als Karte ganz oben auf der Seite Automatik.

### Der Durchgang

Welche Pflanze gilt, entscheidet der Betreiber über `run_activate` (`vgrow/plant_run.py`, `STATE_DIR/plant-run.json`). Eine neue Pflanze
startet einen neuen Plan. Zwei Dinge legt er für den Durchgang auf der Karte unter "Einstellen" fest: das Lichtziel in Prozent (Zustand des
Plans, `boxes/box-1/grow-plan.json`) und den ersten Tag mit Dünger (`feed_from` im Pflanzen-Log; `feeding.setup_of` reicht ihn als
`not_before` weiter, davor gilt nur Wasser).

### Licht

Der Lichttimer besitzt die Helligkeit. Der Plan sagt ihm den Wert des Tages (`GrowPlan.light_stage` im `requirements_source` des Timers).
Einmal je Dunkelphase entscheidet er über die nächste Stufe (+10 Punkte), sie gilt ab dem nächsten Einschalten. Er hält an, wenn

- in der letzten Hellphase 9 von 10 Werten über Bandende minus 0,3 °C lagen, eine Stufe braucht Platz (60 auf 40 % bewegten den Fühler um rund 0,45 °C),
- der mittlere VPD über Bandende plus 0,3 kPa lag (eigene Wahl, keine Quelle),
- weniger als 35 cm bis zur Lampe bleiben (92 cm Wuchshöhe minus Höhe) oder keine Höhe bekannt ist,
- zu wenig Messwerte da sind,
- der Betreiber selbst einen Wert gesetzt hat. Der gilt dann auch nach dem nächsten Schalttermin, bis er "Weiter hochfahren" tippt.

Macht die eigene Stufe es 20 Minuten lang um mehr als 1,5 °C zu warm, geht sie sofort zurück, neuer Versuch nach 48 Stunden.

So viel Licht, wie das Klima hält (Betreiber, 06.11.2026): Bleibt der Fühler über dem Temperaturband oder 0,3 kPa über dem VPD-Band, geht
das Licht in der laufenden Hellphase 10 Punkte runter. Erst Lüfter und Befeuchter: bei Abluft ab Stufe 8
wartet die Regel 20 Minuten, darunter eine Stunde, weil der Autopilot die Abluft dann noch hochziehen kann. Zwischen zwei Schritten
45 Minuten, so lange braucht eine Stufe am Fühler (40 auf 80 % stieg nach 45 Minuten noch). Nie unter 40 %, dort kam die Temperatur am 06.11.2026
mit allen Lüftern oben ins Band (eigene Wahl, für diese Lampe gibt es keine Quelle), nie über den Wert des Betreibers hinweg. Neuer Versuch nach oben nach 48 Stunden.

Warum so: 23 Abluftschritte aus dem Speicher (23.10. bis 05.11.2026) bewegten die Temperatur um rund 0,4 °C je Stufe, den VPD aber nur um
0,03 kPa. Der Befeuchter (30 bis 50 ml/h) bringt, wenn er läuft, rund 5 Punkte und 0,2 kPa. Das Licht ist der stärkste Hebel: 0 auf 100 %
sind 7 bis 10 °C und 0,9 bis 1,9 kPa.

Aus bleibt Aus: der Plan schaltet den Timer nie ein. Ohne Ziel in der Datei gilt die Voreinstellung der Phasenkarte wie bisher. Der Autopilot lässt
das Licht in Ruhe, solange der Timer läuft (`ap.hold.light_schedule`), und fragt dann auch nicht nach dem Lampenabstand.

Mehr Licht gibt es auf zwei Wegen: mehr Stunden (im Wachstum 18 bis 24 laut Grow Weed Easy, der Plan erlaubt 20) und mehr Helligkeit. Die
Stunden heben die Spitzentemperatur nicht, darum sind sie der erste Hebel, wenn die Box an die Wärmegrenze stößt.

### Höhe

Die GrowCam zeigt den Messstreifen an der hinteren Schiene. `ruler.py` findet die grünen Felder und nimmt ihre Kanten als Maßstab
(Trellis endet bei 20 cm, Topping 20 bis 25, Flowering 25 bis 30, am Bild vom 06.11.2026 abgelesen). Die Pflanze ist die größte Gruppe
Blattgrün, die bis zum Topf reicht, die Spitze ihre oberste Zeile. Streifengrün ist rein (Rot etwa gleich Blau), Blätter sind gelblich
(Blau unter Rot), so bleibt auch eine Spitze vor dem Streifen lesbar. Kein Modell, keine Bildbibliothek: ffmpeg und Python.

Alle drei Stunden bei Licht ein Bild. Der Tageswert ist der Median, er geht ins Pflanzen-Log (`method: camera_strip`) und auf den Eintrag
der Pflanze. Ein Sprung über 3 cm plus 6 cm je Tag oder ein Abfall über 6 cm zählt erst, wenn die nächste Lesung ihn bestätigt.
Ein Formular braucht diese Pflanze nicht mehr: es kommt erst zurück, wenn die Kamera über zwei Tage nichts gelesen hat. Stimmt die Linie
nicht, gibt es unter dem Bild ein Feld ("Linie falsch?"): der Wert gewinnt an seinem Tag und setzt den Versatz für spätere Lesungen. Die
Pflanze steht vor dem Streifen, der Wert ist deshalb ein Schätzwert (≈). Sieht die Kamera den Streifen nicht, sagt die Kachel das, nach einem Tag kommt eine Nachricht.

### Markierungen

Der Streifen druckt, was bei welcher Höhe dran ist. Jede Markierung meldet sich einmal: Netz (ab 15 cm, der Streifen sagt bis 20),
Toppen (20 bis 25, nur Photo-Sorten), Blüte (25 bis 30, nur Photo-Sorten), Lollipopping (ab 40). Umstellen auf Blüte bleibt ein Tipp des
Betreibers, zwei Tipps auf der Karte. Danach folgt alles der Phasenkarte: Licht 12/12 mit derselben Einschaltzeit, Spektrum, Klima, Düngerwoche.
Das Feld passt zur Faustregel: umstellen bei der halben Endhöhe, die Pflanze verdoppelt sich etwa (S43). 92 cm minus 35 cm zur Lampe sind
57 cm, die Hälfte 28 cm.

### Gießwache

Entscheiden tut weiter die Bedarfsprüfung (`thirst.gate`). Der Plan richtet sie ein und schaut hinterher nach:

- zwei Prüfungen am Tag aus dem Lichtplan: eine Stunde nach Licht an (S1) und bis zu 13 Stunden danach, spätestens drei Stunden vor Licht
  aus. Ändert sich der Lichtplan, ziehen sie mit. Ändert der Betreiber den Gießplan von Hand, gehört er ihm.
- die Laufzeit aus der Obergrenze der Phase und dem gemessenen Durchfluss; das Gate kürzt auf die Gabe.
- eine Stunde nach jedem Lauf ab 30 Sekunden der Blick auf den Bodensensor: stieg er um mindestens 2 Punkte, kam das Wasser an, sonst
  eine Nachricht. USB-Strom ist kein Wasserfluss.
- der Messbecher-Lauf (`Thirst.jug_run`) wird gesehen, zählt aber nie als Wasser im Topf.

Ein voller Tank ist nie ein Grund zu gießen, das bleibt im Gate. Der Plan startet die Pumpe nur auf den Tipp des Betreibers (60 Sekunden
Messbecher, 1 Liter).

### Was nur Hände können

Die Karte zeigt immer nur den nächsten offenen Schritt ganz: Fühler auf Pflanzenhöhe, Pumpe messen, einmal durchgießen und Sensor
einstellen, Gießwache einschalten. Jeder Schritt liest seinen Stand aus der Box. Die Übersicht nennt nur den nächsten.

## English

### What it is for

Light, exhaust, circulation, humidifier, E6, watering schedule and soil sensor each ran on their own and read the stage from `plant.json`.
The plan connects them to the plant: to its stage and to its height. It is the card at the top of the Automation page.

### The run

Which plant counts is the owner's choice through `run_activate` (`vgrow/plant_run.py`, `STATE_DIR/plant-run.json`). A new plant starts a new
plan. Two things he sets for the run on the card under "Settings": the light goal in percent (the plan's state,
`boxes/box-1/grow-plan.json`) and the first day with fertilizer (`feed_from` in the plant log; `feeding.setup_of` hands it on as
`not_before`, plain water before it).

### Light

The light timer owns the brightness. The plan tells it the level of the day (`GrowPlan.light_stage` in the timer's `requirements_source`).
Once per dark phase it decides the next step (+10 points), which applies from the next lights on. It holds when

- 9 of 10 readings of the last light period were above the band's end minus 0.3 °C, a step needs room (60 to 40 % moved the probe by about 0.45 °C),
- the median VPD was above the band's end plus 0.3 kPa (own choice, no source),
- less than 35 cm remain to the lamp (92 cm growing height minus height) or no height is known,
- there are too few readings,
- the owner set a level himself. It then also holds after the next switch, until he taps "Keep going up".

If its own step makes it more than 1.5 °C too warm for 20 minutes, it goes back at once, next try after 48 hours.

As much light as the climate holds (owner, Nov 6, 2026): if the probe stays over the temperature band or 0.3 kPa over the VPD band, the light
comes down 10 points within the running light period. Fans and humidifier first: with the exhaust at step 8 or more the
rule waits 20 minutes, below that an hour, because the autopilot can still raise the exhaust. 45 minutes between two steps, that is how long
a step takes to show at the probe (40 to 80 % was still climbing after 45 minutes). Never below 40 %, where the temperature came into the band on
Nov 6, 2026 with every fan up (own choice, no source for this lamp), never over the owner's own level. Next try upward after 48 hours.

Why this way: 23 exhaust steps from the store (Oct 23 to Nov 5, 2026) moved the temperature by about 0.4 °C per step, the VPD by only 0.03 kPa.
The humidifier (30 to 50 ml/h) gives about 5 points and 0.2 kPa when it runs. The light is the strongest lever: 0 to 100 % is 7 to 10 °C and
0.9 to 1.9 kPa.

Off stays off: the plan never switches the timer on. Without a goal in the file the stage card's preset counts as before. The autopilot leaves the light alone
while the timer runs (`ap.hold.light_schedule`) and then does not ask for the lamp distance either.

There are two ways to more light: more hours (18 to 24 in veg per Grow Weed Easy, the plan allows 20) and more brightness. Hours do not raise
the peak temperature, so they are the first lever when the box hits its heat limit.

### Height

The GrowCam shows the measuring strip on the rear rail. `ruler.py` finds the green fields and takes their edges as the scale (Trellis ends
at 20 cm, Topping 20 to 25, Flowering 25 to 30, read off the picture of Nov 6, 2026). The plant is the biggest group of leaf green that
reaches down to the pot, its tip that group's top row. Strip green is pure (red about equal to blue), leaves lean to yellow (blue below
red), so a tip in front of the strip stays readable. No model, no image library: ffmpeg and Python.

A picture every three hours with the lamp on. The day's value is the median; it goes into the plant log (`method: camera_strip`) and onto
the plant's record. A jump of more than 3 cm plus 6 cm per day, or a drop of more than 6 cm, counts only when the next reading confirms it.
This plant needs no form any more: it comes back only when the camera has read nothing for over two days. If the line is off, a field under
the picture ("Line off?") takes the real height: it wins on its day and sets the offset for later readings. The plant stands in front of
the strip, so the value is an estimate (≈). If the camera does not see the strip the tile says so, and after a day a message goes out.

### Marks

The strip prints what is due at which height. Each mark speaks up once: net (from 15 cm, the strip says up to 20), topping (20 to 25, photo
strains only), flower (25 to 30, photo strains only), lollipopping (from 40). Switching to flower stays the owner's tap, two taps on the
card. After that everything follows the stage card: light 12/12 with the same on time, spectrum, climate, feeding week. The field fits the
rule of thumb: switch at half the final height, the plant about doubles (S43). 92 cm minus 35 cm to the lamp are 57 cm, half of that 28 cm.

### Water watch

The need check (`thirst.gate`) still decides. The plan sets it up and looks afterwards:

- two checks a day from the light plan: an hour after lights on (S1) and up to 13 hours later, at the latest three hours before lights off.
  When the light plan changes they follow. When the owner changes the watering schedule by hand it is his.
- the run time from the stage's cap and the measured flow; the gate shortens it to the dose.
- an hour after every run of 30 seconds or more a look at the soil sensor: a rise of at least 2 points means the water arrived, otherwise
  a message. USB power is no water flow.
- the measuring jug run (`Thirst.jug_run`) is seen but never counts as water in the pot.

A full tank is never a reason to water; that stays in the gate. The plan starts the pump only on the owner's tap (60 seconds into the jug,
1 litre).

### What only hands can do

The card shows only the next open step in full: probe at plant height, measure the pump, water through once and set the sensor, switch on
the water watch. Each step reads its state from the box. The overview names only the next one.
