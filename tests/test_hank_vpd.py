"""Hank while the VPD is above its band (vgrow/hank/vpd.py, 23.10.2026): the today list and the answer to a question
about the VPD say why in plain words, what runs, what blocks the humidifier rule and the autopilot (with their two-tap
cards or the place on the page) and why the light waits; never a bare "humidifier on" card, never straight to Automatik.

The live state is the eval fixture F22-vpdhigh (the climate measured on 23.10.2026 at 21:20) with the day frozen; other
moments are that fixture changed by hand.
"""

from __future__ import annotations

import asyncio
import copy
import math
from typing import Any

import pytest

from hank_eval import fixtures
from vgrow import plant
from vgrow.hank import core, today, vpd

EM_DASH = chr(0x2014)


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures.freeze(monkeypatch)


def high(lang: str = "de") -> fixtures.Live:
    return fixtures.build("F22-vpdhigh", lang=lang)


def with_climate(live: fixtures.Live, **climate: float) -> dict[str, Any]:
    """The same moment with other probe values: the checks follow, as plant.view computes them."""
    src = copy.deepcopy(live.sources())
    src["snapshot"]["climate"].update(climate)
    src["plant_view"]["checks"] = plant.evaluate(live.stage, src["snapshot"])
    return src


def ruling(src: dict[str, Any], floor: float = 56.0) -> dict[str, Any]:
    """The humidifier rule regulating: on, the owner's stage, the humidifier named on USB 2."""
    src["automation_view"].update(enabled=True, needs_stage_confirmation=False, estimate=None,
                                  port_device="humidifier", port_hold=None, implausible=False,
                                  floor={"floor": floor, "reason": "vpd", "card_min": 45, "temp": 25.06,
                                         "vpd_max": 1.4, "clamped": False})
    return src


def pilot(src: dict[str, Any], mode: str, *, stage: bool = False, lamp: bool = False,
          dfan: str = "") -> dict[str, Any]:
    box = src["snapshot"]["autopilot"]["boxes"][0]
    box.update(mode=mode, needs={"stage": stage, "lamp": lamp})
    box["devices"]["dfan"]["status"] = {"de": dfan, "en": dfan}
    return src


def test_catalog_parity_and_no_em_dash() -> None:
    assert vpd.check() == []
    assert all(EM_DASH not in v for v in (*vpd.DE.values(), *vpd.EN.values()))


def test_nothing_while_the_vpd_is_in_band_or_the_readings_are_old() -> None:
    assert vpd.explain(fixtures.build("F22").sources(), "de") is None  # F22 has no VPD reading
    src = copy.deepcopy(high().sources())
    src["snapshot"]["sensors_age"] = fixtures.STALE_SECONDS
    assert vpd.explain(src, "de") is None
    assert vpd.explain(with_climate(high(), probe_temp=23.0, probe_humidity=55.0, probe_vpd=1.26), "de") is None


def test_the_measured_case_in_plain_words() -> None:
    found = vpd.explain(high().sources(), "de")
    assert found is not None
    need = 100 * (1 - 1.4 / (0.6108 * math.exp(17.27 * 25.06 / (25.06 + 237.3))))
    text = found.text()
    assert "Der VPD steht bei 1,73 kPa, über dem Ziel 1 bis 1,4 kPa." in text
    assert f"mindestens {need:.1f} %".replace(".", ",") in text and "45,5 %" in text and "25,1 °C" in text
    assert "zu trocken" in text
    assert "Was schon läuft: Luftbefeuchter (USB 2) aus, Abluft Stufe 3." in text
    # what blocks the humidifier rule, each with its card or its place on the page
    assert "bestätigen mit zwei Tipps (Karte unten)" in text
    assert "„An USB 2 hängt: Befeuchter“" in text
    # the autopilot is off: observe first, never straight to Automatik
    assert "auf „Beobachten“" in text and "Automatik erst danach" in text
    assert found.link == {"href": "#autopilot", "label": "Zur Karte Autopilot"}
    # the light waits, and the light without a plan is named once, with the card's own light-cycle note
    assert "Das Licht wartet: mehr Licht macht es wärmer und die Luft für die Pflanze trockener." in text
    assert text.count("ohne Lichtplan") == 1 and "18/6 oder 20/4" in text and "(S6)" not in text
    kinds = [(c.kind, c.body) for c in found.cards]
    assert kinds == [("plant_stage", {"action": "plant_stage", "key": "early_flower"})]
    assert all(c.two_taps for c in found.cards)


def test_never_a_bare_humidifier_on_card() -> None:
    src = copy.deepcopy(high().sources())
    src["automation_view"]["enabled"] = False
    found = vpd.explain(src, "de")
    assert found is not None
    assert not any(c.body.get("action") == "port" for c in found.cards)
    rule = [c for c in found.cards if c.kind == "auto_humidifier"]
    assert rule and rule[0].body == {"action": "auto_humidifier", "on": True} and rule[0].two_taps
    assert "Die Befeuchter-Regel ist aus: einschalten mit zwei Tipps (Karte unten)." in found.text()


def test_a_regulating_rule_names_its_floor_and_nothing_blocks_it() -> None:
    src = ruling(copy.deepcopy(high().sources()))
    found = vpd.explain(src, "de")
    assert found is not None
    assert "die Befeuchter-Regel schaltet unter 56,0 % ein (für 25,1 °C berechnet)" in found.text()
    assert "Befeuchter-Regel schaltet erst" not in found.text() and found.cards == []


def test_the_autopilot_observing_or_waiting_says_why() -> None:
    src = pilot(ruling(copy.deepcopy(high().sources())), "observe",
                dfan="hält: VPD 1,73 kPa über dem Band, aber weniger Abluft erst, wenn der Fühler höchstens 25 °C zeigt")
    text = vpd.explain(src, "de").text()  # type: ignore[union-attr]
    assert "der Autopilot beobachtet nur" in text and "Autopilot, Abluft: hält: VPD 1,73 kPa" in text
    assert "Beobachten" not in text  # it already observes
    src = pilot(ruling(copy.deepcopy(high().sources())), "auto", stage=True, lamp=True)
    text = vpd.explain(src, "de").text()  # type: ignore[union-attr]
    assert "Auch der Autopilot regelt erst mit bestätigter Phase." in text
    assert "den Lampenabstand bestätigst" in text


def test_too_warm_for_any_humidity_in_the_band() -> None:
    src = with_climate(high(), probe_temp=27.5, probe_humidity=50.0, probe_vpd=1.84)
    text = vpd.explain(src, "de").text()  # type: ignore[union-attr]
    assert "mehr als die Obergrenze von 60 %" in text and "vor allem zu warm" in text


def test_english_twin() -> None:
    found = vpd.explain(high("en").sources(), "en")
    assert found is not None
    text = found.text()
    assert "The VPD is 1.73 kPa, above the target 1 to 1.4 kPa." in text
    assert "45.5%" in text and "25.1 °C" in text and "\"Observe\"" in text and "18/6 or 20/4" in text
    assert found.link == {"href": "#autopilot", "label": "To the Autopilot card"}


def test_the_today_list_carries_the_explanation_and_its_cards() -> None:
    lines, cards = today.lines(high().sources(), "de")
    vpd_line = next(line for line in lines if line.startswith("VPD am Fühler"))
    assert "zu trocken" in vpd_line and "Beobachten" in vpd_line
    assert "Befeuchter-Regel schaltet erst mit bestätigter Phase" not in vpd_line  # the stage line says it
    light = next(line for line in lines if line.startswith("Licht:"))
    assert "Das Licht wartet" in light and light.count("ohne Lichtplan") == 1
    assert any(c.kind == "plant_stage" for c in cards) and not any(c.body.get("action") == "port" for c in cards)
    assert len(lines) <= today.MAX_LINES
    answer = today.answer(high().sources(), "de", answer_id="a-1")
    assert answer.link == {"href": "#autopilot", "label": "Zur Karte Autopilot"}
    assert EM_DASH not in answer.text


def test_the_question_about_the_vpd_opens_with_the_explanation() -> None:
    async def go(question: str, lang: str) -> dict[str, Any]:
        live = high(lang)
        return await core.eval_answer(question, lang=lang, live=live.sources())

    out = asyncio.run(go("Warum ist der VPD so hoch?", "de"))
    assert out["route"] == "grow"
    assert out["text"].startswith("Der VPD steht bei 1,73 kPa, über dem Ziel 1 bis 1,4 kPa. Die Luft ist für 25,1 °C")
    assert "Das Licht wartet" in out["text"] and EM_DASH not in out["text"]
    out = asyncio.run(go("Why is the VPD so high?", "en"))
    assert out["text"].startswith("The VPD is 1.73 kPa, above the target 1 to 1.4 kPa.")
    # a question about something else gets no VPD lead
    out = asyncio.run(go("Wie hoch ist die Abluft gerade?", "de"))
    assert not out["text"].startswith("Der VPD steht")


def test_the_cards_of_the_answer_are_registered_for_the_tap() -> None:
    async def go() -> tuple[Any, Any]:
        live = high().sources()
        hank = core.Hank(live=lambda _lang: dict(live), client=None, model=False, index=core.shared_index())
        asked = await hank.ask_full("Warum ist der VPD so hoch?", lang="de", model=False)
        return asked.answer, hank.registry

    answer, registry = asyncio.run(go())
    stage_cards = [c for c in answer.proposals if c.kind == "plant_stage"]
    assert stage_cards and stage_cards[0].id in registry._cards  # the fixture day lies before today: no expiry check
    assert answer.link == {"href": "#autopilot", "label": "Zur Karte Autopilot"}
