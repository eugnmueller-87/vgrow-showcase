"""Hank WP11: the calibrated thresholds, the off-topic rows 3b and 4b of the router, the dense or lexical state of
GET /api/hank/state, and the replay of scripts/hank_calibrate.py against the real router (docs/hank-v1-spec.md 2.4,
10.4 WP11, 11.3; addendum N2; docs/hank-v1-messung.md).

The dense tests use the fake embedder's hash vectors: they carry no meaning, so the question's vector is set to the
very vector of an off-topic example (cosine 1) or of a card passage, which is what row 3b decides on.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from hank_eval import fixtures, report
from hank_eval.fake_ollama import FakeOllama, hash_vector
from vgrow.hank import core
from vgrow.hank.kartei_index import Index
from vgrow.hank.router import Router

REPO = Path(__file__).resolve().parent.parent
OFFTOPIC = json.loads((REPO / "vgrow" / "hank" / "data" / "offtopic.json").read_text(encoding="utf-8"))["examples"]
# Off-topic sentences of the file that the lexical search scores at T_HIT through one common card word (23.10.2026).
COLLIDING = ("Wie spät ist es?", "How do I bake a cake?", "What time is it?", "Welche Partei ist die beste?",
             "Can you recommend a TV series?")


@pytest.fixture(scope="module")
def index() -> Index:
    return Index.load()


def test_thresholds_file_is_calibrated_with_both_sets_and_rows() -> None:
    data = json.loads((REPO / "vgrow" / "hank" / "data" / "thresholds.json").read_text(encoding="utf-8"))
    cal = data["calibration"]
    assert cal["date"] == "2026-10-23" and cal["embed_model"] == "granite-embedding:278m"
    assert set(cal["sets"]) == {"dense", "lexical"}
    # M_OFF 0.09 by hand after WP11: the middle of the measured gap (off-topic at least 0.126, growing at most 0.057)
    assert data["dense"]["M_OFF"] == 0.09 and "T_OFF_HARD" not in data["dense"]
    assert data["lexical"]["T_OFF_HARD"] == 0.75 and "M_OFF" not in data["lexical"]
    for name in ("dense", "lexical"):
        assert data[name]["T_DOMAIN"] <= data[name]["T_HIT"]


def test_row_4b_turns_down_the_colliding_offtopic_sentences(index: Index) -> None:
    plain = Router(index)
    without = Router(index, thresholds={"lexical": {"T_OFF_HARD": None}})
    for sentence in COLLIDING:
        assert plain.route(sentence).name == "offtopic", sentence
        assert without.route(sentence).name == "grow", sentence  # the problem the row fixes
    assert plain.route("Kann ich meinen Salat verkaufen?").name != "sale"


def test_rows_never_turn_down_the_active_growing_cases(index: Index, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = report.load_expected()
    plain = Router(index)
    fixtures.freeze(monkeypatch)
    for case in report.load_cases():
        exp = expected[case["id"]]
        if exp["status"] == "waits" or exp["route_today"] != "grow":
            continue
        live = fixtures.build(exp["fixture"], lang=case["lang"], ledger=exp["ledger"]).sources()
        assert plain.route(case["question"], lang_hint=case["lang"], live=live).name == "grow", case["id"]


def _dense(tmp_path: Path) -> tuple[Index, Router, FakeOllama]:
    fake = FakeOllama()
    idx = Index.load(embedder=fake, cache_dir=tmp_path)
    router = Router(idx)

    async def build() -> None:
        assert await idx.build_dense() == "used"
        assert await router.warm(fake)

    asyncio.run(build())
    return idx, router, fake


def test_row_3b_dense_nearest_offtopic_beats_a_card_hit(tmp_path: Path) -> None:
    idx, router, _ = _dense(tmp_path)
    sentence = "Wie spät ist es?"
    vec = hash_vector(sentence)  # the example's own vector: cosine 1 to it, near 0 to every card passage
    got = router.route(sentence, dense_vec=vec, dense_state="used")
    assert got.name == "offtopic", got.reason
    # A card passage's own vector is nearest to that card: row 3b stays quiet, the question is answered.
    doc = idx._state.docs[idx._state.by_id["stages/autoflower#late_flower.probe_humidity.all"]]
    got = router.route("Wie hoch darf die Luftfeuchte in der späten Blüte sein?",
                       dense_vec=hash_vector(doc.embed_text), dense_state="used")
    assert got.name == "grow", got.reason
    # Without M_OFF in the dense set the row does nothing.
    off = Router(idx, gates=router.gates, offtopic=router.offtopic, thresholds={"dense": {"M_OFF": None}})
    assert not off.route(sentence, dense_vec=vec, dense_state="used").reason.startswith("offtopic_nn:")


def test_row_3b_fires_where_row_2_does_not(tmp_path: Path) -> None:
    """A question that reaches T_HIT (so row 2 passes it) but whose vector sits on an off-topic example."""
    idx, router, _ = _dense(tmp_path)
    question = "Wieviel ml Bio-Bloom gebe ich in der 4. Blütewoche? Ich habe Light-Mix Erde."  # no live metric
    got = router.route(question, dense_vec=hash_vector(OFFTOPIC["de"][0]), dense_state="used")
    assert got.name == "offtopic" and got.reason.startswith("offtopic_nn:"), got.reason


def test_row_3b_never_overrides_the_live_state_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, router, _ = _dense(tmp_path)
    fixtures.freeze(monkeypatch)
    live = fixtures.build("F22", lang="de").sources()
    vec = hash_vector(OFFTOPIC["de"][0])  # as close to an off-topic example as a vector can be
    got = router.route("Wie feucht ist es gerade in der Box?", live=live, dense_vec=vec, dense_state="used")
    assert got.name == "grow" and got.reason.startswith("state:"), got.reason


def test_index_max_cosine_and_bank_best_cosine(tmp_path: Path) -> None:
    idx, router, _ = _dense(tmp_path)
    doc = idx._state.card_docs[0]
    assert idx.max_cosine(hash_vector(doc.embed_text)) == pytest.approx(1.0, abs=1e-5)
    assert Index.load().max_cosine(hash_vector("x")) is None  # no vectors: no answer
    cos, which = router.offtopic.best_cosine(hash_vector(OFFTOPIC["en"][3]))
    assert which == OFFTOPIC["en"][3] and cos == pytest.approx(1.0, abs=1e-5)


def test_state_shows_dense_or_lexical_search_and_the_last_fallback(tmp_path: Path) -> None:
    lexical = core.Hank(index=Index.load())
    lexical.ensure_index()
    embed = lexical.state("de")["embed"]
    assert embed["search"] == "lexical" and embed["state"] == "off" and embed["last_query"] is None

    idx, _, fake = _dense(tmp_path)
    hank = core.Hank(index=idx)
    hank.ensure_index()
    asyncio.run(idx.embed_query("Wie feucht ist es?"))
    embed = hank.state("de")["embed"]
    assert embed["search"] == "dense" and embed["last_query"]["state"] == "used"
    fake.delay["embed"] = 3.0  # the embedding model does not answer in time: this question went lexical
    asyncio.run(idx.embed_query("Wie feucht ist es?"))
    embed = hank.state("de")["embed"]
    assert embed["search"] == "dense" and embed["last_query"]["state"] == "timeout" and embed["timeouts"] == 1


def test_calibration_replay_matches_the_router_on_the_eval_set() -> None:
    """scripts/hank_calibrate.py replays the decision table in plain Python; the replay must give the routes the
    real router gives (lexical set, every case and the off-topic file)."""
    if str(REPO / "scripts") not in sys.path:
        sys.path.append(str(REPO / "scripts"))
    import hank_calibrate as hc

    items = [i for i in hc.build_items() if i.source in ("case", "offtopic_file")]
    with hc.isolated() as root:
        lives = hc.LiveBook(root)
        idx = Index.load()
        router = Router(idx)
        records = hc.build_records(router, items, lives, lambda _q: None)
        th = dict(idx.thresholds["lexical"])
        assert hc.verify(router, records, th, "lexical", lives) == []
        assert not any(hc.route_of(r, th, inbank=True) == "grow" for r in records if r.item.expected == "offtopic")
