"""Hank's answer shape and the acceptance of a polished version (acceptance 23.10.2026; addendum N0, N6).

Two defects from the acceptance screens:
1. "Wie warm ist es?" came back as "Das steht so in meiner Kartei [1]. Das Ziel ist ein allgemeiner Richtwert ...",
   marked "überarbeitet": the polished version had lost the temperature and still replaced the draft. Addendum N0: a
   version that does not pass leaves the draft. Now the draft names its core facts and a polished version replaces
   it only when every one is still there (compose.accept), and a version that says nothing is a filler.
2. Drafts were a wall of text (the light answer dumped the whole stage card with six citations). Now the bubble opens
   with the direct answer, then at most three short sentences and the proposal line; the rest is folded away as
   "Mehr aus der Kartei" (Answer.more).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestServer

from hank_eval import fixtures, report
from hank_eval.fake_ollama import FakeOllama
from vgrow import i18n, plant
from vgrow.hank import compose, context, core, ollama, stores, validator
from vgrow.hank.types import Answer, SourceRef
from vgrow.plant import PlantLog

REPO = Path(__file__).resolve().parent.parent
CASES = {c["id"]: c for c in report.load_cases()}
EXPECTED = report.load_expected()
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
WARM_C = 22.1  # a probe temperature the F22 day does not have (it has none): the asked live value


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def f22(lang: str, *, temp: float | None = None) -> dict[str, Any]:
    """The F22 moment (probe 79 %, light 60 %), optionally with a probe temperature, without its ledger."""
    built = fixtures.build("F22", lang=lang)
    if temp is not None:
        built.snapshot["climate"]["probe_temp"] = temp
        built.checks = plant.evaluate(built.stage, built.snapshot)
        built.plant_view = plant.view(built.profile, PlantLog(), built.snapshot, lang)
    sources = built.sources()
    sources.pop("ledger")
    return sources


def new_hank(*, temp: float | None = WARM_C, client: Any = None) -> core.Hank:
    return core.Hank(live=lambda lang: f22(lang, temp=temp), client=client, index=core.shared_index())


def ask(question: str, lang: str = "de", *, temp: float | None = WARM_C) -> core.Asked:
    return asyncio.run(new_hank(temp=temp).ask_full(question, lang=lang, model=False))


def sheet(question: str, lang: str = "de", *, temp: float | None = WARM_C) -> context.Context:
    """The context of a question as Hank builds it (route, cards, passage sources)."""
    hank = new_hank(temp=temp)
    asked = asyncio.run(hank.ask_full(question, lang=lang, model=False))
    return context.build(asked.route, hank._sources(lang), None, lang, question=question,
                         proposals=asked.answer.proposals, passage_sources=hank._passage_sources)


def proposal_line(answer: Answer) -> str:
    """The proposal line compose.tail writes for the answer's cards (empty without cards)."""
    labels = [p.label for p in answer.proposals if p.label]
    if not labels:
        return ""
    if len(labels) == 1:
        two = next(p for p in answer.proposals if p.label).two_taps  # a two-tap card says so (compose.tail)
        line = i18n.t("hank.proposal.sentence_two" if two else "hank.proposal.sentence", answer.lang, label=labels[0])
    else:
        line = i18n.t("hank.proposal.sentence_many", answer.lang, labels="; ".join(labels))
    if any(p.kind == "port" for p in answer.proposals):
        line += " " + i18n.t("hank.proposal.hint_automation", answer.lang, minutes=60)
    return line


def bubble_sentences(answer: Answer) -> list[str]:
    """The bubble's sentences without the proposal line, which must close the bubble when there are cards, and
    without the code's lead line in front (v1.3, Answer.lead: "Im Chat schalte ich nichts ohne dich: ..."), which is
    checked on its own."""
    body = answer.text
    if answer.lead:
        assert body.startswith(answer.lead + "\n"), (body, answer.lead)
        body = body[len(answer.lead) + 1:]
    line = proposal_line(answer)
    if line:
        assert body.endswith(line), (body, line)
        body = body[: -len(line)]
    return validator.split_sentences(body)


def first_sentence(answer: Answer) -> str:
    body = answer.text[len(answer.lead) + 1:] if answer.lead else answer.text
    return validator.split_sentences(body)[0]


# ---------- A. the acceptance of a polished version ----------

def test_the_draft_names_its_core_facts() -> None:
    ctx = sheet("Wie warm ist es?")
    draft = compose.build(ctx)
    direct = draft.core[0]
    assert direct.kind == "direct" and direct.words == ("im Ziel",)
    assert [(n.value, n.unit) for n in direct.numbers] == [(WARM_C, "°C")]
    assert draft.text.startswith("Temperatur am Fühler: 22,1 °C, im Ziel (Ziel 20 bis 26 °C")


def test_a_polished_version_that_lost_the_number_keeps_the_draft() -> None:
    ctx = sheet("Wie warm ist es?")
    draft = compose.build(ctx)
    lost = dataclasses.replace(draft, text=draft.text.replace("22,1 °C", "angenehm"))
    assert compose.accept(draft.core, draft, lost, "de") == "direct lost the number 22.1 °C"
    assert compose.accept(draft.core, draft, draft, "de") is None


def test_a_polished_version_that_lost_the_verdict_word_keeps_the_draft() -> None:
    ctx = sheet("Wie warm ist es?")
    draft = compose.build(ctx)
    lost = dataclasses.replace(draft, text=draft.text.replace("im Ziel (", "("))
    assert compose.accept(draft.core, draft, lost, "de") == "direct lost the verdict word 'im Ziel'"


def test_a_polished_version_that_lost_the_proposal_keeps_the_draft() -> None:
    ctx = sheet("Ist die Luftfeuchte gerade okay?")
    draft = compose.build(ctx)
    line = next(c for c in draft.core if c.kind == "proposal")
    assert line.text.startswith("Meine Vorschläge: Abluft auf Stufe 4")
    lost = dataclasses.replace(draft, text=draft.text.split(" Meine Vorschläge:")[0])
    assert compose.accept(draft.core, draft, lost, "de").startswith("proposal lost")


def test_a_polished_version_with_a_new_number_keeps_the_draft() -> None:
    ctx = sheet("Wie warm ist es?")
    draft = compose.build(ctx)
    more = dataclasses.replace(draft, text=draft.text + " Stell die Abluft auf 31 °C.")
    assert compose.accept(draft.core, draft, more, "de") == "new number 31 °C"


def test_a_filler_only_version_keeps_the_draft() -> None:
    ctx = sheet("Wie warm ist es?")
    v = validator.validate("Gern. Schau mal.", ctx, "de")  # catalog fillers alone: no model sentence
    assert v.used_draft and v.text.startswith("Temperatur am Fühler: 22,1 °C, im Ziel")
    v = validator.validate("Das steht so in meiner Kartei [F1].", ctx, "de")  # the screen of 23.10.2026
    assert v.used_draft and [d.reason for d in v.dropped] == ["filler"] and v.rejected.startswith("filler")
    assert v.text.startswith("Temperatur am Fühler: 22,1 °C, im Ziel")
    assert v.report()["rejected"] == v.rejected and v.drop_rate == 0.0  # a version kept back drops no sentence


def test_a_version_that_lost_the_asked_target_keeps_the_draft() -> None:
    q = "Wie hoch darf die Luftfeuchte in der späten Blüte sein?"
    ctx = sheet(q)
    draft = compose.build(ctx)
    assert draft.text.startswith("Späte Blüte (Reife), Luftfeuchte: 40 bis 50 %")
    fid = next(f for f in ctx.prompt_facts if "40 bis 50 %" in ctx.facts[f].text)
    v = validator.validate(f"In der späten Blüte gilt das, was die Karte sagt [{fid}].", ctx, "de")
    # only the question's words: a filler (finding 3); the draft stays
    assert v.used_draft and v.rejected.startswith("filler") and v.text == draft.text
    lost = dataclasses.replace(draft, text="In der späten Blüte gilt, was die Karte sagt [1].")
    assert compose.accept(draft.core, draft, lost, "de") == "direct lost the number 40 %"


def test_a_good_polished_version_is_taken() -> None:
    ctx = sheet("Wie warm ist es?")
    climate = next(g.id for g in ctx.groups if g.key == "climate")
    v = validator.validate(f"Am Fühler ist es mit 22,1 °C zu warm [{climate}].", ctx, "de")
    assert v.used_draft  # "zu warm" against the code's "im Ziel": the verdict is the code's (spec 5.4)
    v = validator.validate(f"Am Fühler misst die Box gerade 22,1 °C [{climate}].", ctx, "de")
    assert not v.used_draft and v.rejected is None and v.kept == 1
    assert v.text.startswith("Temperatur am Fühler: 22,1 °C, im Ziel")  # the code's verdict stays in front
    assert "Am Fühler misst die Box gerade 22,1 °C." in v.sentences


@contextlib.asynccontextmanager
async def fake_server(fake: FakeOllama) -> AsyncIterator[str]:
    server = TestServer(fake.app())
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


def test_the_screen_of_23_10_never_shows_revised_without_the_temperature() -> None:
    """The acceptance screen end to end: the fake model answers as it did then; the draft stays, draft_only."""
    fake = FakeOllama(reply=lambda body: "Das steht so in meiner Kartei [F1].")

    async def go() -> core.Job:
        async with fake_server(fake) as url:
            client = ollama.OllamaClient(url=url)
            try:
                asked = await new_hank(client=client).ask_full("Wie warm ist es?", lang="de")
                assert asked.job is not None and asked.job.task is not None
                await asked.job.task
                return asked.job
            finally:
                await client.close()

    job = asyncio.run(go())
    assert job.model_calls == 1 and job.answer is not None and job.answer.draft_only
    assert job.answer.text == job.draft.text and job.answer.text.startswith("Temperatur am Fühler: 22,1 °C, im Ziel")
    turn = stores.TurnLog().all()[-1]
    assert turn["polished"] is False and turn["validator"]["rejected"].startswith("filler")


# ---------- B. the answer shape ----------

ACTIVE_GROW = [cid for cid, exp in EXPECTED.items() if exp["status"] != "waits" and exp["route_today"] == "grow"]
# What the first sentence of each active growing case must hold: the live value or the asked target.
DIRECT = {
    "grow-01": ["40 bis 50 %"], "grow-02": ["12/12"], "grow-03": ["PPFD"], "grow-04": ["8 bis 9", "120 cm"],
    "grow-05": ["70 to 80%", "milky"], "grow-06": ["Blütewoche 4", "Bio-Bloom 4 ml/l"], "grow-07": ["1/2 bis 1/4"],
    "grow-08": ["40 bis 60 %", "1,2 bis 1,6 kPa"], "grow-17": ["16 bis 20 °C", "55 bis 60 %"],
    "live-01": ["79 %", "zu hoch"], "live-02": ["Bio-Bloom 2 ml/l"], "live-03": ["Licht: 60 %", "zu niedrig"],
    "live-04": ["Phase nicht von dir bestätigt"], "live-05": ["25 Minuten"], "live-06": ["79 %", "zu hoch"],
    "fix-n01": ["79%", "too high"],  # "Can you fix the humidity?": a growing question, never the fix wish
    # feeding v2: grams and spoons of the VIVOSUN card, and no amount while no fertilizer is chosen
    "feed-01": ["Base A 0,75 g, 1,5 Löffel"], "feed-02": ["Base B 0.75 g, 1.5 spoons"],
    "feed-03": ["0,75 g, 1,5 Löffel"], "feed-11": ["Zwei Dünger", "keine Mengen"],
    # critique 23.10.2026: the age of the water and of the seeds is no age of the plant; the other fertilizer's name
    "feed-16": ["Wasserstand im Tank"], "feed-17": ["seed"], "feed-19": ["Gerade gilt BioBizz"],
    "nodata-03": ["Stickstoff", "unten an alten Blättern"],  # 01.11.2026: the nitrogen card (shelves.py)
    "grow-10": ["15 h Licht", "Ruhe"], "grow-13": ["Erbse 8 bis 10"], "grow-18": ["burp twice a day"],  # their cards
    # review 23.10.2026: "Wo finde ich die Photoperiode?" is a growing question again, no buy question
    "strains-13": ["Licht"],
    # VPD too high (23.10.2026): the reading with its verdict stays the bubble's first sentence
    "vpd-01": ["1,73 kPa", "zu hoch"], "vpd-02": ["1.73 kPa", "too high"], "vpd-03": ["1,73 kPa", "zu hoch"],
}
# The code's lead line in front of the bubble (v1.3): only the switching wish of live-03 has one.
LEADS = {"live-03": ["Im Chat schalte ich nichts ohne dich", "Licht auf 85 %"],
         # VPD too high (23.10.2026): why, what runs, what blocks, why the light waits (vgrow/hank/vpd.py)
         "vpd-01": ["zu trocken", "56,0 %", "Beobachten", "Das Licht wartet"],
         "vpd-02": ["too dry", "56.0%", "Observe", "The light waits"],
         "vpd-03": ["zu trocken", "56,0 %", "Beobachten", "Das Licht wartet"]}


def eval_answer(case_id: str) -> Answer:
    case, exp = CASES[case_id], EXPECTED[case_id]
    built = fixtures.build(exp["fixture"], lang=case["lang"], ledger=exp.get("ledger")).sources()
    hank = core.Hank(live=lambda _lang: dict(built), index=core.shared_index())
    return asyncio.run(hank.ask_full(case["question"], lang=case["lang"], model=False)).answer


def test_every_active_growing_case_is_listed() -> None:
    assert sorted(DIRECT) == sorted(ACTIVE_GROW)


@pytest.mark.parametrize("case_id", ACTIVE_GROW)
def test_every_active_eval_bubble_is_short_and_answers_first(case_id: str) -> None:
    answer = eval_answer(case_id)
    sentences = bubble_sentences(answer)
    assert 1 <= len(sentences) <= compose.BUBBLE_SENTENCES, (case_id, sentences)
    first = sentences[0]
    for needle in DIRECT[case_id]:
        assert needle in first, (case_id, needle, first)
    # a switching wish opens with "Im Chat schalte ich nichts ohne dich" and its card (review 1, N04), own line
    assert (answer.lead is not None) == (case_id in LEADS), (case_id, answer.lead)
    if answer.lead:
        assert all(needle in answer.lead for needle in LEADS[case_id]), (case_id, answer.lead)
    # at most three numbered sources in sight; only a direct sentence that itself cites more may bring more
    refs = {int(n) for found in re.findall(r"\[(\d+(?:,\s*\d+)*)\]", answer.text) for n in found.split(",")}
    if len(answer.sources) > compose.BUBBLE_SOURCES:
        own = {int(n) for found in re.findall(r"\[(\d+(?:,\s*\d+)*)\]", first) for n in found.split(",")}
        assert own == refs == set(range(1, len(answer.sources) + 1)), (case_id, answer.text)
    assert refs <= set(range(1, len(answer.sources) + 1)), (case_id, answer.text)
    assert all(s.id not in ("cockpit", "live") for s in answer.sources + answer.more_sources)


def test_the_folded_part_numbers_its_sources_on_and_counts_for_the_eval() -> None:
    answer = eval_answer("live-04")
    assert answer.more and answer.more_sources
    refs = {int(n) for found in re.findall(r"\[(\d+(?:,\s*\d+)*)\]", answer.more) for n in found.split(",")}
    assert max(refs) == len(answer.sources) + len(answer.more_sources)
    plain = answer.plain()
    assert answer.text in plain and answer.more in plain
    assert f"[{len(answer.sources) + 1}] {answer.more_sources[0].id}" in plain
    shown = core.answer_dict(answer)["more"]
    assert shown["text"] == answer.more and [s["id"] for s in shown["sources"]] == [s.id for s in answer.more_sources]
    assert core.answer_dict(dataclasses.replace(answer, more="", more_sources=[]))["more"] is None


def test_answer_plain_lists_the_folded_sources_after_the_bubble() -> None:
    a = Answer("a-1", "grow", "de", "Satz [1].", [SourceRef("S5", "VIVOSUN", None)], more="Mehr [2].",
               more_sources=[SourceRef("S7", "Lampe", "https://example.org")])
    assert a.plain() == "Satz [1]. Mehr [2].\nQuellen: [1] S5 VIVOSUN; [2] S7 Lampe https://example.org"


@pytest.mark.parametrize(("question", "lang", "start"), [
    ("Wie warm ist es?", "de", "Temperatur am Fühler: 22,1 °C, im Ziel"),
    ("Ist es zu warm?", "de", "Temperatur am Fühler: 22,1 °C, im Ziel"),
    ("Wie kalt ist es?", "de", "Temperatur am Fühler: 22,1 °C, im Ziel"),
    ("How warm is it?", "en", "Temperature at the probe: 22.1 °C, on target"),
    ("Is it too hot?", "en", "Temperature at the probe: 22.1 °C, on target"),
    ("Wie feucht ist es?", "de", "Luftfeuchte am Fühler: 79 %, zu hoch"),
    ("How humid is it?", "en", "Humidity at the probe: 79%, too high"),
    ("Wie ist das Licht?", "de", "Licht: 60 %, zu niedrig"),
])
def test_a_live_question_gets_the_live_reading_first(question: str, lang: str, start: str) -> None:
    asked = ask(question, lang)
    assert asked.route.name == "grow", (question, asked.route.reason)
    assert asked.answer.text.startswith(start), asked.answer.text
    assert len(bubble_sentences(asked.answer)) <= compose.BUBBLE_SENTENCES


def test_no_reading_says_so_first() -> None:
    answer = ask("Wie warm ist es?", temp=None).answer
    assert answer.text.startswith("Temperatur am Fühler: kein Messwert.")


def test_the_next_feeding_is_a_growing_question_with_the_rhythm_first() -> None:
    asked = ask("Wann dünge ich das nächste Mal?")
    assert asked.route.name == "grow"  # it resembled "Wann ist das nächste Länderspiel?" and came back off-topic
    assert first_sentence(asked.answer).startswith("Die Tabelle sagt nicht, wie oft gedüngt wird")
    # "wann", not "wie viel": no dose sentences (finding 10); they come with every amount question (addendum N5)
    assert "Faktor 0,5" not in asked.answer.text and "Nie mehr als die Tabelle." not in asked.answer.text
    assert "Entwurf" not in asked.answer.text
    amount = ask("Wie viel dünge ich das nächste Mal?").answer.text
    assert "Faktor 0,5" in amount and "Nie mehr als die Tabelle." in amount
    assert ask("Wie mach ich ein gutes Pesto aus Basilikum, Pinienkernen und Parmesan?").route.name == "offtopic"


def test_the_light_answer_no_longer_dumps_the_stage_card() -> None:
    answer = eval_answer("live-03")
    assert "Temperatur ohne Licht" not in answer.text and "Temperatur ohne Licht" in answer.more
    assert "Das Cockpit erhöht das Licht höchstens um 25 Punkte pro Tippen" in answer.text
    assert len(answer.sources) <= compose.BUBBLE_SOURCES


# ---------- C. the page and its texts ----------

def test_more_has_its_text_in_both_languages_and_a_native_details_element() -> None:
    raw = (REPO / "web" / "hank-i18n.js").read_text(encoding="utf-8")
    catalog = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    assert catalog["de"]["hank.more"] == "Mehr aus der Kartei"
    assert catalog["en"]["hank.more"] == "More from the card file"
    js = (REPO / "web" / "hank.js").read_text(encoding="utf-8")
    assert 'el("details", "hk-more")' in js and 'say(el("summary", "hk-more-h"), "hank.more")' in js
    assert "a.more" in js and "ol.start = offset + 1" in js
    assert ".hk-more" in (REPO / "web" / "hank.css").read_text(encoding="utf-8")


# ---------- quality review of the fix (23.10.2026): the same defect class for other questions ----------

BEFEUCHTER = "Warum ist der Befeuchter aus?"
AUTO_STAGE = "Solange die Phase nicht von dir bestätigt ist, schaltet die Automatik ihn nie ein."


def group_marker(ctx: context.Context, key: str) -> str:
    return next(g.id for g in ctx.groups if g.key == key)


def test_a_direct_answer_without_a_number_is_kept_word_for_word() -> None:
    """Must 1: the automation's reason has no number, so accept() checked nothing and "Die Luftfeuchte ist hoch"
    replaced it, marked "überarbeitet"."""
    ctx = sheet(BEFEUCHTER, temp=None)
    draft = compose.build(ctx)
    assert draft.text.startswith(AUTO_STAGE)
    direct = next(c for c in draft.core if c.kind == "direct")
    assert direct.exact and direct.text == AUTO_STAGE and not direct.numbers
    lost = dataclasses.replace(draft, text=draft.text.replace(AUTO_STAGE, "Die Luftfeuchte ist hoch [1]."))
    assert compose.accept(draft.core, draft, lost, "de").startswith("direct lost")
    v = validator.validate(f"Die Luftfeuchte ist hoch [{group_marker(ctx, 'climate')}].", ctx, "de")
    assert v.used_draft and v.rejected and v.text == draft.text and AUTO_STAGE in v.text
    # a version with something to say keeps the reason in the code's words, first
    v = validator.validate(f"Die Automatik ist an, das Band liegt bei 45 bis 60 % [{group_marker(ctx, 'automation')}].",
                           ctx, "de")
    assert v.text.startswith(AUTO_STAGE), v.text


def test_the_light_advice_is_said_in_both_languages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Must 2 (guarantee C): the English answer lost "raise it step by step, check the distance to the lamp first"
    because it shared "light 100 flower" with the verdict. Each language in its own state: the ledger says an
    advice once (spec 6.3), so a second ask in the same state would leave it out for that reason."""
    from vgrow import config

    de = ask("Wie ist das Licht?", temp=None).answer.text  # the F22 day as the eval set has it
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state-en")
    en = ask("How is the light?", "en", temp=None).answer.text
    assert "schrittweise hoch, vorher Abstand zur Lampe prüfen" in de
    assert "raise it step by step, check the distance to the lamp first" in en


def test_the_verdict_advice_is_a_core_fact() -> None:
    """Should 5: a polished version that moves the advice into "more" keeps the draft."""
    hank = new_hank(temp=None)
    asked = asyncio.run(hank.ask_full("Wie ist das Licht?", lang="de", model=False))
    ctx = context.build(asked.route, hank._sources("de"), [], "de", question="Wie ist das Licht?",
                        proposals=asked.answer.proposals, passage_sources=hank._passage_sources)  # no ledger yet
    draft = compose.build(ctx)
    advice = next(c for c in draft.core if c.kind == "advice")
    assert advice.exact and advice.text.startswith("Licht unter der VGrow-Blühvoreinstellung")
    lost = dataclasses.replace(draft, text=draft.text.replace(advice.text, ""), more=f"{draft.more} {advice.text}")
    assert compose.accept(draft.core, draft, lost, "de").startswith("advice lost")


def test_restates_needs_most_of_the_words() -> None:
    assert not compose._restates("Light below the VGrow flower preset (100%): raise it step by step.",
                                 "Light: 60%, too low (target 100%, Early flower (stretch)).", "en")
    assert compose._restates("Das Cockpit erhöht höchstens 25 Punkte pro Tippen.",
                             "Das Cockpit erhöht das Licht höchstens um 25 Punkte pro Tippen.", "de")


@pytest.mark.parametrize(("question", "lang"), [
    ("Ist die Erde zu feucht?", "de"), ("Wie feucht muss die Erde sein?", "de"),
    ("Is the soil too wet or humid?", "en"), ("Wie heiß wird die Lampe?", "de"),
    ("Can I water with cold water?", "en"),
])
def test_an_adjective_about_another_object_names_no_air_reading(question: str, lang: str) -> None:
    """Should 6: "feucht", "heiß", "cold" name the air's reading only when the question names no other object."""
    asked = ask(question, lang)
    text = asked.answer.text
    for reading in ("Luftfeuchte am Fühler", "Temperatur am Fühler", "Humidity at the probe",
                    "Temperature at the probe"):
        assert not text.startswith(reading), (question, text)
    assert "Temperatur am Fühler: kein Messwert" not in text and "Abluft höher" not in text.split(".")[0]


def test_an_adjective_alone_still_asks_for_the_reading() -> None:
    assert ask("Wie warm ist es?").answer.text.startswith("Temperatur am Fühler: 22,1 °C")
    assert ask("Wie feucht ist es?").answer.text.startswith("Luftfeuchte am Fühler: 79 %")


def test_next_time_is_no_timer() -> None:
    """Should 7: "time" matched the German "Timer" (both stemmed to "tim")."""
    from vgrow.hank import text

    assert text.tokens("Timer", lang="de") == ["timer"] and text.tokens("time", lang="de") == ["tim"]
    asked = ask("When do I feed the next time?", "en")
    assert "timer" not in asked.route.retrieval.slots.devices
    assert not asked.answer.proposals and "Humidity at the probe" not in asked.answer.text
    assert asked.answer.text.startswith("The chart does not say how often to feed")


def test_a_mandatory_sentence_that_restates_the_answer_says_it_once() -> None:
    """Should 8 (grow-07): the card's "1/2 bis 1/4" and the dose factor sentence with the same claim."""
    answer = eval_answer("grow-07")
    assert answer.text.count("1/2 bis 1/4") == 1, answer.text
    assert answer.text.startswith("Faktor 0,5 für die Autoflower")


def test_a_seed_shop_number_carries_its_marker() -> None:
    """Should 9 (grow-04): "indoor bis 120 cm laut einem Samenhändler" stood without its source."""
    answer = eval_answer("grow-04")
    first = bubble_sentences(answer)[0]
    assert "120 cm" in first and re.search(r"\[\d+\]", first), first
    assert any("Hanfgarten" in s.title for s in answer.sources)
    assert context.overlap(["laut"], "laut einem Samenhändler", "de") == 0  # "laut" (loud) is not "laut" (per)


def test_stale_readings_get_no_card_text_and_the_tank_answers_with_its_level() -> None:
    """Should 11: live-05 folded seven card sources under the stale sentence; "Wie viel Wasser ist im Tank?" opened
    with the BioBizz chart."""
    stale = eval_answer("live-05")
    assert not stale.more and not stale.more_sources
    assert ask("Wie viel Wasser ist im Tank?").answer.text.startswith("Wasserstand im Tank: 75 %.")


def test_reference_numbers_are_ascending_and_the_legal_marker_sits_before_the_period() -> None:
    """Nice 15: "[2, 1, 3]" and "KCanG). [1]"."""
    numbering = compose.Numbering("de")
    a, b, c = (SourceRef(i, f"Quelle {i}", None) for i in ("S1", "S2", "S3"))
    numbering.number(b)
    assert numbering.render(compose.Piece("Ein Satz.", (a, b, c))) == "Ein Satz [1, 2, 3]."
    legal = eval_answer("legal-01").text
    assert "). [1]" not in legal and "KCanG) [1]." in legal, legal


SHAPE_FILES = [REPO / "vgrow" / "hank" / name for name in ("compose.py", "context.py", "validator.py", "router.py",
                                                           "core.py", "types.py", "texts_live.py", "slots.py",
                                                           "text.py", "replies.py")]
SHAPE_FILES += [REPO / "web" / name for name in ("hank.js", "hank-i18n.js", "hank.css")]
SHAPE_FILES += [REPO / "kartei" / "_hank" / "vocab.json", REPO / "scripts" / "preview_mock.js",
                REPO / "scripts" / "hank_screens.py", Path(__file__)]


@pytest.mark.parametrize("path", SHAPE_FILES, ids=lambda p: p.name)
def test_changed_files_have_no_em_dash_no_bom_and_lf(path: Path) -> None:
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r\n" not in data and EM_DASH not in data.decode("utf-8")



def test_more_numbers_on_from_every_source_of_the_bubble() -> None:
    """Nice 14: compose numbers every source, titled or not; the folded list goes on from all of them."""
    js = (REPO / "web" / "hank.js").read_text(encoding="utf-8")
    assert "moreBlock(a.more, (a.sources || []).length)" in js


def test_a_consequence_of_another_reading_is_never_said() -> None:
    """At 53 % (on target) "Bleibt es dauerhaft zu feucht, drohen Ertragsverluste" stood in the bubble of "Wie
    feucht ist es?" (demo box, 23.10.2026): what follows from "too high" is no fact while the reading is fine."""
    built = fixtures.build("F22", lang="de")
    built.snapshot["climate"]["probe_humidity"] = 53.0
    built.checks = plant.evaluate(built.stage, built.snapshot)
    built.plant_view = plant.view(built.profile, PlantLog(), built.snapshot, "de")
    sources = built.sources()
    sources.pop("ledger")
    hank = core.Hank(live=lambda _lang: dict(sources), index=core.shared_index())
    answer = asyncio.run(hank.ask_full("Wie feucht ist es?", lang="de", model=False)).answer
    assert answer.text.startswith("Luftfeuchte am Fühler: 53 %, im Ziel"), answer.text
    assert "Ertragsverluste" not in answer.text and "Ertragsverluste" not in answer.more


# ---------- C. device status questions (live test on the Mac, 23.10.2026) ----------

@pytest.mark.parametrize(("question", "lang", "want"), [
    ("Läuft der Lüfter?", "de", "umluft"),
    ("Läuft die Abluft?", "de", "abluft"),
    ("Ist die Lampe an?", "de", "licht"),
    ("Is the fan running?", "en", "fan"),
])
def test_a_device_status_question_opens_with_the_device_state(question: str, lang: str, want: str) -> None:
    """"Läuft der Lüfter?" opened with the strain card's "Braucht gute Lüftung gegen Schimmel"; the device's own
    state line answers first now."""
    answer = ask(question, lang).answer
    assert answer.route == "grow", answer.route
    first = first_sentence(answer)
    assert want in first.lower(), first
    assert "Schimmel" not in first and "mold" not in first.lower(), first


def test_a_lamp_height_question_still_answers_from_the_card() -> None:
    """Only status wordings get the device line; "Wie hoch soll die Lampe hängen?" is no status question."""
    first = first_sentence(ask("Wie hoch soll die Lampe hängen?").answer)
    assert not first.startswith("Licht "), first
