# Betriebsreferenz (Deutsch)

[Repository overview](../README.md) · [Documentation index](README.md) · [Setup guide](getting-started.md)

Diese Referenz bewahrt die ausführlichen Betriebsdetails des bisherigen README. Stand: 26.10.2026. Die englische Repository-Übersicht und die verlinkten Fachseiten erklären den aktuellen Produktumfang. Entwürfe sind keine Zusage implementierter Funktionen.


GrowCam und eine zusätzliche USB-Kamera am Mac (Logitech C920) erscheinen nebeneinander,
am Handy untereinander. Jede Kamera hat eigene Livebild-, Standbild- und Aufnahmefunktionen.
Einrichtung und macOS-Kamerafreigabe: [USB-Kamera](../docs/usb-camera.md).

Kein VIVOSUN-Produkt und nicht von VIVOSUN.

Eine Handy-Seite für die VIVOSUN VGrow Smart Grow Box (Modell `VSCTLY18`), als Ergänzung
zur VIVOSUN-App, kein Ersatz. Läuft rund um die Uhr auf dem MacBook (launchd), das Handy
öffnet sie über Tailscale von überall, im Heim-WLAN auch ohne.

Das Cockpit hat eigene Seiten für **Übersicht**, **Mein Grow** (Wachstum, Düngen, KI-Einschätzung,
Journal), **Automatik**, **Geräte**, **Auswertung**, **Alarme & Aktivitäten** und **Einstellungen**.
Am Desktop führt die Seitenleiste dorthin, am Handy das Menü. News, der Wiki-Themenindex und die VIVOSUN Community stehen unter
„Wissen & Hilfe“. Alte Links wie `#autopilot` oder `#pflanze` öffnen die passende neue Seite.
Die Übersicht zeigt Messwerte, Kamera, Handlungsbedarf und Gerätestand; Sensor- und Gerätemeldungen haben
weiter eigene Zeitangaben. Aufbau und Zuordnung aller Funktionen: [Cockpit-Redesign](../docs/cockpit-redesign.md).

Die hervorgehobene **Licht**-Kachel öffnet **Geräte → Lichtplan** (`#devices/light`). Dort stehen die zuletzt gemeldete
Helligkeit, die gemeldete Plansteuerung und gespeicherte Lichtwerte der letzten 24 Stunden. Die Automatik bleibt in
der Seitenleiste und als Zusammenfassung unterhalb der Übersicht erreichbar. Die Helligkeitsregler bleiben unter
„Steuerung“; ein Link im Lichtplan führt dorthin.

Ein Helligkeitswert ist kein Tagesplan. Unter **Geräte → Lichtplan** lässt sich ein eigener täglicher Lichttimer mit
Ein-/Ausschaltzeit und Ein-Helligkeit speichern und aktivieren. Er beginnt ausgeschaltet, enthält keine geratenen
Zeiten und zeigt Status, tägliche Dauer und nächsten Wechsel an. Unter **Automatik** führt eine eigene Karte dorthin.
Er schaltet unabhängig von Sensorabfragen; während er aktiv ist, übernimmt er das Licht statt der Helligkeitsrampe.
Der Mac und die Cloud-Verbindung müssen verfügbar bleiben: der Timer wird nicht auf dem Gerät installiert.
Ein VIVOSUN-Plan wird nicht überschrieben. Nur ein frischer Bericht mit manuellem Modus und ohne Geräteplan erlaubt
Schalten. Einschalten des Timers gleicht die aktuelle Tagesphase ab; auch nach Neustart oder Wiederverbindung werden
keine alten Schalttermine nachgespielt. Handbefehle und erkannte Änderungen in der VIVOSUN-App gelten bis zum nächsten
Schalttermin. Ein unbestätigter Wechsel wird einmal wiederholt und danach als Fehler angezeigt.
Sommerzeit: eine doppelte Uhrzeit gilt beim ersten Auftreten, eine nicht existierende Uhrzeit entfällt.
Der Timer lebt in `~/.vgrow-control/boxes/box-1/light-schedule.json`. Es gibt keine stündliche oder tägliche Befehlsquote.
Unter **Geräte → Steuerung → Licht** lädt **Automatisch** die belegten Lichtanforderungen der bestätigten
Pflanzenphase. Die Karte zeigt vor dem Tippen Ein-/Aus-Zeit, tägliche Licht-/Dunkelstunden, Ein-Helligkeit und Quellen.
Die gespeicherte Ein-Zeit bleibt erhalten; ohne gespeicherte Zeit ist 06:00 ausdrücklich als Vorschlag sichtbar.
Die Autoflower-Karte verwendet 18/6 (S6), eine bereits gespeicherte 20/4-Alternative bleibt erhalten. Ein 12/12-Plan
wird für diese Karte auf 18/6 angepasst, nicht wegen der Bezeichnung „Blüte“ übernommen. Photoperiodische Karten
brauchen ihre eigenen ausdrücklich belegten Phasenzyklen.
Der Tipp beendet eine manuelle Pause und gleicht die aktuelle Ein- oder Aus-Phase sofort ab. Danach folgt der Timer
weiteren bestätigten Phasen und deren Helligkeit. Fehlen belastbare Anforderungen, startet er keine neue Ein-Phase;
die bereits gespeicherte Dunkelphase schaltet weiterhin aus. Für neue Boxen oder nicht zugeordnete Pflanzen gibt es
keine Übernahme der Anforderungen der verbundenen Pflanze. Ein veralteter Vorschlag wird vor dem Speichern abgelehnt.
Die Auswahl folgt aus der lokalen Pflanzenkartei, ohne Modellaufruf. Die Prozentwerte sind VGrow-Voreinstellungen,
keine gemessenen PPFD-Werte; Abstand zur Lampe und das separat gewählte Spektrum bleiben zu beachten.
Handbefehle pausieren weiter bis zum nächsten Schalttermin; **Automatisch** setzt früher fort. Das Speichern eines
eigenen Lichtplans beendet das automatische Folgen der Phasen. Bestehende Pläne bleiben nach dem Update unverändert,
bis „Automatisch“ tatsächlich angeklickt wird. Deaktivieren des Timers erhält die aktuelle Helligkeit.
Unbekannte Schaltzeiten eines VIVOSUN-Plans werden weiterhin nicht aus „100 %“ oder dem Verlauf erfunden.
Gespeicherte Controllerwerte können den zuletzt bekannten Gerätestand wiedergeben; sie sind weder ein Strommessgerät
noch ein zukünftiger Plan. Im Verlaufsbalken gelten Meldungen höchstens
ein Abfrageintervall (90 s), fehlende Abschnitte bleiben unbekannt. Die Daten kommen ausschließlich aus dem lokalen
Messwertspeicher. Neue Boxen ohne Gerätezuordnung erhalten keine Daten der verbundenen Box.

**Community** (`#community`) zeigt einen öffentlichen Feed direkt im Cockpit: die jüngsten
Diskussionstitel aus r/VIVOSUN, von VIVOSUN vorgestellte Geschichten und Creator-Videos mit Bildern.
Vorschaubilder werden erst nach dem Dekodieren und einer Größenprüfung angezeigt: mindestens 640 × 360 Pixel
und genug Auflösung für Kartenbreite, 16:9-Ausschnitt und Pixeldichte des Bildschirms. Zu kleine oder fehlerhafte
Bilder entfallen; Beiträge ohne geeignetes Bild bleiben kompakte Textkarten.
Suche, Ansichten für Diskussionen/Geschichten/Videos und gespeicherte Beiträge erleichtern den Einstieg.
Videos öffnen auf Wunsch einen schließbaren Player im Cockpit. Gespeicherte Beiträge und die Suche bleiben
im Browser erhalten, beim Seitenwechsel auch die Stelle im Feed. Alle 10 Minuten wird bei geöffneter
Community aktualisiert; bei Quellenausfällen bleibt der letzte erfolgreiche Stand sichtbar und als gespeichert markiert.

VIVOSUN, Discord und Facebook haben eigene Begleitfenster. Ein erneuter Klick holt das vorhandene
Fenster nach vorn, solange der Browser dessen Fensterverweis erhält; die normale Navigation schließt es nicht.
Ein ausdrücklich gewählter Artikel bzw. die Anmeldung öffnet diese Seite im VIVOSUN-Fenster. Browser können
Fenster aus Sicherheitsgründen voneinander isolieren; nach einem Cockpit-Reload können alte Fenster nicht
zuverlässig wiedergefunden werden. Manche Mobilbrowser verwenden Tabs statt Fenstern. Für blockierte Pop-ups
gibt es einen sichtbaren Hinweis und einen normalen Link. Anmeldung und Cookies bleiben beim jeweiligen Dienst.
Das Cockpit kopiert keine Zugangsdaten und behauptet keinen verbundenen Social-Account.

Reddit öffnet normale Browser-Tabs statt Begleit-Pop-ups. „Reddit öffnet nicht?“ bietet Anmeldung und Support;
Beitragslinks lassen sich kopieren. Ohne nutzbare Zwischenablage erscheint der markierte Link zum manuellen
Kopieren. Eine Reddit-Netzwerksperre wird nicht als erfolgreicher Seitenaufruf ausgegeben und nicht automatisch
über andere Hosts oder Proxys umgangen. Das Cockpit kann die fremde Seite oder deren Anmeldestatus nicht prüfen.

Der Discord-Überblick zeigt öffentliche, ungefähre Mitglieder-/Onlinezahlen. Private Discord-Nachrichten und
Facebook-Gruppenbeiträge sind kein Teil des öffentlichen Feeds. Das Cockpit bettet keine vollständigen
Login-Seiten ein und umgeht keine Browsersperren. Einzelne YouTube-Videos verlangen eine Anmeldung oder
erlauben keine eingebettete Wiedergabe; der Player bietet dafür den Original-Link. Technik und Quellen:
[`docs/community-hub.md`](../docs/community-hub.md).

**Was sie kann (Version 1):**
- **Steuerung:** Licht an/aus und Helligkeit, Spektrum (Keimling, Wachstum, Blüte),
  Umluft und Abluft in den zehn Stufen der App, USB 1 und USB 2.
  Jeder Befehl zeigt, ob die Box ihn **bestätigt** hat (15 s), nicht nur, ob er abgeschickt wurde.
- **Verlauf:** Temperatur, Luftfeuchte, VPD (Dampfdruckdefizit), Wasserstand und
  Wassertemperatur über 6 h, 24 h oder 7 Tage.
- **Alarme:** Push aufs Handy über ntfy, wenn es zu kalt, zu warm oder zu feucht wird, das
  Wasser niedrig ist oder die Box sich nicht meldet. Einmal beim Eintreten, Erinnerung alle
  6 h, Entwarnung, wenn es wieder passt. Grenzen und Quellen stehen in `alerts.json`.
- **Düngerplan:** das BioBizz-Düngeschema 2026 als Karte (`kartei/nutrients/biobizz-2026.json`), Seite,
  vorhandene Produkte und Faktor in `plant.json` (`feeding`). Die Woche folgt der Phase, die man auf der
  Seite setzt; eine Schätzung des Blütebeginns (`bloom_start_estimate`, etwa aus dem Kamerabild) rechnet nie
  Mengen. Mengen erst, wenn die Karte auf der Seite bestätigt ist (`~/.vgrow-control/feeding.json`) und die
  Woche auch; jedes Düngen landet in `~/.vgrow-control/feeding-log.jsonl`. Auf der Seite steht das im Block
  "Düngen" der Pflanzenkarte: als Entwurf die Tabelle ab dieser Woche, darunter der Faktor, der nach dem
  Bestätigen gilt, und die offizielle Tabelle zum Vergleichen, bestätigt mit zweimal "Stimmt mit meinen
  Flaschen überein". Danach die Dosis je Liter dieser Woche, die Mengen für die Kanne (die Liter merkt sich
  das Handy, rechnen tut der Server) und "Gefüttert" für das Log.
  Seit 23.10.2026 zwei Dünger: BioBizz (ml) und das Etikett von VIVOSUN Base A + B
  (`kartei/nutrients/vivosun-base-ab.json`, g je Liter, dazu halbe Messlöffel zu 0,5 g, immer abgerundet).
  `plant.json` hält beide unter `feeding.setups`; welcher gilt, wählt man auf der Seite mit zwei Tippern (im
  Zustandsordner gespeichert), bis dahin rechnet das Cockpit nichts. Bestätigt wird je Karte. Der Start der
  Pflanze (Datum oder Alter in Wochen, Pflanzenkarte) datiert den Plan: Pflanzenwoche, Daten je Blütewoche,
  nächste Änderung der Dosis. Die Dosis setzt das Alter nie; sie folgt Phase und Blütebeginn. Nach Blütewoche 8
  endet die VIVOSUN-Tabelle: dann rechnet das Cockpit keine Menge.

**Grenzen der Herstellerintegration:** VIVOSUN-Gerätepläne anlegen oder deren native Abluftgrenzen einstellen.
Die eigenen Cockpit-Timer sind separat. Für Herstellerprogramme bleibt die Vivosun-App.

## Boxen und Pflanzen

„Boxen & Pflanzen“ ersetzt das einzelne aufgeklappte Formular durch Box-Tabs und eigene
Pflanzeneinträge. „Box hinzufügen“ erfasst Name, Typ/Standort, Plätze und Notizen; „Pflanze
hinzufügen“ legt einen oder mehrere getrennte Einträge an (bis zu 50 auf einmal). Jede Pflanze
hat einen Namen, Sorte/Art, ein manuell erfasstes Stadium, Startdatum, Höhenmessungen und ein
eigenes datiertes Journal. Pflanzen können zwischen Boxen verschoben und mit ihren Messungen
und Notizen archiviert oder wiederhergestellt werden. Boxen dürfen unterschiedliche Größen und
mehrere Pflanzeneinträge haben; die erfasste Platzzahl ist eine Beschreibung, keine Sperre.

Beim Feld Sorte schlägt das Formular die Sorten aus Lexikon und Enzyklopädie vor (`vgrow/plant_run.py`), freier Text bleibt
möglich. Eine Auswahl bringt Blühtyp, Blütezeit, Zeit vom Samen bis zur Ernte und Höhe gegen die Box mit, je mit dem Namen der
Quelle. Klima, Gießen und Dünger bleiben bei der Phasenkarte: Je Sorte stehen dazu keine Zahlen in den Daten. Der Blühtyp ist
dein Wort, die Enzyklopädie sagt ihn nicht. Ein Eintrag allein ändert keine Automatik.

Der Haken „Das Cockpit regelt nach dieser Pflanze“ tut das: Er schreibt `STATE_DIR/plant-run.json`, das ab dann `plant.json`
ersetzt (Medium, Wasserziele und Düngerwahl kommen von dort mit), legt das Protokoll der bisherigen Pflanze unter
`STATE_DIR/plant-logs/` ab und beginnt ein neues mit der gewählten Phase. Autoflower: Lichtplan der Karte (18/6 oder 20/4).
Photoperiodisch: 18/6 vor der Blüte, 12/12 in der Blüte, und die Tagesfenster führen sie nie von allein in die Blüte.
Blühtyp offen: kein automatischer Lichtplan. Zurück zu `plant.json`: die Datei auf dem Mac löschen.

Der notierte Phasenbeginn (`phase_since`) und das Datum des Eintrags (`phase_recorded`) sind getrennt.
Nur ein ausdrücklich notierter Beginn ergibt eine Dauer in Kalendertagen; fehlt er, steht dort „nicht
erfasst“. Das Speichern einer Phase bedeutet nicht, dass sie erst an diesem Tag begonnen hat. Umbenennen,
Messungen und Verschieben setzen den Zähler nicht zurück. Offene Seiten aktualisieren die Dauer beim
Tageswechsel über die nächste Statusmeldung oder bei der Rückkehr zum Tab. Beim Upgrade übernimmt das Cockpit aus einem
passenden alten manuellen Phaseneintrag nur dessen datiertes Eintragsdatum und erfindet keinen beobachteten
Beginn. Die zusätzlichen Daten stehen in der Tabelle `phase_dates`; vorhandene Einträge bleiben erhalten.
Im bisherigen Controllerprofil heißt ein manueller Datensatz deshalb „Phaseneintrag“ mit „vor … Tagen“.
Notierte Beobachtungsdaten ändern weder dieses Controllerprofil noch Geräteprogramme.

Höhen sind als manuelle Messungen mit Datum gekennzeichnet. Die Fotoauswertung liefert derzeit keinen
Höhenwert aus einem Maßband. Eine sichtbare Skala allein reicht nicht für eine verlässliche physische Höhe:
Spitze, Basis und Referenz müssen erkennbar sein; Perspektive und unterschiedliche Bildebenen können die
Messung verzerren. Ein abgeschnittenes oder verdecktes Ziel erhält keinen erfundenen Kameramesswert.

Beim ersten Aufruf übernimmt das Cockpit den vorhandenen Pflanzennamen, Start, das erfasste
Stadium und Höhenmessungen einmalig. `plant.json` und `plant-log.json` bleiben dabei unverändert.
Die neuen Einträge liegen dauerhaft in `~/.vgrow-control/workspace/inventory.sqlite3`
(in der Demo im eigenen Demo-Zustandsordner). Diese Datei gehört zum Backup des Zustandsordners;
eine konsistente Kopie bei laufendem Server mit der SQLite-Backup-Funktion anlegen. Box-/Pflanzenauswahl
merkt sich nur der jeweilige Browser. Gleichzeitige Bearbeitungen werden über eine Revision geprüft;
ein veralteter Stand überschreibt keine neueren Daten.

**Geräte bleiben bei ihrer Box.** Zurzeit ist nur die vorhandene VGrow mit einem Controller
verbunden. Neue Boxen sind zunächst Einträge ohne Geräteverbindung. Übersicht, Automatik,
Kamera, Geräteverlauf und Alarme zeigen dort keine Daten der vorhandenen Box. Ein ausdrücklich
für eine unverbundene Box gesendeter Befehl wird serverseitig abgewiesen. Zusätzliche Controller
zu verbinden ist in diesem Schritt noch nicht implementiert.

Die manuell erfassten Stadien und Stammdaten ändern keine Geräteprogramme. Das bisherige
Controllerprofil bleibt für seine vorhandenen Verbraucher bestehen und ist beim importierten
Eintrag über „Bestehendes Controllerprofil“ erreichbar. Sein Hank-Kontext und seine bisherigen
Profilansichten werden nicht auf andere Pflanzeneinträge übertragen. Der importierte Eintrag
und seine verbundene Box lassen sich deshalb nicht verschieben bzw. archivieren. Den Pflanzeneintrag kannst
du mit „Eintrag abschließen“ beenden; das verändert keine Gerätesteuerung.

**Hier starten** ist als hervorgehobener Knopf bei der Box-Auswahl erreichbar. Der Leitfaden erklärt Geräteprüfung,
Pflanzeneinträge, Ausgangspunkte (Samen, Steckling oder vorhandene Pflanze), Beobachtungen und den Abschluss.
Er setzt keine Geräteprogramme und enthält keine kulturspezifische Anbauanleitung.

**Eintrag abschließen** speichert Enddatum und Abschlussnotiz, friert das Alter ein und bewahrt eine schreibgeschützte
Momentaufnahme aller erfassten Stammdaten, Höhen, Journaleinträge und Änderungen auf. Das funktioniert auch für den
importierten Eintrag. „Verlauf & Archiv“ öffnet abgeschlossene und archivierte Einträge. Für eine neue Pflanze legst
du einen neuen Eintrag mit eigener ID an; weitere aktive Pflanzen behalten ihre Daten. Geräteeinstellungen laufen
unabhängig weiter. Alte Änderungen, die nie protokolliert wurden, werden nicht erfunden.

Der ZIP-Download enthält den vollständigen Eintrag (auch über das Vorschau-Limit von 100 hinaus) und verfügbare
gemeinsame Sensorwerte aus den protokollierten Box-Zeiträumen. Ein Umzug beendet den bisherigen Zeitraum. Die
Controller-ID bleibt am Zeitraum erhalten; ein späterer Controllerwechsel liest keine Werte des falschen Geräts.
Erst ab Einführung der Zuordnung werden diese Zeiträume protokolliert; ältere Sensorwerte bleiben im Box-Speicher,
werden aber ohne belegte Zuordnung keinem Pflanzeneintrag zugeschrieben. Fotos und KI-Auswertungen bleiben ebenfalls
bei der Box, bis es eine ausdrückliche Zuordnung gibt. Eine Abschlussnotiz ist kein Nachweis einer automatischen Analyse.

Die Daten liegen in zusätzlichen Tabellen der vorhandenen `inventory.sqlite3`; das Backup muss weiterhin den ganzen
Zustandsordner einschließlich `measurements`, `photos`, `analysis` und Aufnahmen erhalten. Einzelheiten und Grenzen:
[Einträge, Verlauf und lokales Modell](../docs/record-lifecycle.md).

Browserprüfung gegen eine isolierte lokale Demo:
`python scripts/workspace_screens.py _shots/workspace-browser` mit `VGROW_URL` als authentifizierter
localhost-Demo-URL. Sie prüft u. a. sechs Pflanzen, getrennte Journale, Verschieben, Archivieren,
Fehler/Konflikte, Kontextwechsel sowie DE/EN und helle/dunkle Ansichten bis 320 px. Sie legt nur
in der Demo `QA_`-Einträge an und sendet keine Gerätebefehle.

## Kamera (GrowCam C4)

Die Kamerarollen sind getrennt: **GrowCam** dient als Höhenreferenz mit dem Maßband, die **C920** als Detailkamera.
Auf jeder Kamerakarte steht ihre Rolle. Eine vom Nutzer am GrowCam-Maßband abgelesene Höhe lässt sich als Schätzung
speichern; das gilt sowohl für die bisherige Pflanzenkarte als auch für die neuen Pflanzeneinträge. Solche Werte
erscheinen mit `≈`, Kameraquelle und Herkunft `user_ruler_estimate`. Diese Angaben bleiben im Verlauf und Export
erhalten. Ein späterer exakter Handmesswert am selben Tag ersetzt die Schätzung einschließlich ihrer Kennzeichnung.
Eine zuverlässige Kamera-Ablesung setzt sichtbare Maßbandmarken und die höchste Pflanzenspitze voraus. Ein
abgeschnittener oder perspektivisch unklarer Ausschnitt ist keine automatische Höhenmessung. Der C920-Ausschnitt
wird nicht als Ersatz für die Höhenreferenz verwendet.

Unter **Mein Grow → KI-Einschätzung** lässt sich die Kamera für eine neue Beobachtung wählen; mit angeschlossener
USB-Kamera ist HD vorausgewählt. „Jetzt einschätzen“ nimmt ein frisches Bild genau dieser Kamera auf und nutzt den
lokalen Modellprozess. Ein Ausfall führt zu einer Fehlermeldung, nicht zum stillen Wechsel auf eine andere Kamera.
Das Ergebnis enthält Kamera, Auflösung, Aufnahmezeit, sichtbare Merkmale und eine Konfidenzangabe. Ältere Ergebnisse
ohne Kamera-Nachweis werden als solche bezeichnet. Die neue Beobachtung ist beschreibend: Sie erteilt keine
Anbauanweisungen und verändert weder Phase, Phasenstart, Höhe noch Geräteeinstellungen. Ein einzelner Ausschnitt kann
keinen genauen Phasenbeginn und keine Wassermenge feststellen. Die bisherige tägliche GrowCam-Aufnahme bleibt erhalten;
zusätzliche Bilder tragen Kamera und Zeit im Dateinamen. Chat und Bildanalyse teilen weiterhin eine Modell-Warteschlange.

Standbild alle 10 s, "Live" (MJPEG, 5 Bilder/s aus dem Nebenstrom 800x448, läuft, solange die Seite
offen ist, höchstens 12 h am Stück, `LIVE_MAX_SECONDS` in `vgrow/camera.py`)
und Aufnahmen aus dem Hauptstrom 2560x1440 ohne Neukodierung (höchstens 2 h, mindestens 5 GB frei),
abgelegt auf dem Mac in `~/.vgrow-control/recordings/`. **Immer ohne Ton:** die Kamera hat ein
Mikrofon, und nichtöffentlich Gesprochenes anderer aufzunehmen ist nach § 201 StGB strafbar.

- Zugangsdaten der Kamera kommen aus dem Vivosun-Konto, die Adresse aus `GROWCAM_IP` in
  `~/.vgrow.env` (sonst sucht der Server das Heimnetz ab). Am besten die Adresse in der
  FRITZ!Box fest vergeben.
- ffmpeg kommt als Python-Paket `imageio-ffmpeg` in die venv, kein Homebrew nötig.
- **macOS "Lokales Netzwerk" (einmalig, nur am Bildschirm):** Programme von Drittanbietern
  brauchen ab macOS 15 eine Erlaubnis für Geräte im Heimnetz. Ohne sie meldet Python
  `No route to host`, das Internet und der Router gehen trotzdem. Systemeinstellungen →
  Datenschutz & Sicherheit → Lokales Netzwerk → **python3.12** einschalten (erteilt am
  22.10.2026). Über SSH lässt sich das nicht setzen. Wer Python per `brew upgrade` tauscht,
  muss den Schalter womöglich neu setzen.

## Wie es funktioniert

Vivosun hat keine offene Schnittstelle. Die Seite spricht dieselbe Cloud an wie die App:
REST für Login und Messwerte, MQTT über AWS IoT für Befehle und Gerätestatus. Das
Cockpit fragt den Gerätestatus zusätzlich alle **10 Minuten** lesend ab, auch ohne geänderte Einstellungen.
Dieser Hintergrundlauf ist unabhängig von den Sensorabfragen alle 90 Sekunden. Der Status-Zeitstempel wird erst
mit einer empfangenen Gerätestatus-Antwort aktualisiert; bei Verbindungsproblemen bleibt das tatsächliche Alter sichtbar.
Die Abfrage schaltet keine Geräte, und ein Fehler beendet den nächsten geplanten Versuch nicht. Das
Protokoll stammt aus [lientry/homeassistant-vivosun-growhub](https://github.com/lientry/homeassistant-vivosun-growhub)
(MIT), unverändert eingebunden in `vgrow/vivosun/`, siehe `vgrow/vivosun/UPSTREAM.md`.

**Inoffiziell.** Ändert Vivosun die App, kann der Login brechen, bis Upstream nachzieht.
Die Alarme der Vivosun-App bleiben das Sicherheitsnetz.

## Einrichten (MacBook, der Server)

Das MacBook ist die Maschine, die 24/7 läuft (schläft am Netzteil nie, siehe
`Notes/macbook-brain-host-setup.md` im Brain). Der Mac hat keinen GitHub-Zugang für dieses
Repo, deshalb kommt der Code per git direkt vom PC:

```
# einmalig auf dem PC
git remote add mac ssh://<mac-user>@<mac-host>/Users/<mac-user>/vgrow-control
# ausliefern, jedes Mal
git push mac main && ssh <mac-user>@<mac-host> "cd ~/vgrow-control && bash scripts/install_mac.sh"
```

Auf dem Mac nimmt das Repo Pushes direkt in den Arbeitsstand an
(`receive.denyCurrentBranch updateInstead`). `install_mac.sh` legt die venv an und lädt den
launchd-Dienst `de.vgrow.control` (startet beim Hochfahren, startet nach Absturz neu).

**Zugangsdaten** in `~/.vgrow.env` auf dem Mac, nie im Repo:
```
VIVOSUN_EMAIL=...
VIVOSUN_PASSWORD=...
NTFY_SERVER=...
VGROW_NTFY_TOPIC=...
```
Am bequemsten am PC in `%USERPROFILE%\.vgrow.env` eintragen und per SSH rüberkopieren.
Optional `VGROW_LANG=en`: Sprache für Pushes und die tägliche KI-Einschätzung (Standard `de`). Wer auf
der Seite die Sprache umstellt, legt sie in `~/.vgrow-control/settings.json` ab, und die gilt dann vor
`VGROW_LANG`.
Optional für das lokale Sprachmodell (Ollama, siehe "Hank"): `OLLAMA_URL` (Standard
`http://127.0.0.1:11434`), `HANK_MODEL` oder `VISION_MODEL` (Standard `gemma4:12b`), `HANK_EMBED_MODEL`
(Standard `granite-embedding:278m`).
**Google-Login reicht nicht:** der Server meldet sich selbst an, rund um die Uhr, und kann das nur
mit E-Mail und Passwort. Wer sich in der App mit Google anmeldet, braucht für das Konto ein
Passwort (eingerichtet am 22.10.2026).

**Test, nur lesend**, schickt keinen Befehl:
`ssh <mac-user>@<mac-host> "cd ~/vgrow-control && .venv/bin/python -m vgrow --check"`

**Handy:** den Link einmal öffnen (enthält den Zugangsschlüssel), dann "Zum Home-Bildschirm".
Über Tailscale `http://<tailscale-ip>:8742/?key=…`, im Heim-WLAN auch die LAN-Adresse.
Der Schlüssel liegt auf dem Mac in `~/.vgrow-control/access-key`, das Log in
`~/.vgrow-control/server.log`. Im Log steht der Schlüssel nicht (siehe Schutzregeln), den Link baut man
aus der Datei: `echo "http://<tailscale-ip>:8742/?key=$(cat ~/.vgrow-control/access-key)"`.

**Steuern:** `launchctl list | grep vgrow` (läuft?), neu starten mit
`launchctl kickstart -k gui/$(id -u)/de.vgrow.control`.

Auf Windows geht es auch (`scripts\start.cmd`, `scripts\autostart.ps1`), aber nur einer von
beiden soll laufen, sonst kommt jeder Alarm doppelt.

## Entwickeln

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
.venv\Scripts\python.exe -m pytest              # offline, kein Login
.venv\Scripts\python.exe -m vgrow --demo         # erfundene Werte, gelbes DEMO-Banner
```

Die Demo zeigt jede Karte ohne Box: Platzhalter-Kamera ("DEMO-Kamera", auf Englisch "DEMO camera",
keine Aufnahmen), eine
Demo-Einschätzung (Modell "demo"), eine Befeuchter-Automatik mit Verlauf und eine gefüllte
Pflanzenkarte. Die Phase der Demo ist nur geschätzt, deshalb pausiert die Automatik nach der ersten
Abfrage, bis man den Vorschlag bestätigt (Knopf in der Autopilot- oder der Pflanzenkarte, zweimal tippen), wie
im echten Betrieb. Die Düngertabelle startet bei jedem Demo-Start als Entwurf; zweimal "Stimmt mit meinen
Flaschen überein" bestätigt die Tabelle, und weil die Phase nur geschätzt ist, kommen die Mengen, sobald man
auch die Phase bestätigt (Knopf im Block "Düngen"). Die Demo hat ihren eigenen Zustand in `~/.vgrow-control/demo/` (Windows
`%LOCALAPPDATA%\vgrow-control\demo`), mit eigenem Schlüssel, und fasst den echten Durchgang nie an.
Pushes aus der Demo tragen "DEMO" im Titel.

## Hank-Protokoll: Problem, Aufgabe und Ergebnis

Unter **Aktivität → Hank-Protokoll** stehen aktuelle Probleme und die dazugehörigen Aufgaben. Links führen
auch aus dem Aktionszentrum und aus Hanks Fenster dorthin. Ein Problem zeigt Messwert, Zielbereich und die
wirkliche Entscheidung des zuständigen Controllers, einschließlich Übernahmepause, Gerätegrenze,
Verbindungsproblem oder fehlender Pumpeneinrichtung. Filter trennen Automatik, angenommene Hank-Vorschläge,
manuelle Befehle, Lichttimer, Gießtimer, Schutzabschaltungen und gemeldete Änderungen außerhalb des Cockpits.

Eine angenommene HTTP-Anfrage, die Bestätigung des Gerätezustands und die spätere Entwicklung der Messwerte
sind getrennte Schritte. Ohne Bestätigung nach 20 Sekunden steht „nicht bestätigt“. Für Klimaaufgaben zählt
erst ein späterer frischer Messwert als Beobachtung. Das Protokoll zeigt Annäherung, Verschlechterung,
Überschreiten auf die andere Seite oder nach 30 Minuten keine deutliche Verbesserung. Eine neue Aufgabe am
selben Gerät oder ein Wechsel von Pflanze/Phase beendet die Zuordnung zur vorherigen Aufgabe; nach höchstens
zwei Stunden endet deren Beobachtung. Andere Geräte und die Raumluft können ebenfalls zu einer Änderung
beitragen. Bestätigte USB-Leistung beweist weder Wasserfluss noch Nebel.

Der Server beobachtet alle drei Sekunden, auch ohne geöffnete Seite. Die lokale SQLite-Datei
`~/.vgrow-control/boxes/box-1/hank-protocol.sqlite3` erhält Probleme, Entscheidungen und Aufgaben über Neustarts.
Sie gehört zum Zustandsbackup. Alte Journale werden übernommen, fehlende Gerätebestätigungen oder spätere
Messungen bleiben ausdrücklich unbekannt. Über „Ältere laden“ lässt sich weiter zurückblättern. Eine
unterbrochene Verbindung zeigt einen Hinweis und lässt bereits geladene Einträge sichtbar. Neue, nur im
Workspace angelegte Boxen erhalten keine erfundene Protokollhistorie des angeschlossenen Controllers.

Der Beobachter sendet selbst keine Gerätebefehle und verwendet kein Sprachmodell. Die Controller und ihre
Einstellungen bleiben für die eigentliche Regelung zuständig. Ein Speicherfehler im Protokoll wird sichtbar,
blockiert aber keinen Gerätebefehl. `GET /api/protocol` und die Zähler unter `GET /api/state` benötigen dieselbe
Anmeldung wie das Cockpit.

## Hank (Chat-Assistent, lokal)

Hank beantwortet Anbaufragen aus der Kartei (`kartei/`) und dem Live-Stand der Box auf dem Mac, ohne
kostenpflichtige Inferenz-API (einzige Ausnahme, nur mit Schlüssel: die Jev-Beobachtung unten). Die
Gerätesteuerung nutzt weiterhin die VIVOSUN-Cloud.
Bauplan: `docs/hank-v1-spec.md`, verbindlicher Nachtrag `docs/hank-v1-nachtrag.md`.

- **Das Sprachmodell schaltet nie.** Ein Vorschlag im Chat ist eine Karte auf der Seite; wer sie antippt,
  schickt einen normalen Befehl an `/api/cmd`, mit allen Sperren des Cockpits.
  Bei Licht, Lüfter und USB wartet die Karte danach auf den gemeldeten Zielwert: „Gesendet“ ist noch keine
  Gerätebestätigung. Sie zeigt anschließend „Von der Box bestätigt“, eine Ablehnung oder nach spätestens
  20 Sekunden ohne passende frische Meldung „Keine Bestätigung erhalten“. Die Prüfung sendet keinen weiteren
  Schaltbefehl. Lokale Einstellungen werden als „Gespeichert“ angezeigt. Ein früherer fehlgeschlagener
  Befeuchter-Wirkungstest wird als frühere Prüfung beschrieben und behauptet keinen aktuellen USB-Stromzustand.
- **"Hank regelt" ist der Schalter des Autopiloten** (`vgrow/hankpilot.py`, `web/hank.js`), kein zweiter. Aus,
  Beobachten und Automatik im Hank-Fenster schicken dieselbe Aktion `autopilot_mode` wie die Autopilot-Karte und
  lesen denselben Stand (`/api/state`, `autopilot.boxes[].hank`); es gibt keinen eigenen Speicher und keine eigene
  Regel. Automatik braucht auch hier zwei Tipps, Aus und Beobachten einen. Darunter steht, was ihn aufhält
  (Phase, USB 2, Befeuchter-Regel, Lampenabstand, Anschlüsse), jeweils mit dem Schritt: der Tipp darauf ist der
  erste der zwei Tipps und öffnet die bestehende Bestätigung in der Karte, gesendet wird erst dort. Unter jedem
  Hinweis steht, ob Hank das gerade regelt, was er im Beobachten tun würde oder welcher Schritt fehlt. Der
  Befeuchter folgt weiter nur seiner eigenen Regel mit eigenem Schalter. Steht der Chat auf "aus", regelt der
  Autopilot in seiner Stellung weiter.
- **Hank ist Gast.** Der Server baut ihn als Letztes. Scheitert der Bau oder der Start, steht das im Log, und
  Cockpit, Alarme, Befeuchter-Automatik und die Anschluss-Sperre laufen ohne ihn weiter. In der Abfragekette der
  Box (`poll_handler`) hängt nichts von Hank. Ohne Hank gibt es nur seine Wege `/api/hank/*` nicht.
- **Ein Modell für alles:** Hank und die tägliche Foto-Einschätzung nehmen dasselbe Modell, weil sich zwei
  Modelle mit je etwa 8 GB auf 16 GB gegenseitig aus dem Speicher werfen. Der Name kommt aus `HANK_MODEL`, sonst
  `VISION_MODEL`, sonst `gemma4:12b` (vorher nahm das Foto `gemma3:12b`). Steht auf dem Mac noch
  `VISION_MODEL=gemma3:12b` in `~/.vgrow.env`, gilt das weiter, für beide. Das Modell braucht
  `ollama pull gemma4:12b`, die Suche nach Bedeutung `ollama pull granite-embedding:278m`; ohne das zweite
  Modell sucht Hank nur über Wörter und Buchstabenstücke, ohne Bedeutungsvergleich.
- **Ein Modellauftrag zur Zeit:** das Foto wartet, bis eine laufende Chat-Antwort fertig ist. Der Chat wartet nie
  auf das Foto, er antwortet dann mit dem Entwurf aus dem Code. Nach dem Foto wird das Modell wie bisher sofort
  entladen (`keep_alive` 0).
- **Modus** "an", "nur Warnungen", "aus" steht in `~/.vgrow-control/settings.json` unter `hank_mode`. Die Datei
  wird in einem Schritt geschrieben (auch beim Umstellen der Sprache), damit eine halb geschriebene Datei nie
  still Sprache und Modus zurücksetzt.
- **Zustand** in `~/.vgrow-control/hank/`: Lückenliste, Rückmeldungen, gelernte Schreibweisen, Empfehlungsbuch,
  Chatverlauf, Messlog und das Journal `actions.jsonl` (jede Schaltung mit Herkunft: Mensch, Automatik oder
  angenommener Hank-Vorschlag). Nichts davon verlässt den Rechner.
- **Jev-Beobachtung** (`vgrow/hank/jev.py`, seit 28.10.2026): steht `TYPESAFE_API_KEY=...` in `~/.vgrow.env`,
  geht jede Chatfrage nach der Entscheidung des Routers zusätzlich an TypeSafes Jev (api.typesafe.ai, in den USA
  betrieben; laut Datenschutzerklärung kein Training auf Eingaben). Jev beurteilt vier Dinge (Pflanzen, Box, App,
  Regeln zum Eigenanbau); eine Zeile je Frage mit den vier Werten und dem Weg des Routers landet in
  `~/.vgrow-control/hank/jev-watch.jsonl`. **Nur Beobachtung:** keine Antwort ändert sich, die Antwort wartet nie
  darauf, ein Fehler ist eine Zeile mit `error`, nie ein Nein. Ohne Schlüssel geht nichts hinaus; der Schlüssel
  wird je Frage gelesen, Eintragen braucht keinen Neustart. Kosten: etwa 0,003 Cent je Frage (0,042 USD je Million
  Eingabe-Token, gemessen 28.10.2026). Auswerten: `.venv/bin/python scripts/jev_watch_report.py`. Messung vorab:
  Zweig `claude/jev-frontdoor`, `experiments/jev-frontdoor/`.
- **Demo:** `python -m vgrow --demo` zeigt Hank ohne Ollama, nur mit Antworten aus dem Code.
- **Messwerte vom Mac** (gemessen 23.10.2026, Tunnel vom PC; "Wand" enthält den Weg durch Tunnel und LAN,
  "Server" sind Ollamas eigene Zeiten; ganz in `docs/hank-v1-messung.md`):
  - Einbettung `granite-embedding:278m`: warm je Frage Server p95 0,23 s (Wand 0,28 s), keine von 64 Fragen über
    dem Limit 0,8 s; kalt 1,8 bis 2,1 s; alle 93 Passagen 15,7 s (Stapel zu 16).
  - `gemma4:12b` mit Hanks echtem Blatt (bis 500 Token, `num_predict` 100, `think` false, `num_thread` 3): kalt
    72,6 bis 82,8 s (Laden 22 bis 24 s); warm 45 bis 57 s je Antwort. Lesen etwa 12 Token/s, Schreiben 4 Token/s.
  - Zwischenspeicher: nur ein wortgleiches Blatt wird schnell gelesen (1,1 s statt 40 s). Ein neues Blatt mit
    demselben Systemtext wird ganz neu gelesen.
  - Speicher: gemma4 6,3 GB, Granite 568 MB laut `ollama ps`; mit beiden geladen 0,25 GB frei und etwa 2 GB
    ausgelagert.
  - Prüfsatz mit Modell (15 Fälle): 13 bestanden, 0 Verstöße gegen `must_not`, Streichquote des Prüfers
    32,4 %; 4 Blätter passen nicht in 500 Token, dort bleibt der Entwurf.
- **Schwellen kalibriert** (`python scripts/hank_calibrate.py --url ... --write`, 711 Fälle, Werte in
  `vgrow/hank/data/thresholds.json`). Hank sucht mit dem dichten Satz, solange alle Kartenvektoren da sind
  (`GET /api/hank/state`: `embed.search` "dense"), und mit dem lexikalischen, wenn das Einbettungsmodell fehlt;
  `embed.last_query` zeigt, ob die letzte Frage zurückfiel (`off`: ein Modellauftrag hielt die CPU, `timeout`,
  `missing`). Zwei neue Zeilen im Router weisen Alltagsfragen ab, die über ein einzelnes Kartenwort an eine
  Anbaufrage heranreichen ("Wie spät ist es?"): dicht über den Abstand zum nächsten Offtopic-Beispiel, lexikalisch
  nur für die Sätze aus `offtopic.json` und nahe Kopien.
- **Messen auf dem Mac:** `python scripts/hank_bench.py --url http://127.0.0.1:11434 --ssh <user@mac>` und
  `bash scripts/hank_eval_mac.sh` (von Hand). Ein nächtlicher Lauf ist absichtlich nicht eingerichtet (Nachtrag
  D10); `scripts/de.vgrow.hank-eval.plist.template` ist nur eine Vorlage.

## Wiki-Navigation

Die Wiki-Übersicht (`#wiki`) ist ein kompaktes Verzeichnis mit Kategorie, Anzahl und Kurzbeschreibung.
Sie wiederholt darunter keine Artikellisten. Nährstoffprobleme, Schädlinge, Krankheiten und Stress haben
jeweils eine eigene Seite (`#wiki/topic/...`) mit lokaler Suche und alphabetischen Artikeln. Die globale Suche
findet auch die Sortenreferenzen. Artikel haben Brotkrumen und ein Inhaltsverzeichnis. Browser-Zurück, DE/EN,
Tastatur und Handyansicht bleiben verfügbar. Die Herstellerkartei mit 205 Produktprofilen bleibt erreichbar.

### Sorten-Enzyklopädie und Pflanzenpathologie (25.10.2026)

`#wiki/varieties`, `GET /api/wiki/varieties`: 100 alphabetische Sortenporträts aus
[Leaflys redaktioneller Auswahl](https://www.leafly.com/news/strains-products/top-100-marijuana-strains),
keine weltweite Beliebtheitsrangliste. Jedes Porträt nennt seine eigene Referenzseite und Abrufdatum.
Enthalten sind Quellenbezeichnung Indica/Sativa/Hybrid, berichteter THC-Median, bekannte Eltern,
weitere Namen, führende Terpene und in der Quelle genannte Nachkommen. Der THC-Median ist weder eine
Laboranalyse der eigenen Pflanze noch ein garantierter Sortenwert. Unbelegte Prozentanteile und
Samen-/Klonstatus werden nicht geraten. Namensgleiche Produkte aus der alten Kartei erscheinen getrennt
mit ihrem dokumentierten Blühtyp; genetische Identität wird daraus nicht abgeleitet.

Die 100 Einträge haben Quellbilder: 95 sortenspezifische Fotos und fünf ausdrücklich gekennzeichnete
Beispielbilder. Sie laden bei Bedarf direkt von zugelassenen Leafly-Bildhosts, ohne Referrer, mit Quellenlink
und Hinweis auf die ursprünglichen Rechte. Es werden keine fremden Fotos als freie Lizenz ausgegeben oder
ins Repository kopiert. Bei Bildfehlern bleiben Quellenangabe und Ersatzansicht verfügbar.
Die Daten liegen in `kartei/varieties/popular.json`; `scripts/import_variety_reference.py` ist ein manueller
Rechercheimport, kein Serverjob. Er übernimmt keine Wirkungs-/Heilversprechen, Verkaufstexte oder Anbauprotokolle.

`kartei/health/conditions.json` ergänzt 22 allgemeine botanische Referenzen aus RHS, Universitäts-Extension
und öffentlichen Fachquellen: insgesamt jetzt 11 Krankheiten, 15 Schädlinge, 13 Stressbilder und unverändert
10 Nährstoffprobleme. Die neuen Texte beschreiben Merkmale, Ursachen und Verwechslungen mit Hinweis auf
das Wirtsspektrum. Sie sind keine automatische Diagnose und enthalten keine Cannabis-Anbauprotokolle.
Die ältere Kartei und die Automatik werden durch diese Erweiterung nicht geändert.

Prüfung: `pytest tests/test_wiki_encyclopedia.py` und `scripts/lexicon_screens.py` gegen eine isolierte lokale
Demo (Suche, Filter, Paging, Zurück, Bilder und Fehlerersatz in DE/EN, hell/dunkel und 320 bis 1920 px).

### Lesen, Vergleichen und Redaktion

Die Werkzeugleiste führt zu Vergleich, botanischem Atlas, Sammlung und Redaktion (`#wiki/tools/...`).
Zwei bis vier Referenzsorten lassen sich mit ihren belegten Angaben nebeneinander ansehen. Quellen stehen
direkt am jeweiligen Feld; abweichende Angaben bleiben sichtbar, etwa die unterschiedliche Einordnung
von Ice Cream Cake in Leaflys Sortenprofil und redaktionellem Artikel. Der berichtete Stammbaum verlinkt
eindeutig zuordenbare Namen im Katalog. Unbekannte oder mehrdeutige Namen werden gekennzeichnet.
Galerien zeigen bis zu acht zugelassene Quellfotos mit Tastaturbedienung, Vergrößerung und Bildnachweis.
Der Atlas beginnt mit sechs bereits vorhandenen, lizenzierten Fotos und manuell markierten Merkmalen;
Pflanzenart, Vergleichsfoto und Grenzen der Bildaussage stehen beim Foto. Es ist keine automatische Diagnose.

Favoriten, Sammlungen, die Vergleichsauswahl und zuletzt gelesene Artikel werden außerhalb des Repositories
in `<STATE_DIR>/wiki/reading.sqlite3` gespeichert. Sie gehören zum gemeinsamen Cockpit, nicht zu einzelnen
Benutzerkonten. Die Datenbank gehört in die Datensicherung des Zustandsordners. Alle neuen API-Routen liegen
unter `/api/wiki/tools/` und verwenden den vorhandenen Cockpit-Zugriffsschutz. Sie steuern keine Geräte.

Korrekturen benötigen eine aktuelle Artikelversion und eine Begründung; Faktenkorrekturen zusätzlich einen
HTTPS-Quellenlink. Einreichen verändert keinen Artikel. Erst eine ausdrückliche Annahme mit Prüfnotiz
übernimmt die Angabe als lokal geprüfte Ergänzung, neben den ursprünglichen Quellenbehauptungen. Eine seitdem
veraltete Vorlage kann nicht angenommen werden. Alle mit Cockpit-Zugriff können prüfen; es gibt keine
zusätzliche Benutzer- oder Rollenverwaltung. Die Referenzdateien im Repository werden nicht überschrieben.

Der Artikelverlauf beginnt beim ersten erfassten Aufruf. Er speichert nur Änderungen und angenommene
Korrekturen; frühere Bearbeitungen werden nicht rekonstruiert. Bis zu 50 Versionen sind vergleichbar.
Die Redaktion zeigt offene Vorschläge, letzte Prüfentscheidungen und Lücken bei Bildern, Quellen,
Abstammung, Verfügbarkeit sowie Quellenwidersprüche. „Quellenlinks prüfen“ startet ausschließlich von Hand
maximal zwölf HEAD-Anfragen an im Repository hinterlegte HTTPS-Quellen. Private IPs und Weiterleitungen
werden nicht aufgerufen. 404/410 gilt als fehlender Link, Sperren oder Zeitüberschreitungen als unbestätigt.
Es läuft kein zusätzlicher Recherchejob im Hintergrund.

Prüfung: `pytest tests/test_wiki_tools.py`; `scripts/wiki_tools_screens.py` ausschließlich gegen eine lokale
Demo (persistente Sammlungen, Review, Versionsvergleich, Tastaturfokus und DE/EN in beiden Designs).

## Sortenlexikon und Sortenberatung (`vgrow/strains.py`, `vgrow/hank/strains.py`)

Seit 23.10.2026: 205 Sorten mit den Anbaudaten der Hersteller in `kartei/strains/lexikon.json` (aus hanfgarten.at
und barneysfarm.de, privat gesammelt, abgerufen 23.10.2026), Paket Cannabis, Entwurf, `private_only`. Nur Anbaudaten:
Typ, Genetik, Blütezeit, Samen bis Ernte, Höhe, Ertrag, Schwierigkeit, Wuchsform, jede Angabe mit ihrer Quelle und
"Herstellerangabe, ungeprüft". Nie Inhaltsstoffe, Wirkung, Geschmack, Shop-Texte, Shop-Fotos oder Preise; elf Sorten
fehlen, weil ihr Name einen Inhaltsstoff, ein Gesundheitswort, eine Droge oder eine Wirkung nennt (LSD, Ayahuasca,
Green Crack, Euforia, Laughing Buddha, Strawberry Cough und die drei CBD-Namen). Die eigene Karte der laufenden Pflanze
(`kartei/strains/ice-cream-cake-auto.json`) bleibt die eigene: der Lexikoneintrag desselben Produkts zeigt mit
`same_as` auf sie, ihre Zahlen stehen in ihrem Block `grow`.

- **In die Box** (VGrow, 92 cm Wuchshöhe für eine Pflanze, S1 der Stadienkarte): *passt sicher* (indoor höchstens
  92 cm), *mit Training* (das untere Ende passt, das obere nicht: LST), *zu hoch* (schon das untere Ende darüber),
  *Höhe unklar* (keine Angabe, oder nur "bis X" über 92 cm).
- **Wiki, Bereich Sorten** (`#wiki/sorten`, `GET /api/wiki/strains`): Suche nach Name, Marke, Genetik; Filter für
  die Box, Autoflower oder photoperiodisch, Blütezeit, Samen bis Ernte, Schwierigkeit, Marke; eine kompakte Liste in
  Seiten zu 24 ("Mehr laden"). Ein Eintrag zeigt seine Box-Gruppe und unter jeder Angabe Quelle und Hinweis; Shop- und
  Züchterseiten stehen als Adresse mit Datum, nie als Link.
- **Hank** (Route `strains`, aus Code, ohne Modell): "Welche Sorte passt in meine Box?", "Welche Autoflower für
  Anfänger?", "Ich will eine kurze Blüte". Höchstens zwei kurze Fragen, nacheinander (Autoflower oder photoperiodisch;
  erster Grow oder Erfahrung, oder die längste Zeit bis zur Ernte), im Chat gemerkt. Dann drei Vorschläge: passt
  sicher vor mit Training, dann die Wünsche, dann die Schwierigkeit, jeder mit Höhe, Zeit, Schwierigkeit, Grund und
  Quelle. "Was weißt du über ...?" antwortet aus dem Eintrag. Kauffragen beantwortet das Verkaufstor: welche Sorte und
  wie man sie kauft, entscheidet die Person; THC-Fragen das Gesundheitstor. Paket Cannabis aus: Lexikon aus, im Wiki
  und für Hank.
- **Nie öffentlich:** eine öffentliche Fassung lädt das Lexikon leer (`strains.load(public=True)`), und
  `scripts/build_preview.py` bricht ab, wenn ein Schlüssel oder eine Quelladresse daraus in der Vorschau stünde.

## Automatische Bewässerung

Unter **Automatik → Automatische Bewässerung** lassen sich für USB 1 (Pumpe) eine Laufzeit in Sekunden,
Uhrzeiten in **Europe/Berlin**, ein erster Gießtag und ein Abstand zwischen Gießtagen speichern. Es gibt
keine voreingestellte Gießdauer und keinen voreingestellten Wasserbedarf. Automatische Starts sind zunächst aus.
Vor dem Einschalten muss geprüft sein, dass USB 1 die Pumpe und den tatsächlichen Wasserfluss an und aus schaltet.
„Einmal gießen“ verwendet ebenfalls die gespeicherte Laufzeit; ein bestehender Pumpenlauf wird nicht übernommen.

Ein eigener Wächter prüft jede Sekunde den Stopptermin, unabhängig vom 90-Sekunden-Sensorpoll und von Hank.
Ohne frisches Wasser am Sensor, bestätigte Pumpenzuordnung, manuellen USB-Modus und Verbindung startet kein Lauf.
Der Start wird vor dem Senden gespeichert. Beim Neustart wird ein unterbrochener Lauf gestoppt, nie fortgesetzt.
Eine neue USB-Aus-Meldung bestätigt den Stopp; unbestätigte Stopps werden wiederholt und nach 30 Sekunden gemeldet.
Starts und Stopps werden im gemeinsamen Befehlsprotokoll erfasst; es gibt keine stündliche oder tägliche Befehlsquote.
Verpasste und gesperrte Termine entfallen. Mehrere Uhrzeiten sind getrennte Läufe; ein laufender Zyklus sperrt weitere Starts.
Beim Sommerzeitwechsel entfällt eine nicht existierende Uhrzeit, eine doppelte Uhrzeit läuft nur beim ersten Auftreten.
Der Countdown und der nächste Termin stehen auf der Seite und können von Hank gelesen werden.

Der Mac und die Cloud-Verbindung müssen für den Stopp erreichbar bleiben: Dies ist kein im Pumpengerät gespeicherter
Timer. USB-Leistung bestätigt weder Durchfluss noch eine Wassermenge. Die Daten liegen unter
`~/.vgrow-control/boxes/box-1/watering.json`. Änderungen am Gießplan schalten keine fremden manuellen Läufe ab;
der Knopf „Pumpe jetzt stoppen“ ist dafür ausdrücklich verfügbar.

## Durst-Index und Bedarfsprüfung

Neben dem Gießplan steht unter **Automatik** die Karte **Durst-Index** (`vgrow/thirst.py`): wie viel Wasser dem Topf
fehlt (0 frisch gegossen, 100 Zeit zum Prüfen), wann er voraussichtlich fällig ist, etwa wie viel, und der Gießplan
nach Phase für die nächsten Wochen. Ohne eingetragenen Topf steht dort zuerst „Topf einrichten“; einen
voreingestellten Topf gibt es nicht. Eine Prüfung ist ein Tipp: Finger 2,5 oder 5 cm tief (trocken, feucht, nass),
Topf anheben oder wiegen.

Vor jedem automatischen Termin fragt der Gießplan die Bedarfsprüfung. Der Termin läuft nur, wenn der Topf gemessen
fällig ist (Index 100 oder mehr, Prüfung oder Wägung in den letzten 24 h, keine Nässesperre, keine Sperre). Sonst
entfällt er mit dem Grund im Verlauf, etwa „Topf noch nicht fällig: Durst-Index 62 %“, und wird nicht nachgeholt.
Mit bestätigtem Pumpenschalter und gemessenem Durchfluss wird der Lauf auf die Gabe gekürzt, höchstens auf die
Höchstmenge der Phase; ohne gültige Durchflussmessung oder bestätigten Pumpenschalter entfällt der automatische Termin. Verlängern oder starten kann
die Prüfung nie. „Einmal gießen“ fragt sie nicht, zeigt aber den Index und einen Hinweis, wenn der Topf nicht fällig ist.
Ein voller Tank ist nie ein Grund zu gießen. USB-Strom zählt erst als Wasser mit bestätigtem Pumpenschalter und
gemessenem Durchfluss. Einzelheiten, Quellen und Annahmen: [Durst-Index](durst-index.md),
`kartei/water_model.json`.

## Pumpenverlauf für Hank

Hank beantwortet „Wurde heute schon gegossen?“ und „War die Pumpe heute schon an?“ aus dem lokalen Pumpenverlauf
(`vgrow/pump_history.py`, `/api/pump-history`), ohne Sprachmodell oder Kartei-Suche. Die bestätigte Zuordnung ist
USB 1 = Pumpe, USB 2 = Befeuchter. Gemeldete USB-Schaltungen aus der VIVOSUN-App werden sofort gespeichert,
zusätzlich zu den Abfragen alle 90 Sekunden. Nur gemeldete Zustände zählen; ein angeforderter oder abgelehnter
Befehl ist kein Lauf. Vorhandene Messzeilen werden als Stichproben gelesen, vor der bestätigten Zuordnung ohne
Behauptung eines Pumpenlaufs. Der Tag folgt der lokalen Serverzeit (Mac: Europe/Berlin, mit Sommerzeitwechsel).
„Kein Lauf erfasst“ beweist kein ausgebliebenes Gießen; Lücken und eine unbekannte Wassermenge werden benannt.
Bei Gießfragen erhält auch Hanks Beratung diesen Verlauf als feste Tatsache. Diese Erfassung schaltet keine Pumpe.

## Wasser & Boden, Bodensensor und Geräte-Check

Die Seite `#water` (`web/water.js`) zeigt Bodenfeuchte mit Zielband, Tank, Gegossenes, Durst-Index und den Geräte-Check.

- **Bodensensor:** `ECOWITT_IP` in `~/.vgrow.env` nennt das Gateway (GW1200), am besten die Adresse im Router festlegen. Der
  Server fragt es jede Minute über `GET /get_livedata_info`, ohne Login und ohne Cloud (`vgrow/soil.py`, Log und Einstellung
  unter `STATE_DIR/soil/`). Das Handbuch dokumentiert diese Seite nicht: am 06.11.2026 am eigenen Gerät gesehen, Firmware
  GW1200A_V1.4.7.
- **Einstellen:** Rund um den Sensor gießen, zwei Stunden abtropfen lassen, „Topf ist gegossen und abgetropft“ zweimal tippen.
  Ohne Einstellung bleibt es beim Rohwert, und nichts entscheidet danach. Selbsttest laut WH51-Handbuch: an der Luft 0, im
  Wasserglas 90 oder mehr.
- **Geräte-Check:** `scripts/check_devices.py --owner-asked` auf dem Mac, nur auf Wunsch und nach einem Blick aufs Kamerabild.
  Workspace und `plant.json` zeigen nur Eingetragenes: am 06.11.2026 stand dort „leer“, in der Box eine junge Pflanze. Der Lauf
  schaltet Licht und Lüfter um, stellt alles zurück und bricht über 30 °C oder 70 % ab. Die Pumpe bleibt draußen.
- **AeroWave E6 am Stick GrowHub E25** (`vgrow/fan_stick.py`): eigene MQTT-Verbindung, derselbe Login wie die Box. Die Regel
  „Folgt dem Licht“ ist aus, bis du sie einschaltest. Aus, hier oder in der App, schaltet sie ab; eine andere Stufe in der
  App pausiert sie eine Stunde. Mitlesen ohne Schalten: `scripts/capture_accessory.py`.

## Befeuchter-Tank und Nachfüllen

Unter **Automatik → Befeuchter-Tank** steht der separate USB-2-Tank. Das bestätigte Modell ist der
**VIVOSUN AeroStream U075 / VSE-ASU075** mit **750 ml**. Sein Profil nennt die Herstellerangaben separat:
30–50 ml/h, bis zu 18 h Dauernebel oder 28 h Intervallnebel. Das ersetzt keine Verbrauchskalibrierung.
Den am Gerät gewählten Nebelmodus kann man in den Tank-Einstellungen vermerken; die Auswahl schaltet ihn nicht um.
Die eingebaute Abschaltung bei Wassermangel meldet keinen Tankfüllstand an das Cockpit.
Laut [Herstelleranleitung](https://www.vivosun.com/support/guide/aerostream-usb-humidifier) startet das Gerät
mit USB-Strom im gemerkten Modus; falls es nicht wieder anläuft, nach dem Nachfüllen den Docht sättigen lassen
und den Netzschalter drei Sekunden halten. Ein dokumentierter Auto-Start ist kein bestätigter Nebelstart vor Ort.
Die Korrektur einer ungefähren Tankgröße bewahrt eine bereits bestätigte Füllmenge, sofern diese in den Tank passt.
Nach dem Nachfüllen die gesamte Wassermenge im Tank eintragen, z. B. **750 ml**, und bestätigen, ob er vorher
leer war. Das protokolliert Handarbeit, schaltet aber kein Gerät und hebt keine Automatik-Sperre auf.

`vgrow/humidifier_tank.py` zählt ungefähre Einschaltzeit aus gemeldetem USB-2-Zustand, laufender MQTT-Verbindung
und frischen Gerätedaten. Beobachtungslücken über drei Minuten werden nicht überbrückt. Der Tank hat hier keinen
Füllstands- oder Durchflusssensor; ml je Sprühstoß und Stromverbrauch werden nicht erfunden. Eine bekannte
Nebelleistung kann in ml/h eingetragen werden. Sonst lernt Hank erst aus einer bestätigten Füllmenge und dem
später bestätigten leeren Tank, ohne Aufzeichnungslücken, einen ungefähren Verbrauch je Einschaltstunde.
Restmengen gibt es nur auf dieser Grundlage; bei geschätzt höchstens 20 % der Tankgröße empfiehlt er eine Prüfung.

Auch nach erfolgreichem Anlaufen überwacht Hank die Feuchtereaktion: bei etwa 20 Minuten ohne mindestens einen
Prozentpunkt RH- oder 0,05 kPa Dampfdruckzunahme unterhalb des Abschaltziels rät er, Tank und Nebelaustritt zu
prüfen. Änderungen an Abluft/Licht, größere Temperaturänderungen und alte Daten starten diese Beobachtung neu.
Die Schwellen sind Wartungsheuristiken, kein Beweis für einen leeren Tank; starke Abluft oder eine verstopfte Düse
sind weitere Möglichkeiten. Die bestehende Startprüfung mit Abschaltung bleibt separat erhalten.

Hank nennt die Hinweise von sich aus, in der Heute-Liste und bei Fragen nach Befeuchter-Tank oder Verbrauch.
Die Aufzeichnung liegt in `~/.vgrow-control/humidifier-tank.json`, mit den letzten 50 Nachfüllungen und Gesamtzähler.
Ein aufgezeichnetes Einschalten beweist keine Nebelmenge. Die 55-%-Abschaltung, Wiederanlaufschwelle, Anschluss-
Zuordnung und Sicherheitsabschaltungen bleiben unverändert. Stündliche und tägliche Befehlsquoten wurden entfernt.

Der aktuelle Befeuchter hat einen seitlichen Startknopf. Bei aktiviertem Knopf-Hinweis muss der Nebelstart
separat bestätigt werden. USB-Aus beendet die Bestätigung; beim nächsten USB-Ein fordert Hank zum Prüfen des
Knopfes und sichtbaren Nebels auf. Automatischer Nebel-Wiederanlauf ist damit nicht bestätigt. Unbestätigte
Nebelzeiten werden nicht als Wasserverbrauch gerechnet und verhindern die Restmengen-Schätzung für den Zyklus.

## Autopilot (`vgrow/autopilot.py`)

Ein Regelwerk im Code, nicht das Sprachmodell: es stellt Licht, Abluft und Umluft je Box in festen Grenzen.
Entwurf und Kritik: `docs/autopilot-entwurf.md`, `docs/autopilot-kritik.md`.

- **Drei Stellungen**, Standard **Aus**: *Beobachten* rechnet und zeigt, was er tun würde ("Autopilot würde:
  Abluft 5 auf 6, weil ..."), und schaltet nichts. *Automatik* schaltet. Automatik mit zwei Tipps, Aus und
  Beobachten mit einem. `VGROW_AUTOPILOT=0` in `~/.vgrow.env` hält ihn aus. Drei nicht bestätigte Befehle
  hintereinander: er schaltet sich selbst aus. Aus und `VGROW_AUTOPILOT=0` betreffen nur den Autopiloten: die
  Befeuchter-Regel hat ihren eigenen Schalter (vierte Zeile der Ausnahmen), und ihr Fail-Safe wacht über einen
  laufenden Befeuchter, auch wenn die Regel aus ist.
- **Gerätestatus direkt sichtbar**: Licht, Abluft, Umluft und Befeuchter zeigen Messwert, Bereitschaft und Grund
  für eine Wartezeit. Gewählte Automatik heißt nicht, dass ein Gerät schon regelt. Die Übersicht unterscheidet
  Überwachung, Wartezeit, fehlende Einrichtung, Handübernahme und ausstehende Bestätigung. Lampenabstand und
  Anschlusszuordnung lassen sich von dort mit den bestehenden Bestätigungen öffnen. Nach mehr als 10 cm
  protokolliertem Wachstum erscheint die Lampenbestätigung wieder, solange die Helligkeit unter dem Phasenwert liegt.
- **Geräteeinstellungen und Grenzen** (eingeklappt): Automatik, Vorschlag oder Manuell, und ob er ein Gerät anfassen darf,
  das in der VIVOSUN-App auf Automatik oder nach Plan läuft (zwei Tipps; der erste Befehl beendet das, beim Licht
  brennt die Lampe danach ohne Plan durch, bis man ihn in der App neu setzt; Standard: nein). Meldet die Box den
  Modus nicht, fasst er das Gerät nie an, auch mit dieser Ausnahme nicht.
- **Hand gewinnt**: ein Befehl im Cockpit (er pausiert das Gerät, bevor er gesendet wird) oder eine Änderung in
  der VIVOSUN-App pausiert das Gerät 60 min. Danach regelt er vom dann gemeldeten Wert aus, nie zurück auf einen
  gespeicherten; eine Umluftstufe, die man selbst gesetzt hat, bleibt bis zur nächsten Phase. Jeder echte Schritt
  hat 30 min lang "Rückgängig" (schickt den alten Wert als Handbefehl). Ein unbestätigter Befehl wird nur einmal
  und nur Sekunden später wiederholt, nie nach einem Moduswechsel und nie nach einem Neustart.
- **Sperren**: alte Daten (über 10 min, Median neu nach 5 min Lücke) heißen keine Änderung; nur mit einer
  bestätigten Phase; Licht höchstens +25 am Tag, nur bei Licht an, nie über die Voreinstellung, nie bei Wärme
  oder hohem VPD, erst nach einmal bestätigtem Lampenabstand; Nebel-Sperre (Fühler mehr als 20 Punkte über der
  Box: Feuchte und VPD steuern die Abluft nicht); zu kalt und zu feucht zugleich: die Abluft hält.
- Zustand und Protokoll je Box unter `~/.vgrow-control/boxes/<box>/`, nie im Repo. Hank liest den Stand über den
  Schnappschuss, den der Server ihm gibt (sein Sprachmodell importiert den Autopiloten nicht und schaltet nichts;
  den Schalter "Hank regelt" siehe Abschnitt Hank): "Kannst du das
  beheben?" zeigt für Geräte, die der Autopilot regelt, keine Karte, sondern was er gerade tut; "Was hat der
  Autopilot gemacht?" nennt Modus und die letzten drei Schritte.
- **Nur ein Server pro Konto.** Steuerungssperre und Befehlsprotokoll liegen im lokalen Zustandsordner. Laufen zwei Rechner mit
  echten Zugangsdaten (Mac und ein Windows-Testserver), regeln beide und zählen getrennt. Auf einem zweiten Rechner
  `VGROW_AUTOPILOT=0` setzen.

## Schutzregeln im Code

- Spektrum nur im manuellen Modus ohne laufenden Plan (Upstream-Regel).
- USB-Anschlüsse nur, wenn sie manuell laufen; Modus und Zubehörtyp werden nie geändert.
- Läuft ein Grow-Plan, sperrt die Seite das Licht, bis man "Trotzdem manuell steuern" tippt.
- Fehlt ein Messwert, gibt es keinen Alarm. Kein Alarm auf eine Vermutung.
- Ein Anschluss mit `always_on` in `ports.json` schaltet nie aus: die Sperre sitzt im Schaltbefehl selbst
  und gilt auch für Automatik und Voreinstellungen. Seit 23.10.2026 ist keiner gesperrt: an USB 1 hängt
  die Tropfbewässerung (Pumpe, `device: pump`). Der Gießplan steuert sie mit fester Dauer;
  auch USB-1-Einschaltbefehle der Seite verwenden diesen Timer. Der Klima-Autopilot schaltet sie nicht.
  Hank liest Gießplan und Verlauf, erzeugt aber keine Pumpen-Einschaltkarte. Die Kamera braucht seitdem ein eigenes
  USB-Netzteil und hängt an keinem Anschluss mehr; bis es steckt, ist sie offline. Ist `ports.json` nicht
  lesbar, gilt die letzte lesbare Fassung; gab es keine, schaltet das Cockpit gar keinen Anschluss aus.
- Das Alter der Messwerte zählt ab dem Zeitstempel der Messung, nicht ab der Abfrage. Eine Zeile nur
  aus Platzhaltern (-6666) zählt nicht als Messung.
- Luftbefeuchter-Automatik (USB 2): ab Erreichen der oberen Feuchtegrenze der bestätigten Phase AUS,
  beim nächsten Sensorpoll, auch wenn die letzte Schaltung noch keine 15 min her ist. Erst unter der
  effektiven unteren Schwelle wieder EIN, frühestens 15 min nach dem Ausschalten. Dazwischen bleibt
  der Zustand bestehen. Die Einschaltschwelle berücksichtigt bei Licht auch Temperatur und VPD und
  bleibt mindestens 3 Prozentpunkte unter der Obergrenze (soweit das Phasenband breit genug ist).
  Eine eigene niedrigere Ausschaltgrenze lässt sich über den geschützten Befehl
  `{"action":"humidifier_target","off_at":55}` setzen (`off_at:null` stellt die Karte wieder her).
  Sie gilt für die aktuelle bestätigte Phase, bleibt beim Neustart erhalten und ändert keine
  recherchierten Phasenziele. Die Wiedereinschaltschwelle bleibt auch dann mindestens 3 Punkte
  darunter. Bei einer anderen Phase oder einem neuen Phasenbeginn gelten wieder die Kartenwerte.
  Beide Schwellen stehen direkt in der Befeuchter-Kachel. Mit Messwerten älter als 10 min schaltet sie
  nie ein. Sind sie älter als 15 min, oder meldet der Fühler 15 min lang keine Feuchte, und der
  Befeuchter läuft, schaltet sie ihn
  aus (aus ist der sichere Zustand). Erledigt ist das erst, wenn die Box "aus" meldet; sonst kommt ein
  Push "nicht bestätigt", und der Befehl geht in wachsenden Abständen erneut raus (90 s, 3, 6, 12, dann alle
  15 min). Schaltet man in so einer Phase selbst, hält sich die Automatik bis zu frischen Werten heraus.
- Anschlusswächter (`vgrow/portguard.py`): die Befeuchter-Regel schaltet USB 2 nur EIN, wenn man auf der Seite
  gesagt hat, dass dort der Befeuchter hängt ("An USB 2 hängt: Befeuchter / Pumpe / anderes", zwei Tipps).
  `ports.json` kann nur widersprechen: nennt es dort ein anderes Gerät, wird der Anschluss nie geschaltet. Ändert
  sich die Belegung (`ports.json` oder der von der Box gemeldete Zubehörcode), oder ist der gespeicherte Stand
  unlesbar, pausiert alle Automatik, bis man die Belegung auf der Seite bestätigt; danach fragt die Seite für jeden
  geänderten Anschluss neu, was dort hängt. Ausschalten (Fail-Safe, Pausen, Feuchte über dem Band) läuft auch
  währenddessen. Eine Pumpe an USB 2 schaltet nie nach der Feuchte.
- Plausibilitätsstopp: steigt die Fühlerfeuchte nach dem Einschalten von USB 2 in 20 min nicht um 3 Punkte,
  schaltet die Regel aus, schaltet nicht mehr ein, bis man neu sagt, was an USB 2 hängt, und fragt per Push.
- Befehlsprotokoll fürs Konto (`vgrow/budget.py`): Automatik, Befeuchter, Pumpenplan und Handbefehle werden
  weiterhin gezählt. Es gibt keine stündlichen oder täglichen Befehlsquoten, auch nicht je Lüfter.
  Bestehende Protokolle bleiben erhalten; alte Quotensperren und unlesbare Zähler blockieren keine Befehle.
  Anschlusszuordnung, Laufzeiten, Mindestabstände, Bestätigungen und Sicherheitsabschaltungen bleiben aktiv.
- Temperatur- und Feuchte-Alarme lesen den Fühler auf Blatthöhe, ohne Fühlerwert den eingebauten Sensor.
  Einen Alarm, den der Fühler ausgelöst hat, hebt der eingebaute Sensor nie auf.
- Kamera-Alarm auch ohne `GROWCAM_IP`: findet der Server die Kamera aus dem Konto nicht im Netz, zählt
  das als offline.
- Fällt die Cloud aus, laufen Alarme und Automatik trotzdem weiter: so meldet sich "Box offline". Auch
  ein Fehler bei der ersten Abfrage nach dem Start hält den Server nicht mehr an.
- Anmeldung bei VIVOSUN mit Maß: Netz- und Serverfehler warten 10 s, 20 s, 40 s und so weiter, höchstens
  5 min (jede Pause zufällig bis 20 % kürzer). Lehnt VIVOSUN die Zugangsdaten ab (oder hat das Konto keine
  VGrow), steht das klar auf der Seite, und der nächste Versuch kommt frühestens nach 30 min. Nie mehr als
  6 Anmeldungen in einer Stunde, gezählt über Neustarts hinweg (`~/.vgrow-control/login-attempts.json`).
  Der Server trägt bei jeder Anmeldung und Verbindung dieselben Kennungen, auch nach einem Neustart, und
  die 30 min nach einer Ablehnung gelten über Neustarts hinweg (`~/.vgrow-control/login-state.json`);
  `--check` nimmt eigene Kennungen und speichert sie nicht. Eine fehlgeschlagene Anmeldung beim Start hält
  den Server nicht an: launchd würde ihn sonst alle 30 s neu starten, und jeder Start meldete sich neu an.
  Den Schlüssel und den Port holt sich der Start vor der Anmeldung: ist der Port belegt, endet er, ohne
  VIVOSUN zu fragen.
- MQTT-Verbindung mit Maß: trennt die Cloud eine Verbindung innerhalb von 60 s wieder, zählt das als
  Fehler (Pause wie oben, Grund auf der Seite, neue Anmeldung über die Stundengrenze); erst eine Verbindung,
  die 60 s hält, setzt die Pausen zurück. Nie mehr als 12 Verbindungen in einer Stunde (eigene Regel).
- Zugangsschlüssel nie leer oder kurz: fehlt die Datei, ist sie leer oder kürzer als 16 Zeichen, entsteht
  ein neuer (atomar geschrieben, nur für den Besitzer lesbar). Den Link mit Schlüssel zeigt der Start nur
  im Terminal; im Log steht nur, wo die Datei liegt, und jede Logzeile schreibt `key=<redacted>`, auch der
  Traceback einer kaputten Anfragezeile (`GET /?key=... HTTP/9.x`). Ein Cookie mit beliebigen Bytes ist ein
  403, kein 500. `install_mac.sh` schwärzt alte `key=...` im Log und macht das Log nur für den Besitzer lesbar.
- Befeuchter-Automatik nur mit bestätigter Phase: ist die Phase nur geschätzt oder aus den Tagen seit dem
  Start abgeleitet, schaltet sie den Befeuchter nie ein. Läuft er, schaltet sie ihn einmal aus (bestätigt
  wie beim Fail-safe), ein Push sagt, dass sie auf die Phase wartet, die oberste Zeile der letzten Aktionen
  sagt "aus, Phase nicht bestätigt", und die Seite bietet den Vorschlag zum Bestätigen an (zweimal tippen:
  der erste Tipp fragt nur). Als gesetzt zählt nur eine Phase dieses Durchgangs: Einträge im Pflanzenlog vor
  `start_date` gehören zur Pflanze davor. Veraltete oder fehlende Messwerte gehen wie bisher vor.
- `plant.json` nicht lesbar (ein Komma zu viel reicht): es gilt die letzte lesbare Fassung, die Pflanzenkarte
  sagt es. Gab es seit dem Start keine, pausiert die Automatik mit eigenem Grund (Push nennt `plant.json`,
  nicht "keine Phase") und ohne Wartezeit, sobald die Datei wieder lesbar ist.
- Der Feuchte-Alarm folgt der Phase: Grenze ist der Feuchte-Höchstwert der aktuellen Phase plus 10 Punkte
  (eigene Regel des Cockpits, keine belegte Zahl), ohne Phase 70 %. Eine gesetzte Phase hebt oder senkt die
  Grenze: ein gesetzter Keimling (Ziel 65 bis 75 %) alarmiert erst über 85 %. Eine nur geschätzte Phase senkt
  sie höchstens, nie über 70 %: der Alarm ist das Sicherheitsnetz, solange die Automatik auf die Phase wartet.
  Die Alarmkarte zeigt die gerade geltende Grenze, die Phase, aus der sie kommt, und ob die nur geschätzt ist.
- Düngerplan nie über der Tabelle: Menge je Liter = Tabellenwert mal Faktor (höchstens 1), exakt
  gerechnet und auf 0,1 ml abgerundet, die Menge für eine Gießkanne ebenso. Die Seite schickt nur Liter,
  nie Mengen. Wie oft gedüngt wird, sagt die Tabelle nicht, also auch das Cockpit nicht.
- Düngewoche aus der gesetzten Phase: Keimling oder Wachstum heißt Spalte Wachstum, was auch sonst Blüte
  sagt. Ab der frühen Blüte zählen die Blütewochen ab dem Tag, an dem man die erste Blütephase gesetzt hat,
  oder ab `bloom_start` in `plant.json`, wenn man dort selbst ein Datum einträgt. Ohne gesetzte Phase und
  ohne eigenes `bloom_start` ist die Woche nur geschätzt: keine Mengen, bis man die Phase bestätigt. Passen
  Phase und Tabellenwoche nicht zusammen, warnt der Block "Düngen".
- Bestätigt ist die Tabelle, die man verglichen hat: Karte, Version, Einheit, Seite, die Zeilen der eigenen
  Produkte, die Spalten, die Produkte und der Faktor als ein Fingerabdruck. Ändert sich davon etwas (eine
  korrigierte Zahl, eine andere Seite, eine neue Version), ist es wieder ein Entwurf, und die Seite sagt es.
- Liter mit Tippfehler-Schutz: 0,1 bis 20 l. Über 5 l (`confirm_above_liters` in `plant.json`, eigene Regel
  des Cockpits) fragt die Seite ein zweites Mal, bevor sie Mengen zeigt, und der Server rechnet erst danach;
  15 statt 1,5 rutscht so nicht durch. Das Feld nimmt 1,5 und 1.5 in beiden Sprachen.
