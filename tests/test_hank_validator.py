"""Hank WP6: the validator and the claim checks (spec 5, addendum N1). Every case is one model text against the context
of a real eval case, with the outcome the validator must reach: kept, or dropped with its reason."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import hank_live_kit as kit
from hank_eval import fixtures
from vgrow import i18n
from vgrow.hank import claims, compose, context, texts_live, validator
from vgrow.hank.types import Proposal

Q_HUMID = "Ist die Luftfeuchte gerade okay?"
HITS = ["stages/autoflower#early_flower.probe_humidity.all", "stages/autoflower#early_flower.probe_vpd.all",
        "stages/autoflower#notes.2"]


@pytest.fixture(autouse=True)
def frozen(monkeypatch):  # type: ignore[no-untyped-def]
    fixtures.freeze(monkeypatch)
    texts_live.register()


def humid_ctx(lang: str = "de", *, live: dict | None = None, question: str | None = None, hits: list[str] = HITS,
              slots: object = None, intent: str = "info", proposals: list[Proposal] = ()) -> context.Context:
    question = question or (Q_HUMID if lang == "de" else "Is the humidity okay right now?")
    r = kit.route(question, lang, hits, intent=intent, slots=slots)
    return context.build(r, live or kit.live(lang=lang), None, lang, question=question, proposals=list(proposals),
                         limit=10_000)


def fid(ctx: context.Context, passage_id: str) -> str:
    return next(f.id for f in ctx.facts.values() if f.passage == passage_id)


def fid_kind(ctx: context.Context, needle: str) -> str:
    return next(f.id for f in ctx.facts.values() if needle in f.text)


def check(ctx: context.Context, model: str, lang: str | None = None) -> validator.Validation:
    return validator.validate(model, ctx, lang)


def kept(v: validator.Validation) -> bool:
    return not v.used_draft and v.kept > 0


def reasons(v: validator.Validation) -> list[str]:
    return [d.reason for d in v.dropped]


# ---------- the table: (model text with {H} humidity fact, {V} vpd fact, {N} notes.2 fact), kept, reason ----------

DE_CASES = [
    # numbers and units
    ("Am Fühler sind es 79 % [S2].", True, None),
    ("Am Fühler sind es 80 % [S2].", False, "number"),
    ("Am Fühler sind es 79 °C [S2].", False, "unit"),
    ("Am Fühler sind es 79 Prozent [S2].", True, None),
    ("Das Ziel in der frühen Blüte ist 45 bis 60 % [{H}].", True, None),
    ("Das Ziel in der frühen Blüte ist 45-60 % [{H}].", True, None),
    ("Das Ziel in der frühen Blüte ist zwischen 45 und 60 % [{H}].", True, None),
    ("Das Ziel in der frühen Blüte ist 45 bis 65 % [{H}].", False, "number"),
    ("Das Ziel in der frühen Blüte ist 40 bis 60 % [{H}].", False, "number"),
    ("Die Box zeigt innen 47 % [S2].", True, None),
    ("Die Box zeigt innen 47 % [{H}].", False, "number"),
    ("Innen 47 % [S2], als Ziel 45 bis 60 % [{H}].", True, None),
    ("Innen 47 % und als Ziel 45 bis 60 % [S2, {H}].", True, None),
    ("Innen 47 % und als Ziel 45 bis 60 % [S2][{H}].", True, None),
    ("Der VPD soll 1,0 bis 1,4 kPa sein [{V}].", True, None),
    ("Der VPD soll 1,2 bis 1,4 kPa sein [{V}].", False, "number"),
    ("Der VPD soll 1,0 bis 1,4 % sein [{V}].", False, "unit"),
    ("Für Autos nimm 18/6 oder 20/4 [{N}].", True, None),
    ("Für Autos nimm 16/8 [{N}].", False, "number"),
    ("Die Blühvoreinstellung schaltet auf zwölf Stunden Licht [{N}].", False, "unit"),
    ("Stell die Abluft zwei Stufen höher [{H}].", False, "unit"),  # 2 stands there, but as USB 2, not as steps
    ("Das dauert drei Wochen [{H}].", False, "number"),
    ("Es braucht zwanzig Minuten [S2].", False, "number"),
    ("Die Messwerte sind 1 Min alt [S1].", True, None),
    ("Die Phase ist geschätzt, seit dem 22.10. [S7].", True, None),
    ("Die Phase läuft seit Tag 42 [S7].", True, None),
    ("Die Phase läuft seit Tag 43 [S7].", False, "number"),
    # markers
    ("Die Luftfeuchte am Fühler ist sehr feucht.", False, "no_marker"),
    ("Die Luftfeuchte am Fühler ist sehr feucht [F99].", False, "no_marker"),
    ("Die Luftfeuchte am Fühler ist sehr feucht [f1].", False, "no_marker"),
    ("Am Fühler sind es 79 %. [S2]", True, None),
    ("Am Fühler sind es 79 % [S2] .", True, None),
    # fillers (addendum N1: word for word only)
    ("Gern.", None, "filler"),
    ("Schau mal.", None, "filler"),
    ("Gern geschehen.", False, "no_marker"),
    ("Gute Frage, die Feuchte ist hoch.", False, "no_marker"),
    # switch claims (replaced by hank.reply.no_self)
    ("Ich habe den Befeuchter ausgeschaltet [S5].", None, "claim"),
    ("Ich hab die Abluft hochgedreht [S4].", None, "claim"),
    ("Hab ich dir schon eingestellt [S3].", None, "claim"),
    ("Das Licht ist jetzt auf 85 % [S3].", None, "claim"),
    ("Ich schalte jetzt den Befeuchter aus [S5].", None, "claim"),
    ("Ich habe die Phase bestätigt [S7].", None, "claim"),
    ("Ich habe die Phase bestaetigt [S7].", None, "claim"),
    ("Im Chat schalte ich nichts ohne dich [S5].", True, None),  # Hank's own rule; the flat old one: see below
    # verdict words against the code's verdict (humidity is high)
    ("Die Luftfeuchte am Fühler passt [S2].", False, "verdict"),
    ("Die Luftfeuchte ist okay [S2].", False, "verdict"),
    ("Die Luftfeuchte liegt im Ziel [S2].", False, "verdict"),
    ("Die Feuchte ist in Ordnung [S2].", False, "verdict"),
    ("Die Luftfeuchte ist mit 79 % zu hoch [S2].", True, None),
    ("Das Licht steht auf 60 % und ist okay [S3].", True, None),  # no verdict for light here: nothing to contradict
    # forbidden content (chat_policy never_say)
    ("Nach der Ernte kannst du sie rauchen [{H}].", False, "compliance"),
    ("Die Wirkung wird stärker [{H}].", False, "compliance"),
    ("Verkaufen darfst du sie nicht [{H}].", False, "compliance"),
    ("Der Geschmack wird besser [{H}].", False, "compliance"),
    ("Ein Joint geht später [{H}].", False, "compliance"),
]

EN_CASES = [
    ("At the probe it is 79% [S2].", True, None),
    ("At the probe it is 79.5% [S2].", False, "number"),
    ("The target is 45 to 60% [{H}].", True, None),
    ("The target is 45 to 65% [{H}].", False, "number"),
    ("The VPD should be 1.0 to 1.4 kPa [{V}].", True, None),
    ("The VPD should be 1,0 to 1,4 kPa [{V}].", True, None),  # a comma that is no thousands mark reads as decimal
    ("For autos use 18/6 or 20/4 [{N}].", True, None),
    ("I've switched the humidifier off [S5].", None, "claim"),
    ("I turned the exhaust fan up [S4].", None, "claim"),
    ("The light is now at 85% [S3].", None, "claim"),
    ("I'll switch it off for you [S5].", None, "claim"),
    ("I am lowering the light [S3].", None, "claim"),
    ("In the chat I switch nothing without you [S5].", True, None),
    ("The humidity is fine [S2].", False, "verdict"),
    ("The humidity is within range [S2].", False, "verdict"),
    ("You can smoke it after drying [{H}].", False, "compliance"),
    ("The taste gets better [{H}].", False, "compliance"),
    ("Sure.", None, "filler"),
    ("Sure thing.", False, "no_marker"),
]


def _fill(ctx: context.Context, model: str) -> str:
    return model.format(H=fid(ctx, HITS[0]), V=fid(ctx, HITS[1]), N=fid(ctx, HITS[2]))


def _assert_case(ctx: context.Context, model: str, keep: bool | None, reason: str | None, lang: str) -> None:
    v = check(ctx, _fill(ctx, model), lang)
    if reason == "filler":
        assert [d for d in v.dropped] == [] and v.used_draft  # a filler alone is no model sentence: the draft
        return
    if reason == "claim":
        assert v.replaced and "claim" in reasons(v)
        return
    if keep:
        assert kept(v) and v.dropped == [], (model, v.dropped)
        assert "[F" not in v.text and "[S" not in v.text
    else:
        assert reasons(v) == [reason], (model, v.dropped)
        assert v.used_draft and v.text == compose.build(ctx).text


@pytest.mark.parametrize(("model", "keep", "reason"), DE_CASES)
def test_validator_table_de(model: str, keep: bool | None, reason: str | None) -> None:
    _assert_case(humid_ctx("de"), model, keep, reason, "de")


@pytest.mark.parametrize(("model", "keep", "reason"), EN_CASES)
def test_validator_table_en(model: str, keep: bool | None, reason: str | None) -> None:
    _assert_case(humid_ctx("en"), model, keep, reason, "en")


# ---------- whole answers ----------

def test_validator_keeps_good_sentences_and_puts_verdicts_first() -> None:
    proposal = Proposal(id="p-1", kind="dfan", body={"action": "dfan", "step": 4},
                        label=i18n.t("hank.proposal.dfan", "de", step=4), reason="", expires=0.0, two_taps=False)
    ctx = humid_ctx(proposals=[proposal])
    h = fid(ctx, HITS[0])
    model = f"Gern. Zu feucht hilft: Befeuchter aus, Abluft höher [{h}]. Die Box zeigt innen 47 % [S2]."
    v = check(ctx, model)
    assert kept(v) and v.kept == 2 and v.dropped == [] and not v.used_draft
    assert v.sentences[0].startswith("Luftfeuchte am Fühler: 79 %, zu hoch")
    assert v.sentences[-1] == "Mein Vorschlag: Abluft auf Stufe 4. Du bestätigst ihn mit einem Tipp."
    assert "Die Box zeigt innen 47 %." in v.sentences  # a live reading needs no source marker (acceptance 23.10.)
    assert "Gern." not in v.more  # a filler fits into the bubble or is left out, never folded away
    assert v.sources[0].id == "S5" and all(s.id not in ("live", "cockpit") for s in v.sources + v.more_sources)
    assert v.report()["kept"] == 2 and v.drop_rate == 0.0 and v.report()["rejected"] is None


def test_validator_mixed_answer_drops_only_the_bad_sentence() -> None:
    ctx = humid_ctx()
    h = fid(ctx, HITS[0])
    v = check(ctx, f"Das Ziel ist 45 bis 60 % [{h}]. Bei 90 % wird es gefährlich [{h}].")
    assert v.kept == 1 and reasons(v) == ["number"] and not v.used_draft
    assert "90" not in v.text and v.drop_rate == 0.5


def test_validator_claim_is_replaced_once_and_the_rest_stays() -> None:
    ctx = humid_ctx()
    v = check(ctx, "Ich habe den Befeuchter ausgeschaltet [S5]. I've switched the fan off [S4]. "
                   "Am Fühler sind es 79 % [S2].")
    assert v.replaced and reasons(v) == ["claim", "claim"]
    assert v.text.count(i18n.t("hank.reply.no_self", "de")) == 1
    assert "ausgeschaltet" not in v.text and "79 %" in v.text


# "Hank regelt" (23.10.2026) is the autopilot under Hank's name: the filler must stay true in every mode, so it
# speaks of the chat only. The safety behaviour is the same as before: the claim goes, the filler comes once.
NO_SELF = {"de": "Im Chat schalte ich nichts ohne dich. Du kannst es mit einem Tipp bestätigen.",
           "en": "In the chat I switch nothing without you. You can confirm it with one tap."}


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_filler_speaks_of_the_chat_and_is_no_claim_itself(lang: str) -> None:
    assert i18n.t("hank.reply.no_self", lang) == NO_SELF[lang]
    assert not claims.switch_claim(NO_SELF[lang], lang)
    assert "never switch anything" not in NO_SELF[lang] and "Ich schalte nichts selbst" not in NO_SELF[lang]


FLAT = {"de": "Ich schalte nichts selbst", "en": "I never switch anything myself"}


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_flat_old_rule_alone_drops_and_is_no_claim(lang: str) -> None:
    """Under "Hank regelt" the flat "Ich schalte nichts selbst" is no longer Hank's rule; from the model it is no
    switch claim (nothing is replaced), it drops as the flat rule, and nothing else is left: the draft."""
    ctx = humid_ctx(lang)
    v = check(ctx, f"{FLAT[lang]} [S5].")
    assert not v.replaced and reasons(v) == ["flat_rule"] and v.used_draft
    assert FLAT[lang] not in v.text and v.text == compose.build(ctx).text


MIXED = {"de": ["Am Fühler sind es 79 % [S2]. {flat} [S5].", "{flat} [S5]. Die Box zeigt innen 47 % [S2].",
                "Am Fühler sind es 79 % [S2]. Die Box zeigt innen 47 % [S2]. {flat}, du entscheidest [S5]."],
         "en": ["At the probe it is 79% [S2]. {flat} [S5].", "{flat} [S5]. Inside the box it shows 47% [S2].",
                "At the probe it is 79% [S2]. Inside the box it shows 47% [S2]. {flat}, you decide [S5]."]}


@pytest.mark.parametrize("lang", ["de", "en"])
@pytest.mark.parametrize("shape", [0, 1, 2])
def test_the_flat_old_rule_drops_in_a_mixed_answer_too(lang: str, shape: int) -> None:
    """The review's case: next to a grounded model sentence the flat rule stayed in "kept", because only grounded
    pieces count for the filler check. Now it drops in every shape, and the grounded sentences stay."""
    v = check(humid_ctx(lang), MIXED[lang][shape].format(flat=FLAT[lang]))
    assert reasons(v) == ["flat_rule"] and not v.replaced, v.dropped
    assert FLAT[lang].lower() not in v.text.lower()
    assert ("79 %" if lang == "de" else "79%") in v.text
    assert NO_SELF[lang] not in v.text  # the flat rule takes no filler


@pytest.mark.parametrize("lang", ["de", "en"])
def test_a_real_claim_next_to_the_flat_rule_keeps_its_one_filler(lang: str) -> None:
    claim = {"de": "Ich habe den Befeuchter ausgeschaltet [S5].", "en": "I've switched the humidifier off [S5]."}
    probe = {"de": "Am Fühler sind es 79 % [S2].", "en": "At the probe it is 79% [S2]."}
    for model in (f"{claim[lang]} {FLAT[lang]} [S5]. {probe[lang]}", f"{FLAT[lang]} [S5]. {claim[lang]} {probe[lang]}"):
        v = check(humid_ctx(lang), model)
        assert v.replaced and sorted(reasons(v)) == ["claim", "flat_rule"], v.dropped
        assert v.text.count(NO_SELF[lang]) == 1
        assert FLAT[lang] not in v.text and ("ausgeschaltet" if lang == "de" else "switched the") not in v.text
    # a sentence that is both: the claim wins, the filler comes once
    both = {"de": "Ich schalte nichts selbst, ich habe den Befeuchter aber ausgeschaltet [S5].",
            "en": "I never switch anything myself, I've switched the humidifier off [S5]."}
    v = check(humid_ctx(lang), f"{both[lang]} {probe[lang]}")
    assert v.replaced and reasons(v) == ["claim"] and v.text.count(NO_SELF[lang]) == 1


@pytest.mark.parametrize("sentence", [
    "Ich schalte nichts selbst.", "Ich schalte nie etwas.", "Ich schalte niemals selbst.", "Ich selbst schalte nichts.",
    "Selbst schalte ich nichts.", "Nie schalte ich.", "Ich schalte keine Geräte.", "Hank schaltet nichts selbst.",
    "Ich regle nichts selbst.", "Ich kann nichts selbst schalten.", "Ich darf nie etwas einschalten.",
    "Ich schalte nichts selbst, du entscheidest.", "Ich schalte nichts selbst. Du kannst es mit einem Tipp bestätigen.",
    "I never switch anything myself.", "I don't switch anything.", "I do not switch anything myself.",
    "I can't turn anything on.", "Hank never switches anything himself.", "I switch nothing myself.",
    "I never actually control the devices.",
    # "ohne dich", "until you" are no scope: under "Hank regelt" the autopilot switches without a tap (review, must 1)
    "Ich schalte nichts ohne dich.", "I switch nothing without you.", "I never switch anything until you tap.",
    "Ich schalte nichts ohne dein Ok.", "Ich schalte nichts ohne deine Bestätigung.",
    "Ich schalte nichts, es sei denn, du tippst.", "I never switch anything unless you tap.",
    "Ich kann nicht selbst schalten.", "Hank regelt nichts selbst.", "Hank controls nothing.",
    "I never switch myself [S5].",
    # the old hint's wording, "er"/"he" for Hank (review, should 5)
    "Er schaltet nichts selbst.", "He never switches anything himself.",
])
def test_flat_rule_is_found_in_both_languages(sentence: str) -> None:
    assert validator.flat_rule(sentence) and validator.flat_rule(sentence.upper())


@pytest.mark.parametrize("sentence", [
    "Im Chat schalte ich nichts ohne dich.", "In the chat I switch nothing without you.",
    "Im Chat schalte ich nichts selbst; was unter „Hank regelt“ läuft, regelt der Autopilot in seinen Grenzen.",
    "In the chat I switch nothing myself; what runs under \"Hank controls\" is regulated by the autopilot.",
    "Das Sprachmodell schaltet nie.", "The language model never switches anything.",
    "Du kannst den Befeuchter ausschalten.", "Schalte die Abluft eine Stufe höher.", "Der Befeuchter ist aus.",
    "Die Abluft schaltet nicht.", "You can switch it off with one tap.", "The humidifier does not switch on.",
    "Ich kann nicht sagen, ob du schalten solltest.", "Mein Vorschlag: Abluft auf Stufe 4.",
    # concrete and true: a bare "nicht", "can't" with a device, control verbs without a generic object (should 2)
    "Ich kann die Abluft nicht ausschalten, der Anschluss ist gesperrt.", "Ich schalte die Abluft nicht aus, weil sie "
    "gesperrt ist.", "I can't switch the exhaust off, the port is locked.", "I don't control the weather.",
    "Ich kann die Abluft nie ausschalten, sie ist gesperrt.", "Ich regle die Temperatur nicht.",
    "I don't regulate the temperature, the box does.", "Ich schalte die Abluft auf keinen Fall aus.",
    # "er"/"he" for a device stays (should 5)
    "Er schaltet nie ab.", "Er schaltet nicht.", "He does not switch off.",
])
def test_flat_rule_leaves_scoped_and_other_sentences(sentence: str) -> None:
    assert not validator.flat_rule(sentence)


@pytest.mark.parametrize("key", ["hank.reply.no_self", "hank.action.lead", "hank.action.lead_none",
                                 "hank.fix.close_one", "hank.fix.close_many", "hank.fix.close_none",
                                 "hank.fix.close_pilot"])
@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_chat_scoped_sentences_are_never_the_flat_rule(key: str, lang: str) -> None:
    sentence = i18n.t(key, lang, label="Licht auf 85 %")
    assert sentence.startswith("Im Chat " if lang == "de" else "In the chat "), sentence
    assert not validator.flat_rule(sentence), sentence


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_chat_scoped_rule_from_the_model_stays_next_to_a_grounded_sentence(lang: str) -> None:
    scoped = {"de": "Im Chat schalte ich nichts ohne dich [S5].", "en": "In the chat I switch nothing without you [S5]."}
    probe = {"de": "Die Box zeigt innen 47 % [S2].", "en": "Inside the box it shows 47% [S2]."}
    v = check(humid_ctx(lang), f"{probe[lang]} {scoped[lang]}")
    assert kept(v) and v.dropped == [] and not v.replaced
    assert scoped[lang].replace(" [S5]", "") in v.text


def test_a_claim_in_english_is_replaced_by_the_chat_filler() -> None:
    v = check(humid_ctx("en"), "I've switched the humidifier off [S5]. At the probe it is 79% [S2].")
    assert v.replaced and reasons(v) == ["claim"]
    assert v.text.count(NO_SELF["en"]) == 1 and "switched" not in v.text and "79%" in v.text


@pytest.mark.parametrize("key", ["hank.reply.no_self", "hank.action.lead", "hank.action.lead_none",
                                 "hank.fix.close_one", "hank.fix.close_many", "hank.fix.close_none",
                                 "hank.fix.close_pilot"])
@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_chat_sentences_about_switching_are_no_claims_and_name_the_chat(key: str, lang: str) -> None:
    sentence = i18n.t(key, lang, label="Licht auf 85 %")
    assert sentence.startswith("Im Chat " if lang == "de" else "In the chat "), sentence
    assert not claims.switch_claim(sentence, lang), sentence


def test_validator_wrong_language_falls_back_to_the_draft() -> None:
    ctx = humid_ctx("de")
    v = check(ctx, "The humidity at the probe is 79% [S2]. It is far above the band of the stage [S2].")
    assert v.used_draft and reasons(v) == ["lang"] and v.text == compose.build(ctx).text
    en = humid_ctx("en")
    v = check(en, "Die Luftfeuchte am Fühler ist sehr hoch [S2]. Das ist nicht gut für die Blüte [S2].")
    assert v.used_draft and reasons(v) == ["lang"]


def test_validator_empty_and_all_dropped_give_the_draft_or_dont_know() -> None:
    ctx = humid_ctx()
    draft = compose.build(ctx).text
    v = check(ctx, "")
    assert v.used_draft and v.text == draft and reasons(v) == ["empty"]
    v = check(ctx, "Das stimmt so. Bei 99 % ist alles gut [S2].")
    assert v.used_draft and v.text == draft and set(reasons(v)) <= {"no_marker", "number", "verdict"}
    nothing = context.build(kit.route("Hallo", "de"), kit.live(), [], "de", question="Hallo")
    assert compose.build(nothing).text == ""
    v = check(nothing, "Ich weiß es nicht.")
    assert v.used_draft and v.text == i18n.t("hank.reply.dont_know", "de")


def test_question_numbers_are_not_whitelisted() -> None:  # spec 5.3, grow-03
    question = "How many µmol does the VGrow lamp put out at 100%? A lettuce guide says 300 PPFD."
    ctx = humid_ctx("en", question=question, hits=["stages/autoflower#notes.3"])
    n = fid(ctx, "stages/autoflower#notes.3")
    v = check(ctx, f"The lamp gives 300 PPFD at 100% [{n}].", "en")
    assert reasons(v) == ["number"] and v.used_draft
    v = check(ctx, f"VIVOSUN publishes no PPFD values for the VGrow lamp [{n}].", "en")
    assert v.used_draft and reasons(v) == ["filler"]  # the draft says exactly that already (finding 3)
    v = check(ctx, f"VIVOSUN has published no PPFD values for this lamp, so the cockpit sets the light in percent "
                   f"by the box presets [{n}].", "en")
    assert not reasons(v) or reasons(v) == ["filler"]  # no number dropped: the question's 300 never counts
    week = humid_ctx(question="Was gebe ich in der 4. Blütewoche?", hits=["nutrients/biobizz-2026#light_mix.bloom_4"],
                     slots=SimpleNamespace(week=4))
    cell = fid(week, "nutrients/biobizz-2026#light_mix.bloom_4")
    row = "Bio-Grow 4 ml/l, Bio-Bloom 4 ml/l, Top-Max 3 ml/l, Alg-A-Mic 3 ml/l"
    v = check(week, f"In der Woche 4 gibst du {row} [{cell}].")
    assert not v.dropped and v.rejected == validator.UNCHANGED  # it says the draft's row again: the draft stays
    v = check(week, f"In der Woche 5 gibst du {row} [{cell}].")
    assert reasons(v) == ["number"]
    # The week's row is the answer: a version that names only Bio-Bloom never loses the row (acceptance 23.10.):
    # the row stays in the code's words in front, the model's copy adds nothing, so the draft stays
    v = check(week, f"In Woche 4 steht Bio-Bloom bei 4 ml/l [{cell}].")
    assert v.used_draft and row in v.text and v.rejected == validator.UNCHANGED


def test_action_percent_counts_only_for_an_action_wish() -> None:
    question = "Dreh bitte das Licht auf 100 Prozent hoch."
    plain = humid_ctx(question=question, hits=["stages/autoflower#early_flower.preset_note"],
                      slots=SimpleNamespace(percent=85))
    action = humid_ctx(question=question, hits=["stages/autoflower#early_flower.preset_note"],
                       slots=SimpleNamespace(percent=85), intent="action")
    sentence = "Mit einem Tipp geht es auf 85 % [S3]."
    assert reasons(check(plain, sentence)) == ["number"]
    assert kept(check(action, sentence))


def test_validator_stale_readings_allow_no_climate_verdict() -> None:  # live-05
    stale = humid_ctx(live=kit.live("F22-alt"))
    assert stale.verdicts.stale
    for model in ("Die Luftfeuchte ist zu hoch [S2].", "Die Luftfeuchte passt [S2]."):
        assert reasons(check(stale, model)) == ["verdict"], model
    assert kept(check(stale, "Die Messwerte sind 25 Min alt [S1]."))


def test_validator_negative_word_against_an_ok_verdict() -> None:
    live = kit.live()
    live["snapshot"]["climate"]["probe_humidity"] = 50.0
    for row in live["plant_view"]["checks"]:
        if row["metric"] == "probe_humidity":
            row.update(actual=50.0, status="ok", advice=None, consequence=None)
    ctx = humid_ctx(live=live)
    assert next(v for v in ctx.verdicts.verdicts if v.metric == "probe_humidity").status == "ok"
    assert reasons(check(ctx, "Die Luftfeuchte ist zu hoch [S2].")) == ["verdict"]
    assert kept(check(ctx, "Die Luftfeuchte liegt mit 50 % im Ziel [S2]."))


def test_a_sentence_with_only_the_drafts_words_is_a_filler() -> None:
    """Finding 3: the stage label, the question and the draft's bubble are free words; a sentence that shares nothing
    else with what it cites ("Meine Kartei sagt dazu: Frühe Blüte") keeps the draft."""
    ctx = humid_ctx()
    for model in ("Die Luftfeuchte ist zu hoch [S2].", "Meine Kartei sagt dazu: Frühe Blüte [S2].",
                  "Das steht so in meiner Kartei zur frühen Blüte [S2]."):
        v = check(ctx, model)
        assert v.used_draft and reasons(v) == ["filler"] and v.rejected.startswith("filler"), model


def test_validator_replaces_seed_shop_names() -> None:
    question = "Wie hoch wird meine Ice Cream Cake eigentlich?"
    ctx = humid_ctx(question=question, hits=["strains/ice-cream-cake-auto#facts.hohe_indoor"])
    f = fid(ctx, "strains/ice-cream-cake-auto#facts.hohe_indoor")
    v = check(ctx, f"Zamnesia nennt 90 cm [{f}]. Die Karte sagt indoor bis 120 cm, die Box hat 92 cm [{f}].")
    whole = f"{v.text} {v.more}"  # the draft's answer sentence already names the 90 cm: the model's copy goes to "more"
    assert kept(v) and "Zamnesia" not in whole and "Ein Samenhändler nennt 90 cm" in whole  # capital at start
    assert all(s.url is None for s in v.sources if s.id in ("S37", "S40"))


def test_validator_markers_of_trimmed_facts_are_unknown() -> None:
    tight = context.build(kit.route(Q_HUMID, "de", HITS), kit.live(), None, "de", question=Q_HUMID, limit=380)
    assert tight.budget.trimmed
    dropped_fact = next(x.split(":")[1] for x in tight.budget.trimmed if x.startswith("fact:"))
    gone = next((x.split(":")[1] for x in tight.budget.trimmed if x.startswith("group:")), None)
    assert reasons(check(tight, f"Für Autos nimm 18/6 [{dropped_fact}].")) == ["no_marker"]
    if gone:
        marker = context.MARKERS[gone]
        assert reasons(check(tight, f"Das steht so im Stand [{marker}].")) == ["no_marker"]


def test_drop_window_flags_above_30_percent_over_20_answers() -> None:
    ctx = humid_ctx()
    window = validator.DropWindow()
    good = check(ctx, "Am Fühler sind es 79 % [S2].")
    bad = check(ctx, "Am Fühler sind es 80 % [S2].")
    for _ in range(19):
        window.add(bad)
    assert not window.flag()  # fewer than 20 answers
    window.add(bad)
    assert window.flag() and window.rate() == 1.0
    for _ in range(20):
        window.add(good)
    assert not window.flag() and window.rate() == 0.0


# ---------- sentence splitter ----------

@pytest.mark.parametrize(("value", "expected"), [
    ("Gern. Schau mal.", ["Gern.", "Schau mal."]),
    ("Das gilt z. B. für die Blüte [F1]. Und dann [F2].", ["Das gilt z. B. für die Blüte [F1].", "Und dann [F2]."]),
    ("Das sind ca. 5 Tage [F1]. Gut.", ["Das sind ca. 5 Tage [F1].", "Gut."]),
    ("Abluft bzw. Umluft höher [F1]. Fertig.", ["Abluft bzw. Umluft höher [F1].", "Fertig."]),
    ("Der VPD liegt bei 1.4 kPa [F1]. Passt.", ["Der VPD liegt bei 1.4 kPa [F1].", "Passt."]),
    ("Seit dem 22.10. ist Blüte [S7]. Gut so.", ["Seit dem 22.10. ist Blüte [S7].", "Gut so."]),
    ("In der 4. Blütewoche 4 ml [F1]. So.", ["In der 4. Blütewoche 4 ml [F1].", "So."]),
    ("Es ist hoch. [F1] Und das Licht. [S3]", ["Es ist hoch. [F1]", "Und das Licht. [S3]"]),
    ("Use e.g. a loupe [F1]. Done!", ["Use e.g. a loupe [F1].", "Done!"]),
    ("Wirklich? Ja [F1].", ["Wirklich?", "Ja [F1]."]),
    ("Kein Punkt am Ende [F1]", ["Kein Punkt am Ende [F1]"]),
    ("", []),
])
def test_sentence_splitter_abbreviations(value: str, expected: list[str]) -> None:
    assert validator.split_sentences(value) == expected


# ---------- claims ----------

@pytest.mark.parametrize("sentence", [
    "Ich habe den Befeuchter ausgeschaltet.", "Ich hab das Licht hochgedreht.", "Hab ich eingestellt.",
    "Das Licht ist jetzt auf 85 %.", "Ich schalte jetzt die Abluft ein.", "Ich habe es gespeichert.",
    "I have switched it off.", "I've set the light to 85%.", "The fan is now at step 4.", "I'll turn it up.",
    "I'm switching the humidifier off.",
])
def test_switch_claims_are_found(sentence: str) -> None:
    assert claims.switch_claim(sentence, "de") and claims.switch_claim(sentence, "en")


@pytest.mark.parametrize("sentence", [
    "Ich schalte nichts selbst.", "Du kannst den Befeuchter ausschalten.", "Schalte die Abluft eine Stufe höher.",
    "Mein Vorschlag: Abluft auf Stufe 4.", "You can switch it off with one tap.", "I never switch anything myself.",
    "Im Chat schalte ich nichts ohne dich: die Karte unten wartet auf deinen Tipp.",
    "In the chat I switch nothing without you: the card below waits for your tap.",
    "Im Chat schalte ich nichts selbst; was unter „Hank regelt“ läuft, regelt der Autopilot in seinen Grenzen.",
    "In the chat I switch nothing myself; what runs under \"Hank controls\" is regulated by the autopilot.",
    "Der Befeuchter ist aus.",
])
def test_suggestions_are_no_switch_claims(sentence: str) -> None:
    assert not claims.switch_claim(sentence, "de")


def test_policy_lists_load_and_block() -> None:
    assert claims.forbidden("Du kannst es rauchen.", "de") and claims.forbidden("Smoke it.", "en")
    assert claims.forbidden("Die Luftfeuchte ist hoch.", "de") is None
    assert claims.forbidden("The power consumption of the lamp.", "en") is None
    assert claims.blocked_url("https://www.zamnesia.com/x") and claims.blocked_url("https://hanfgartenshop.de/y")
    assert not claims.blocked_url("https://vivosun.com/x") and not claims.blocked_url(None)
    # review 3, L3: the replacement keeps the grammar, and a sentence start keeps its capital
    assert claims.vendors("laut Hanfgarten 120 cm", "de") == "laut einem Samenhändler 120 cm"
    assert claims.vendors("Hanfgarten says up to 120 cm", "en") == "A seed shop says up to 120 cm"
    assert claims.vendors("bei der gleichnamigen Zamnesia-Sorte", "de") == ("bei der gleichnamigen Sorte eines anderen "
                                                                           "Samenhändlers")
    assert claims.vendors("for the Zamnesia strain of the same name", "en") == ("for the strain of the same name "
                                                                               "from another seed shop")
    assert claims.blocked_url("https://420seedz.de/guide") and claims.blocked_url("https://learn.hydrobuilder.com/x")
    assert claims.blocked_url("https://trimleaf.com/b") and claims.blocked_url("https://www.johnnyseeds.com/c")
