"""The exhaust's status says what it waits for while a reading is past its band (owner, 07.11.2026).

Hank said "28.2 °C. That's too warm." in his garden, the line under it "Too warm: turn the exhaust fan up.", and the
exhaust's own status "on target, nothing to do": the autopilot steps only past the band plus its margin, held ten minutes
(docs/autopilot-entwurf.md, Hysterese). "It's on auto, he should do everything himself, not telling me to do it." So the
status names the rule's trigger instead, and where the exhaust can do no more it names what is left, never the card's
"exhaust fan up".
"""

from __future__ import annotations

from typing import Any

from vgrow import autopilot as ap

from test_autopilot import Rig, stage


def hold(rig: Rig) -> dict[str, Any]:
    return rig.pilot.holds.get("dfan") or {}


def veg(temp: float, humidity: float, box_humidity: float | None = None, light: int | None = 40) -> Rig:
    """The owner's stage: Vegetative, day band 22 to 28 °C, humidity 55 to 70 %, VPD 0.8 to 1.2 kPa."""
    rig = Rig(st=stage("veg"))
    rig.box.light["level"] = light
    rig.box.set_climate(temp, humidity, box_humidity=humidity - 4 if box_humidity is None else box_humidity)
    return rig


def test_just_over_the_band_the_exhaust_says_when_it_steps_instead_of_on_target() -> None:
    rig = veg(28.1, 70.0)  # 1.14 kPa: only the temperature is off, by less than the 0.5 °C margin
    rig.run(60)
    assert rig.box.sent == []
    assert hold(rig)["key"] == "ap.hold.watch_high_up"
    en = ap.render(hold(rig), "en")
    assert en == "watching: the temperature is 28.1 °C, above 28.0 °C. One step up once it stays above 28.5 °C for 10 min"
    assert ap.render(hold(rig), "de").startswith("beobachtet: die Temperatur liegt bei 28,1 °C, über 28,0 °C.")
    fan = next(d for d in rig.pilot.view(lang="en")["devices"] if d["id"] == "dfan")
    assert fan["reason_key"] == "ap.hold.watch_high_up" and fan["status"] == en


def test_past_the_margin_it_says_so_while_the_ten_minutes_run_then_steps() -> None:
    rig = veg(28.8, 70.0)
    rig.run(6)
    assert rig.box.sent == [] and hold(rig)["key"] == "ap.hold.watch_high_up"
    rig.run(10)
    assert rig.box.sent == [("dfan", 6)]


def test_just_under_the_band_it_says_one_step_down() -> None:
    rig = veg(21.8, 62.0)
    rig.run(60)
    assert rig.box.sent == [] and hold(rig)["key"] == "ap.hold.watch_low_down"
    assert "below 21.5 °C" in ap.render(hold(rig), "en")


def test_with_the_lights_unknown_it_names_the_outer_trigger() -> None:
    rig = veg(28.6, 70.0, light=None)  # only a rank 1 temperature counts then: two degrees past the band
    rig.run(60)
    assert rig.box.sent == [] and hold(rig)["key"] == "ap.hold.watch_high_up"
    assert "above 30.0 °C" in ap.render(hold(rig), "en")


def test_humidity_and_vpd_just_past_their_bands_are_watched_too() -> None:
    rig = veg(24.0, 71.0)  # one point over 70 %, the margin is two
    rig.run(60)
    assert hold(rig)["key"] == "ap.hold.watch_high_up"
    assert "the humidity" in ap.render(hold(rig), "en")
    rig = veg(22.0, 70.0)  # 0.79 kPa, under 0.8 by less than the 0.05 margin: too humid, one step up
    rig.run(60)
    assert hold(rig)["key"] == "ap.hold.watch_low_up"
    assert "the VPD" in ap.render(hold(rig), "en")


def test_mist_at_the_probe_says_humidity_and_vpd_do_not_count() -> None:
    rig = veg(25.0, 66.0, box_humidity=44.0)  # the owner's gap of 07.11.2026: probe 66 %, box sensor 45 %
    rig.run(60)
    assert rig.box.sent == [] and hold(rig)["key"] == "ap.hold.mist"


def test_after_the_owner_undid_a_step_it_says_it_waits_instead_of_promising_one() -> None:
    rig = veg(28.1, 70.0)
    rig.run(5)  # past the first lights-on edge, which clears every block
    rig.pilot.state["undo_block"]["dfan"] = {"dir": 1, "until": rig.clock[0] + 3600}
    rig.run(5)
    assert hold(rig)["key"] == "ap.hold.undone"


def test_on_target_stays_on_target() -> None:
    rig = veg(25.0, 62.0)
    rig.run(60)
    assert rig.box.sent == [] and hold(rig)["key"] == "ap.hold.on_target"  # nothing past a band
    fan = next(d for d in rig.pilot.view(lang="en")["devices"] if d["id"] == "dfan")
    assert fan["reason_key"] == "ap.hold.on_target"


def test_at_its_limit_it_names_what_the_room_has_to_do_never_exhaust_fan_up() -> None:
    rig = veg(30.5, 60.0)  # over 28 + 2: urgent
    rig.box.dfan["step"] = 8
    rig.pilot.state["base"] = 5  # Vegetative: base 5 plus three, so 8 is the top
    rig.run(2 * 60)
    assert rig.box.sent == [] and hold(rig)["key"] == "ap.hold.at_bound"
    bound = [e for e in rig.kinds("hold") if e["text"]["key"] == "ap.ev.at_bound"]
    assert bound and bound[-1]["text"]["params"]["advice"] == {"t": "ap.bound.temp_high"}
    assert ap.render(bound[-1]["text"], "en").endswith("It cannot cool any further: cool the room or dim the lamp.")
    for lang in ("de", "en"):
        said = ap.render(bound[-1]["text"], lang).lower()
        assert "exhaust fan up" not in said and "abluft hoch" not in said


def test_too_cold_and_too_humid_names_a_warmer_room_not_exhaust_down() -> None:
    rig = Rig()
    rig.box.light["level"] = 0  # night: band 18 to 22 °C
    rig.box.set_climate(17.2, 66.0)
    rig.run(6 * 60)
    said = [e for e in rig.kinds("hold") if e["text"]["key"] == "ap.ev.cold_humid"]
    assert said and said[-1]["text"]["params"]["advice"] == {"t": "ap.cold_humid.fix"}
    for lang in ("de", "en"):
        text = ap.render(said[-1]["text"], lang).lower()
        assert "one step down" not in text and "eine stufe runter" not in text
