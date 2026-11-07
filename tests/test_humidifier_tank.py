"""No sensor is invented; maintenance follows reports and explicit refill confirmations."""
from __future__ import annotations

import asyncio
import copy

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import i18n
from vgrow.alerts import Alerts
from vgrow.automation import HumidifierAutomation
from vgrow.demo import DemoBox
from vgrow.hank import core, fix, humidifier_report, nudges, today
from vgrow.hank.router import Router
from vgrow.humidifier_tank import U075, HumidifierTank
from vgrow.server import COOKIE, make_app


def snapshot(rh=40, *, on=True):
    return {"online": True, "mqtt": True, "shadow_age": 0, "sensors_age": 0,
            "ports": {"usb2": {"on": on}}, "climate": {"probe_humidity": rh, "probe_temp": 24},
            "dfan": {"step": 5}, "light": {"level": 100}}


def watch(tank, start, end, rh=40, **kwargs):
    for stamp in range(start, end + 1, 60):
        tank.observe(snapshot(rh, **kwargs), assigned=True, off_at=55, now=stamp)


def test_detects_empty_later_in_a_successful_run_and_never_claims_a_sensor():
    tank = HumidifierTank()
    watch(tank, 1000, 1600, 40)
    watch(tank, 1660, 2200, 45)  # the startup humidity response succeeded
    assert tank.view(now=2200)["warning"] is None
    watch(tank, 2260, 2980, 45)  # a plateau of more than 20 minutes: the air takes what the mist gives, no alarm
    assert tank.view(now=2980)["warning"] is None
    watch(tank, 3040, 4300, 40)  # now the vapour goes away with the port still on
    view = tank.view(now=4300)
    assert view["warning"] == "no_response"
    assert view["level_measured"] is False
    assert view["remaining_ml_estimate"] is None and view["rate_ml_h"] is None
    assert HumidifierTank().view(now=4300)["warning"] == "no_response"
    assert tank.view(now=5300)["warning"] is None  # old observations cannot imply a live malfunction


def test_it_has_to_answer_again_each_time_it_comes_on():
    tank = HumidifierTank()
    watch(tank, 1000, 1600, 40)
    watch(tank, 1660, 2200, 45)
    watch(tank, 2260, 2500, 45, on=False)
    watch(tank, 2560, 3880, 45)  # on again, and nothing moves: it had answered only in the run before
    assert tank.view(now=3880)["warning"] == "no_response"


def test_ventilation_changes_freshness_and_healthy_rises_reset_response():
    tank = HumidifierTank()
    watch(tank, 1000, 2140)
    changed = snapshot()
    changed["dfan"]["step"] = 6
    tank.observe(changed, assigned=True, off_at=55, now=2200)
    assert tank.view(now=2200)["warning"] is None
    tank.observe(snapshot(42), assigned=True, off_at=55, now=2260)
    assert tank.view(now=2260)["warning"] is None
    stale = snapshot()
    stale["sensors_age"] = 600
    tank.observe(stale, assigned=True, off_at=55, now=2320)
    assert tank.view(now=2320)["warning"] is None
    tank.observe(snapshot(), assigned=False, off_at=55, now=2380)
    assert tank.state["response"] is None


def test_only_full_to_empty_confirmations_calibrate_and_refills_persist():
    tank = HumidifierTank()
    tank.refill(False, now=1000)
    watch(tank, 1000, 8200)  # two reported hours, 800 ml full-to-empty => about 400 ml/h
    tank.refill(True, now=8200)
    assert tank.state["rate_ml_h"] == 400
    assert tank.state["rate_source"] == "refills"
    watch(tank, 8200, 10000)
    view = tank.view(now=10000)
    assert view["used_ml_estimate"] == 200 and view["remaining_ml_estimate"] == 600
    assert view["refill_count"] == 2
    loaded = HumidifierTank()
    assert loaded.view(now=10000) == view
    loaded.refill(False, now=10000)  # a top-up must not learn a different rate
    assert loaded.state["rate_ml_h"] == 400
    loaded.configure(800, None)  # keep a learned rate when changing no settings
    assert loaded.state["rate_source"] == "refills"


def test_off_intervals_unknown_reports_and_restart_gaps_never_consume_water():
    tank = HumidifierTank()
    tank.configure(800, 400)
    tank.refill(False, now=1000)
    watch(tank, 1000, 2800)
    watch(tank, 2860, 4660, on=False)
    assert tank.view(now=4660)["used_ml_estimate"] == 200
    restarted = HumidifierTank()
    watch(restarted, 9000, 9060)
    assert restarted.view(now=9060)["remaining_ml_estimate"] is None
    assert restarted.state["cycle_runtime_s"] == 1860
    restarted.state.update(rate_ml_h=None, rate_source=None)
    restarted.refill(True, now=9060)
    assert restarted.state["rate_ml_h"] is None  # missing time forbids calibration


def test_low_estimate_and_delayed_first_poll_do_not_fabricate_coverage():
    tank = HumidifierTank()
    tank.configure(800, 400)
    tank.refill(False, now=1000)
    watch(tank, 1000, 6760)
    assert tank.view(now=6760)["warning"] == "no_response"  # response warning takes priority
    watch(tank, 6820, 6820, on=False)
    assert tank.view(now=6820)["warning"] == "estimated_low"
    tank.refill(False, now=7000)
    watch(tank, 10000, 10060)
    assert tank.view(now=10060)["remaining_ml_estimate"] is None


@pytest.mark.parametrize("capacity,rate", [(True, 10), (800, True), (float('nan'), 10), (0, 10), (800, -2)])
def test_invalid_configuration_keeps_existing_record(capacity, rate):
    tank = HumidifierTank()
    before = copy.deepcopy(tank.state)
    with pytest.raises(ValueError):
        tank.configure(capacity, rate)
    assert tank.state == before


def test_hank_suggests_inspection_in_chat_today_and_proactive_notes_without_commands():
    tank = HumidifierTank()
    watch(tank, 1000, 2260)
    auto = {"tank": tank.view(now=2260)}
    live = {"snapshot": snapshot(), "automation_view": auto, "now": 2260}
    assessment = nudges.assess(live)
    assert assessment.reason == "humidifier_tank" and assessment.level == "yellow"
    assert assessment.problems == ["humidifier_tank"]
    for lang in ("de", "en"):
        hint = humidifier_report.warning(auto, lang)
        assert hint and hint in today.answer(live, lang, answer_id="today").text
        answer = humidifier_report.answer(live, lang, answer_id="tank")
        assert hint in answer.text and answer.proposals == []
        assert hint in fix.compose(live, lang, keys=["humidifier_tank"]).lines
    assert humidifier_report.asks("Is the humidifier tank empty?")
    assert humidifier_report.asks("Wie viel Wasser verbraucht der Befeuchter?")
    assert not humidifier_report.asks("Is the irrigation tank empty?")
    assert i18n.DE.keys() == i18n.EN.keys()


def test_authenticated_refill_and_settings_api_never_switch_ports():
    async def run():
        box = DemoBox()
        hum = HumidifierAutomation(box, None, None)
        async with TestClient(TestServer(make_app(box, Alerts(None), "test-key", automation=hum))) as client:
            assert (await client.post('/api/cmd', json={"action": "humidifier_refill", "was_empty": False})).status == 403
            headers = {"Cookie": f"{COOKIE}=test-key"}
            before = copy.deepcopy(box.ports)
            for body in ({"action": "humidifier_tank_config", "capacity_ml": 800, "rate_ml_h": None},
                         {"action": "humidifier_refill", "was_empty": False, "amount_ml": 750},
                         {"action": "humidifier_tank_config", "capacity_ml": 750, "rate_ml_h": None,
                          "model": U075['id'], "mist_mode": 'continuous'}):
                response = await client.post('/api/cmd', json=body, headers=headers)
                assert response.status == 200, await response.text()
            assert box.ports == before and hum.state["enabled"] is False
            assert hum.tank.state["refill_count"] == 1
            assert hum.tank.state["cycle_start_ml"] == 750
            assert hum.tank.view()['model']['id'] == U075['id']
            assert hum.tank.state['mist_mode'] == 'continuous'
            bad = await client.post('/api/cmd', json={"action": "humidifier_refill", "was_empty": "yes"}, headers=headers)
            assert bad.status == 400
    asyncio.run(run())


@pytest.mark.parametrize("question,lang", [
    ("Is the humidifier tank empty?", "en"), ("How much water does the humidifier consume?", "en"),
    ("Ist der Befeuchter-Tank leer?", "de"), ("Wie viel Wasser verbraucht der Befeuchter?", "de"),
])
def test_real_hank_answers_maintenance_without_retrieval_or_a_model(question, lang):
    router = Router(core.shared_index())
    route = router.route(question, lang_hint=lang)
    assert route.name == "humidifier_tank" and route.retrieval is None
    tank = HumidifierTank()
    watch(tank, 1000, 2260)
    live = {"snapshot": snapshot(), "automation_view": {"tank": tank.view(now=2260)}, "now": 2260}
    hank = core.Hank(live=lambda lang: live, client=None, index=core.shared_index())
    async def run():
        result = await hank.ask(question, lang=lang)
        assert result["done"] and result["answer"]["route"] == "humidifier_tank"
        assert result["answer"]["proposals"] == []
        assert result["answer"]["link"]["href"] == "#automation"
        assert humidifier_report.warning(live["automation_view"], lang) in result["answer"]["text"]
    asyncio.run(run())


def test_recording_failure_and_corrupt_storage_leave_estimates_unknown(monkeypatch):
    tank = HumidifierTank()
    tank.configure(800, 400)
    tank.refill(False, now=1000)
    watch(tank, 1000, 1600)
    def broken(data):
        raise OSError("test disk unavailable")
    monkeypatch.setattr(tank, "_save", broken)
    tank.observe(snapshot(), assigned=True, off_at=55, now=1660)
    assert tank.view(now=1660)["recording_error"]
    assert tank.view(now=1660)["remaining_ml_estimate"] is None
    tank.path.write_text('{"last": "bad"}', encoding='utf-8')
    loaded = HumidifierTank()
    assert loaded.error and loaded.view(now=1660)["remaining_ml_estimate"] is None


def test_750_ml_refill_uses_recorded_amount_instead_of_tank_capacity():
    tank = HumidifierTank()
    tank.refill(True, amount_ml=750, now=1000)
    watch(tank, 1000, 8200)
    tank.refill(True, amount_ml=750, now=8200)
    assert tank.state["rate_ml_h"] == 375
    watch(tank, 8200, 11800)
    view = tank.view(now=11800)
    assert view["remaining_ml_estimate"] == 375 and view["starting_ml"] == 750
    assert view["refills"][0]["amount_ml"] == 750
    with pytest.raises(ValueError):
        tank.refill(True, amount_ml=801, now=12000)


def test_device_consumption_question_does_not_hide_an_ingestion_question():
    router = Router(core.shared_index())
    question = 'How much water does the humidifier consume and how much cannabis can I consume?'
    assert router.route(question, lang_hint='en').name == 'health'


def test_unchanged_shadow_with_live_telemetry_still_records_runtime():
    tank = HumidifierTank()
    for now in range(1000, 2261, 60):
        snap = snapshot()
        snap['shadow_age'] = 9000 + now
        tank.observe(snap, assigned=True, off_at=55, now=now)
    assert tank.view(now=2260)['runtime_s'] == 1260
    assert tank.view(now=2260)['warning'] == 'no_response'
    snap['mqtt'] = False
    tank.observe(snap, assigned=True, off_at=55, now=2320)
    assert tank.state['response'] is None


def test_side_button_never_equates_power_with_mist_or_automatic_restart():
    tank = HumidifierTank()
    tank.configure(800, 400, button_start=True)
    tank.refill(True, amount_ml=750, now=1000)
    tank.confirm_mist(now=1000)
    watch(tank, 1000, 4600)
    assert tank.view(now=4600)['remaining_ml_estimate'] == 350
    watch(tank, 4660, 4660, on=False)
    assert tank.view(now=4660)['mist_confirmed_at'] is None
    watch(tank, 4720, 5980)
    view = tank.view(now=5980)
    assert view['warning'] == 'start_button' and view['mist_confirmation_needed']
    assert view['remaining_ml_estimate'] is None
    assert view['cycle_runtime_s'] == 3600
    assert 'side start button' in humidifier_report.warning({'tank': view}, 'en')
    tank.confirm_mist(now=5980)
    watch(tank, 6040, 6040)
    assert not tank.view(now=6040)['mist_confirmation_needed']
    assert tank.view(now=6040)['remaining_ml_estimate'] is None  # the prior unconfirmed interval remains a gap


def test_u075_capacity_correction_preserves_real_fill_history_and_confirmed_mist():
    tank = HumidifierTank()
    tank.configure(800, None, button_start=True)
    tank.refill(True, amount_ml=750, now=1000)
    tank.confirm_mist(now=1000)
    watch(tank, 1000, 1600)
    before = copy.deepcopy(tank.state)
    tank.configure(750, None, model=U075['id'])
    assert tank.state['capacity_ml'] == 750
    for key in ('refilled_at', 'cycle_start_ml', 'cycle_runtime_s', 'refill_count', 'refills', 'mist_confirmed_at'):
        assert tank.state[key] == before[key]
    view = HumidifierTank().view(now=1600)
    assert view['model']['model_number'] == 'VSE-ASU075'
    assert view['model']['low_water_shutoff'] is True
    assert view['level_measured'] is False
    assert view['rate_ml_h'] is None and view['remaining_ml_estimate'] is None
    assert view['mist_mode'] == 'unknown'  # the model alone does not identify the selected spray mode


def test_capacity_correction_does_not_preserve_a_fill_larger_than_the_real_tank():
    tank = HumidifierTank()
    tank.refill(True, amount_ml=800, now=1000)
    tank.configure(750, None, model=U075['id'])
    assert tank.state['refilled_at'] is None and tank.state['cycle_start_ml'] is None
    assert tank.state['refill_count'] == 1 and len(tank.state['refills']) == 1


def test_changing_mist_mode_invalidates_prior_calibration_and_retains_fill_log():
    tank = HumidifierTank()
    tank.configure(750, None, model=U075['id'], mist_mode='continuous')
    tank.refill(True, now=1000)
    watch(tank, 1000, 8200)
    tank.refill(True, now=8200)
    assert tank.state['rate_source'] == 'refills'
    tank.configure(750, None, mist_mode='intermittent')
    assert tank.state['mist_mode'] == 'intermittent' and tank.state['rate_ml_h'] is None
    assert not tank.state['cycle_complete'] and tank.state['refill_count'] == 2
    assert tank.state['cycle_start_ml'] == 750
    assert tank.view(now=8200)['model']['id'] == U075['id']


@pytest.mark.parametrize('kwargs', [{'model': 'invented'}, {'mist_mode': 'auto'}, {'model': []}, {'mist_mode': True}])
def test_unknown_model_and_mode_are_rejected_without_rewriting_the_tank(kwargs):
    tank = HumidifierTank()
    before = copy.deepcopy(tank.state)
    with pytest.raises(ValueError):
        tank.configure(750, None, **kwargs)
    assert tank.state == before


def test_u075_cannot_claim_the_wrong_capacity_but_custom_capacity_remains_available():
    tank = HumidifierTank()
    with pytest.raises(ValueError):
        tank.configure(800, None, model=U075['id'])
    tank.configure(750, None, model=U075['id'])
    tank.configure(900, None)
    assert tank.view()['model'] is None
    assert tank.state['capacity_ml'] == 900


def test_hank_explains_local_water_sensor_and_specs_without_inventing_live_telemetry():
    tank = HumidifierTank()
    tank.configure(750, None, model=U075['id'])
    view = tank.view()
    for lang in ('de', 'en'):
        text = '\n'.join(humidifier_report.lines({'tank': view}, lang))
        assert 'AeroStream U075' in text and 'VSE-ASU075' in text and '750' in text
        assert '30' in text and '50' in text and '18' in text and '28' in text
        assert ('nicht gemeldet' if lang == 'de' else 'not reported') in text
        assert ('noch unbekannt' if lang == 'de' else 'still unknown') in text
        assert ('nicht gemessen' if lang == 'de' else 'not been measured') in text
    assert humidifier_report.asks('What is the AeroStream U075 tank capacity?')
    answer = humidifier_report.answer({'automation_view': {'tank': view}}, 'en', answer_id='u075')
    assert len(answer.sources) == 1 and answer.sources[0].url == U075['source']
    assert not answer.proposals
