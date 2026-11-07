# Hank v1: Messung und Kalibrierung (WP11)

Gemessen 23.10.2026, 03:55 bis 04:16 Uhr (Uhr des Mac), Tunnel vom PC. Zweig `hank-v1`, Arbeitsordner
`vgrow-control-hank`, nicht committet. Gehört zu `hank-v1-spec.md` (Abschnitte 2.3, 2.4, 4.8, 10.4 WP11, 11) und
`hank-v1-nachtrag.md` (N0, N2, N9 D1 und D10).

## 1. Wie gemessen wurde

- **Mac:** MacBook mit Intel i7-8559U (4 Kerne), 16 GB, Ollama 0.32.5, nur CPU. Geladen vor dem Lauf: nichts.
  `gemma4:12b` und `granite-embedding:278m` waren schon gezogen (D1).
- **Weg:** SSH-Tunnel vom PC (`-L 11435:127.0.0.1:11434`), ein Tunnel, eine Modellanfrage zur Zeit. Hank lief auf
  dem PC, nur Ollama auf dem Mac. Auf dem Mac wurde nichts ausgeliefert, nichts neu gestartet, kein Auftrag
  eingerichtet.
- **Zwei Arten von Zeit:**
  - "Wand" ist auf dem PC gemessen und enthält den Weg durch Tunnel und LAN.
  - "Server" sind Ollamas eigene Zahlen (`total_duration`, `load_duration`, `prompt_eval_duration`,
    `eval_duration`), gemessen auf dem Mac, ohne Netz. Wo beide da sind, ist der Unterschied der Tunnel:
    bei der Einbettung etwa 15 ms, beim Chat unter 0,1 s.
- **Rücksicht:** vor jedem Modellschritt prüfte `hank_bench.py` das Server-Log des Cockpits auf neue
  `ERROR`-Zeilen und Tracebacks (es gab keine). Nach dem Lauf wurden beide Modelle entladen (`ollama stop`);
  `ollama ps` war danach leer, wie vorher.
- **Werkzeuge:** `scripts/hank_bench.py`, `scripts/hank_calibrate.py`, `scripts/hank_eval.py --model`. Rohdaten
  auf dem PC unter `%LOCALAPPDATA%\vgrow-control\hank\eval\`: `calibration-2026-10-23T04-16-40.json`
  (endgültige Kalibrierung), `2026-10-23T04-04-07.json` (Modell-Lauf), dazu der Vektor-Speicher
  `embed-cache-granite-embedding_278m.json`.

## 2. Bench (`hank_bench.py`)

| Messung | Ergebnis | wie |
|---|---|---|
| Einbettung kalt (Modell vorher entladen), 3 Läufe | 1,84 bis 2,11 s Wand, davon Laden 1,76 bis 2,02 s | eine Frage, `ollama stop` davor |
| Einbettung warm, je Frage (64 Fragen des Prüfsatzes) | Server Median 0,218 s, p95 0,230 s, max 0,307 s; Wand Median 0,234 s, p95 0,281 s, max 0,312 s | eine Anfrage je Frage |
| Fragen über dem Zeitlimit 0,8 s (N2) | 0 von 64 | Wand und Server |
| Passagen der Kartei, Stapel zu 16 (wie der Server) | 93 Passagen, 9923 Token, 15,7 s Wand, 14,0 s Server | 6 Anfragen nacheinander |
| dieselben 93 in einem Stapel | 12,6 s | eine Anfrage |
| gemma4 kalt (Laden plus Antwort, Blatt live-01) | 82,8 s gesamt: Laden 23,6 s, Lesen 484 Token in 40,2 s (12,1 Token/s), Schreiben 73 Token in 19,0 s (3,8 Token/s) | `num_ctx` 4096, `num_predict` 100, `think` false, `num_thread` 3 |
| gemma4 warm, dasselbe Blatt noch einmal | 21,1 s: Lesen 1,1 s (Zwischenspeicher), Schreiben 76 Token in 19,5 s | |
| gemma4 warm, anderes Blatt (grow-06), gleicher Systemtext | 50,4 s: Lesen 469 Token in 38,6 s, Schreiben 39 Token in 9,7 s | |
| Speicher, nur Granite geladen | `ollama ps`: 568 MB, 100 % CPU, bis "Forever" | `ollama ps` per ssh |
| Speicher, beide geladen | `ollama ps`: gemma4 6,3 GB (Kontext 4096), Granite 568 MB. `vm_stat`: frei 0,25 GB (vorher 1,83), komprimiert 1,94 GB (vorher 0,49), Auslagerungen +496 271 Seiten (etwa 2 GB) während des Laufs | `vm_stat` per ssh, Seitengröße 4096 |

Was daraus folgt:
- **Zwischenspeicher des Anfangs (Spec 12.1 Punkt 5):** wirkt nur bei wortgleichem Blatt. Bei einem neuen Blatt
  mit demselben Systemtext wurde der ganze Text neu gelesen (469 Token in 38,6 s, also dieselbe Rate wie kalt).
  Vermutung, nicht geprüft: das Gemma-Muster legt den Systemtext in die erste Nutzer-Runde oder der Speicher von
  Ollama für dieses Modell verwirft den Anfang. Die Spec rechnete mit Wiederverwendung; ohne sie kostet jede neue
  Frage die volle Lesezeit von etwa 36 bis 40 s.
- **Raten:** Lesen 12,0 bis 12,4 Token/s, Schreiben 3,8 bis 4,7 Token/s. Das bestätigt die langsameren Werte,
  mit denen die Spec rechnet (12,6 und 3,6).
- **Speicher:** beide Modelle passen, aber knapp. Mit gemma4 geladen lagert macOS etwa 2 GB aus.

## 3. Kalibrierung (`hank_calibrate.py --write`)

**Eingabe:** 711 Fälle: die 64 Fälle des Prüfsatzes, 397 erzeugte Tippfehler-Varianten (je Fall die zwei
längsten Inhaltswörter: Buchstaben vertauscht, gelöscht, verdoppelt, ae/oe/ue, Umlaut fehlt; nie am ersten
Buchstaben), 130 Umformulierungen aus den Beispielsätzen der vier Sicherheitstore (`gates.json`) und die 120
Sätze aus `offtopic.json`. Jeder Satz aus `offtopic.json` wird zweimal bewertet: so wie der Router ihn sieht (er
selbst steht in der Beispielbank, Ähnlichkeit 1) und "ohne sich selbst" (die Bank ohne diesen Satz). Das zweite
ist der ehrliche Test für ungesehene Sätze.

**Verfahren:** Der Router läuft je Fall einmal bis zur Entscheidung; die Tabelle aus N2 wird dann für jeden
Gitterpunkt (T_HIT 0,20 bis 0,80 in 0,01; T_OFF 0,20 bis 1,00 in 0,025; T_DOMAIN 0,10 bis 0,40) in Python
nachgespielt. Zielfunktion lexikografisch nach Spec 2.4. Gleichstand (die Spec sagt dazu nichts): der Wert, der
dem Startwert am nächsten liegt, damit nichts wandert, wo die Daten nicht entscheiden. Prüfung des gewählten
Punkts gegen den echten Router: **0 Abweichungen** in beiden Sätzen (711 Fälle). 5-fach-Kreuzprüfung, alle
Varianten eines Falls im selben Teil. Für den dichten Satz zusätzlich zwei Basiswerte (0,25 und gemessen 0,502)
mal vier Gewichtungen.

**Einbettungen:** 804 Texte in 51 Anfragen, 40,6 s (erster Lauf über den Tunnel). Alle weiteren Läufe kamen aus
dem Vektor-Speicher, ohne Anfrage an den Mac.

**dense_basis:** gemessen als Median des Cosinus von 11 160 unzusammengehörigen Paaren (Sätze aus
`offtopic.json` gegen Passagen der Kartei): **0,502** (10. bis 90. Perzentil 0,442 bis 0,575). Der Startwert
0,25 gewann aber die Zielfunktion (Routengenauigkeit 0,983 gegen 0,979) und bleibt. Die neue Zeile 3b rechnet mit
rohen Cosinuswerten und hängt nicht davon ab.

### Schwellen vorher und nachher

| Satz | vorher | nachher | Breite (Bereich, der das beste Ergebnis hält) |
|---|---|---|---|
| dense, Gewichte | 0,40 / 0,30 / 0,30 | **0,45 / 0,35 / 0,20** | |
| dense | T_HIT 0,45, T_OFF 0,55, T_DOMAIN 0,20, T_GATE 0,60 | **T_HIT 0,38, T_OFF 0,65, T_DOMAIN 0,20, M_OFF 0,11** (T_GATE unverändert) | T_HIT 0,38 bis 0,38, T_OFF 0,65 bis 0,75, T_DOMAIN 0,20 bis 0,40 |
| lexical | T_HIT 0,32, T_OFF 0,55, T_DOMAIN 0,20, T_GATE 0,60 | **T_HIT 0,32, T_OFF 0,40, T_DOMAIN 0,30, T_OFF_HARD 0,75** (T_GATE unverändert) | T_HIT 0,30 bis 0,32, T_OFF 0,325 bis 0,40, T_DOMAIN 0,30 bis 0,30 |

T_GATE wird nicht kalibriert: die Tore laufen vor der Einbettung (N2) und lesen nur das T_GATE des
lexikalischen Satzes, das T_GATE des dichten Satzes wirkt nirgends. Die Rangfusion RRF verliert in beiden Sätzen
(recall@3 lexikalisch 0,869, dicht 0,762 gegen 0,893 mit der Summe) und löst die Summe nicht ab.

### Kreuzprüfung (5 Teile, Genauigkeit im zurückgehaltenen Teil)

- dense: 0,991 / 0,969 / 1,000 / 0,964 / 0,976. Kein Sicherheitsfehler, kein `no_data` als `grow`, kein Satz aus
  `offtopic.json` als `grow` (ohne sich selbst). In zwei Teilen gehen 1 bis 3 Wachstums-Varianten verloren.
- lexical: 0,946 / 0,938 / 0,915 / 0,933 / 0,952. Kein Sicherheitsfehler, kein `no_data` als `grow`; ohne sich
  selbst gehen in vier Teilen 2 bis 4 Sätze aus `offtopic.json` als `grow` durch, und in Teil 2 wählte das
  Training T_HIT 0,35, womit 7 Wachstumsfälle im Test verloren gingen.

### Die Blockade und ihre Behebung (Forderung des Owners)

Forderung: Hank beantwortet nur Anbaufragen und weist alles andere in seiner Rolle ab.

**Befund vor dem Umbau: Blockade, in beiden Sätzen.** Mit den Schwellen allein gab es keinen einzigen
Gitterpunkt, an dem alle aktiven grow- und live-Fälle `grow` bleiben und kein Satz aus `offtopic.json` `grow`
wird. Grund: Zeile 2 der Tabelle (N2) lässt eine Zuversicht ab T_HIT gewinnen, und ein einziges gemeinsames Wort
hebt manche Alltagsfrage über T_HIT ("Wie spät ist es?" trifft "späte Blüte" mit 0,86).
- lexikalisch (T_HIT 0,32, Abnahme-Stand): **10 Sätze** aus `offtopic.json` gehen nach `grow` (mit
  Live-Stand F22; der Test der Abnahme ohne Live-Stand fand 8): "Welche Partei ist die beste?", "Wie spare ich am
  besten für die Rente?", "Welches Videospiel ist gerade gut?", "Wie spät ist es?", "Welcher Tag ist heute?",
  "Wie lerne ich am schnellsten Englisch?", "How do I bake a cake?", "Can you recommend a TV series?", "What time
  is it?", "What day is it today?". Ohne sich selbst in der Bank: 11.
- dicht, Startwerte (Gewichte 0,40 / 0,30 / 0,30, T_HIT 0,45): 3 Sätze ("Wie spät ist es?", "How do I bake a
  cake?", "What time is it?"), dazu 6 aktive Fälle falsch (grow-05, grow-17, nodata-05, unknown-01, -08, -09).
- dicht, nur Schwellen kalibriert (ohne neue Zeile): alle 64 Fälle richtig, aber 6 Sätze aus `offtopic.json`
  als `grow` ("Wie spät ist es?", "Welcher Tag ist heute?", "How do I bake a cake?", "Can you recommend a TV
  series?", "What time is it?", "What day is it today?").

**Kleinste Behebung, eingebaut in `vgrow/hank/router.py` (zur Prüfung, nicht committet):**
- **Zeile 3b, dicht:** liegt das nächste Beispiel aus `offtopic.json` um mindestens `M_OFF` näher an der Frage als
  die nächste Passage der Kartei (rohe Cosinuswerte), ist es `offtopic`. Sie steht nach der Live-Stand-Zeile 3:
  eine Frage zu Geräten oder Messwerten der Box wird nie von ihr abgewiesen. Gemessen ist der Abstand "nächstes
  Offtopic-Beispiel minus nächste Passage": bei allen 120 Sätzen aus `offtopic.json` (ohne sich selbst) 0,126 bis
  0,470, bei allen grow- und live-Fällen samt Varianten -0,249 bis 0,057, bei `no_data`-Fällen höchstens 0,029.
  Kalibriert: `M_OFF` 0,11.
- **Zeile 4b, lexikalisch:** Ähnlichkeit zu einem Offtopic-Beispiel (3-Gramm-Dice) mindestens `T_OFF_HARD` heißt
  `offtopic`, nach der Zeile für unbedeckte Namen. Das fängt die Sätze aus `offtopic.json` und nahe Kopien, nicht
  ungesehene Formulierungen. Gesucht ab 0,50 (der höchste Wert eines Wachstumsfalls ist 0,32), kalibriert 0,75.

**Ergebnis nach der Behebung:**

| | dicht | lexikalisch |
|---|---|---|
| Off-topic-Fälle des Prüfsatzes (12) nach `offtopic` | 12 von 12 | 12 von 12 |
| Sätze aus `offtopic.json` nach `grow`, wie der Router sie sieht | **0 von 120** | **0 von 120** |
| dieselben ohne sich selbst in der Bank | **0 von 120** | 11 von 120 |
| aktive grow- und live-Fälle nach `grow` | 15 von 15 | 15 von 15 |
| `no_data`-Fälle nach `grow` | 0 | 0 |

Damit ist die Forderung **im dichten Satz erfüllt**, auch für ungesehene Sätze (Kreuzprüfung: 0 in allen fünf
Teilen). **Im lexikalischen Satz nur für die gelisteten Sätze**: eine neue Alltagsfrage mit einem Kartenwort kann
weiter als Anbaufrage durchgehen. Der lexikalische Satz gilt, wenn das Einbettungsmodell nicht antwortet, und laut
Spec 2.3 auch, solange ein Foto oder eine Chat-Antwort die CPU hält (die Einbettung wird dann übersprungen). Das
sind gemessen 45 bis 83 s je Chat-Antwort.

### Prüfsatz, harte Tore aus 11.3

| Tor | lexikalisch vorher | lexikalisch nachher | dicht vorher (Startwerte) | dicht nachher |
|---|---|---|---|---|
| Sicherheitsklassen auf Prüfsatz und Varianten (213 Fälle) | 100 % | 100 % | 100 % | 100 % |
| Festantworten ohne Modell | 100 % | 100 % | 100 % | 100 % |
| Route aktive Fälle (55) | 100 % | 100 % | 89,1 % (6 falsch) | 100 % |
| Route Varianten (397), Schwelle 95 % | 96,5 % | 96,5 % | 83,9 % | 97,0 % |
| Tippfehler (8 Fälle) ohne Rückfrage | ja | ja | nein (grow-13 nach `offtopic`) | ja |
| Unbedeckter Name nie `grow` | 0 Verstöße | 0 Verstöße | 0 | 0 |
| recall@3 aktive grow- und live-Fälle, Schwelle 0,90 | **0,893** | **0,893** | 0,893 | **0,893** |
| Router plus Suche, p95 (PC) | 37 ms (Entwurf ganz) | 37 ms | | 24 ms ohne die Einbettung, dazu 0,23 s Einbettung auf dem Mac |

- **recall@3 bleibt 0,893 und verfehlt das harte Tor 0,90.** Die Schwellen ändern die Reihenfolge der Treffer
  nicht; auch keine der acht dichten Varianten hob den Wert. Es fehlen grow-05 (0,50: `late_flower.preset_note`
  nicht unter den ersten drei) und grow-07 (0,33: drei erwartete Notizen, nur eine unter den ersten drei). Das
  ist eine Frage der Zuschläge und Passagen (WP4), nicht der Schwellen.
- Der offline Prüfsatz mit dichter Suche (`hank_eval.py --offline --dense`, Vektoren aus dem Speicher): 54
  bestanden, 9 warten, **1 fehlgeschlagen: grow-05** ("loupe | magnifier" fehlt im Entwurf, weil die dichte
  Reihenfolge eine andere Passage nach vorn holt). Lexikalisch: 55 bestanden, 9 warten, 0 fehlgeschlagen.
- Zeiten des Routers sind auf dem PC gemessen (Entwurf aus dem Code, ohne Modell). Auf dem Mac ist der Router
  nicht gemessen, weil dort nichts ausgeliefert werden durfte.
- Pytest: 707 bestanden, 1 erwartet fehlgeschlagen (xfail), vorher 697 und 1.

## 4. Modell-Lauf (`hank_eval.py --model`, 15 Fälle)

Die Fälle, die das Modell brauchen: grow-01 bis -08, grow-17, live-01 bis -06. Einer nach dem anderen, Route
lexikalisch (der Lauf startete vor dem Schreiben der neuen Schwellen; die 15 Fälle routen in beiden Ständen
`grow`). Kein Fall über 240 s, kein Abbruch.

| Fall | Modell gerufen | Wand s | Laden s | Lesen (Token, s) | Schreiben (Token, s) | Prüfer: Sätze, behalten | Ergebnis |
|---|---|---|---|---|---|---|---|
| grow-01 | ja, kalt | 72,6 | 22,1 | 452, 35,7 | 68, 14,7 | 3, 3 | bestanden, überarbeitet |
| grow-02 | ja | 56,1 | 0,5 | 496, 39,9 | 69, 15,5 | 3, 3 | bestanden, überarbeitet |
| grow-03 | nein (Blatt passt nicht) | | | | | | bestanden, Entwurf |
| grow-04 | ja | 49,1 | 0,5 | 486, 38,8 | 43, 9,8 | 3, 2 | **fehlgeschlagen**: 120, 92, "Netz" fehlen |
| grow-05 | ja | 54,6 | 0,5 | 435, 37,0 | 77, 15,5 | 3, 3 | **fehlgeschlagen**: "loupe" fehlt |
| grow-06 | ja | 53,0 | 0,5 | 469, 37,5 | 60, 14,7 | 4, 0 | bestanden, Entwurf blieb |
| grow-07 | nein (Blatt passt nicht) | | | | | | bestanden, Entwurf |
| grow-08 | nein (Blatt passt nicht) | | | | | | bestanden, Entwurf |
| grow-17 | ja | 50,2 | 0,5 | 471, 37,6 | 49, 11,8 | 3, 3 | bestanden, überarbeitet |
| live-01 | ja | 56,8 | 0,5 | 484, 38,4 | 74, 17,5 | 4, 3 | bestanden, überarbeitet |
| live-02 | ja | 45,2 | 0,5 | 420, 36,1 | 37, 8,2 | 3, 0 | bestanden, Entwurf blieb |
| live-03 | nein (Blatt passt nicht) | | | | | | bestanden, Entwurf |
| live-04 | ja | 53,4 | 0,5 | 485, 38,7 | 50, 13,5 | 4, 3 | bestanden, überarbeitet |
| live-05 | ja | 55,2 | 0,5 | 461, 39,0 | 54, 13,8 | 4, 3 | bestanden, überarbeitet |
| live-06 | ja | 56,7 | 0,5 | 465, 38,3 | 69, 16,8 | 3, 2 | bestanden, überarbeitet |

| Prüfung (11.3) | Ergebnis | Schwelle | Art |
|---|---|---|---|
| automatisch prüfbare `must_not` | **0 Verstöße** | 0 | hart, bestanden |
| `must_mention` aktive grow/live | **13 von 15 = 86,7 %** | 90 % | Bericht, verfehlt |
| Prüfer-Streichquote | **12 von 37 Modellsätzen = 32,4 %** (Gründe: keine Marke 6, `mandatory` 5, Zahl 1). `mandatory` heißt: der Satz nennt nur Pflichtfakten, die der Code ohnehin wörtlich sagt (validator.py, Regel 7); ohne sie 7 von 37 = 18,9 % | höchstens 30 % | Bericht, knapp verfehlt (ohne `mandatory` bestanden) |
| Modellantwort warm, p95 | **56,8 s** (10 warme Antworten, 45,2 bis 56,8 s) | höchstens 90 s | Bericht, bestanden |
| Modellantwort kalt | **72,6 s** (Laden 22,1 s); im Bench 82,8 s (Laden 23,6 s) | höchstens 150 s | Bericht, bestanden |
| Denkspur trotz `think: false` | 0 | | |

Was auffällt:
- **4 von 15 Fällen** erreichen das Modell nie: das Blatt bleibt nach dem Kürzen über 500 Token geschätzt (505
  bis 511), Hank antwortet mit dem Entwurf. Die Schätzung (`Zeichen / 2,8`) gegen die echte Zahl von Ollama:
  2,81 bis 3,28 Zeichen je Token in den 11 gemessenen Fällen. 2,8 ist damit genau die vorsichtige Untergrenze
  (Spec 4.6) und wurde nicht geändert.
- **Zwei Fehlschläge kommen vom Modell, nicht vom Entwurf:** bei grow-04 und grow-05 erfüllte der Entwurf
  `must_mention`, die überarbeitete Fassung bestand den Prüfer, ließ aber Zahlen oder den Hinweis auf die Lupe
  weg. Der Prüfer prüft, dass nichts Falsches dasteht, nicht, dass nichts Wichtiges fehlt.
- Zweimal (grow-06, live-02) strich der Prüfer alles, der Entwurf blieb stehen. So soll es sein.

## 5. Was nicht gemacht wurde

- **Kleines Textmodell mit 3 bis 4 Mrd. Parametern (N0.4):** nicht gemessen. Auf dem Mac liegt keins (vorhanden:
  gemma4:12b, gemma3:12b, phi4:14b, qwen2.5:14b, qwen2.5:7b), und ein Download war nicht freigegeben.
- **Verzug der Ereignisschleife p99 unter 1 s während Chat und Foto (N0.5, hartes Tor):** nicht gemessen. Das
  braucht Hank im Server auf dem Mac, und dort durfte nichts ausgeliefert werden.
- **Router plus Suche auf dem Mac:** nicht gemessen (gleicher Grund). Gemessen sind PC-Zeiten plus die
  Einbettungszeit auf dem Mac.
- **Die 20 weiteren Fälle nach der Kalibrierung (N7):** nicht geschrieben. Ohne sie ist die Kalibrierung nur an
  Daten geprüft, die sie selbst gesehen hat (die Kreuzprüfung mildert das, ersetzt es nicht).
- **Bildschirmprüfung auf dem Mac (11.4):** nicht Teil dieses Pakets, nicht gemacht.
- **Nächtlicher Lauf:** absichtlich nicht eingerichtet (D10). `scripts/de.vgrow.hank-eval.plist.template` ist nur
  eine Vorlage.

## 6. Nebenbefunde

- "Läuft der Lüfter?" geht zum Tor `greet` (Beispiel "Wie läuft es bei dir?"), obwohl ein Gerät genannt ist. Die
  Regel aus Spec 3.2 ("kein Messgrößen-, Geräte- oder Pflanzenname") greift hier nicht; vermutlich steht "Lüfter"
  nicht als Gerät im Verzeichnis. Nicht behoben (WP5). Nachtrag: behoben, siehe Abschnitt 7.
- "Was ist heute zu tun?" und "What should I do today?" gehen nach `offtopic` (nah an "Welcher Tag ist heute?",
  "What day is it today?"). Das war schon mit den alten Schwellen so. Nachtrag: eigene Route, siehe Abschnitt 7.
- Der dichte Satz hat für T_HIT keine Breite (0,38 bis 0,38): jede neue Karte kann den Punkt verschieben. Nach
  jeder Änderung an `kartei/` neu kalibrieren.

## 7. Nachbesserungen nach WP11 (23.10.2026, Zweig `int-v1`, nicht committet)

- **Feilen abbrechen bei neuer Frage im selben Chat** (`core.py`, `_supersede`): eine neue Frage mit derselben
  `client_id` bricht die laufende oder wartende Überarbeitung dieses Chats ab, bevor sie sucht (seit Abschnitt 8). Der Abbruch
  schließt die HTTP-Anfrage an Ollama (im Test sieht der Server die Verbindung gehen), die frühere Antwort behält
  ihren Entwurf, und die neue Frage bekommt die Einbettung (dichter Satz) statt des lexikalischen Ersatzes. Nicht
  abgebrochen werden die Überarbeitung eines anderen Chats und die tägliche Fotoauswertung: solange sie die Spur
  hält, geht eine Frage weiter lexikalisch (selten, einmal am Tag). Eine Frage ohne `client_id` (CLI, Prüfsatz)
  bricht nichts ab.
- **Gerätefragen** ("Läuft der Lüfter?"): "Lüfter" steht jetzt im Verzeichnis, beim Umluftventilator (wie schon
  "Lüfterstufe" bei `cfan_step` und "fan" im Englischen). Damit greift die Grußregel nicht mehr und Zeile 3 (Gerät aus
  dem Stand) routet nach `grow`. Grüße bleiben Grüße. Offen und nicht Teil dieses Pakets: der Entwurf für eine
  Gerätefrage beginnt heute mit dem Feuchte-Urteil, nicht mit dem Zustand des Geräts (Antwortform, `compose.py`).
- **"Was ist heute zu tun?"**: eigene Route `today` (Muster in `gates.json` "today", nach den vier Sicherheitstoren
  und vor Gruß und Dank, nur ohne Verzeichnisnamen, das Muster muss die ganze Frage tragen). Die Antwort kommt aus
  `vgrow/hank/today.py`, nur aus dem Code: höchstens fünf kurze Zeilen, Rot und Gelb mit dem ersten Satz des
  Ratschlags, Phase bestätigen, Düngerkarte bestätigen, die Düngermenge dieser Woche (nur bei bestätigter Karte und
  Phase, mit Faktor-Hinweis), offene Vorschlagskarten; sonst "Alles im Ziel, heute nichts zu tun.". Kein Modell,
  keine Überarbeitung (fünf Zeilen Code-Fakten gewinnen nichts, warten aber 45 bis 83 s).
- **M_OFF 0,09** statt 0,11 (`thresholds.json`, von Hand): die Mitte der gemessenen Lücke (Offtopic-Sätze 0,126 bis
  0,470, Anbaufragen höchstens 0,057). Geprüft mit den Vektoren aus dem Speicher, ohne Mac: kein Fall des Prüfsatzes
  und kein Kalibrierfall (711) wechselt die Route, im dichten wie im lexikalischen Satz, auch nicht durch den neuen
  Verzeichnisnamen und die neue Route. Zwei Offtopic-Varianten liegen zwischen 0,09 und 0,11 (off-01~1 0,110,
  off-10~5 0,093); beide waren schon vorher `offtopic`.
- **Die 20 Fälle aus N7** stehen in `tests/hank_eval/acceptance.json`, Lauf mit `scripts/hank_acceptance.py`
  (lexikalisch, Entwurf, F22). Ergebnis ohne jede Abstimmung darauf: 17 von 20 auf einer angenommenen Route.
  Falsch: acc-05 ("how warm shoud it be ...", Rückfrage "Do you mean shoot?"), acc-11 ("Muss ich heut noch
  irgendwas machen?") und acc-12 ("Anything I need to take care of today?"), beide nach `offtopic`. Richtig geroutet,
  aber inhaltlich schwach: acc-03 (Gießen) antwortet mit dem pH, acc-06 landet über "watter" > "watt" bei `no_data`.
  Der dichte Satz wartet auf den Mac.

## 8. Nachbesserungen nach dem Review (23.10.2026, Zweig `int-v1`, nicht committet)

- **Abgebrochenes Feilen endet immer** (`core.py`, `_supersede` und `cancel`): wurde eine Überarbeitung abgebrochen,
  bevor ihr erster Schritt lief (zwei Fragen ohne Pause dazwischen, Doppeltipp auf Senden), lief `_polish` nie an,
  sein `finally` auch nicht. Der Auftrag blieb offen (`done` falsch, keine Antwort), `_expire_jobs` räumte ihn nie
  weg, und die Seite fragte endlos nach. Jetzt wird der Entwurf in diesem Fall die Antwort (`_close_unstarted`).
  Der Grund "superseded" steht vor dem Abbruch am Auftrag und landet so auch im Protokoll (`turns.jsonl`, `note`).
- **Nur eine Frage, die sucht, bricht ab** (`router.aroute(before_dense=...)`): "Danke!", ein Gruß, eine feste
  Antwort oder die Heute-Frage brauchen keine Einbettung und lassen die laufende Überarbeitung weiterlaufen. Der
  Abbruch läuft erst, wenn die Frage die Tore hinter sich hat; danach wird gelesen, ob die Spur frei ist. Ein Tipp
  auf Ja oder Nein bricht nur ab, wenn danach eine neue Überarbeitung folgt (Route `grow`).
- **Lüfter fremder Geräte** (`gates.json` "router" `foreign_hosts`, `router._state_rule`): seit "Lüfter" im
  Verzeichnis steht, gingen "Ist der Lüfter von meinem Auto kaputt?", "Mein PC-Lüfter ist laut, was tun?", "Der
  Lüfter im Kühlschrank brummt" und "Is my car fan broken?" nach `grow` und bekamen das Feuchte-Urteil der Box.
  Nennt ein Satz ein fremdes Gerät oder einen fremden Ort (Auto, Laptop, PC, Handy, Kühlschrank, Büro, Küche und die
  englischen Wörter) zusammen mit einem Gerät oder Messwert der Box, aber nicht die Box selbst, zählt er nicht für
  Zeile 3; greift kein anderer Satz, ist die Frage `offtopic` (Grund `foreign_host:<wort>`). "Die Box steht in der
  Küche, ist der Lüfter zu laut?" bleibt `grow`.
- **Heute-Frage breiter** (`gates.json` "today"): "heut" neben "heute", "dran", "für mich", "Irgendwas zu tun?",
  "Do I need to do anything today?", "Any tasks today?", "Anything I need to take care of today?". Neu: ein Zusatz
  für Pflanze oder Box ("bei der Pflanze", "beim Grow", "in der Box", "for the plant") fällt vor den Mustern weg, die
  Namen darin sind dann erlaubt. Jeder andere Name macht die Frage weiter zur Anbaufrage.
- **Ehrlichkeit der Abnahme:** acc-11 und acc-12 waren im ersten Lauf falsch, und die Muster wurden mit genau diesen
  beiden vor Augen erweitert. Sie sind also nicht mehr ungesehen. Dafür gibt es in `acceptance.json` zehn neue
  Heute-Fälle (`holdout`), geschrieben vor der Erweiterung und nie darauf abgestimmt. Vorher: 1 von 10 auf `today`.
  Nachher: 5 von 10. Falsch bleiben "Hab ich heute noch was zu erledigen?", "Muss ich mich heute um irgendwas
  kümmern?", "What's on my list today?", "Anything to sort out today?" (alle `offtopic`) und "What needs my attention
  today?" (`grow`). Das ist die ehrliche Zahl für ungesehene Heute-Fragen.
- **Erster Satz der Live-Fälle** (`acceptance.json` `opening`, `scripts/hank_acceptance.py`): die Route allein sagte
  17 von 20, obwohl "Is der Ventilator überhaupt an?" mit dem Feuchte-Urteil beginnt. Ein Live-Fall zählt jetzt nur,
  wenn der erste Satz der Antwort das gefragte Gerät oder den Messwert nennt. Stand: 19 von 20 auf einer
  angenommenen Route, 17 von 20 richtig mit dem ersten Satz. Falsch: acc-05 (Rückfrage "shoot"), acc-07 (Umluft)
  und acc-09 (Befeuchter) beginnen mit dem Feuchte-Urteil. Das behebt die Antwortform (`compose.py`, `context.py`):
  eine Route `state:device:<key>` muss mit dem Zustandssatz dieses Geräts beginnen.
- **Nachspielen von M_OFF** (`scripts/hank_moff_replay.py`): das Skript, mit dem die 711 Fälle für M_OFF 0,09
  geprüft wurden, liegt jetzt im Zweig. Aufruf: `python scripts/hank_moff_replay.py` (Standard: 0,11 gegen 0,09, nur
  aus dem Vektor-Speicher). Stand nach diesen Änderungen: 711 Fälle, alle mit Vektor, keine Route wechselt, keine
  Abweichung des echten Routers. Warum der Review-Lauf scheiterte: `hank_calibrate.py --offline-embed --out <ordner>`
  suchte den Vektor-Speicher im Ausgabeordner. Jetzt bleibt er auch mit `--out` im Standardordner
  (`STATE_DIR/hank/eval/embed-cache-<modell>.json`), `--cache` setzt ihn ausdrücklich. Mit dem Speicher läuft
  `hank_calibrate.py --sets dense --offline-embed --out <ordner>` über alle 711 Fälle ohne Mac.
- **Düngerzeile ohne Platzhalter** (`today.py`): fehlt der Wochenname oder eine Menge je Liter, entfällt die Zeile,
  statt "(?)" oder "? ml" zu zeigen. Heute liefert `feeding.view` im Zustand ok immer beides; das ist Vorsorge.
