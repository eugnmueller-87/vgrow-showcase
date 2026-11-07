"""Server texts in German and English, the request language and the server language.

The page sends X-Lang on every API call; a middleware puts it into REQUEST_LANG for that request.
Everything else (pushes, the daily plant reading, requests without the header) uses the server
language: STATE_DIR/settings.json, else VGROW_LANG in ~/.vgrow.env, else German.
Content files keep German base fields with English twins (label, label_en); localize() picks them.
Units stay metric in both languages; only the decimal separator changes.
"""

from __future__ import annotations

import json
import logging
import string
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config

_LOGGER = logging.getLogger(__name__)

LANGS = ("de", "en")
DEFAULT_LANG = "de"
LOCALES = {"de": "de-DE", "en": "en-US"}  # what the page formats dates and numbers with
TWIN = "_en"  # suffix of the English twin of a displayed content field
REQUEST_LANG: ContextVar[str | None] = ContextVar("vgrow_request_lang", default=None)


class UserError(ValueError):
    """Invalid input whose message is already a sentence for the page."""


class Text:
    """A catalog text said when it is read, in the reader's language, not the one it was made in."""

    def __init__(self, key: str, /, **params: Any) -> None:
        self.key, self.params = key, params

    def text(self, lang: str | None = None) -> str:
        return t(self.key, lang, **self.params)

    def __str__(self) -> str:
        return self.text()


class TextError(RuntimeError):
    """An error that keeps its catalog key and values: stored now, translated whenever it is shown.
    A Text or TextError among the values is translated along with it, in the same language."""

    def __init__(self, key: str, /, **params: Any) -> None:
        super().__init__(key)
        self.message = Text(key, **params)

    def text(self, lang: str | None = None) -> str:
        return self.message.text(lang)

    def __str__(self) -> str:
        return self.message.text()


class TimeOfDay:
    """A time of day, said in the reader's language when the text is read: 14:30 in German, 2:30 PM in English."""

    def __init__(self, epoch: float) -> None:
        self.epoch = epoch

    def text(self, lang: str | None = None) -> str:
        return fmt_time(self.epoch, lang)

    def __str__(self) -> str:
        return self.text()


def reason(err: BaseException) -> Any:
    """What to keep of an error for later display: itself if it can be translated later, else its text."""
    return err if isinstance(err, TextError) else (str(err) or type(err).__name__)


DE: dict[str, str] = {
    # requests
    "error.unknown_action": "Unbekannte Aktion {action}",
    "error.bad_request": "Ungültige Anfrage: {reason}",
    "error.cloud": "Die Cloud hat nicht geantwortet: {reason}",
    "error.unknown_lang": "Unbekannte Sprache {code}, möglich sind de und en",
    "guard.wrong_key": "Falscher Schlüssel",
    "guard.need_key": "Den Link mit ?key=... einmal auf diesem Gerät öffnen.",
    # page metadata (the home-screen manifest)
    "meta.title": "VGrow Cockpit (inoffiziell)",
    "meta.description": "Inoffizielles Cockpit für die VIVOSUN VGrow. Nicht von VIVOSUN.",
    # box and ports
    "port.always_on": "{port} bleibt immer an (ports.json)",
    "port.rules_unreadable": "Die Anschluss-Konfiguration ist nicht lesbar: Ausschalten bleibt gesperrt, bis sie "
                             "repariert ist",
    "box.no_connection": "Gerade keine Live-Verbindung zur Box",
    "box.unknown_spectrum": "Unbekanntes Spektrum {name}",
    "box.spectrum_manual_only": "Das Spektrum ändert sich nur im manuellen Modus ohne laufenden Plan",
    "box.natural_wind_app": "Natürlicher Wind lässt sich hier noch nicht schalten, bitte in der Vivosun-App",
    "box.not_a_port": "{port} ist kein Anschluss dieser Box",
    "box.port_not_manual": "{port} läuft auf Automatik oder nach Plan; erst in der Vivosun-App auf manuell stellen",
    "box.no_vgrow": "Keine VGrow ({model}) in diesem Vivosun-Konto. Gefunden: {found}",
    "box.none_found": "nichts",
    "box.error.connection": "Verbindung: {reason}",
    "box.error.login": "Anmeldung: {reason}",
    "box.error.sensors": "Messwerte: {reason}",
    "box.not_logged_in": "Noch nicht bei VIVOSUN angemeldet",
    "box.login_rejected": "VIVOSUN lehnt die Anmeldung ab ({reason}). Bitte VIVOSUN_EMAIL und VIVOSUN_PASSWORD in "
                          "~/.vgrow.env prüfen und den Server neu starten",
    "box.retry_at": "{reason}. Nächster Versuch um {at}",
    "box.retry_slow": "{reason}. Nächster Versuch um {at}; nach einer Ablehnung höchstens alle {minutes} Minuten",
    "box.login_capped": "Höchstens {count} Anmeldungen pro Stunde, nächster Versuch um {at}",
    "box.login_capped_after": "{reason}. Höchstens {count} Anmeldungen pro Stunde, nächster Versuch um {at}",
    "box.login_rejected_before": "Die letzte Anmeldung vor dem Neustart hat VIVOSUN abgelehnt",
    "box.link_dropped": "Die Cloud trennt die Verbindung kurz nach dem Aufbau",
    "box.connect_capped": "Höchstens {count} Verbindungen pro Stunde, nächster Versuch um {at}",
    # camera
    "camera.none": "Keine Kamera eingerichtet",
    "camera.no_account": "Keine GrowCam mit Zugangsdaten im Vivosun-Konto",
    "camera.no_ffmpeg": "ffmpeg fehlt (pip install imageio-ffmpeg)",
    "camera.not_found": "GrowCam im Netz nicht gefunden, suche weiter",
    "camera.no_answer": "Kamera antwortet nicht",
    "camera.usb_unavailable": "USB-Kamera liefert kein Bild. Anschluss und macOS-Kamerafreigabe prüfen.",
    "camera.usb_not_seen": "macOS sieht die Kamera nicht. USB-Stecker und Adapter prüfen.",
    "camera.too_many_viewers": "Zu viele Live-Zuschauer",
    "camera.recording_running": "Es läuft schon eine Aufnahme",
    "camera.low_disk": "Weniger als {gb} GB frei auf dem Server",
    "camera.recording_missing": "Aufnahme nicht gefunden",
    "camera.stop_first": "Laufende Aufnahme erst stoppen",
    # demo
    "demo.no_recording": "Die Demo nimmt nichts auf: es gibt keine echte Kamera",
    "demo.camera_title": "DEMO-Kamera",
    "demo.camera_caption": "Platzhalter, kein echtes Kamerabild",
    "demo.photo_caption": "Demo-Tagesfoto, kein echtes Bild",
    "demo.footer": "VGrow Cockpit (inoffiziell, kein VIVOSUN-Produkt)",
    # plant
    "plant.unknown_stage": "Unbekannte Phase",
    "plant.no_preset": "Diese Phase hat keine Voreinstellung",
    "plant.applied_light": "Licht auf {level}",
    "plant.applied_light_partial": "Licht auf {level} (Ziel {target}, später erneut übernehmen)",
    "plant.applied_spectrum": "Spektrum",
    "plant.applied_cfan": "Umluft",
    "plant.applied_dfan": "Abluft",
    "plant.ph_or_ec": "pH oder EC eintragen",
    "plant.ph_range": "pH zwischen 0 und 14",
    "plant.ec_range": "EC in mS/cm zwischen 0 und 10",
    "plant.height_range": "Höhe in cm zwischen 1 und 300",
    "plant.bloom_bad_date": "Kein Datum: {value}",
    "plant.bloom_future": "Der Blütebeginn kann nicht in der Zukunft liegen",
    "plant.bloom_before_start": "Der Blütebeginn liegt vor dem Start dieser Pflanze ({date})",
    "plant.stage_from_bloom": "Geschätzt aus deinem Blütebeginn am {date}: {stage}. Bitte bestätigen oder die Phase "
                              "setzen.",
    "plant.bloom_stage_before": "Du hast die Phase {stage} gesetzt. Setze zuerst eine Blütephase, dann den "
                                "Blütebeginn.",
    "plant.bloom_by.owner": "von dir eingetragen",
    "plant.bloom_by.plan": "aus plant.json",
    "plant.bloom_by.stage": "ab dem Tag, an dem du die Blütephase gesetzt hast",
    "plant.bloom_by.estimate": "geschätzt aus dem Kamerabild",
    "plant.not_a_number": "Keine Zahl: {value}",
    # the plant's start and age (plant.age_view, PlantLog.set_start_date); the age never changes a dose
    "plant.age_w1": "1 Woche",
    "plant.age_wn": "{n} Wochen",
    "plant.age_d1": "1 Tag",
    "plant.age_dn": "{n} Tage",
    "plant.age_and": "{weeks} und {days}",
    "plant.age_line": "Die Pflanze ist {age} alt (Tag {days}, Pflanzenwoche {life}), Start {date}, {by}.",
    "plant.age_line_approx": "Die Pflanze ist etwa {age} alt (Pflanzenwoche {life}), Start etwa {date}, {by}.",
    "plant.age_unknown": "Alter unbekannt: noch kein Starttag.",
    "plant.age_ask_unknown": "Wann hat die Pflanze gekeimt? Trag den Starttag oder ihr Alter in Wochen in der "
                             "Pflanzenkarte ein.",
    "plant.age_ask_approx": "Der Start {date} ist nur ungefähr. Weißt du den Tag der Keimung? Dann trag ihn in der "
                            "Pflanzenkarte ein.",
    "plant.start_by.owner": "von dir eingetragen",
    "plant.start_by.owner_weeks": "von dir als Alter in Wochen eingetragen",
    "plant.start_by.owner_days": "von dir als Alter in Tagen eingetragen",
    "plant.start_by.plan": "aus plant.json",
    "plant.start_one_of": "Genau eins angeben: ein Datum, ein Alter in Wochen oder ein Alter in Tagen",
    "plant.start_bad_date": "Kein Datum: {value}",
    "plant.start_weeks_range": "Alter in Wochen zwischen 0 und {max}",
    "plant.start_days_range": "Alter in Tagen zwischen 0 und {max}",
    "plant.start_future": "Der Start kann nicht in der Zukunft liegen",
    "plant.start_too_old": "Der Start liegt mehr als {days} Tage zurück",
    "plant.start_crosses_entry": "Am {date} steht ein Eintrag von dir (Phase oder Blütebeginn). Mit dem Start am "
                                 "{start} fiele er still weg oder ein alter Eintrag zählte wieder; bitte zuerst "
                                 "diesen Eintrag prüfen.",
    "plant.start_preview": "Das wäre der {date}: {age} alt, Pflanzenwoche {life}. Die Düngermengen ändern sich "
                           "dadurch nicht.",
    "plant.start_preview_later": "Das wäre der {date}, {n} Tage später als bisher ({old}): {age} alt, Pflanzenwoche "
                                 "{life}. Die Düngermengen ändern sich dadurch nicht.",
    "plant.start_preview_earlier": "Das wäre der {date}, {n} Tage früher als bisher ({old}): {age} alt, "
                                   "Pflanzenwoche {life}. Die Düngermengen ändern sich dadurch nicht.",
    "plant.start_preview_same": "Das ist der Start, der schon gilt ({date}).",
    # feeding planner
    "feeding.unknown_setup": "Diesen Dünger gibt es in plant.json nicht: {setup} (vorhanden: {known})",
    "feeding.bad_unit": "Die Düngerkarte {card} hat eine Einheit, die das Cockpit nicht rechnet: {unit}",
    "feeding.one_card_only": "Es gilt immer nur ein Dünger: zwei Tabellen zugleich würden doppelt düngen.",
    "feeding.needs_choice": "Erst den Dünger wählen: bis dahin rechnet das Cockpit keine Mengen",
    "feeding.label_ends": "Ab Blütewoche {week} nennt die Tabelle keine Menge mehr, das Cockpit rechnet deshalb "
                          "keine. Die Menge entscheidest du",
    "feeding.none": "Keine Düngertabelle eingerichtet (feeding in plant.json)",
    "feeding.no_card": "Die Düngerkarte {card} fehlt in kartei/nutrients/",
    "feeding.no_page": "Die Düngerkarte {card} hat keine Seite {page}",
    "feeding.bad_factor": "dose_factor in plant.json muss eine Zahl über 0 und höchstens 1 sein",
    "feeding.bad_date": "{field} in plant.json ist kein Datum (JJJJ-MM-TT)",
    "feeding.not_confirmed": "Erst die Düngertabelle prüfen und bestätigen: bis dahin rechnet das Cockpit keine "
                             "Mengen",
    "feeding.not_before": "Dünger erst ab {date}: bis dahin nur Wasser, das ist deine Ansage.",
    "feeding.needs_stage": "Erst die Phase bestätigen: bis dahin ist die Woche nur geschätzt, und das Cockpit "
                           "rechnet keine Mengen",
    "feeding.wrong_card": "Auf dem Server gilt inzwischen die Düngerkarte {card}: bitte die Seite neu laden",
    "feeding.confirmations_broken": "Die Datei mit deinen Bestätigungen ({file}) ist nicht lesbar. Das Cockpit "
                                    "überschreibt sie nicht, sonst ginge die Bestätigung des anderen Düngers still "
                                    "verloren; bitte die Datei prüfen",
    "feeding.unit_withheld": "Mengen für {card} stehen nur auf der Cockpit-Seite: diese Karte rechnet in Gramm und "
                             "Löffeln, und hier werden bisher nur Milliliter angesagt",
    "feeding.week_span": "Das Etikett nennt für diese Phase nur Woche {first} bis {last}, die Pflanze ist älter. Das "
                         "Cockpit bleibt bei dieser Spalte, bis du eine andere Phase setzt",
    "feeding.changed": "Die Tabelle hat sich geändert, seit die Seite sie gezeigt hat: bitte neu laden und neu "
                       "vergleichen",
    "feeding.harvest": "Ab Blütewoche {week} sieht die Tabelle keinen Dünger mehr vor",
    "feeding.liters_range": "Liter zwischen {min} und {max}",
    "feeding.liters_large": "{liters} l ist mehr als {limit} l: bitte die Liter prüfen und bestätigen",
    "feeding.stage_before_bloom": "Deine Phase ist {stage}, plant.json setzt den Blütebeginn aber auf {date}. Das "
                                  "Cockpit nimmt die Spalte Wachstum; stimmt der Blütebeginn, die Phase ändern.",
    "feeding.stage_before_owner_bloom": "Deine Phase ist {stage}, in der Pflanzenkarte steht aber ein Blütebeginn am "
                                        "{date}. Das Cockpit nimmt die Spalte Wachstum; stimmt der Blütebeginn, die "
                                        "Phase ändern.",
    "feeding.stage_misfit": "Deine Phase ist {stage}, die Tabelle steht aber bei {week} (Blütebeginn {date}). Bitte "
                            "die Phase prüfen oder in plant.json bei bloom_start den richtigen Blütebeginn "
                            "eintragen.",
    # AI assessment
    "vision.limit": "Heute schon {count} Einschätzungen",
    "vision.busy": "Eine Einschätzung läuft schon",
    "vision.no_ollama": "Ollama läuft auf dem Mac nicht. Die Ollama-App auf dem Mac starten.",
    "vision.no_model": "Das Modell {model} fehlt auf dem Mac (ollama pull {model}).",
    "vision.timeout": "Das lokale Modell hat länger als {minutes} Minuten gebraucht und wurde abgebrochen.",
    "vision.failed": "Einschätzung fehlgeschlagen: {reason}",
    "vision.photo_failed": "Tagesfoto fehlgeschlagen: {reason}",
    "vision.unreadable": "Das lokale Modell hat keine lesbare Einschätzung geliefert",
    "vision.unknown": "unbekannt",
    "vision.none": "keine",
    "vision.ctx.photo": "Foto vom {when}.",
    "vision.ctx.plant": "Sorte: {strain}. Tag {day} seit Start.",
    "vision.ctx.stage_manual": "Vom Betreiber gesetzte Phase: {stage}.",
    "vision.ctx.stage_estimate": "Geschätzte Phase, vom Betreiber nicht bestätigt: {stage}.",
    "vision.ctx.height": "Zuletzt gemessene Höhe: {cm} cm am {date}.",
    "vision.ctx.climate": "Klima jetzt: am Fühler (Blatthöhe) {probe_temp} °C, {probe_humidity} % Luftfeuchte, "
                          "VPD {probe_vpd} kPa; eingebauter Sensor {temp} °C, {humidity} %.",
    "vision.ctx.controls": "Licht {light} %, Spektrum {spectrum}. Umluft Stufe {cfan}, Abluft Stufe {dfan}.",
    "vision.ctx.previous": "Letzte Einschätzung ({date}): {stage}, Probleme: {issues}.",
    # pushes
    "push.title": "VGrow: {title}",
    "push.title_demo": "VGrow DEMO: {title}",
    "push.test_title": "Test",
    "push.test_body": "Wenn du das liest, kommen Alarme an.",
    "push.plant": "Pflanze",
    # pest watch (vgrow/pest_watch.py)
    "pest.title": "Möglicher Schädlingsbefall",
    "pest.push_body": "{signs}. Bitte nachsehen, auch unter den Blättern, und im Cockpit antworten: da ist was, oder nichts.",
    "pest.sign.white_powder": "weißer, mehliger Belag",
    "pest.sign.speckles": "viele helle Pünktchen auf Blättern",
    "pest.sign.webbing": "feine Gespinste",
    "pest.sign.insects": "Insekten, Milben, Eier oder Häutungsreste",
    "pest.sign.residue": "klebriger Glanz oder schwarzer Belag",
    "pest.sign.curled": "eingerollte oder verformte Blätter",
    "pest.sign.bud_mold": "Schimmel oder matschige Stellen in Blüten",
    "pest.sign.chewed": "Fraßstellen oder Löcher",
    "pest.sign.patches": "gelbe oder braune Flecken",
    "pest.sign.unusual": "sonst auffällig",
    "pest.maybe": ", unsicher",
    "pest.where_unknown": "Stelle unklar",
    "pest.no_camera": "Keine Kamera erreichbar.",
    "pest.failed": "Schädlingsprüfung fehlgeschlagen: {reason}",
    "pest.bad_answer": "Die Antwort ist ja, nein oder unsicher.",
    "pest.unknown_check": "Diese Prüfung gibt es nicht.",
    "pest.bad_card": "Diese Karte passt nicht zu den bestätigten Zeichen.",
    # the grow report (vgrow/grow_report.py)
    "report.well": "{label} in der Phase {stage}: {inside} % der gemessenen Minuten im Zielband ({rows} Minuten).",
    "report.poor_above": "{label} in der Phase {stage}: nur {inside} % im Zielband, {off} % darüber ({rows} Minuten).",
    "report.poor_below": "{label} in der Phase {stage}: nur {inside} % im Zielband, {off} % darunter ({rows} Minuten).",
    "report.pest": "Schädlinge bestätigt am {date}: {what}.",
    "report.use_water": "Wasser: {litres} Liter gezählt.",
    "report.use_runs": "Pumpenläufe ohne gemessene Menge: {runs}, zusammen {minutes} Minuten.",
    "report.use_feed": "{label}: {amount}.",
    "report.use_power": "Strom der Lampe: etwa {kwh} kWh, geschätzt aus {watts} W Nennleistung mal gemeldeter Helligkeit. Lüfter, Befeuchter, Pumpe und Rechner sind nicht dabei.",
    # alerts
    "alert.limit": " (Grenze {limit})",
    "alert.message": "{label}{limit}. {reading}",
    "alert.message_reminder": "Immer noch: {label}{limit}. {reading}",
    "alert.recovered_title": "Wieder ok",
    "alert.recovered": "{label}: wieder im Rahmen. {reading}",
    "alert.reading": "Jetzt {value}.",
    "alert.reading_fallback": "Jetzt {value} am eingebauten Sensor, der Fühler meldet keinen Wert.",
    "alert.no_data": "Keine Messwerte seit dem Start.",
    "alert.last_reading": "Letzter Messwert vor {minutes} min.",
    "alert.camera": "Kein Bild, seit {minutes} min nicht im Netz. Hat ihr Netzteil Strom?",
    "alert.camera_back": "Sie antwortet wieder.",
    "alert.usb_camera": "Seit mindestens {minutes} min kein Bild. {reason}",
    "alert.usb_camera_hours": "Seit mindestens {hours} Stunden kein Bild. {reason}",
    "alert.usb_camera_why": "Die Schädlingswache nimmt solange die GrowCam.",
    "alert.light_day": "Letzte 24 Stunden: {lit} Stunden Licht, {dark} Stunden dunkel.",
    "alert.dark_why": "Läuft der Lichtplan?",
    "alert.short_day_why": "Die Phase sieht {hours} Stunden vor. Läuft der falsche Lichtplan?",
    "alert.stage_limit": "Die Grenze folgt der Phase {stage}: Ziel höchstens {max} plus {margin} = {limit}.",
    "alert.margin_points": "{n} Punkte",
    "alert.stage_estimated": "{stage} (geschätzt)",
    # ---------- the tank (type-9 wet/dry water sensor) ----------
    "alert.tank_wet": "Wasser im Tank: vorhanden.",
    "alert.tank_dry": "Der Wassersensor meldet trocken.",
    "alert.tank_dry_since": "Der Wassersensor ist seit {time} trocken.",
    "alert.tank_why": "Mit dem Docht zieht die Pflanze ihr Wasser aus diesem Tank: bitte auffüllen.",
    # humidifier automation
    "auto.none": "Keine Automatik eingerichtet",
    "auto.target_stage": "Zuerst eine Phase mit Feuchtegrenzen bestätigen.",
    "auto.target_range": "Die Ausschaltgrenze muss zwischen {lo} und {hi} liegen (mindestens 3 Punkte Abstand zur Untergrenze).",
    "auto.stuck_title": "Feuchte bleibt hoch",
    "auto.stuck_body": "Befeuchter ist aus, am Fühler trotzdem {value} (Ziel höchstens {limit}) seit über einer "
                       "Stunde. Abluft höher stellen.",
    # fail-safe pushes: title by reason and outcome, body = reason sentence + outcome sentence
    "auto.failsafe_title": "Luftbefeuchter aus: keine frischen Messwerte",
    "auto.failsafe_title_probe": "Luftbefeuchter aus: Fühler meldet keinen Wert",
    "auto.failsafe_title_unconfirmed": "Luftbefeuchter: Ausschalten nicht bestätigt",
    "auto.failsafe_title_late": "Luftbefeuchter jetzt aus",
    "auto.failsafe_stale": "Seit {minutes} min kein frischer Messwert.",
    "auto.failsafe_never": "Seit dem Start kein Messwert.",
    "auto.failsafe_probe": "Der Fühler meldet seit {minutes} min keine Luftfeuchte.",
    "auto.failsafe_off": "Das Cockpit hat den Luftbefeuchter ({port}) ausgeschaltet, die Box hat es bestätigt. Aus ist "
                         "der sichere Zustand; die Automatik schaltet wieder, sobald frische Werte kommen.",
    "auto.failsafe_unconfirmed": "Das Ausschalten des Luftbefeuchters ({port}) ist nicht bestätigt: die Box antwortet "
                                 "nicht. Bitte den Befeuchter selbst ausschalten; das Cockpit versucht es in wachsenden "
                                 "Abständen erneut (90 s, 3, 6, 12, dann alle 15 min).",
    "auto.failsafe_late": "Die Box meldet den Luftbefeuchter ({port}) jetzt als aus.",
    # automation without a confirmed stage: title, then cause + outcome + what to do
    "auto.stage_title": "Luftbefeuchter-Automatik pausiert",
    "auto.stage_cause": "Die Phase ist nicht bestätigt, vorgeschlagen ist {stage}.",
    "auto.stage_cause_none": "Es ist keine Phase gesetzt.",
    "auto.stage_off": "Das Cockpit hat den Luftbefeuchter ({port}) ausgeschaltet, die Box hat es bestätigt.",
    "auto.stage_not_switchable": "Der Luftbefeuchter ({port}) läuft in der Vivosun-App auf Automatik oder nach "
                                 "Plan; das Cockpit schaltet ihn nicht.",
    "auto.stage_paused": "Die Automatik pausiert, bis die Phase bestätigt ist: auf der Seite den Vorschlag "
                         "bestätigen oder die Phase setzen.",
    # automation with an unreadable plant.json: the same pause, the real cause
    "auto.profile_title": "Luftbefeuchter-Automatik pausiert: plant.json nicht lesbar",
    "auto.profile_cause": "plant.json ist nicht lesbar ({error}), damit fehlen die Phase und ihre Zielwerte.",
    "auto.profile_paused": "Die Automatik pausiert, bis plant.json wieder lesbar ist.",
    # autopilot (vgrow/autopilot.py) and the port guard of the humidifier rule (vgrow/portguard.py)
    "auto.port_title": "Befeuchter-Automatik pausiert: Anschlüsse geändert",
    "auto.port_body": "Die Belegung der Anschlüsse hat sich geändert. Die Automatik schaltet {port} nicht, bis du die Belegung auf der Seite bestätigst.",
    "ap.dev.light": "Licht",
    "ap.dev.dfan": "Abluft",
    "ap.dev.cfan": "Umluft",
    "ap.metric.probe_temp": "die Temperatur",
    "ap.metric.probe_humidity": "die Feuchte",
    "ap.metric.probe_vpd": "der VPD",
    "ap.level.auto": "Automatik",
    "ap.level.suggest": "Vorschlag",
    "ap.level.manual": "Manuell",
    "ap.na": "N/A",
    "ap.step.would": "Autopilot würde: {device} {old} auf {new}, weil {reason}.",
    "ap.step.suggest": "Vorschlag: {device} {old} auf {new}, weil {reason}.",
    "ap.step.done": "Autopilot: {device} {old} auf {new}, weil {reason}.",
    "ap.reason.temp_high": "die Temperatur am Fühler mit {value} über {limit} liegt",
    "ap.reason.temp_low": "die Temperatur am Fühler mit {value} unter {limit} liegt",
    "ap.reason.hum_high": "die Feuchte am Fühler mit {value} über {limit} liegt",
    "ap.reason.vpd_low": "der VPD am Fühler mit {value} unter {limit} liegt (Luft zu feucht)",
    "ap.reason.vpd_high": "der VPD am Fühler mit {value} über {limit} liegt (Luft zu trocken, weniger Abluft hält die "
                          "Feuchte)",
    "ap.reason.return_base": "alle Werte seit 90 Minuten sicher im Ziel liegen (zurück zur Grundstufe {base})",
    "ap.reason.windup": "die Abluft hier nicht wirkt: {metric} hat sich nach zwei Schritten nicht bewegt (zurück zur Grundstufe {base}, diese Regel ruht 6 Stunden, kommt das binnen zwei Tagen wieder, 24)",
    "ap.reason.cfan_preset": "die Phase {stage} Umluftstufe {step} vorsieht",
    "ap.reason.light_ramp": "die Voreinstellung der Phase {preset} ist und der Fühler mit {value} seit 30 Minuten mindestens 1 °C unter {limit} bleibt (höchstens +25 am Tag)",
    "ap.reason.light_heat": "es nach seiner eigenen Erhöhung am Fühler {value} warm ist, über {limit} (zurück auf den Wert davor)",
    "ap.ev.mode_off": "Autopilot aus. Er schaltet nichts mehr, alle Geräte bleiben, wie sie sind.",
    "ap.ev.mode_observe": "Autopilot beobachtet: er rechnet und zeigt, was er tun würde, und schaltet nichts.",
    "ap.ev.mode_auto": "Autopilot auf Automatik: er schaltet in festen Grenzen.",
    "ap.ev.forced_off": "Autopilot aus: VGROW_AUTOPILOT=0 in ~/.vgrow.env.",
    "ap.ev.humidifier_off": "Die Befeuchter-Automatik ist mit ausgeschaltet.",
    "ap.ev.level": "{device}: {level}.",
    "ap.ev.lamp": "Lampenabstand bestätigt (mindestens {cm} bis zur Blattkrone). Die Lichtrampe darf ab jetzt erhöhen.",
    "ap.ev.manual_cockpit": "Du hast {device} selbst gestellt. Der Autopilot fasst es bis {until} nicht an.",
    "ap.ev.manual_app": "{device} in der VIVOSUN-App oder an der Box geändert ({old} auf {new}). Der Autopilot fasst es bis {until} nicht an.",
    "ap.ev.undo": "Rückgängig: {device} zurück auf {value}. Pause bis {until}.",
    "ap.ev.pause_end": "Pause für {device} vorbei: der Autopilot regelt vom jetzigen Wert aus weiter.",
    "ap.ev.failed": "{device} auf {value}: die Box hat es auch beim zweiten Versuch nicht bestätigt. Pause bis {until}.",
    "ap.ev.to_manual": "{device} steht jetzt auf Manuell: drei Fehlschläge an einem Tag. Zurückstellen in den Ausnahmen.",
    "ap.ev.self_off": "Autopilot hat sich selbst ausgeschaltet: drei Befehle hintereinander nicht bestätigt.",
    "ap.ev.safety_flood": "Autopilot hat sich selbst ausgeschaltet: zu viele Sicherheitsbefehle in einer Stunde.",
    "ap.ev.stale": "Die Messwerte sind älter als 10 Minuten: der Autopilot schaltet nichts, bis frische kommen.",
    "ap.ev.ports_changed": "Die Belegung der Anschlüsse hat sich geändert. Alle Automatik pausiert, bis du sie bestätigst.",
    "ap.ev.mist": "Nebel am Fühler: Fühler {probe}, Box {box}. Feuchte und VPD steuern die Abluft nicht. Fühler oder Befeuchter umstellen.",
    "ap.ev.mist_long": "Der Fühler stand in den letzten 24 Stunden {minutes} im Nebel. Fühler oder Befeuchter umstellen.",
    "ap.ev.mist_end": "Kein Nebel mehr am Fühler: Feuchte und VPD zählen wieder.",
    "ap.ev.cold_humid": "Zu kalt und zu feucht zugleich: die Abluft kann nur eins und hält. {advice}",
    "ap.ev.conflict": "Zwei Ziele ziehen die Abluft gleich stark in beide Richtungen: sie hält.",
    "ap.ev.at_bound": "{device} steht am Anschlag ({value}), der Wert bleibt seit 30 Minuten außerhalb. {advice}",
    "ap.ev.budget": "Die Befehlsgrenze ist erreicht. Der nächste automatische Befehl geht frühestens um {at}.",
    "ap.hold.off": "aus",
    "ap.hold.next_poll": "rechnet ab der nächsten Abfrage (alle 90 s)",
    "ap.hold.forced_off": "aus: VGROW_AUTOPILOT=0 in ~/.vgrow.env",
    "ap.hold.locked": "regelt nicht: ein anderer Server regelt diese Box",
    "ap.hold.warmup": "startet: die ersten zwei Abfragen schaltet er nichts",
    "ap.hold.ports": "pausiert: die Anschlussbelegung hat sich geändert, bitte bestätigen",
    "ap.hold.profile": "wartet: plant.json nicht lesbar ({error})",
    "ap.hold.stage": "wartet auf eine bestätigte Phase",
    "ap.hold.harvest": "Ernte oder eine Phase ohne Ziele: der Autopilot regelt nichts",
    "ap.hold.no_link": "wartet: keine Verbindung zur Box",
    "ap.hold.stale": "wartet: Messwerte älter als 10 Minuten",
    "ap.hold.gathering": "sammelt Messwerte (Median der letzten Abfragen)",
    "ap.hold.manual_level": "Manuell: der Autopilot fasst es nicht an",
    "ap.hold.paused": "Pause bis {until}, du hast übernommen",
    "ap.hold.paused_failed": "Pause bis {until}: Befehl nicht bestätigt",
    "ap.hold.no_value": "kein gemeldeter Wert",
    "ap.hold.natural_wind": "Natürlicher Wind ist an: der Autopilot fasst die Umluft nicht an",
    "ap.hold.app_plan": "läuft nach einem Plan der VIVOSUN-App: nicht angefasst (siehe Ausnahmen)",
    "ap.hold.app_auto": "läuft auf Automatik der VIVOSUN-App: nicht angefasst (siehe Ausnahmen)",
    "ap.hold.in_flight": "wartet auf die Bestätigung der Box",
    "ap.hold.on_target": "im Ziel, nichts zu tun",
    "ap.hold.cold_humid": "hält: zu kalt und zu feucht zugleich",
    "ap.hold.conflict": "hält: zwei Ziele ziehen gegeneinander",
    "ap.hold.windup": "hält: die Abluft wirkt hier nicht",
    "ap.hold.at_bound": "am Anschlag ({value})",
    "ap.hold.gap": "wartet auf den Mindestabstand bis {at}",
    "ap.hold.per_device": "Schritte pro Stunde oder Tag erreicht",
    "ap.hold.budget": "Befehlsgrenze erreicht",
    "ap.hold.light_schedule": "Der tägliche Lichttimer übernimmt Ein/Aus und Helligkeit. Einstellungen unter Lichtplan.",
    "ap.hold.quiet": "wartet: eben hat der Autopilot schon etwas geschaltet",
    "ap.hold.refused": "nicht gesendet: {error}",
    "ap.hold.no_preset": "die Phase hat keine Lichtvoreinstellung",
    "ap.hold.light_off": "Licht aus: der Autopilot schaltet es nie ein",
    "ap.hold.lights_unknown": "Lichtzustand unbekannt",
    "ap.hold.at_preset": "auf der Voreinstellung ({value}) oder darüber: keine Rampe",
    "ap.hold.series_stopped": "Rampe angehalten: du hast das Licht selbst geändert. Den Lampenabstand bestätigen setzt sie fort.",
    "ap.hold.lamp": "wartet auf deine Bestätigung des Lampenabstands",
    "ap.hold.lamp_grown": "Pflanze seit der Bestätigung {cm} gewachsen: bitte den Lampenabstand neu bestätigen",
    "ap.hold.wait_24h": "nächster Lichtschritt frühestens {at} (24 Stunden nach der letzten Änderung)",
    "ap.hold.window": "wartet auf das Zeitfenster 1 bis 6 Stunden nach Licht an",
    "ap.hold.too_warm": "wartet: der Fühler muss 30 Minuten unter {limit} bleiben und der VPD im Band",
    "ap.hold.vpd_no_room": "hält: VPD {value} über dem Band, aber weniger Abluft erst, wenn der Fühler höchstens {limit} "
                           "zeigt (sonst wird es zu warm)",
    "ap.hold.vpd_humid": "hält: VPD {value} über dem Band, aber die Feuchte liegt über ihrem Band",
    "ap.hold.vpd_heat": "hält: VPD {value} über dem Band, aber es war eben zu warm: weniger Abluft frühestens {at}",
    "ap.hold.vpd_no_temp": "hält: VPD {value} über dem Band, aber ohne Temperaturband oder Fühlertemperatur bleibt "
                           "die Abluft, wie sie ist",
    "ap.status.observe": "beobachtet {n} von {total} Geräten und schaltet nichts",
    "ap.status.auto": "hat {n} von {total} Geräten auf Automatik",
    "ap.health.hum_off": "Befeuchter-Regel ausgeschaltet",
    "ap.health.hum_unassigned": "Bitte bestätigen, was an USB 2 angeschlossen ist",
    "ap.health.hum_other": "Der Anschluss ist keinem Befeuchter zugeordnet",
    "ap.health.hum_implausible": "Feuchte ist nach dem Einschalten nicht gestiegen; Befeuchter prüfen",
    "ap.health.hum_ready": "Überwacht Feuchte und VPD nach der bestätigten Phase",
    "ap.refused.forced_off": "Der Autopilot ist über VGROW_AUTOPILOT=0 in ~/.vgrow.env gesperrt.",
    "ap.refused.undo_gone": "Dieser Schritt lässt sich nicht mehr rückgängig machen (älter als 30 Minuten, oder der Wert hat sich seitdem geändert).",
    "ap.refused.unknown_box": "Unbekannte Box {box}.",
    "ap.refused.no_ports": "Die Box meldet noch nicht alle Anschlüsse. Gleich noch einmal versuchen.",
    "ap.push.failed_title": "Autopilot: Befehl nicht bestätigt",
    "ap.push.failed_body": "{device}: die Box hat den Befehl auch beim zweiten Versuch nicht bestätigt. Der Autopilot lässt {device} eine Stunde in Ruhe.",
    "ap.push.manual_title": "Autopilot: Gerät auf Manuell",
    "ap.push.manual_body": "{device} ist an einem Tag dreimal gescheitert und steht jetzt auf Manuell, bis du es zurückstellst.",
    "ap.push.off_title": "Autopilot hat sich ausgeschaltet",
    "ap.push.off_body": "Drei Befehle hintereinander sind nicht angekommen. Alle Geräte bleiben, wie sie sind.",
    "ap.push.budget_title": "Autopilot: Befehlsgrenze erreicht",
    "ap.push.budget_body": "Der Autopilot wartet, bis wieder Befehle frei sind. Handbefehle gehen immer.",
    "ap.push.ports_title": "Automatik pausiert: Anschlüsse geändert",
    "ap.push.ports_body": "Die Belegung der USB-Anschlüsse hat sich geändert. Autopilot und Befeuchter-Automatik schalten nichts, bis du die Belegung auf der Seite bestätigst.",
    "ap.push.mist_title": "Fühler im Nebel",
    "ap.push.mist_body": "Der Fühler bekommt Nebel vom Befeuchter ab, in den letzten 24 Stunden zusammen über 2 Stunden. Fühler oder Befeuchter umstellen.",
    "ap.summary": "{day}: Schritte {steps}, hätte getan {would}, rückgängig {undos}, Handgriffe {pauses}, Fehlschläge {failed}. Im Ziel: Temperatur {t}, Feuchte {h}, VPD {v} der Zeit.",
    "ap.hold.mode_unknown": "die Box meldet nicht, ob ein Plan oder die VIVOSUN-Automatik läuft: nicht angefasst",
    "ap.hold.rest": "hält: die Regel für {metric} ruht bis {at}, die Abluft hat dort nicht gewirkt",
    "ap.hold.box_humidity_missing": "die Boxfeuchte fehlt: Feuchte und VPD zählen nicht, nur die Temperatur",
    "ap.hold.watch_high_up": "beobachtet: {metric} liegt bei {value}, über {limit}. Eine Stufe hoch, wenn der Wert {minutes} über {trigger} bleibt",
    "ap.hold.watch_low_down": "beobachtet: {metric} liegt bei {value}, unter {limit}. Eine Stufe runter, wenn der Wert {minutes} unter {trigger} bleibt",
    "ap.hold.watch_low_up": "beobachtet: {metric} liegt bei {value}, unter {limit}. Eine Stufe hoch, wenn der Wert {minutes} unter {trigger} bleibt",
    "ap.hold.watch_high_down": "beobachtet: {metric} liegt bei {value}, über {limit}. Eine Stufe runter, wenn der Wert {minutes} über {trigger} bleibt",
    "ap.hold.mist": "hält: Nebel am Fühler, Feuchte und VPD zählen nicht, nur die Temperatur",
    "ap.hold.undone": "hält: du hast diesen Schritt zurückgenommen, die Regel wartet bis zum nächsten Licht an, spätestens {at}",
    "ap.bound.temp_high": "Weiter kühlen kann sie nicht: den Raum kühlen oder die Lampe dimmen.",
    "ap.bound.temp_low": "Mehr Wärme halten kann sie nicht: den Raum wärmen.",
    "ap.bound.humid": "Mehr Luft tauschen kann sie nicht: die Raumluft muss trockener werden.",
    "ap.bound.dry": "Weiter runter kann sie nicht: gegen die Trockenheit arbeitet nur der Befeuchter.",
    "ap.cold_humid.fix": "Ein wärmerer Raum hilft gegen beides.",
    "ap.status.observe_hum": "beobachtet {n} von {total} Geräten und schaltet nichts; die Befeuchter-Automatik schaltet weiter",
    "ap.push.flood_body": "Mehr als 20 Sicherheitsbefehle in einer Stunde: da stimmt etwas nicht. Der Autopilot ist aus, alle Geräte bleiben, wie sie sind.",
    "ap.refused.always_on": "{port} ist laut ports.json immer an: dort hängt kein schaltbares Gerät.",
    "ap.refused.not_humidifier": "An {port} hängt laut ports.json {device}, kein Luftbefeuchter. Erst ports.json ändern.",
    "auto.port_unassigned_title": "Befeuchter-Automatik wartet auf dich",
    "auto.port_unassigned_body": "Die Automatik schaltet {port} erst ein, wenn du auf der Seite sagst, was dort hängt (Karte Autopilot, Ausnahmen, Befeuchter). Ausschalten und der Fail-Safe laufen weiter.",
    "auto.implausible_title": "Befeuchter-Automatik gestoppt",
    "tank.capacity_range": "Tankgröße muss zwischen 50 und 10000 ml liegen.",
    "tank.rate_range": "Nebelleistung muss zwischen 0,1 und 5000 ml/h liegen oder leer bleiben.",
    "tank.refill_kind": "Bitte bestätigen, ob der Befeuchter-Tank vor dem Nachfüllen leer war.",
    "tank.refill_range": "Wassermenge muss über 0 ml liegen und darf die Tankgröße nicht überschreiten.",
    "tank.button_kind": "Bitte bestätigen, ob ein Startknopf vorhanden ist.",
    "tank.model_kind": "Unbekanntes Befeuchtermodell oder unbekannter Nebelmodus.",
    "tank.model_capacity": "Der AeroStream U075 hat einen 750-ml-Tank.",
    "tank.mist_power": "Nebelstart erst bestätigen, wenn USB 2 als Befeuchter zugeordnet und eingeschaltet ist.",
    "auto.implausible_body": "Nach dem Einschalten von {port} stieg die Feuchte am Fühler in {minutes} Minuten nicht um 3 Punkte ({start} auf {now}). Hängt dort wirklich der Befeuchter, und hat er Wasser? {port} ist aus; die Automatik schaltet erst wieder ein, wenn du auf der Seite neu sagst, was an {port} hängt.",
    # console (ASCII only: Windows consoles and log files with old code pages)
    "console.running": "\nVGrow Cockpit laeuft (inoffiziell, kein VIVOSUN-Produkt).\n"
                       "  Handy (einmal oeffnen): {phone}\n"
                       "  Dieser PC:              {local}\n"
                       "  Alarme per ntfy:        {ntfy}\n"
                       "  Kamera:                 {camera}\n",
    # Not a terminal (launchd, pythonw): stdout is server.log, and the key stays out of it.
    "console.running_hidden": "\nVGrow Cockpit laeuft (inoffiziell, kein VIVOSUN-Produkt).\n"
                              "  Handy (einmal oeffnen): {phone}\n"
                              "  Dieser PC:              {local}\n"
                              "  Schluessel:             steht in {key_file}, als ?key= an den Link haengen "
                              "(absichtlich nicht im Log)\n"
                              "  Alarme per ntfy:        {ntfy}\n"
                              "  Kamera:                 {camera}\n",
    "console.ntfy_on": "an",
    "console.ntfy_off": "AUS (kein ntfy gefunden)",
    "console.no_camera": "keine im Konto",
    "console.camera_at": "{name} auf {ip}",
    "console.camera_off": "AUS ({error})",
    "console.demo_camera": "DEMO-Platzhalter, kein echtes Bild",
    # news (/api/news): the error, and the demo's fixed list (made-up sources, links to example.org)
    "news.unavailable": "Die News sind gerade nicht lesbar.",
    # the local measurement store (/api/measurements/summary)
    "measurements.unavailable": "Der Messwertspeicher ist gerade nicht lesbar.",
    "measurements.bad_request": "Ungültige Anfrage: days ist eine ganze Zahl, band hat die Form messwert:von:bis.",
    "news.demo.official": "Beispielbehörde (Demo)",
    "news.demo.official_title": "Beispiel: Zwischenbericht zur Evaluation des Cannabisgesetzes erschienen",
    "news.demo.court": "Beispielgericht (Demo)",
    "news.demo.court_attribution": "Pressemitteilung des Beispielgerichts vom {date} (Demo)",
    "news.demo.court_title": "Beispiel: Urteil zur Besitzgrenze nach dem Konsumcannabisgesetz",
    "news.demo.advocacy": "Beispielverband (Demo)",
    "news.demo.advocacy_title": "Beispiel: Verband nimmt Stellung zur Anhörung im Gesundheitsausschuss",
    "news.demo.research": "Beispieljournal (Demo)",
    "news.demo.research_title": "Beispiel: Studie zu Lichtspektren in Gewächshäusern",
    "news.demo.horticulture": "Beispielkammer (Demo)",
    # wiki (/api/wiki): the cards of kartei/ as pages
    "wiki.unavailable": "Das Wiki ist gerade nicht lesbar.",
    "wiki.not_found": "Diese Karte gibt es nicht.",
    "wiki.hidden": "Diese Karte gehört zum Cannabis-Wissen, und das ist gerade ausgeschaltet.",
}

EN: dict[str, str] = {
    # requests
    "error.unknown_action": "Unknown action {action}",
    "error.bad_request": "Bad request: {reason}",
    "error.cloud": "The cloud did not answer: {reason}",
    "error.unknown_lang": "Unknown language {code}, use de or en",
    "guard.wrong_key": "Wrong key",
    "guard.need_key": "Open the link with ?key=... once on this device.",
    # page metadata (the home-screen manifest)
    "meta.title": "VGrow Cockpit (unofficial)",
    "meta.description": "Unofficial cockpit for the VIVOSUN VGrow. Not by VIVOSUN.",
    # box and ports
    "port.always_on": "{port} always stays on (ports.json)",
    "port.rules_unreadable": "The port configuration cannot be read: switching off stays blocked until it is fixed",
    "box.no_connection": "No live connection to the box right now",
    "box.unknown_spectrum": "Unknown spectrum {name}",
    "box.spectrum_manual_only": "The spectrum only changes in manual mode without a running plan",
    "box.natural_wind_app": "Natural wind cannot be switched here yet, please use the Vivosun app",
    "box.not_a_port": "{port} is not a port on this box",
    "box.port_not_manual": "{port} runs on auto or a plan; switch it to manual in the Vivosun app first",
    "box.no_vgrow": "No VGrow ({model}) in this Vivosun account. Found: {found}",
    "box.none_found": "nothing",
    "box.error.connection": "Connection: {reason}",
    "box.error.login": "Login: {reason}",
    "box.error.sensors": "Readings: {reason}",
    "box.not_logged_in": "Not logged in to VIVOSUN yet",
    "box.login_rejected": "VIVOSUN rejects the login ({reason}). Please check VIVOSUN_EMAIL and VIVOSUN_PASSWORD in "
                          "~/.vgrow.env and restart the server",
    "box.retry_at": "{reason}. Next try at {at}",
    "box.retry_slow": "{reason}. Next try at {at}; after a rejection at most every {minutes} minutes",
    "box.login_capped": "At most {count} logins per hour, next try at {at}",
    "box.login_capped_after": "{reason}. At most {count} logins per hour, next try at {at}",
    "box.login_rejected_before": "VIVOSUN rejected the last login before the restart",
    "box.link_dropped": "The cloud closes the connection right after it opens",
    "box.connect_capped": "At most {count} connections per hour, next try at {at}",
    # camera
    "camera.none": "No camera set up",
    "camera.no_account": "No GrowCam with credentials in the Vivosun account",
    "camera.no_ffmpeg": "ffmpeg is missing (pip install imageio-ffmpeg)",
    "camera.not_found": "GrowCam not found on the network, still searching",
    "camera.no_answer": "The camera does not answer",
    "camera.usb_unavailable": "USB camera is not delivering frames. Check the connection and macOS camera permission.",
    "camera.usb_not_seen": "macOS does not see this camera. Check the USB plug and adapter.",
    "camera.too_many_viewers": "Too many live viewers",
    "camera.recording_running": "A recording is already running",
    "camera.low_disk": "Less than {gb} GB free on the server",
    "camera.recording_missing": "Recording not found",
    "camera.stop_first": "Stop the running recording first",
    # demo
    "demo.no_recording": "The demo records nothing: there is no real camera",
    "demo.camera_title": "DEMO camera",
    "demo.camera_caption": "Placeholder, not a real camera image",
    "demo.photo_caption": "Demo daily photo, not a real picture",
    "demo.footer": "VGrow Cockpit (unofficial, not a VIVOSUN product)",
    # plant
    "plant.unknown_stage": "Unknown stage",
    "plant.no_preset": "This stage has no preset",
    "plant.applied_light": "Light to {level}",
    "plant.applied_light_partial": "Light to {level} (target {target}, apply again later)",
    "plant.applied_spectrum": "Spectrum",
    "plant.applied_cfan": "Circulation fan",
    "plant.applied_dfan": "Exhaust fan",
    "plant.ph_or_ec": "Enter pH or EC",
    "plant.ph_range": "pH between 0 and 14",
    "plant.ec_range": "EC in mS/cm between 0 and 10",
    "plant.height_range": "Height in cm between 1 and 300",
    "plant.bloom_bad_date": "Not a date: {value}",
    "plant.bloom_future": "The flowering start cannot be in the future",
    "plant.bloom_before_start": "The flowering start is before this plant's start ({date})",
    "plant.stage_from_bloom": "Estimated from your flowering start on {date}: {stage}. Please confirm or set the "
                              "stage.",
    "plant.bloom_stage_before": "You set the stage {stage}. Set a flowering stage first, then the flowering start.",
    "plant.bloom_by.owner": "entered by you",
    "plant.bloom_by.plan": "from plant.json",
    "plant.bloom_by.stage": "from the day you set the flowering stage",
    "plant.bloom_by.estimate": "estimated from the camera image",
    "plant.not_a_number": "Not a number: {value}",
    # the plant's start and age (plant.age_view, PlantLog.set_start_date); the age never changes a dose
    "plant.age_w1": "1 week",
    "plant.age_wn": "{n} weeks",
    "plant.age_d1": "1 day",
    "plant.age_dn": "{n} days",
    "plant.age_and": "{weeks} and {days}",
    "plant.age_line": "The plant is {age} old (day {days}, plant week {life}), start {date}, {by}.",
    "plant.age_line_approx": "The plant is about {age} old (plant week {life}), start about {date}, {by}.",
    "plant.age_unknown": "Age unknown: no start day yet.",
    "plant.age_ask_unknown": "When did the plant germinate? Enter the start day or its age in weeks on the plant "
                             "card.",
    "plant.age_ask_approx": "The start {date} is only approximate. Do you know the day it germinated? Then enter it on "
                            "the plant card.",
    "plant.start_by.owner": "entered by you",
    "plant.start_by.owner_weeks": "entered by you as an age in weeks",
    "plant.start_by.owner_days": "entered by you as an age in days",
    "plant.start_by.plan": "from plant.json",
    "plant.start_one_of": "Give exactly one: a date, an age in weeks or an age in days",
    "plant.start_bad_date": "Not a date: {value}",
    "plant.start_weeks_range": "Age in weeks between 0 and {max}",
    "plant.start_days_range": "Age in days between 0 and {max}",
    "plant.start_future": "The start cannot be in the future",
    "plant.start_too_old": "The start is more than {days} days ago",
    "plant.start_crosses_entry": "You have an entry on {date} (a stage or the flowering start). With the start on "
                                 "{start} it would silently drop out, or an old entry would count again; please "
                                 "check that entry first.",
    "plant.start_preview": "That would be {date}: {age} old, plant week {life}. The feeding amounts do not change.",
    "plant.start_preview_later": "That would be {date}, {n} days later than before ({old}): {age} old, plant week "
                                 "{life}. The feeding amounts do not change.",
    "plant.start_preview_earlier": "That would be {date}, {n} days earlier than before ({old}): {age} old, plant "
                                   "week {life}. The feeding amounts do not change.",
    "plant.start_preview_same": "That is the start that already counts ({date}).",
    # feeding planner
    "feeding.unknown_setup": "There is no such fertilizer in plant.json: {setup} (available: {known})",
    "feeding.bad_unit": "The feeding card {card} has a unit the cockpit does not compute: {unit}",
    "feeding.one_card_only": "Only one fertilizer applies at a time: two charts together would double the feed.",
    "feeding.needs_choice": "Choose the fertilizer first: until then the cockpit computes no amounts",
    "feeding.label_ends": "From flowering week {week} on the table gives no amount, so the cockpit computes none. "
                          "The amount is your call",
    "feeding.none": "No feeding chart set up (feeding in plant.json)",
    "feeding.no_card": "The feeding card {card} is missing from kartei/nutrients/",
    "feeding.no_page": "The feeding card {card} has no page {page}",
    "feeding.bad_factor": "dose_factor in plant.json must be a number above 0 and at most 1",
    "feeding.bad_date": "{field} in plant.json is not a date (YYYY-MM-DD)",
    "feeding.not_confirmed": "Check and confirm the feeding chart first: until then the cockpit computes no amounts",
    "feeding.not_before": "No fertilizer before {date}: plain water until then, that is your call.",
    "feeding.needs_stage": "Confirm the stage first: until then the week is only an estimate, and the cockpit "
                           "computes no amounts",
    "feeding.wrong_card": "The server now uses the feeding card {card}: please reload the page",
    "feeding.confirmations_broken": "The file with your confirmations ({file}) cannot be read. The cockpit does not "
                                    "overwrite it, or the other fertilizer's confirmation would be lost without a "
                                    "word; please check the file",
    "feeding.unit_withheld": "Amounts for {card} are only on the cockpit page: this card computes in grams and "
                             "spoons, and only milliliters are given here so far",
    "feeding.week_span": "The label names only weeks {first} to {last} for this stage, the plant is older. The "
                         "cockpit stays with this column until you set another stage",
    "feeding.changed": "The chart has changed since the page showed it: please reload and compare again",
    "feeding.harvest": "From flowering week {week} on, the chart has no more feeding",
    "feeding.liters_range": "Liters between {min} and {max}",
    "feeding.liters_large": "{liters} l is more than {limit} l: please check the liters and confirm",
    "feeding.stage_before_bloom": "Your stage is {stage}, but plant.json sets the flowering start to {date}. The "
                                  "cockpit uses the vegetative column; if the flowering start is right, change the "
                                  "stage.",
    "feeding.stage_before_owner_bloom": "Your stage is {stage}, but the plant card holds a flowering start on {date}. "
                                        "The cockpit uses the vegetative column; if the flowering start is right, "
                                        "change the stage.",
    "feeding.stage_misfit": "Your stage is {stage}, but the chart is at {week} (flowering start {date}). Please check "
                            "the stage, or enter the right flowering start as bloom_start in plant.json.",
    # AI assessment
    "vision.limit": "Already {count} assessments today",
    "vision.busy": "An assessment is already running",
    "vision.no_ollama": "Ollama is not running on the Mac. Start the Ollama app on the Mac.",
    "vision.no_model": "The model {model} is missing on the Mac (ollama pull {model}).",
    "vision.timeout": "The local model took longer than {minutes} minutes and was stopped.",
    "vision.failed": "Assessment failed: {reason}",
    "vision.photo_failed": "Daily photo failed: {reason}",
    "vision.unreadable": "The local model returned no readable assessment",
    "vision.unknown": "unknown",
    "vision.none": "none",
    "vision.ctx.photo": "Photo from {when}.",
    "vision.ctx.plant": "Strain: {strain}. Day {day} since the start.",
    "vision.ctx.stage_manual": "Stage set by the operator: {stage}.",
    "vision.ctx.stage_estimate": "Estimated stage, not confirmed by the operator: {stage}.",
    "vision.ctx.height": "Last measured height: {cm} cm on {date}.",
    "vision.ctx.climate": "Climate now: at the probe (leaf height) {probe_temp} °C, {probe_humidity} % humidity, "
                          "VPD {probe_vpd} kPa; built-in sensor {temp} °C, {humidity} %.",
    "vision.ctx.controls": "Light {light} %, spectrum {spectrum}. Circulation fan step {cfan}, "
                           "exhaust fan step {dfan}.",
    "vision.ctx.previous": "Last assessment ({date}): {stage}, issues: {issues}.",
    # pushes
    "push.title": "VGrow: {title}",
    "push.title_demo": "VGrow DEMO: {title}",
    "push.test_title": "Test",
    "push.test_body": "If you can read this, alerts get through.",
    "push.plant": "Plant",
    # pest watch (vgrow/pest_watch.py)
    "pest.title": "Possible pest problem",
    "pest.push_body": "{signs}. Please take a look, under the leaves too, and answer in the cockpit: something there, or nothing.",
    "pest.sign.white_powder": "white, powdery coating",
    "pest.sign.speckles": "many pale dots on leaves",
    "pest.sign.webbing": "fine webbing",
    "pest.sign.insects": "insects, mites, eggs or shed skins",
    "pest.sign.residue": "sticky shine or black coating",
    "pest.sign.curled": "curled or deformed leaves",
    "pest.sign.bud_mold": "mold or mushy spots in buds",
    "pest.sign.chewed": "chewed spots or holes",
    "pest.sign.patches": "yellow or brown patches",
    "pest.sign.unusual": "something else unusual",
    "pest.maybe": ", unsure",
    "pest.where_unknown": "spot unclear",
    "pest.no_camera": "No camera reachable.",
    "pest.failed": "Pest check failed: {reason}",
    "pest.bad_answer": "The answer is yes, no or unsure.",
    "pest.unknown_check": "There is no such check.",
    "pest.bad_card": "That card does not fit the confirmed signs.",
    # the grow report (vgrow/grow_report.py)
    "report.well": "{label} in {stage}: {inside}% of the measured minutes inside the target band ({rows} minutes).",
    "report.poor_above": "{label} in {stage}: only {inside}% inside the target band, {off}% above it ({rows} minutes).",
    "report.poor_below": "{label} in {stage}: only {inside}% inside the target band, {off}% below it ({rows} minutes).",
    "report.pest": "Pests confirmed on {date}: {what}.",
    "report.use_water": "Water: {litres} litres counted.",
    "report.use_runs": "Pump runs without a measured amount: {runs}, {minutes} minutes in all.",
    "report.use_feed": "{label}: {amount}.",
    "report.use_power": "Lamp power: about {kwh} kWh, estimated from {watts} W rated power times the reported brightness. Fans, humidifier, pump and computer are not included.",
    # alerts
    "alert.limit": " (limit {limit})",
    "alert.message": "{label}{limit}. {reading}",
    "alert.message_reminder": "Still: {label}{limit}. {reading}",
    "alert.recovered_title": "Back to normal",
    "alert.recovered": "{label}: back in range. {reading}",
    "alert.reading": "Now {value}.",
    "alert.reading_fallback": "Now {value} at the built-in sensor, the probe reports no value.",
    "alert.no_data": "No readings since the start.",
    "alert.last_reading": "Last reading {minutes} min ago.",
    "alert.camera": "No picture, off the network for {minutes} min. Is its power supply plugged in?",
    "alert.camera_back": "It answers again.",
    "alert.usb_camera": "No picture for at least {minutes} min. {reason}",
    "alert.usb_camera_hours": "No picture for at least {hours} hours. {reason}",
    "alert.usb_camera_why": "The pest watch uses the GrowCam meanwhile.",
    "alert.light_day": "Last 24 hours: {lit} hours of light, {dark} hours dark.",
    "alert.dark_why": "Is the light plan running?",
    "alert.short_day_why": "The stage calls for {hours} hours. Is the wrong light plan running?",
    "alert.stage_limit": "The limit follows the stage {stage}: target at most {max} plus {margin} = {limit}.",
    "alert.margin_points": "{n} points",
    "alert.stage_estimated": "{stage} (estimated)",
    # ---------- the tank (type-9 wet/dry water sensor) ----------
    "alert.tank_wet": "Water in the tank: present.",
    "alert.tank_dry": "The water sensor reports dry.",
    "alert.tank_dry_since": "The water sensor has been dry since {time}.",
    "alert.tank_why": "With the wick the plant draws its water from this tank: please refill it.",
    # humidifier automation
    "auto.none": "No automation set up",
    "auto.target_stage": "First confirm a stage with humidity limits.",
    "auto.target_range": "The switch-off threshold must be between {lo} and {hi} (at least 3 points above the lower limit).",
    "auto.stuck_title": "Humidity stays high",
    "auto.stuck_body": "The humidifier is off, yet the probe reads {value} (target at most {limit}) for over an "
                       "hour. Turn the exhaust fan up.",
    # fail-safe pushes: title by reason and outcome, body = reason sentence + outcome sentence
    "auto.failsafe_title": "Humidifier off: no fresh readings",
    "auto.failsafe_title_probe": "Humidifier off: the probe reports no value",
    "auto.failsafe_title_unconfirmed": "Humidifier: switching off not confirmed",
    "auto.failsafe_title_late": "Humidifier off now",
    "auto.failsafe_stale": "No fresh reading for {minutes} min.",
    "auto.failsafe_never": "No reading since the start.",
    "auto.failsafe_probe": "The probe has reported no humidity for {minutes} min.",
    "auto.failsafe_off": "The cockpit switched the humidifier ({port}) off and the box confirmed it. Off is the safe "
                         "state; the automation switches again once fresh values come in.",
    "auto.failsafe_unconfirmed": "Switching the humidifier ({port}) off is not confirmed: the box does not answer. "
                                 "Please switch the humidifier off yourself; the cockpit tries again at growing intervals "
                                 "(90 s, 3, 6, 12, then every 15 min).",
    "auto.failsafe_late": "The box now reports the humidifier ({port}) as off.",
    # automation without a confirmed stage: title, then cause + outcome + what to do
    "auto.stage_title": "Humidifier automation paused",
    "auto.stage_cause": "The stage is not confirmed; the suggestion is {stage}.",
    "auto.stage_cause_none": "No stage is set.",
    "auto.stage_off": "The cockpit switched the humidifier ({port}) off and the box confirmed it.",
    "auto.stage_not_switchable": "The humidifier ({port}) runs on auto or a plan in the Vivosun app; the cockpit "
                                 "does not switch it.",
    "auto.stage_paused": "The automation is paused until the stage is confirmed: confirm the suggestion on the page "
                         "or set the stage.",
    # automation with an unreadable plant.json: the same pause, the real cause
    "auto.profile_title": "Humidifier automation paused: plant.json unreadable",
    "auto.profile_cause": "plant.json cannot be read ({error}), so the stage and its targets are missing.",
    "auto.profile_paused": "The automation is paused until plant.json can be read again.",
    # autopilot (vgrow/autopilot.py) and the port guard of the humidifier rule (vgrow/portguard.py)
    "auto.port_title": "Humidifier automation paused: ports changed",
    "auto.port_body": "The port assignment has changed. The automation does not switch {port} until you confirm the assignment on the page.",
    "ap.dev.light": "Light",
    "ap.dev.dfan": "Exhaust fan",
    "ap.dev.cfan": "Circulation fan",
    "ap.metric.probe_temp": "the temperature",
    "ap.metric.probe_humidity": "the humidity",
    "ap.metric.probe_vpd": "the VPD",
    "ap.level.auto": "Automatic",
    "ap.level.suggest": "Suggest",
    "ap.level.manual": "Manual",
    "ap.na": "N/A",
    "ap.step.would": "Autopilot would: {device} {old} to {new}, because {reason}.",
    "ap.step.suggest": "Suggestion: {device} {old} to {new}, because {reason}.",
    "ap.step.done": "Autopilot: {device} {old} to {new}, because {reason}.",
    "ap.reason.temp_high": "the probe temperature is {value}, above {limit}",
    "ap.reason.temp_low": "the probe temperature is {value}, below {limit}",
    "ap.reason.hum_high": "the probe humidity is {value}, above {limit}",
    "ap.reason.vpd_low": "the probe VPD is {value}, below {limit} (air too humid)",
    "ap.reason.vpd_high": "the probe VPD is {value}, above {limit} (air too dry; less exhaust keeps the moisture in)",
    "ap.reason.return_base": "all readings have been safely on target for 90 minutes (back to the base step {base})",
    "ap.reason.windup": "the exhaust fan does not work here: {metric} has not moved after two steps (back to the base step {base}, this rule rests for 6 hours, 24 if it happens again within two days)",
    "ap.reason.cfan_preset": "the stage {stage} sets circulation step {step}",
    "ap.reason.light_ramp": "the stage preset is {preset} and the probe has stayed at {value}, at least 1 °C below {limit}, for 30 minutes (at most +25 a day)",
    "ap.reason.light_heat": "the probe reads {value} after its own increase, above {limit} (back to the value before)",
    "ap.ev.mode_off": "Autopilot off. It switches nothing any more; every device stays as it is.",
    "ap.ev.mode_observe": "Autopilot observing: it computes and shows what it would do, and switches nothing.",
    "ap.ev.mode_auto": "Autopilot on automatic: it switches within fixed limits.",
    "ap.ev.forced_off": "Autopilot off: VGROW_AUTOPILOT=0 in ~/.vgrow.env.",
    "ap.ev.humidifier_off": "The humidifier automation is switched off as well.",
    "ap.ev.level": "{device}: {level}.",
    "ap.ev.lamp": "Lamp distance confirmed (at least {cm} to the canopy). The light ramp may raise the light from now on.",
    "ap.ev.manual_cockpit": "You set the {device} yourself. The autopilot leaves it alone until {until}.",
    "ap.ev.manual_app": "{device} changed in the VIVOSUN app or at the box ({old} to {new}). The autopilot leaves it alone until {until}.",
    "ap.ev.undo": "Undone: {device} back to {value}. Paused until {until}.",
    "ap.ev.pause_end": "Pause for the {device} over: the autopilot regulates on from the current value.",
    "ap.ev.failed": "{device} to {value}: the box did not confirm it on the second try either. Paused until {until}.",
    "ap.ev.to_manual": "{device} is now on manual: three failures in one day. Set it back in the exceptions.",
    "ap.ev.self_off": "The autopilot switched itself off: three commands in a row were not confirmed.",
    "ap.ev.safety_flood": "The autopilot switched itself off: too many safety commands in one hour.",
    "ap.ev.stale": "The readings are older than 10 minutes: the autopilot switches nothing until fresh ones arrive.",
    "ap.ev.ports_changed": "The port assignment has changed. All automation is paused until you confirm it.",
    "ap.ev.mist": "Mist at the probe: probe {probe}, box {box}. Humidity and VPD do not drive the exhaust fan. Move the probe or the humidifier.",
    "ap.ev.mist_long": "The probe has been in mist for {minutes} over the last 24 hours. Move the probe or the humidifier.",
    "ap.ev.mist_end": "No more mist at the probe: humidity and VPD count again.",
    "ap.ev.cold_humid": "Too cold and too humid at once: the exhaust fan can only do one, so it holds. {advice}",
    "ap.ev.conflict": "Two targets pull the exhaust fan equally hard both ways: it holds.",
    "ap.ev.at_bound": "{device} is at its limit ({value}) and the reading has stayed outside for 30 minutes. {advice}",
    "ap.ev.budget": "The command limit is reached. The next automatic command goes out at {at} at the earliest.",
    "ap.hold.off": "off",
    "ap.hold.next_poll": "computes from the next poll on (every 90 s)",
    "ap.hold.forced_off": "off: VGROW_AUTOPILOT=0 in ~/.vgrow.env",
    "ap.hold.locked": "not regulating: another server regulates this box",
    "ap.hold.warmup": "starting: it switches nothing on the first two polls",
    "ap.hold.ports": "paused: the port assignment has changed, please confirm",
    "ap.hold.profile": "waiting: plant.json cannot be read ({error})",
    "ap.hold.stage": "waiting for a confirmed stage",
    "ap.hold.harvest": "harvest or a stage without targets: the autopilot regulates nothing",
    "ap.hold.no_link": "waiting: no connection to the box",
    "ap.hold.stale": "waiting: readings older than 10 minutes",
    "ap.hold.gathering": "collecting readings (median of the last polls)",
    "ap.hold.manual_level": "Manual: the autopilot does not touch it",
    "ap.hold.paused": "paused until {until}, you took over",
    "ap.hold.paused_failed": "paused until {until}: command not confirmed",
    "ap.hold.no_value": "no reported value",
    "ap.hold.natural_wind": "Natural wind is on: the autopilot does not touch the circulation fan",
    "ap.hold.app_plan": "runs on a plan of the VIVOSUN app: left alone (see exceptions)",
    "ap.hold.app_auto": "runs on the VIVOSUN app's automation: left alone (see exceptions)",
    "ap.hold.in_flight": "waiting for the box to confirm",
    "ap.hold.on_target": "on target, nothing to do",
    "ap.hold.cold_humid": "holding: too cold and too humid at once",
    "ap.hold.conflict": "holding: two targets pull against each other",
    "ap.hold.windup": "holding: the exhaust fan does not work here",
    "ap.hold.at_bound": "at its limit ({value})",
    "ap.hold.gap": "waiting for the minimum gap until {at}",
    "ap.hold.per_device": "steps per hour or day reached",
    "ap.hold.budget": "command limit reached",
    "ap.hold.light_schedule": "The daily light timer controls on/off and brightness. Open Light schedule for settings.",
    "ap.hold.quiet": "waiting: the autopilot switched something a moment ago",
    "ap.hold.refused": "not sent: {error}",
    "ap.hold.no_preset": "the stage has no light preset",
    "ap.hold.light_off": "light off: the autopilot never switches it on",
    "ap.hold.lights_unknown": "light state unknown",
    "ap.hold.at_preset": "at the preset ({value}) or above: no ramp",
    "ap.hold.series_stopped": "ramp stopped: you changed the light yourself. Confirming the lamp distance resumes it.",
    "ap.hold.lamp": "waiting for you to confirm the lamp distance",
    "ap.hold.lamp_grown": "the plant has grown {cm} since the confirmation: please confirm the lamp distance again",
    "ap.hold.wait_24h": "next light step at {at} at the earliest (24 hours after the last change)",
    "ap.hold.window": "waiting for the window 1 to 6 hours after lights on",
    "ap.hold.too_warm": "waiting: the probe must stay below {limit} for 30 minutes and the VPD in its band",
    "ap.hold.vpd_no_room": "holding: VPD {value} above its band, but less exhaust only once the probe shows at most "
                           "{limit} (it would get too warm)",
    "ap.hold.vpd_humid": "holding: VPD {value} above its band, but the humidity is above its band",
    "ap.hold.vpd_heat": "holding: VPD {value} above its band, but it was too warm just now: less exhaust at {at} at "
                        "the earliest",
    "ap.hold.vpd_no_temp": "holding: VPD {value} above its band, but without a temperature band or probe temperature "
                           "the exhaust stays as it is",
    "ap.status.observe": "observing {n} of {total} devices, switches nothing",
    "ap.status.auto": "has {n} of {total} devices set to Auto",
    "ap.health.hum_off": "Humidifier rule is off",
    "ap.health.hum_unassigned": "Confirm what is connected to USB 2",
    "ap.health.hum_other": "This port is assigned to a different device",
    "ap.health.hum_implausible": "Humidity did not rise after switching on; check the humidifier",
    "ap.health.hum_ready": "Monitoring humidity and VPD for the confirmed stage",
    "ap.refused.forced_off": "The autopilot is locked by VGROW_AUTOPILOT=0 in ~/.vgrow.env.",
    "ap.refused.undo_gone": "This step can no longer be undone (older than 30 minutes, or the value has changed since).",
    "ap.refused.unknown_box": "Unknown box {box}.",
    "ap.refused.no_ports": "The box does not report every port yet. Try again in a moment.",
    "ap.push.failed_title": "Autopilot: command not confirmed",
    "ap.push.failed_body": "{device}: the box did not confirm the command on the second try either. The autopilot leaves the {device} alone for an hour.",
    "ap.push.manual_title": "Autopilot: device on manual",
    "ap.push.manual_body": "{device} failed three times in one day and is now on manual until you set it back.",
    "ap.push.off_title": "The autopilot switched itself off",
    "ap.push.off_body": "Three commands in a row did not arrive. Every device stays as it is.",
    "ap.push.budget_title": "Autopilot: command limit reached",
    "ap.push.budget_body": "The autopilot waits until commands are free again. Manual commands always go through.",
    "ap.push.ports_title": "Automation paused: ports changed",
    "ap.push.ports_body": "The assignment of the USB ports has changed. The autopilot and the humidifier automation switch nothing until you confirm the assignment on the page.",
    "ap.push.mist_title": "Probe in mist",
    "ap.push.mist_body": "The probe gets mist from the humidifier, over 2 hours in total in the last 24 hours. Move the probe or the humidifier.",
    "ap.summary": "{day}: steps {steps}, would have done {would}, undone {undos}, manual changes {pauses}, failures {failed}. On target: temperature {t}, humidity {h}, VPD {v} of the time.",
    "ap.hold.mode_unknown": "the box does not report whether a plan or the VIVOSUN automation runs: left alone",
    "ap.hold.rest": "holding: the rule for {metric} rests until {at}, the exhaust fan did not work there",
    "ap.hold.box_humidity_missing": "no box humidity: humidity and VPD do not count, only the temperature",
    "ap.hold.watch_high_up": "watching: {metric} is {value}, above {limit}. One step up once it stays above {trigger} for {minutes}",
    "ap.hold.watch_low_down": "watching: {metric} is {value}, below {limit}. One step down once it stays below {trigger} for {minutes}",
    "ap.hold.watch_low_up": "watching: {metric} is {value}, below {limit}. One step up once it stays below {trigger} for {minutes}",
    "ap.hold.watch_high_down": "watching: {metric} is {value}, above {limit}. One step down once it stays above {trigger} for {minutes}",
    "ap.hold.mist": "holding: mist at the probe, humidity and VPD do not count, only the temperature",
    "ap.hold.undone": "holding: you undid this step, the rule waits for the next lights on, until {at} at the latest",
    "ap.bound.temp_high": "It cannot cool any further: cool the room or dim the lamp.",
    "ap.bound.temp_low": "It cannot keep in more warmth: warm the room.",
    "ap.bound.humid": "It cannot move more air: the room air has to be drier.",
    "ap.bound.dry": "It cannot go lower: only the humidifier works against the dryness.",
    "ap.cold_humid.fix": "A warmer room helps against both.",
    "ap.status.observe_hum": "observing {n} of {total} devices, switches nothing; the humidifier automation still switches",
    "ap.push.flood_body": "More than 20 safety commands in one hour: something is wrong. The autopilot is off; every device stays as it is.",
    "ap.refused.always_on": "{port} is always on according to ports.json: no switchable device hangs there.",
    "ap.refused.not_humidifier": "According to ports.json, {device} hangs on {port}, not a humidifier. Change ports.json first.",
    "auto.port_unassigned_title": "Humidifier automation waits for you",
    "auto.port_unassigned_body": "The automation switches {port} on only once you say on the page what hangs there (Autopilot card, exceptions, humidifier). Switching off and the fail-safe go on.",
    "auto.implausible_title": "Humidifier automation stopped",
    "tank.capacity_range": "Tank capacity must be between 50 and 10000 ml.",
    "tank.rate_range": "Mist output must be between 0.1 and 5000 ml/h, or left blank.",
    "tank.refill_kind": "Please confirm whether the humidifier tank was empty before refilling.",
    "tank.refill_range": "Water volume must be greater than 0 ml and cannot exceed tank capacity.",
    "tank.button_kind": "Please confirm whether there is a start button.",
    "tank.model_kind": "Unknown humidifier model or mist mode.",
    "tank.model_capacity": "The AeroStream U075 has a 750 ml tank.",
    "tank.mist_power": "Confirm mist only when USB 2 is assigned to the humidifier and reported on.",
    "auto.implausible_body": "After {port} was switched on, the probe humidity did not rise by 3 points in {minutes} minutes ({start} to {now}). Is it really the humidifier there, and does it have water? {port} is off; the automation switches on again only once you say on the page again what hangs on {port}.",
    # console (ASCII only: Windows consoles and log files with old code pages)
    "console.running": "\nVGrow Cockpit is running (unofficial, not a VIVOSUN product).\n"
                       "  Phone (open once): {phone}\n"
                       "  This PC:           {local}\n"
                       "  Alerts via ntfy:   {ntfy}\n"
                       "  Camera:            {camera}\n",
    # Not a terminal (launchd, pythonw): stdout is server.log, and the key stays out of it.
    "console.running_hidden": "\nVGrow Cockpit is running (unofficial, not a VIVOSUN product).\n"
                              "  Phone (open once): {phone}\n"
                              "  This PC:           {local}\n"
                              "  Key:               in {key_file}, append it to the link as ?key= "
                              "(kept out of the log on purpose)\n"
                              "  Alerts via ntfy:   {ntfy}\n"
                              "  Camera:            {camera}\n",
    "console.ntfy_on": "on",
    "console.ntfy_off": "OFF (no ntfy found)",
    "console.no_camera": "none in the account",
    "console.camera_at": "{name} at {ip}",
    "console.camera_off": "OFF ({error})",
    "console.demo_camera": "DEMO placeholder, no real picture",
    # news (/api/news): the error, and the demo's fixed list (made-up sources, links to example.org)
    "news.unavailable": "The news cannot be read right now.",
    # the local measurement store (/api/measurements/summary)
    "measurements.unavailable": "The measurement store cannot be read right now.",
    "measurements.bad_request": "Invalid request: days is a whole number, band has the form metric:low:high.",
    "news.demo.official": "Example authority (demo)",
    "news.demo.official_title": "Example: interim report on the evaluation of the Cannabis Act published",
    "news.demo.court": "Example court (demo)",
    "news.demo.court_attribution": "Press release of the example court of {date} (demo)",
    "news.demo.court_title": "Example: ruling on the possession limit under the Consumer Cannabis Act",
    "news.demo.advocacy": "Example association (demo)",
    "news.demo.advocacy_title": "Example: association comments on the health committee hearing",
    "news.demo.research": "Example journal (demo)",
    "news.demo.research_title": "Example: study on light spectra in greenhouses",
    "news.demo.horticulture": "Example chamber (demo)",
    # wiki (/api/wiki): the cards of kartei/ as pages
    "wiki.unavailable": "The wiki cannot be read right now.",
    "wiki.not_found": "This card does not exist.",
    "wiki.hidden": "This card belongs to the cannabis knowledge, which is switched off right now.",
}

CATALOG: dict[str, dict[str, str]] = {"de": DE, "en": EN}


def normalize(lang: Any) -> str | None:
    value = str(lang or "").strip().lower()
    return value if value in LANGS else None


def settings_path() -> Path:
    return config.STATE_DIR / "settings.json"


def _settings() -> dict[str, Any]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def server_lang() -> str:
    """Language for pushes, the daily reading and requests without X-Lang."""
    return (normalize(_settings().get("lang"))
            or normalize(config.read_env(config.ENV_FILE).get("VGROW_LANG"))
            or DEFAULT_LANG)


def set_server_lang(lang: str) -> str:
    value = normalize(lang)
    if value is None:
        raise UserError(t("error.unknown_lang", code=repr(lang)))
    data = {**_settings(), "lang": value}  # keep whatever else lives in settings.json (Hank's mode, for one)
    # In one step (spec 7): a half-written file would read as {} and silently reset the language and Hank's mode.
    config.atomic_write(settings_path(), json.dumps(data, indent=2) + "\n")
    return value


def current_lang() -> str:
    """The request's language inside a request, the server language everywhere else."""
    return REQUEST_LANG.get() or server_lang()


def t(key: str, /, lang: str | None = None, **params: Any) -> str:
    lang = normalize(lang) or current_lang()
    template = CATALOG[lang].get(key) or DE.get(key)
    if template is None:
        _LOGGER.warning("Missing text %s", key)
        return key
    # A nested text (an error reason kept as a key, a time of day) is said in the same language as its sentence.
    params = {k: v.text(lang) if isinstance(v, Text | TextError | TimeOfDay) else v for k, v in params.items()}
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        _LOGGER.warning("Text %s does not fit its parameters %s", key, sorted(params))
        return template


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def localize(obj: Any, lang: str | None = None) -> Any:
    """A copy for one language: in English every key k takes k_en when present; no *_en key is left over."""
    lang = normalize(lang) or current_lang()
    if isinstance(obj, list):
        return [localize(item, lang) for item in obj]
    if not isinstance(obj, dict):
        return obj
    twins = {k: v for k, v in obj.items() if isinstance(k, str) and k.endswith(TWIN) and len(k) > len(TWIN)}
    out = {k: localize(v, lang) for k, v in obj.items() if k not in twins}
    if lang == "en":
        for k, v in twins.items():
            if v is not None:
                out[k[: -len(TWIN)]] = localize(v, lang)
    return out


def fmt_num(value: Any, lang: str | None = None, digits: int | None = None) -> str:
    """Comma for German, dot for English. digits None: whole numbers bare, others with up to two decimals."""
    if value is None:
        return "?"
    lang = normalize(lang) or current_lang()
    number = float(value)
    if digits is not None:
        text = f"{number:.{digits}f}"
    elif number.is_integer():
        text = str(int(number))
    else:
        text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if lang == "de" else text


def fmt_time(epoch: float, lang: str | None = None) -> str:
    """A time of day in local time: '14:30' in German, '2:30 PM' in US English (as the page writes times)."""
    lang = normalize(lang) or current_lang()
    when = datetime.fromtimestamp(epoch)
    if lang == "de":
        return f"{when:%H:%M}"
    return f"{when.hour % 12 or 12}:{when:%M} {'PM' if when.hour >= 12 else 'AM'}"


def fmt_qty(value: Any, unit: str, lang: str | None = None, digits: int | None = None) -> str:
    """A number with its unit: '31,5 °C', '79 %' in German; '31.5 °C', '79%' in US English."""
    lang = normalize(lang) or current_lang()
    number = fmt_num(value, lang, digits)
    if not unit:
        return number
    return f"{number}{'' if unit == '%' and lang == 'en' else ' '}{unit}"
