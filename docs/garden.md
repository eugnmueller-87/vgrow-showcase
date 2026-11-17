# Hanks Garten / Hank's garden

## Deutsch

Der Startbildschirm des Cockpits (Betreiber, 06.11.2026). Nach einer Minute ohne Eingabe legt sich eine kleine Pixelwelt über die Seite und zeigt, was
Hank gerade tut. Von Hand geht sie über den Knopf "Hanks Garten" in der oberen Leiste auf, jede Seite hat ihn, oder in der Seitenleiste unter "Quellen". Von allein
springt sie nicht auf, solange jemand tippt, im Vollbild schaut, Hanks Fenster, das Wiki oder einen Dialog offen hat.

**Sie bleibt, bis man sie schließt:** Maus, Klick und Tasten schließen sie nicht, nur der Knopf "Zum Cockpit" und Esc. Mit "Offen lassen" öffnet die Seite auf diesem Gerät
gleich im Garten, auch wenn sie sich für eine neue Version neu lädt. Die beiden Knöpfe liegen über dem Bild: Sprechblase und Name unter der Maus weichen ihnen aus, zur Seite und wo dort kein Platz ist darunter.

**Spielen:** ein Klick auf den Boden schickt Hank dorthin, ein Klick auf ein Ding schickt ihn hin, und er sagt, wofür es steht: Thermometer die Werte am Fühler, Pflanzen Höhe
und Boden, Windrad die Lüfterstufen, Brunnen den Befeuchter, Lampe oder Sonne das Licht, das Brett die Zettel. Kühlschrank, Sessel, Beistelltisch und PC sind seine Freizeit. Die
Gießkanne an der Tonne ist Show und sagt das auch. Das alles schaltet nichts. Die Box geht vor: ein neuer Alarm oder die Pumpe holt ihn sofort weg, und läuft ein Alarm schon, wenn
du ihn wegschickst, kommt er danach zu ihm zurück. Die Welt läuft mit 65 % der echten Zeit, das Windrad nochmal deutlich langsamer.

Kein Spiel-Baukasten, keine Bilddateien, nichts zu laden: `web/garden-art.js` zeichnet alles aus Code, `web/garden-logic.js` entscheidet, `web/garden.js`
spielt ab. Gerechnet wird im Browser des Betrachters, der Mac merkt davon nichts. Der Garten schaltet nichts und schreibt nichts: er liest `/api/state` und
`/api/hank/state`.

**Was Hank tut, kommt von der Box, in dieser Reihenfolge:**

| Die Box meldet | Hank | Sprechblase |
| --- | --- | --- |
| Offline, Fehler oder Messwerte älter als 15 min | kratzt sich am Kopf | "Die Box antwortet nicht." |
| Tank-Sensor trocken | schaut in die Tonne | "Wir brauchen Wasser! Der Tank ist leer, die Pumpe kann nicht pumpen." |
| Pumpe läuft | holt Wasser an der Tonne, gießt | "Die Pumpe läuft, ich gieße. Boden bei ... %." |
| Alarm zur Luft (zu warm, zu kalt, zu feucht) | Hut, Schal, Schirm | der Wert |
| Durst-Index: gießen fällig, Fingerprobe, zu lange nass | kniet am Beet | was gebraucht wird; ohne gemessene Pumpe auch das |
| Befeuchter fast leer | am Brunnen | "Der Befeuchter ist fast leer." |
| Klima außer Band | Hut, Schal, Sprühflasche, Schirm, mit Pausen | der Wert |
| Nichts davon | Freizeit | Bier, Saft, Zigarette, PC, Blätter ansehen, nachts auch Nickerchen |

Das Licht unter dem Kartenwert ist keine Arbeit: der Plan hält es dort absichtlich. Offene Schritte des Plans und alle anderen Alarme (Kameras, Lichtregeln) hängen als Zettel am
Brett, alle gut sieben Minuten geht er hin und nennt den nächsten. Das ist Absicht: "Nahkamera offline" ließ ihn sonst tagelang stehen, und der Adapter war noch in der Post. Auch die
Zeile unter seinem Tun nennt so einen Alarm nicht, dort steht, worum es bei der Arbeit geht. Überhaupt hält ihn nichts für immer an einem Fleck: nach knapp vier Minuten macht er auch bei einem Alarm Pause und sagt dabei weiter, was los ist. Nur solange die Pumpe läuft, gießt er durch. Ein Wert
genau auf der Bandkante lässt ihn nicht hin und her rennen: ein Wechsel ohne Alarm muss gut eine Minute stehen.

**Auf Automatik macht er es selbst:** regelt der Autopilot die Abluft, steht unter "Fächelt sich Luft zu" und den anderen Klimaarbeiten nie der Rat der Karte ("Zu warm: Abluft
hoch"), sondern was der Autopilot tut: sein Abluftschritt der letzten halben Stunde, sonst worauf die Abluft wartet ("beobachtet: die Temperatur liegt bei 28,1 °C, über 28,0 °C.
Eine Stufe hoch, wenn der Wert 10 min über 28,5 °C bleibt"). Gegen trockene Luft steht der laufende Befeuchter davor. Der Betreiber am 07.11.2026: "it's on auto, he should
do everything himself, not telling me to do it". Dieselbe Regel gilt im Cockpit: die Warnkarte zeigt dann den Messwert und darunter, was getan wird, die Pflanzenkarte führt
es nicht als Aufgabe, und Hank sagt bei "Beheben" und "Was ist heute zu tun" statt des Rats, was der Autopilot tut. Steht die Abluft auf Manuell, Beobachten oder Pause, ist der Rat wieder seiner. Braucht die Zeile
eine Reihe mehr, als neben dem Hinweis "Klick auf etwas" Platz hat, nimmt sie dessen Platz, statt ihr Ende zu verlieren.

**Was die Welt zeigt, sind Messwerte:** Lampe aus ist Nacht, die Sonne steht so hoch wie die Lampe hell ist; Windrad nach Abluftstufe, Blätter nach Umluftstufe;
Nebel am Brunnen, wenn der Befeuchter läuft; Wasser im Schlauch, wenn die Pumpe läuft; Tonne leer, wenn der Tank-Sensor trocken ist; Erde dunkler, je feuchter;
die Pflanzen so groß wie die Kamera misst, in der Blüte mit Knospen, Purple Punch dann violett. Nur die mittlere Pflanze ist die echte, die beiden daneben sind
Kulisse und wachsen mit.

**Sein Helfer, der kleine Roboter** (Betreiber, 15.11.2026): gebaut aus Teilen der Box, die Beine zwei Bodensensor-Spieße, der Körper das Gateway-Kästchen, der Kopf
der Klimafühler, obendrauf ein Spross mit Signallampe. Was er meldet, liest er an einer Stelle (`GardenLogic.buddy`): jede aktive Zeile der Alarmliste des Servers, auch die
Aufsichtsregeln für still gewordene Signale (gelb oder rot, ohne Stufe zählt rot), und ob die Seite den Server noch erreicht: `vgrow:offline` und 10 s kein neuer Stand, oder
30 s gar keiner (nach einem Ruhezustand des Rechners gezählt ab dem Aufwachen). Eine gelbe Zeile, im Cockpit nur ein Hinweis, zählt für ihn wie eine rote; ob sie leiser
sein soll, entscheidet der Betreiber. Bei einem neuen Problem rennt er zu Hank, schneller als der geht, die Lampe rot, und sagt es in seiner eigenen Sprechblase ("Hank! Zu warm (Fühler)!"). Hank
dreht sich zu ihm und antwortet kurz. Solange es bleibt, bleibt die Lampe rot, und er sagt es erst nach gut zwölf Minuten wieder; ein weiteres neues Problem ist ein neuer Weg.
Was auf einmal neu ist, sagt er auf demselben Weg der Reihe nach, alles; was vorbei ist, bevor er dazu kommt, lässt er weg. Seine Sprechblase trägt oben einen Streifen in
der Farbe seiner Lampe, damit sie auch über Hanks Blase als seine erkennbar ist, und seine Zeilen brechen dort um, wo ein Satz endet.
Ist alles wieder gut, sagt er das einmal, und die Lampe wird wieder gelb. Sonst ist er bei Hank: bei der Arbeit steht er daneben und schaut mit der Lupe, in der Freizeit hängt
er mit ihm ab. Bei Hanks Bier- und Rauchpausen, und solange Hank so etwas in der Hand hat, geht er zu den Pflanzen. **Seine Ladestation** (Betreiber, 16.11.2026): vorne
auf der freien Wiese steht ein Ladepfosten mit Bildschirm und Lampe, davor eine Ladeplatte. Ist alles ruhig und Hank macht ein Nickerchen, geht er dorthin, stellt sich auf die
Platte, das Kabel steckt im Rücken, die Augen zu, die Lampe auf dem Kopf grün, und über seinem Kopf füllt sich eine Batterie (leer bis voll in 90 Weltsekunden, Hanks Nickerchen
dauert 80). Voll steht er wach auf der Platte. Unterwegs läuft die Batterie langsam leer, beim Rennen schneller, nie unter 15 %. Lampe aus: er schläft auf der Ladeplatte, die
Batterie über sich; ein Problem weckt ihn sofort. Ein Problem, das bleibt, hält ihn auch während Hanks Nickerchen bei Hank. Ab und zu erzählt er einen Witz, nur wenn alles ruhig ist und Hanks Ampel nicht rot, und nie über Hanks Sprechblase: die beiden
reden abwechselnd. Ist über ihnen kein Platz für zwei Blasen, sagt erst er seinen Satz, dann antwortet Hank. An Hanks Tag ändert er nichts (ein Test vergleicht ihn mit und
ohne Roboter Bild für Bild), anklicken lässt er sich nicht, und ein Fehler in ihm beendet nur ihn, nie den Garten. Bei reduzierter Bewegung läuft er nicht, steht neben Hank,
und seine Sprechblase kommt trotzdem.

**Bier, Zigarette:** nur auf der eigenen Box, die Demo zeigt sie nie (`chillPool`). Einen Joint gibt es in dieser öffentlichen Fassung nicht
(Betreiber, 07.11.2026; Paragraf 6 KCanG).

**Hank im Cockpit ist derselbe Mann:** `mascot()` in `web/hank.js` und das Gerüst in `web/hank-scenes.js` zeichnen seit dem 06.11.2026 die Formen des Porträts aus `web/garden-art.js` nach, in Linien statt
Pixeln und in denselben Farben. Alle Gelenke und Abläufe blieben, darum laufen die alten Szenen weiter, und sie bleiben ohne Bier und Rauch: die Demo zeigt sie.

Der Stil ist eigen gezeichnet: ein Retro-Look nach Wunsch des Betreibers, aber Welt, Figur und Schrift stammen von hier, nichts aus einem anderen Spiel.

## English

The cockpit's home screen (owner, Nov 6, 2026). After a minute without input a small pixel world covers the page and shows what Hank is doing. By hand it opens from
the "Hank's garden" button in the top bar of every page, or from the sidebar. It does not jump in by itself while someone is typing, watching in fullscreen, or has Hank's
window, the wiki or a dialog open.

**It stays until it is closed:** mouse, clicks and keys do not close it, only the "To the cockpit" button and Esc. With "Keep open" the page opens in the garden on this device,
also after it reloads for a new version. The two buttons lie on top of the picture: the bubble and the name under the mouse keep clear of them, to the side, and below them where there is no room there.

**Playing:** a click on the ground sends Hank there. A click on a thing sends him to it and he says what it stands for: the thermometer the probe's values, the plants height and soil,
the windmill the fan steps, the fountain the humidifier, the lantern or the sun the light, the board its notes. Fridge, armchair, side table and PC are his free time. The watering can at
the barrel is for show and says so. None of it switches anything. The box comes first: a new alarm or the pump takes him away at once, and an alarm that was already on gets him back when
he is done. The world runs at 65 % of real time, the windmill much slower still.

No game engine, no image files, nothing to download: `web/garden-art.js` draws everything from code, `web/garden-logic.js` decides, `web/garden.js` plays it. It
runs in the viewer's browser; the Mac does not notice. The garden switches nothing and writes nothing: it reads `/api/state` and `/api/hank/state`.

**What Hank does comes from the box, in this order:** box not answering, tank sensor dry, pump running, an alarm about the air (too warm, too cold, too humid), the thirst
decision, the humidifier running out, climate off target (with breaks), and otherwise free time: beer, juice, cigarette, the PC, a look at the leaves, a nap at night. The
light below the card's value is not a job, because the plan holds it there on purpose. Open steps of the plan and every other alarm (cameras, light rules) hang on the notice board as
notes, and every seven minutes or so he names the next one. That is on purpose: "close camera offline" kept him standing for days, with the adapter still in the post. The line under
what he does does not name such an alarm either, it says what the work is about. Nothing holds him on one spot for good: after almost four minutes he takes a break from an alarm too, and keeps saying what is wrong. Only while the pump runs he waters without a break. A value on the
band's edge does not make him run back and forth: a change without an alarm has to stand for a little over a minute.

**On Automatik he does it himself:** while the autopilot runs the exhaust, the line under "Fanning himself" and the other climate jobs is never the card's advice ("Too warm:
turn the exhaust fan up") but what the autopilot does: its exhaust step of the last half hour, else what the exhaust waits for ("watching: the temperature is 28.1 °C, above
28.0 °C. One step up once it stays above 28.5 °C for 10 min"). Against dry air the running humidifier comes first. The owner on Nov 7, 2026: "it's on auto, he should do
everything himself, not telling me to do it". The cockpit follows the same rule: the warning card then shows the reading and, under it, what is being done, the plant card
does not list it as a task, and for "Fix" and "What is there to do today" Hank says what the autopilot does instead of the advice. With the exhaust on manual, observe or paused, the advice is the owner's again. When the line
needs one row more than fits beside the hint "Click on something", it takes the hint's place instead of losing its end.

**The world shows readings:** lamp off is night, the sun stands as high as the lamp is bright; the windmill follows the exhaust, the leaves the circulation; mist
at the fountain while the humidifier runs, water in the hose while the pump runs, an empty barrel when the tank sensor is dry, darker soil the wetter it is, and
plants as tall as the camera measures. Only the middle plant is the real one; the two beside it are scenery and grow along.

**His helper, the little robot** (owner, Nov 15, 2026): built from parts of the box, his legs two soil-sensor stakes, his body the gateway box, his head the climate probe,
a sprout with a beacon on top. What he reports he reads in one place (`GardenLogic.buddy`): every active row of the server's alert list, the supervision rules for signals
that went silent included (yellow or red, a row without a level counts as red), and whether the page still reaches the server: `vgrow:offline` and no new state for 10 s, or
none at all for 30 s (after the computer slept, counted from its waking up). A yellow row, only a notice in the cockpit, counts for him like a red one; whether it should be
quieter is the owner's call. A new problem sends him running to Hank, faster than Hank walks, his beacon red, and he says it in his own bubble ("Hank! Too warm (probe)!"). Hank
turns to him and answers in a few words. While it stays, the beacon stays red and he says it again only after about twelve minutes; another new problem is another run.
Whatever is new at one moment he says on the same run, all of it, one after the other; what is over before he gets to it he leaves out. His bubble has a strip in his beacon's
colour along its top, so it reads as his also when it sits above Hank's, and his lines break where a sentence ends. When
all is clear he says so once and the beacon turns amber again. Otherwise he keeps Hank company: at work he stands beside him with his magnifier, in Hank's free time he hangs
out with him. During Hank's beer and smoke breaks, and as long as Hank holds one, he goes to the plants. **His charger** (owner, Nov 16, 2026): on the free grass at the
front stands a charging post with a screen and a lamp, a charging pad in front of it. When all is calm and Hank takes a nap, he goes there, stands on the pad, the cable in his
back, eyes shut, his beacon green, and a battery over his head fills up (empty to full in 90 world seconds; Hank's nap lasts 80). Full, he stands on the pad awake. About the
island the battery runs down slowly, faster when he runs, never below 15 %. Lamp off: he sleeps on the pad with the battery over him; a problem wakes him at once. A problem
that stays keeps him with Hank, also through Hank's nap. Now and then he tells a joke, only when all is calm and Hank's light is not red, and never over Hank's bubble: they take turns. Where there is no
room above them for two bubbles, he says his line first and Hank answers after. He changes nothing of Hank's day (a test compares it with and without him, frame by frame),
he cannot be clicked, and a fault in him ends only him, never the garden. With reduced motion he does not walk and stands beside Hank, and his bubble still comes.

**Beer, cigarette:** on the owner's own box only, the demo never shows them (`chillPool`). This public version has no joint (owner, Nov 7, 2026;
Paragraph 6 KCanG).

**Hank in the cockpit is the same man:** since Nov 6, 2026 `mascot()` in `web/hank.js` and the rig in `web/hank-scenes.js` draw the shapes of the portrait in `web/garden-art.js`, in lines instead of pixels
and in the same colours. Every joint and timing stayed, so the old scenes keep running, and they stay without beer and smoke: the demo shows them.

The style is drawn here: a retro look as the owner asked, but the world, the figure and the font are our own, nothing taken from another game.
