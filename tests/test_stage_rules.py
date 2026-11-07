"""Rules that depend on the plant stage (22.10.2026): the humidifier automation only runs with a stage the owner
confirmed for this run, and the humidity alarm follows the stage instead of a fixed 70 %, loosening only on a stage
the owner set."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from vgrow import automation as am
from vgrow import config, i18n
from vgrow.alerts import Alerts
from vgrow.automation import HumidifierAutomation
from vgrow.demo import DemoBox
from vgrow.plant import PlantLog, current_stage, load_profile, owner_stages, profile_problem
from vgrow.server import COOKIE, make_app, poll_handler

STAGES = [
    {"key": "veg", "label": "Wachstum", "label_en": "Vegetative", "from_day": 0, "to_day": 30,
     "targets": [{"metric": "probe_humidity", "label": "Feuchte", "unit": "%", "min": 55, "max": 70}]},
    {"key": "bloom", "label": "Blüte", "label_en": "Flower", "from_day": 30, "to_day": 999,
     "targets": [{"metric": "probe_humidity", "label": "Feuchte", "unit": "%", "min": 40, "max": 55}]},
]
ESTIMATED = {"plant": {"start_date": "2026-08-31"}, "stages": STAGES,
             "stage_estimate": {"key": "bloom", "since": "2026-10-22", "note": "Geschätzt", "note_en": "Estimated"}}
BY_DAYS = {"plant": {"start_date": "2026-08-31"}, "stages": STAGES}
PAUSED = "Die Automatik pausiert, bis die Phase bestätigt ist: auf der Seite den Vorschlag bestätigen oder die Phase setzen."


class Pushes:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    async def push(self, title: str, message: str, *, priority: int = 3) -> bool:
        self.pushed.append((title, message))
        return True


class SilentBox(DemoBox):
    """Takes every command and never reports it: the box is offline while the cloud is up."""

    sent = 0

    async def set_port(self, port: str, on: bool) -> None:
        self.sent += 1


def automation(monkeypatch, profile: dict[str, Any] = ESTIMATED, box: DemoBox | None = None):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(am, "load_profile", lambda: profile)
    pushes, box, log = Pushes(), box or DemoBox(), PlantLog()
    auto = am.HumidifierAutomation(box, pushes, log)
    # The owner named the humidifier on USB 2 on the page: only then does the rule switch it on (review 2, M3).
    auto.guard.assign("usb2", "humidifier", box.snapshot())
    auto.set_enabled(True)
    return box, auto, log, pushes


def snap(box: DemoBox, rh: float, age: float | None = 40) -> dict:  # type: ignore[type-arg]
    s = box.snapshot()
    s["climate"]["probe_humidity"] = rh
    s["sensors_age"] = age
    return s


def logged(auto: am.HumidifierAutomation) -> list[str]:
    return [json.loads(line)["action"] for line in auto.log_path.read_text(encoding="utf-8").splitlines()]


# ---------- the automation ----------

def test_an_estimated_stage_never_switches_the_humidifier_on(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto, log, pushes = automation(monkeypatch)
    box.ports["usb2"] = False
    assert asyncio.run(auto.check(snap(box, 30))) == "paused_unconfirmed_stage"  # far below the band: still off
    assert box.ports["usb2"] is False
    assert pushes.pushed == [("Luftbefeuchter-Automatik pausiert",
                              f"Die Phase ist nicht bestätigt, vorgeschlagen ist Blüte. {PAUSED}")]
    for _ in range(3):
        assert asyncio.run(auto.check(snap(box, 30))) is None  # once: no more pushes, no switching
    assert box.ports["usb2"] is False and len(pushes.pushed) == 1
    view = auto.view(snap(box, 30))
    assert view["needs_stage_confirmation"] is True
    assert view["estimate"] == {"key": "bloom", "label": "Blüte", "set_by": "estimate"}
    assert "paused_unconfirmed_stage" not in [e["action"] for e in view["recent"]]  # the page's list leaves it out
    assert logged(auto).count("paused_unconfirmed_stage") == 1  # the log file keeps it
    log.set_stage(view["estimate"]["key"])  # what the one-tap confirm does (the existing plant_stage action)
    assert auto.view(snap(box, 30))["needs_stage_confirmation"] is False and auto.view(snap(box, 30))["estimate"] is None
    assert asyncio.run(auto.check(snap(box, 30))) == "humidifier_on"  # confirmed: the band applies again
    assert not any(auto.state[flag] for flag in am.PAUSE_FLAGS)  # and a later unconfirmed stage acts once more


def test_a_running_humidifier_goes_off_once_with_the_confirmed_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto, _, pushes = automation(monkeypatch)
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(snap(box, 50))) == "paused_unconfirmed_stage" and box.ports["usb2"] is False
    assert pushes.pushed == [("Luftbefeuchter-Automatik pausiert",
                              "Die Phase ist nicht bestätigt, vorgeschlagen ist Blüte. Das Cockpit hat den "
                              f"Luftbefeuchter (USB 2) ausgeschaltet, die Box hat es bestätigt. {PAUSED}")]
    assert auto.state["last_action"]["action"] == "humidifier_off"
    assert auto.state["last_action"]["reason"] == "unconfirmed_stage"
    box.ports["usb2"] = True  # switched on again by hand: once means once
    assert asyncio.run(auto.check(snap(box, 50))) is None and box.ports["usb2"] is True and len(pushes.pushed) == 1


def test_an_unconfirmed_off_goes_out_again_until_the_box_reports_it(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(am, "CONFIRM_SECONDS", 0)
    import time

    clock = [time.time()]
    monkeypatch.setattr(am.time, "time", lambda: clock[0])
    box, auto, _, pushes = automation(monkeypatch, box=SilentBox())
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(snap(box, 50))) == "stage_pause_unconfirmed" and box.sent == 1  # type: ignore[attr-defined]
    title, body = pushes.pushed[0]
    assert title == "Luftbefeuchter: Ausschalten nicht bestätigt"
    assert "Bitte den Befeuchter selbst ausschalten" in body and body.endswith(PAUSED)
    assert asyncio.run(auto.check(snap(box, 50))) == "stage_pause_unconfirmed" and box.sent == 1  # type: ignore[attr-defined]
    clock[0] += 90  # the first wait of the backoff is over
    assert asyncio.run(auto.check(snap(box, 50))) == "stage_pause_unconfirmed" and box.sent == 2  # type: ignore[attr-defined]
    assert len(pushes.pushed) == 1 and auto.state["last_action"] is None  # said once, nothing claims "off"
    box.ports["usb2"] = False  # the box is back and has taken the off
    assert asyncio.run(auto.check(snap(box, 50))) == "paused_unconfirmed_stage"
    assert pushes.pushed[-1] == ("Luftbefeuchter jetzt aus", "Die Box meldet den Luftbefeuchter (USB 2) jetzt als aus.")
    assert logged(auto)[-2:] == ["failsafe_unconfirmed", "stage_pause_off"]  # the off landed late, and it is logged


def test_the_pause_off_shows_in_the_timeline_as_the_newest_line(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The review's probe: the pause switched USB 2 off, yet the newest timeline line still said "on at 44.6 %"."""
    box, auto, _, _ = automation(monkeypatch)
    auto._log({"action": "humidifier_on", "value": 44.6})
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(snap(box, 50))) == "paused_unconfirmed_stage" and box.ports["usb2"] is False
    recent = auto.view(snap(box, 50))["recent"]
    assert [e["action"] for e in recent[:2]] == ["stage_pause_off", "humidifier_on"]  # newest first
    restarted = am.HumidifierAutomation(box, None, PlantLog())  # read back from the log file after a restart
    assert restarted.view(snap(box, 50))["recent"][0]["action"] == "stage_pause_off"
    box2, auto2, _, _ = automation(monkeypatch)  # nothing to switch: the pause stays out of the timeline
    box2.ports["usb2"] = False
    asyncio.run(auto2.check(snap(box2, 50)))
    assert logged(auto2)[-1] == "paused_unconfirmed_stage"
    assert auto2.view(snap(box2, 50))["recent"][0]["action"] == "automation_on"  # its own switch-on stays the newest


def test_an_unreadable_plant_json_names_itself_and_leaves_the_stage_pause_alone(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """plant.json does not parse and no readable version is known (a restart with a broken file): the push says so,
    instead of "no stage is set", and nothing waits 15 minutes once the file is fixed."""
    broken = "JSONDecodeError: Expecting ',' delimiter: line 3 column 5 (char 41)"
    box, auto, log, pushes = automation(monkeypatch, {"plant": {"start_date": "2026-08-31"}, "stages": STAGES})
    log.set_stage("bloom", "2026-10-22")  # the owner's stage is in the log, only plant.json is unreadable
    monkeypatch.setattr(am, "load_profile", lambda: None)
    monkeypatch.setattr(am, "profile_problem", lambda: broken)
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(snap(box, 50))) == "paused_unreadable_profile" and box.ports["usb2"] is False
    assert pushes.pushed == [("Luftbefeuchter-Automatik pausiert: plant.json nicht lesbar",
                              f"plant.json ist nicht lesbar ({broken}), damit fehlen die Phase und ihre Zielwerte. Das "
                              "Cockpit hat den Luftbefeuchter (USB 2) ausgeschaltet, die Box hat es bestätigt. Die "
                              "Automatik pausiert, bis plant.json wieder lesbar ist.")]
    assert not any("keine Phase" in body for _, body in pushes.pushed)
    assert auto.state["last_switch"] is None and not any(auto.state[flag] for flag in am.PAUSE_FLAGS)
    assert auto.state["last_action"]["reason"] == "unreadable_profile"
    view = auto.view(snap(box, 50))
    assert view["profile_problem"] == broken and view["recent"][0]["action"] == "profile_pause_off"
    assert asyncio.run(auto.check(snap(box, 50))) is None and len(pushes.pushed) == 1  # once
    profile = {"plant": {"start_date": "2026-08-31"}, "stages": STAGES}
    monkeypatch.setattr(am, "load_profile", lambda: profile)  # fixed: the owner's stage counts at the next poll
    monkeypatch.setattr(am, "profile_problem", lambda: None)
    assert asyncio.run(auto.check(snap(box, 30))) == "humidifier_on"  # no 15-minute wait from the pause
    assert not any(auto.state[flag] for flag in am.PROFILE_FLAGS)
    assert auto.view(snap(box, 30))["profile_problem"] is None


def test_stale_readings_still_go_first(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto, _, pushes = automation(monkeypatch)
    box.ports["usb2"] = True
    assert asyncio.run(auto.check(snap(box, 50, 899))) is None and box.ports["usb2"] is True  # the fail-safe's wait
    assert asyncio.run(auto.check(snap(box, 50, 901))) == "failsafe_off" and box.ports["usb2"] is False
    assert pushes.pushed[0][0] == "Luftbefeuchter aus: keine frischen Messwerte"
    assert asyncio.run(auto.check(snap(box, 50))) == "paused_unconfirmed_stage"  # fresh again: now the pause
    assert pushes.pushed[-1] == ("Luftbefeuchter-Automatik pausiert",
                                 f"Die Phase ist nicht bestätigt, vorgeschlagen ist Blüte. {PAUSED}")


def test_days_since_the_start_or_no_stage_never_confirm(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto, _, _ = automation(monkeypatch, BY_DAYS)
    view = auto.view(snap(box, 50))
    assert view["needs_stage_confirmation"] is True and view["estimate"]["set_by"] == "days"
    token = i18n.REQUEST_LANG.set("en")
    try:
        assert auto.view(snap(box, 50))["estimate"]["label"] in ("Flower", "Vegetative")  # the page's language
    finally:
        i18n.REQUEST_LANG.reset(token)
    box, auto, _, pushes = automation(monkeypatch, {"plant": {}, "stages": []})
    box.ports["usb2"] = True
    view = auto.view(snap(box, 50))
    assert view["needs_stage_confirmation"] is True and view["estimate"] is None and view["band"] == [None, None]
    assert asyncio.run(auto.check(snap(box, 50))) == "paused_unconfirmed_stage" and box.ports["usb2"] is False
    assert pushes.pushed[0][1].startswith("Es ist keine Phase gesetzt. Das Cockpit hat den Luftbefeuchter")


def test_a_manual_switch_in_the_pause_means_the_owner_took_over(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    box, auto, _, pushes = automation(monkeypatch)
    box.ports["usb2"] = True  # switched on by hand before the first poll of the pause
    auto.manual_switch()
    assert asyncio.run(auto.check(snap(box, 50))) is None and box.ports["usb2"] is True and pushes.pushed == []


def test_one_tap_confirms_the_estimate_with_the_existing_action() -> None:
    async def run() -> None:
        box, alerts, log = DemoBox(), Alerts(), PlantLog()
        app = make_app(box, alerts, "k", plant_log=log, automation=HumidifierAutomation(box, alerts, log))
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            before = (await (await client.get("/api/state", headers={"X-Lang": "en"})).json())["automation"]
            assert before["needs_stage_confirmation"] is True
            assert before["estimate"] == {"key": "early_flower", "label": "Early flower (stretch)", "set_by": "estimate"}
            ok = await client.post("/api/cmd", json={"action": "plant_stage", "key": before["estimate"]["key"]})
            assert ok.status == 200
            after = await (await client.get("/api/state")).json()
            assert after["automation"]["needs_stage_confirmation"] is False and after["automation"]["estimate"] is None
            assert after["plant"]["stage"]["set_by"] == "manual" and after["automation"]["band"] == [45, 60]

    asyncio.run(run())


# ---------- the humidity alarm ----------

class RecordingAlerts(Alerts):
    def __init__(self, rules: dict) -> None:  # type: ignore[type-arg]
        self._session = None
        self.config = {"remind_hours": 6, "rules": rules}
        self._state_path = config.STATE_DIR / "alerts-state.json"
        self.state = {}
        self.target = ("https://ntfy.example", "t")
        self.pushed: list[str] = []

    async def push(self, title: str, message: str, *, priority: int = 3) -> bool:
        self.pushed.append(f"{title}|{message}")
        return True


def humidity_alerts() -> RecordingAlerts:
    return RecordingAlerts({"humidity_high": {**config.alert_rules()["rules"]["humidity_high"], "for_minutes": 0}})


def real_stage(key: str, set_by: str) -> dict[str, Any]:
    stage = next(s for s in load_profile()["stages"] if s["key"] == key)  # type: ignore[index]
    return {**stage, "set_by": set_by}


def test_the_rule_keeps_70_as_fallback_and_names_the_margin_as_its_own() -> None:
    rule = config.alert_rules()["rules"]["humidity_high"]
    assert rule["above"] == 70 and rule["stage_max_margin"] == 10
    assert rule["stage_max_margin_note"].startswith("Eigene Regel des Cockpits, keine belegte Zahl")


def test_seedling_72_is_fine_early_flower_72_alarms_and_without_a_stage_70_applies() -> None:
    seedling = humidity_alerts()  # seedling band 65 to 75 %, set by the owner: the limit is 75 + 10
    asyncio.run(seedling.check({"climate": {"probe_humidity": 72.0}, "stage": real_stage("seedling", "manual")}))
    assert seedling.pushed == []
    flower = humidity_alerts()  # early flower: at most 60 %, so 60 + 10; an estimate may hold it there (not above 70)
    asyncio.run(flower.check({"climate": {"probe_humidity": 72.0}, "stage": real_stage("early_flower", "estimate")}))
    assert flower.pushed == ["Zu feucht (Fühler)|Zu feucht (Fühler) (Grenze 70 %). Jetzt 72,0 %. Die Grenze folgt der "
                             "Phase Frühe Blüte (Stretch) (geschätzt): Ziel höchstens 60 % plus 10 Punkte = 70 %."]
    none = humidity_alerts()
    asyncio.run(none.check({"climate": {"probe_humidity": 72.0}}))
    assert none.pushed == ["Zu feucht (Fühler)|Zu feucht (Fühler) (Grenze 70 %). Jetzt 72,0 %."]
    asyncio.run(seedling.check({"climate": {"probe_humidity": 86.0}, "stage": real_stage("seedling", "manual")}))
    assert seedling.pushed[0].startswith("Zu feucht (Fühler)|Zu feucht (Fühler) (Grenze 85 %). Jetzt 86,0 %.")
    english = humidity_alerts()
    token = i18n.REQUEST_LANG.set("en")
    try:
        asyncio.run(english.check({"climate": {"probe_humidity": 72.0}, "stage": real_stage("early_flower", "manual")}))
    finally:
        i18n.REQUEST_LANG.reset(token)
    assert english.pushed == ["Too humid (probe)|Too humid (probe) (limit 70%). Now 72.0%. The limit follows the stage "
                              "Early flower (stretch): target at most 60% plus 10 points = 70%."]


def test_an_unconfirmed_stage_may_tighten_the_alarm_but_never_loosen_it() -> None:
    """The review's probes: an estimated veg stage made 78 % pass (limit 80), an estimated seedling raised it to 85,
    while the automation in the same snapshot refused that very stage."""
    for key, set_by in (("veg", "estimate"), ("seedling", "estimate"), ("seedling", "days")):
        alerts = humidity_alerts()
        asyncio.run(alerts.check({"climate": {"probe_humidity": 78.0}, "stage": real_stage(key, set_by)}))
        assert alerts.pushed == ["Zu feucht (Fühler)|Zu feucht (Fühler) (Grenze 70 %). Jetzt 78,0 %."], (key, set_by)
        rule = next(r for r in alerts.view(real_stage(key, set_by)) if r["name"] == "humidity_high")
        assert (rule["limit"], rule["stage_limit"]) == (70, None)  # the fixed limit holds, and the page says so
    tighter = humidity_alerts()  # late flower, at most 50 %: an estimate may lower the limit to 60
    asyncio.run(tighter.check({"climate": {"probe_humidity": 62.0}, "stage": real_stage("late_flower", "estimate")}))
    assert tighter.pushed and "(Grenze 60 %)" in tighter.pushed[0]
    confirmed = humidity_alerts()  # the owner's veg stage raises it to 80: 78 % is fine
    asyncio.run(confirmed.check({"climate": {"probe_humidity": 78.0}, "stage": real_stage("veg", "manual")}))
    assert confirmed.pushed == []


def test_the_page_shows_the_limit_that_applies_now() -> None:
    alerts = humidity_alerts()
    rule = next(r for r in alerts.view(real_stage("seedling", "manual")) if r["name"] == "humidity_high")
    assert (rule["op"], rule["limit"]) == (">", 85)
    assert rule["stage_limit"] == {"key": "seedling", "label": "Keimling", "max": 75, "margin": 10, "limit": 85,
                                   "set_by": "manual"}
    estimated = next(r for r in alerts.view(real_stage("early_flower", "estimate")) if r["name"] == "humidity_high")
    assert estimated["limit"] == 70 and estimated["stage_limit"]["set_by"] == "estimate"  # the page marks it
    fixed = next(r for r in alerts.view() if r["name"] == "humidity_high")
    assert fixed["limit"] == 70 and fixed["stage_limit"] is None
    other = RecordingAlerts({"temp_high": config.alert_rules()["rules"]["temp_high"]})
    assert other.view(real_stage("seedling", "manual"))[0]["stage_limit"] is None  # only rules with a margin follow


def test_every_poll_hands_the_stage_to_the_alarm() -> None:
    class HumidBox(DemoBox):
        def snapshot(self) -> dict[str, Any]:
            s = super().snapshot()
            s["climate"]["probe_humidity"] = 72.0
            return s

    alerts, log = humidity_alerts(), PlantLog()
    on_poll = poll_handler(alerts, None, {"camera": None}, log)
    asyncio.run(on_poll(HumidBox()))  # the real profile: estimated early flower, limit 70
    assert len(alerts.pushed) == 1 and "Frühe Blüte (Stretch)" in alerts.pushed[0]
    log.set_stage("seedling")
    asyncio.run(on_poll(HumidBox()))  # the owner says seedling: 72 % is inside its limit of 85
    assert alerts.pushed[-1].startswith("Wieder ok|Zu feucht (Fühler): wieder im Rahmen.")


# ---------- the stage belongs to this run, plant.json stays readable ----------

def test_a_stage_from_an_earlier_run_confirms_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The review's probe: the log still held late flower since 1 June from the plant before, the new plant.json
    starts on 22.10.2026, and the automation ran the old band on the new plant as if the owner had confirmed it."""
    profile = {"plant": {"start_date": "2026-10-22"}, "stages": STAGES}
    box, auto, log, _ = automation(monkeypatch, profile)
    log.data["stages"] = [{"key": "bloom", "since": "2026-06-01"}, {"key": "veg"}, "not an entry"]
    assert owner_stages(profile, log) == [] and current_stage(profile, log)["set_by"] == "days"
    assert auto.view(snap(box, 60))["needs_stage_confirmation"] is True
    box.ports["usb2"] = False
    assert asyncio.run(auto.check(snap(box, 60))) == "paused_unconfirmed_stage" and box.ports["usb2"] is False
    log.set_stage("veg", "2026-10-22")  # set for this plant, on its first day: counts
    assert current_stage(profile, log)["set_by"] == "manual" and owner_stages(profile, log)[-1]["key"] == "veg"
    undated = {"plant": {}, "stages": STAGES}  # a run without a start date: every entry counts, as before
    assert len(owner_stages(undated, log)) == 3


def test_a_broken_plant_json_keeps_the_last_readable_version(tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
    run = tmp_path / "plant.json"
    run.write_text(json.dumps({"plant": {"start_date": "2026-08-31"}, "stages": STAGES}), encoding="utf-8")
    assert load_profile(run)["stages"][0]["key"] == "veg" and profile_problem(run) is None  # type: ignore[index]
    run.write_text('{"plant": {"start_date": "2026-08-31"},}', encoding="utf-8")  # one trailing comma
    for _ in range(3):
        assert load_profile(run)["stages"][0]["key"] == "veg"  # type: ignore[index]  # the last readable version
    assert (profile_problem(run) or "").startswith("JSONDecodeError")
    assert caplog.text.count("plant.json unreadable") == 1  # logged once, not on every poll
    run.write_text(json.dumps({"plant": {}, "stages": []}), encoding="utf-8")
    assert load_profile(run)["stages"] == [] and profile_problem(run) is None  # type: ignore[index]
    never = tmp_path / "never.json"
    never.write_text("{", encoding="utf-8")
    assert load_profile(never) is None and profile_problem(never)  # no readable version known: None, and why
    missing = tmp_path / "missing.json"
    assert load_profile(missing) is None and profile_problem(missing) is None  # missing is not broken
