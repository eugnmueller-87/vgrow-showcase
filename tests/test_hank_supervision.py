"""Hank and the supervision rules (Aufsicht, 15.11.2026: the thirst index sat nine days without a measurement and
nothing said so; vgrow/supervision.py turns such silences into alarm rules of alerts.json).

Red supervision rules reach Hank's light like every alarm rule. Yellow ones are no rung of the ladder, because the
ladder is first-match and a standing yellow rule would hide every climate and thirst line: they are merged into what
the ladder found (nudges._with_notice). While poll_stalled is active the offline rung is skipped, so the light names the
dead poll and does not blame the box. The today list and the fix answer carry the rule's own hint (alerts.json hint,
hint_en), never "Alles im Ziel" while one is active.

The rules here are this file's own (RULES, texts after the spec), written into a copy of alerts.json beside the
existing rules; the rows are built the way Alerts.view gives them (kind, level, signal), so nothing depends on the
rule file or the view code of the supervision itself. The live state is the eval fixture F22 with the day frozen,
changed with variant() of tests/test_hank_fix.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hank_eval import fixtures
from test_hank_fix import MIN, Clock, variant
from test_hank_today import quiet as today_quiet
from vgrow import config, i18n
from vgrow.hank import core, fix, nudges, stores, texts_live, today

SUP = "supervision"
RULES: dict[str, dict[str, Any]] = {
    "poll_stalled": {
        "label": "Abfrage der Box steht", "label_en": "Box polling stalled",
        "hint": "Solange laufen Alarme, Automatik, Gießplan und Durst-Index nicht. Bitte VGrow auf dem Mac neu starten.",
        "hint_en": "Until then alerts, automation, the watering plan and the thirst index do not run. Please restart "
                   "VGrow on the Mac.",
        "level": "red", "priority": 4, "for_minutes": 1, "remind_hours": 6, "driver": "watchdog"},
    "soil_silent": {
        "label": "Bodensensor meldet nichts", "label_en": "Soil sensor reports nothing",
        "hint": "Der Durst-Index sieht solange nichts vom Boden. Gateway: Netzteil und WLAN prüfen. Fühler: Batterie "
                "prüfen.",
        "hint_en": "The thirst index sees nothing of the soil meanwhile. Gateway: check power and WiFi. Probe: check "
                   "the battery.",
        "level": "red", "priority": 4, "for_minutes": 20, "remind_hours": 24},
    "soil_uncalibrated": {
        "label": "Bodensensor nicht eingestellt", "label_en": "Soil sensor not set",
        "hint": "Der Durst-Index bekommt deshalb nichts von ihm. Auf der Seite Wasser & Boden: gießen, 2 Stunden "
                "abtropfen lassen, dann einmal Einstellen tippen.",
        "hint_en": "The thirst index gets nothing from it. On the Water & Soil page: water, let it drain for 2 hours, "
                   "then tap Set once.",
        "level": "yellow", "priority": 3, "for_minutes": 10, "remind_hours": 24},
    "news_silent": {
        "label": "Nachrichten-Abruf ruht", "label_en": "News fetch is idle",
        "hint": "Die Nachrichtenliste ist nicht mehr aktuell. Nichts Dringendes.",
        "hint_en": "The news list is out of date. Nothing urgent.",
        "level": "yellow", "priority": 2, "for_minutes": 60, "remind_hours": 24},
    "ollama_silent": {  # no hint: the plain sentences
        "label": "Lokales Modell antwortet nicht", "label_en": "Local model is not answering",
        "level": "yellow", "priority": 2, "for_minutes": 10, "remind_hours": 24},
}
for _name, _rule in RULES.items():
    _rule.update(kind=SUP, metric=SUP, signal=_name, source="Testregel dieser Datei, nicht alerts.json")
CALM = {"probe": 50.0, "light": 100, "auto": {"needs_stage_confirmation": False}}  # nothing off target (test_hank_fix)
SOIL_DE = RULES["soil_uncalibrated"]["hint"]


def row(name: str) -> dict[str, Any]:
    """A rule's row as Alerts.view gives it, with the supervision's kind, level and signal."""
    rule = RULES[name]
    return {"name": name, "label": rule["label"], "metric": SUP, "fallback": None, "op": None, "limit": None,
            "for_minutes": rule["for_minutes"], "active": False, "stage_limit": None, "kind": SUP,
            "level": rule["level"], "signal": name}


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> fixtures.Live:
    """F22 with this file's rules: in the view (any supervision rows of the repo's view replaced) and in a copy of
    alerts.json, where Hank reads label and hint."""
    fixtures.freeze(monkeypatch)
    live = fixtures.build("F22")
    live.alerts_view = [r for r in live.alerts_view if SUP not in (r.get("kind"), r.get("metric"))] + \
        [row(name) for name in RULES]
    real = json.loads(config.ALERTS_FILE.read_text(encoding="utf-8"))
    own = {k: v for k, v in real["rules"].items() if isinstance(v, dict) and v.get("metric") != SUP}
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps({**real, "rules": {**own, **RULES}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "ALERTS_FILE", path)
    return live


# ---------- the light ----------

def test_a_red_supervision_rule_makes_hank_red_with_its_label(base: fixtures.Live) -> None:
    found = nudges.assess(variant(base, alerts=("soil_silent",)))
    assert (found.level, found.reason) == ("red", "alert:soil_silent")
    assert found.text == {"de": "Jetzt handeln: Bodensensor meldet nichts.",
                          "en": "Act now: Soil sensor reports nothing."}
    assert found.problems == ["alert:soil_silent"]  # no check belongs to it


def test_a_yellow_rule_alone_gives_a_yellow_light_that_names_it(base: fixtures.Live) -> None:
    assert nudges.assess(variant(base, **CALM)).level == "green"
    found = nudges.assess(variant(base, **CALM, alerts=("soil_uncalibrated",)))
    assert (found.level, found.reason) == ("yellow", "notice:soil_uncalibrated")
    assert found.text == {"de": "Bitte nachsehen: Bodensensor nicht eingestellt.",
                          "en": "Please take a look: Soil sensor not set."}
    assert found.problems == ["alert:soil_uncalibrated"] and found.alerts == []
    two = nudges.assess(variant(base, **CALM, alerts=("soil_uncalibrated", "news_silent")))
    assert two.reason == "notice:news_silent+soil_uncalibrated"  # sorted, like the red reason
    assert two.text["de"] == "Bitte nachsehen: Bodensensor nicht eingestellt, Nachrichten-Abruf ruht."
    assert two.problems == ["alert:news_silent", "alert:soil_uncalibrated"]


def test_a_yellow_rule_beside_a_climate_verdict_keeps_the_climate_text_and_appends_the_notice(
        base: fixtures.Live) -> None:
    plain = nudges.assess(variant(base))  # F22: probe 79 %, light 60 %
    both = nudges.assess(variant(base, alerts=("soil_uncalibrated",)))
    assert plain.level == both.level == "yellow" and plain.reason.startswith("check:")
    assert both.text["de"] == plain.text["de"] + " Bitte nachsehen: Bodensensor nicht eingestellt."
    assert both.text["en"] == plain.text["en"] + " Please take a look: Soil sensor not set."
    assert both.text["de"].startswith("Genauer hinschauen: Luftfeuchte am Fühler: 79 %")
    assert both.reason == plain.reason + ";notice:soil_uncalibrated"
    assert both.problems == [*plain.problems, "alert:soil_uncalibrated"]
    assert [v.metric for v in both.negative] == [v.metric for v in plain.negative] and both.sources == plain.sources


def test_red_wins_and_the_yellow_rule_is_not_in_the_red_list(base: fixtures.Live) -> None:
    found = nudges.assess(variant(base, alerts=("humidity_high", "soil_uncalibrated")))
    assert (found.level, found.reason) == ("red", "alert:humidity_high")
    assert [a["name"] for a in found.alerts] == ["humidity_high"]
    assert "alert:soil_uncalibrated" not in found.problems and "Bodensensor" not in found.text["de"]
    assert found.text["de"] == "Jetzt handeln: Zu feucht (Fühler)."
    red = nudges.assess(variant(base, alerts=("soil_silent", "news_silent")))
    assert (red.level, red.reason) == ("red", "alert:soil_silent") and "Nachrichten" not in red.text["de"]


def test_the_offline_rung_is_skipped_while_poll_stalled_is_active(base: fixtures.Live) -> None:
    old = variant(base, age=1500)  # 25 minutes: past the 900 s rung
    assert nudges.assess(old).reason == "offline"
    found = nudges.assess(variant(base, age=1500, alerts=("poll_stalled",)))
    assert (found.level, found.reason) == ("red", "alert:poll_stalled")
    assert found.text["de"] == "Jetzt handeln: Abfrage der Box steht." and "offline" not in found.problems
    # another red rule leaves the offline rung as it is: only the dead poll explains old readings
    assert nudges.assess(variant(base, age=1500, alerts=("soil_silent",))).reason == "offline"


def test_one_note_per_new_set_and_an_update_in_place_for_a_changed_set(base: fixtures.Live) -> None:
    clock = Clock()
    store = stores.JsonDoc("aufsicht.json", {"version": 1})

    def tick(*names: str, calm: bool = True) -> nudges.NudgeState:
        live = variant(base, **(CALM if calm else {}), alerts=names)
        return nudges.tick(live, None, clock.advance(MIN), store=store)

    assert tick().level == "green"
    first = tick("soil_uncalibrated")
    assert first.level == "yellow" and [m["op"] for m in first.messages] == ["new"]
    assert first.messages[0]["text"]["de"] == "Bitte nachsehen: Bodensensor nicht eingestellt."
    assert first.messages[0]["fix"] == ["alert:soil_uncalibrated"]
    assert first.light("de")["fix"] == ["alert:soil_uncalibrated"]  # the light's tap asks the fix answer for it
    assert tick("soil_uncalibrated").messages == []  # the same set is never noted twice
    more = tick("soil_uncalibrated", "news_silent")
    assert [m["op"] for m in more.messages] == ["update"]  # another notice at the same level: in place
    assert more.messages[0]["fix"] == ["alert:news_silent", "alert:soil_uncalibrated"]
    climate = tick("soil_uncalibrated", "news_silent", calm=False)  # a climate line comes beside the notices
    assert [m["op"] for m in climate.messages] == ["update"]
    assert climate.text["de"].startswith("Genauer hinschauen:") and climate.text["de"].endswith(
        "Bitte nachsehen: Bodensensor nicht eingestellt, Nachrichten-Abruf ruht.")
    red = tick("soil_uncalibrated", "soil_silent", calm=False)  # a red supervision rule: a new note
    assert red.level == "red" and [m["op"] for m in red.messages] == ["new"]
    assert red.messages[0]["fix"] == ["alert:soil_silent"]


# ---------- the today list ----------

def test_today_names_a_red_rule_with_its_hint_and_a_yellow_one_as_a_notice(base: fixtures.Live) -> None:
    red, _ = today.lines(variant(base, alerts=("soil_silent",)), "de")
    assert red[0] == "Jetzt handeln, Bodensensor meldet nichts: " + RULES["soil_silent"]["hint"]
    assert red[1].startswith("Luftfeuchte am Fühler: 79 %")  # its check line stays: no check belongs to the rule
    yellow, _ = today.lines(variant(base, alerts=("soil_uncalibrated",)), "de")
    assert yellow[0] == f"Bitte nachsehen, Bodensensor nicht eingestellt: {SOIL_DE}"
    assert yellow[1].startswith("Luftfeuchte am Fühler: 79 %")
    assert not any(line.startswith("Jetzt handeln") for line in yellow)  # a yellow rule is never a red line
    en, _ = today.lines(variant(base, alerts=("soil_uncalibrated",)), "en")
    assert en[0] == "Please take a look, Soil sensor not set: " + RULES["soil_uncalibrated"]["hint_en"]
    calm = today_quiet(variant(base, alerts=("soil_uncalibrated",)))
    assert today.lines(calm, "de")[0] == [f"Bitte nachsehen, Bodensensor nicht eingestellt: {SOIL_DE}"]
    assert today.lines(today_quiet(variant(base)), "de")[0] == ["Alles im Ziel, heute nichts zu tun."]
    bare = today_quiet(variant(base, alerts=("ollama_silent",)))  # a rule without a hint: the light's words
    assert today.lines(bare, "de")[0] == ["Bitte nachsehen: Lokales Modell antwortet nicht."]


def test_today_puts_a_stalled_poll_first_and_does_not_blame_the_box(base: fixtures.Live) -> None:
    lines, _ = today.lines(variant(base, age=1500, alerts=("poll_stalled",)), "de")
    assert lines[0] == "Jetzt handeln, Abfrage der Box steht: " + RULES["poll_stalled"]["hint"]
    assert lines[1] == "Die Messwerte sind 25 Minuten alt."
    assert not any(line.startswith("Seit 25 Minuten kommen keine frischen Messwerte") for line in lines)


# ---------- the fix answer ----------

def test_the_fix_answer_says_label_and_hint_never_all_on_target(base: fixtures.Live) -> None:
    line_de = f"Bodensensor nicht eingestellt: {SOIL_DE}"
    only = fix.compose(variant(base, alerts=("soil_uncalibrated",)), "de", keys=["alert:soil_uncalibrated"])
    assert only.lines == [line_de] and not only.nothing and only.cards == [] and only.advised == []
    en = fix.compose(variant(base, alerts=("soil_uncalibrated",)), "en", keys=["alert:soil_uncalibrated"])
    assert en.lines == ["Soil sensor not set: " + RULES["soil_uncalibrated"]["hint_en"]]
    # old readings do not hold it back: a stalled poll is what ages them
    stalled = fix.compose(variant(base, age=1500, alerts=("poll_stalled",)), "de", keys=["alert:poll_stalled"])
    assert stalled.lines == ["Abfrage der Box steht: " + RULES["poll_stalled"]["hint"]]
    # typed "Kannst du das beheben?" (no keys) with nothing else off target: the rule's line, not "Alles im Ziel"
    calm = fix.compose(variant(base, **CALM, alerts=("soil_uncalibrated",)), "de")
    assert calm.lines == [line_de] and not calm.nothing
    assert fix.compose(variant(base, **CALM), "de").lines == ["Alles im Ziel, da gibt es nichts zu beheben."]
    # a rule that is no longer active is a problem back on target, as any other
    gone = fix.compose(variant(base, **CALM), "de", keys=["alert:soil_uncalibrated"])
    assert gone.lines == ["Alles im Ziel, da gibt es nichts zu beheben."] and gone.nothing
    # no hint: the label alone
    assert fix.compose(variant(base, **CALM, alerts=("ollama_silent",)), "de").lines == \
        ["Lokales Modell antwortet nicht."]


def test_a_mixed_set_puts_the_supervision_lines_first(base: fixtures.Live) -> None:
    keys = ["probe_humidity:high", "alert:soil_uncalibrated"]
    plain = fix.compose(variant(base), "de", keys=["probe_humidity:high"])
    mixed = fix.compose(variant(base, alerts=("soil_uncalibrated",)), "de", keys=keys)
    assert mixed.lines == [f"Bodensensor nicht eingestellt: {SOIL_DE}", *plain.lines]
    assert [c.body for c in mixed.cards] == [c.body for c in plain.cards] and mixed.advised == plain.advised
    # the light's own keys at red: the red supervision rule first, then what helps against the humidity
    red = fix.compose(variant(base, alerts=("soil_silent", "humidity_high")), "de",
                      keys=["alert:humidity_high", "alert:soil_silent", "probe_humidity:high"])
    alone = fix.compose(variant(base, alerts=("humidity_high",)), "de",
                        keys=["alert:humidity_high", "probe_humidity:high"])
    assert red.lines == ["Bodensensor meldet nichts: " + RULES["soil_silent"]["hint"], *alone.lines]
    assert red.lines[1].startswith("Das hilft") and red.lines[2].startswith("Jetzt handeln: Luftfeuchte am Fühler")
    # the note's humidity is back on target, its supervision rule is not: that rule is the answer, not "resolved"
    back = fix.compose(variant(base, **CALM, alerts=("soil_uncalibrated",)), "de", keys=keys)
    assert back.lines == [f"Bodensensor nicht eingestellt: {SOIL_DE}"]
    # typed with old readings: the rule's line before the old-readings sentences
    old = fix.compose(variant(base, age=700, alerts=("soil_uncalibrated",)), "de")
    assert old.lines == [f"Bodensensor nicht eingestellt: {SOIL_DE}", "Die Messwerte sind 11 Minuten alt.",
                         "Ohne frische Messwerte schlage ich nichts vor."]


def test_an_unchecked_rule_is_said_as_unchecked_never_as_the_parts_fault(base: fixtures.Live) -> None:
    """Review of 15.11.2026: a supervision reader that raised (state unknown in the alarm view, Alerts.found) made Hank
    say "Jetzt handeln: Bodensensor meldet nichts" and the part's consequences as if verified. He says what the push
    says: the part cannot be checked, which is no finding about it, and its one step."""
    live = variant(base, alerts=("soil_silent",))
    for alert in live["alerts_view"]:
        if alert["name"] == "soil_silent":
            alert["state"] = "unknown"
    found = nudges.assess(live)
    assert found.level == "red" and found.text == {"de": "Jetzt handeln: Bodensensor meldet nichts (nicht prüfbar).",
                                                   "en": "Act now: Soil sensor reports nothing (cannot be checked)."}
    lines, _ = today.lines(live, "de")
    assert lines[0].startswith("Jetzt handeln, Bodensensor meldet nichts (nicht prüfbar): Die Aufsicht kann diesen "
                               "Teil gerade nicht prüfen; das ist kein Befund über den Teil selbst.")
    answer = fix.compose(live, "en", keys=["alert:soil_silent"])
    assert answer.lines == ["Soil sensor reports nothing (cannot be checked): The supervision cannot check this part "
                            "right now; this is no finding about the part itself. Please restart VGrow on the Mac; if "
                            "it happens again, read the log."]
    assert all(RULES["soil_silent"]["hint"] not in line for line in lines)  # the silent part's cost is not verified
    # the same rule silent again: its own label and hint
    for alert in live["alerts_view"]:
        alert.pop("state", None)
    assert nudges.assess(live).text["de"] == "Jetzt handeln: Bodensensor meldet nichts."


def test_red_supervision_rules_come_before_yellow_ones_in_the_fix_answer(base: fixtures.Live) -> None:
    found = fix.compose(variant(base, **CALM, alerts=("soil_uncalibrated", "soil_silent")), "de")
    assert [line.split(":")[0] for line in found.lines] == ["Bodensensor meldet nichts",
                                                             "Bodensensor nicht eingestellt"]


# ---------- texts ----------

def test_the_new_texts_have_both_languages_and_pass_the_catalog_checks() -> None:
    assert core is not None  # importing core registers every Hank catalog (spec 10.3)
    assert texts_live.check() == [] and today.check() == [] and fix.check() == []
    for key in ("hank.nudge.yellow_notice", "hank.today.notice", "hank.fix.supervision", "hank.nudge.unchecked",
                "hank.nudge.unchecked_hint"):
        assert key in i18n.DE and key in i18n.EN, key
    assert i18n.t("hank.nudge.yellow_notice", "de", detail="X") == "Bitte nachsehen: X"
    assert i18n.t("hank.nudge.yellow_notice", "en", detail="X") == "Please take a look: X"
    assert i18n.t("hank.today.notice", "en", label="A", hint="B") == "Please take a look, A: B"
    for text in (texts_live.DE["hank.nudge.yellow_notice"], texts_live.EN["hank.nudge.yellow_notice"],
                 today.DE["hank.today.notice"], today.EN["hank.today.notice"]):
        assert chr(0x2013) not in text and chr(0x2026) not in text  # no en dash, no ellipsis
