"""Regressions from the live cockpit: selected Automatic must not mean every device can act."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from test_autopilot import Rig
from test_hank_regelt import hum_view

from vgrow import hankpilot


def humidifier(**changes):
    values = hum_view(**changes)
    return SimpleNamespace(state={"enabled": values["enabled"]}, port="usb2", view=lambda snap: values)


def test_live_blockers_cannot_be_reported_as_three_regulating_devices():
    rig = Rig(humidifier=humidifier(port_device=None, port_hold="unassigned"))
    rig.box.light["level"] = 75
    rig.box.dfan["step"] = 6
    rig.box.set_climate(25.98, 50.64, 41.33)
    rig.run(20)
    view = rig.pilot.view(lang="en")
    assert view["mode"] == "auto" and all(d["level"] == "auto" for d in view["devices"])
    assert view["readiness"]["ready"] == 0
    assert view["readiness"]["needs_setup"] == 3
    assert view["readiness"]["waiting"] == 1
    assert view["readiness"]["total"] == 4 and view["readiness"]["state"] == "attention"
    by_id = {d["id"]: d for d in view["devices"]}
    assert by_id["light"]["reason_key"] == "ap.hold.lamp"
    assert by_id["cfan"]["reason_key"] == "ap.hold.natural_wind"
    assert by_id["dfan"]["control_state"] == "waiting"
    assert view["humidifier"]["reason_key"] == "ap.health.hum_unassigned"
    assert "regulating" not in view["status"].lower() and rig.box.sent == []
    # Hank reads the same state instead of claiming the exhaust is already regulating the condition.
    panel = hankpilot.panel(rig.pilot, view, hum_view(port_device=None), "en", now=rig.clock[0])
    assert "Hank is waiting on: Exhaust fan." in panel["regulates"]
    assert panel["about"]["probe_temp:high"]["text"].startswith("Hank is waiting here:")


def test_monitoring_in_band_does_not_require_a_pointless_command():
    rig = Rig(lamp=True)
    rig.box.light["level"] = 100
    rig.box.cfan.update(natural_wind=False, step=rig.stage["preset"]["cfan_step"])
    rig.run(10)
    view = rig.pilot.view(lang="en")
    assert view["readiness"]["ready"] == 3 and view["readiness"]["state"] == "ready"
    assert all(d["control_state"] == "ready" for d in view["devices"])
    assert rig.box.sent == []


def test_readiness_rechecks_stale_values_without_sending_or_changing_policy():
    rig = Rig(lamp=True)
    rig.box.light["level"] = 100
    rig.run(10)
    before = copy.deepcopy(rig.pilot.state)
    rig.box.sensors_age = 601
    for lang in ("de", "en"):
        view = rig.pilot.view(lang=lang)
        assert all(d["control_state"] == "waiting" and d["reason_key"] == "ap.hold.stale" for d in view["devices"])
    assert rig.pilot.state == before and rig.box.sent == []


def test_growing_past_the_confirmed_distance_exposes_the_confirmation_again():
    rig = Rig()
    rig.log.data["heights"] = [{"cm": 50}]
    rig.pilot.confirm_lamp()
    rig.log.data["heights"].append({"cm": 61})
    rig.run(10)
    view = rig.pilot.view(lang="en")
    light = next(d for d in view["devices"] if d["id"] == "light")
    assert view["needs"]["lamp"] is True
    assert light["control_state"] == "needs_setup" and light["reason_key"] == "ap.hold.lamp_grown"
    assert not any(name == "light" for name, _value in rig.box.sent)


def test_suggestions_are_not_counted_as_automatic_control():
    rig = Rig(lamp=True)
    rig.pilot.state["devices"]["light"]["level"] = "suggest"
    rig.pilot.state["devices"]["cfan"]["level"] = "manual"
    rig.run(10)
    assert rig.pilot.view(lang="en")["status"] == "has 1 of 3 devices set to Auto"


def test_humidifier_ready_only_with_its_own_requirements_met():
    for changes, expected in [({}, "ready"), ({"enabled": False}, "off"),
                              ({"port_device": "pump"}, "needs_setup"), ({"stale": True}, "waiting"),
                              ({"needs_stage_confirmation": True}, "needs_setup")]:
        rig = Rig(box_id="box-" + expected + str(len(changes)), humidifier=humidifier(**changes))
        view = rig.pilot.view(lang="en")
        assert view["humidifier"]["control_state"] == expected
        rig.pilot.release()


def test_hank_does_not_claim_the_humidifier_is_regulating_while_disconnected():
    rig = Rig(lamp=True, humidifier=humidifier())
    rig.run(10)
    snap = rig.box.snapshot()
    snap["mqtt"] = False
    view = rig.pilot.view(snap, lang="en")
    assert view["humidifier"]["control_state"] == "waiting"
    assert view["humidifier"]["reason_key"] == "ap.hold.no_link"
    panel = hankpilot.panel(rig.pilot, view, hum_view(), "en", now=rig.clock[0])
    assert "Humidifier" not in panel["regulates"].split("Hank is waiting on:")[0]
    assert "Humidifier" in panel["regulates"].split("Hank is waiting on:")[1]


def test_turning_natural_wind_off_clears_the_warning_before_the_next_poll():
    rig = Rig(lamp=True)
    rig.run(10)
    assert rig.pilot.holds["cfan"]["key"] == "ap.hold.natural_wind"
    rig.box.cfan["natural_wind"] = False  # MQTT updates before the next 90-second sensor poll.
    before = copy.deepcopy(rig.pilot.state)
    holds = copy.deepcopy(rig.pilot.holds)
    for lang in ("de", "en"):
        view = rig.pilot.view(lang=lang)
        fan = next(d for d in view["devices"] if d["id"] == "cfan")
        assert fan["app_mode"] is None and fan["value"] == 10
        assert fan["control_state"] == "waiting" and fan["reason_key"] == "ap.hold.next_poll"
        assert view["readiness"]["needs_setup"] == 0
    assert rig.pilot.state == before and rig.pilot.holds == holds and rig.box.sent == []
    rig.poll()
    fan = next(d for d in rig.pilot.view()["devices"] if d["id"] == "cfan")
    assert fan["control_state"] == "ready" and fan["reason_key"] == "ap.hold.on_target"
    assert rig.box.sent == []


@pytest.mark.parametrize("device,blocked,cleared,reason", [
    ("cfan", {"natural_wind": False, "mode": None}, {"mode": 0}, "ap.hold.mode_unknown"),
    ("dfan", {"mode": 1}, {"mode": 0}, "ap.hold.app_auto"),
    ("light", {"in_plan": True}, {"in_plan": False}, "ap.hold.app_plan"),
])
def test_resolved_app_modes_do_not_keep_the_previous_poll_warning(device, blocked, cleared, reason):
    rig = Rig(lamp=True)
    getattr(rig.box, device).update(blocked)
    rig.run(10)
    assert rig.pilot.holds[device]["key"] == reason
    getattr(rig.box, device).update(cleared)
    before = copy.deepcopy(rig.pilot.state)
    info = next(d for d in rig.pilot.view()["devices"] if d["id"] == device)
    assert info["control_state"] == "waiting" and info["reason_key"] == "ap.hold.next_poll"
    assert rig.pilot.state == before and rig.box.sent == []
