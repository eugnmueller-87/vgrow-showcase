"""The supervision's side of the alarm engine (vgrow/alerts.py, vgrow/supervision.py): each of the 24 rules holds,
pushes, reminds and says when it is back; the states map to the engine; the watchdog's two rules have their own scope;
one bad rule never silences the others; the test doubles that skip __init__ still work; a failed push stops further
tries in that call; alerts.json and the SIGNALS table agree, and every limit is its code constant.

Why (15.11.2026): the thirst index sat nine days without a measurement and nothing said so.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from vgrow import alerts as alerts_mod
from vgrow import automation, box, config, fan_stick, grow_plan, i18n, light_schedule, light_view, measurements, news
from vgrow import soil, supervision, thirst, watering
from vgrow.alerts import Alerts, breached
from vgrow.hank import core as hank_core
from vgrow.i18n import localize

REPO = Path(__file__).resolve().parent.parent
RULES = config.alert_rules()["rules"]
NAMES = [name for name, rule in RULES.items() if rule.get("kind") == "supervision"]
OLD = ["temp_low", "temp_high", "humidity_high", "water_low", "tank_empty", "offline", "camera_offline",
       "usb_camera_offline", "dark_period", "short_day"]
HOUR, DAY = 3600.0, 86400.0
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as codes, so this file holds neither itself

# A silent block's values per signal, as its reader gives them.
SAMPLE: dict[str, dict[str, Any]] = {
    "poll_stalled": {"age": 400.0, "phase": "Thirst.check"},
    "supervision_failing": {"cause": "step", "what": [["step", "Thirst.check"]], "n": 3, "error": "KeyError"},
    "supervision_silent": {"age": 200.0},
    "tasks_dead": {"names": ["soil_sensor", "growcam_watch"]},
    "watering_watchdog": {"age": 45.0},
    "light_timer": {"cause": "unconfirmed"},
    "device_link": {"age": 600.0, "cause": "link"},
    "probe_silent": {"age": 1000.0},
    "builtin_silent": {"age": 1000.0},
    "tank_sensor_silent": {"age": 1000.0},
    "soil_silent": {"age": 700.0, "cause": "gateway"},
    "thirst_engine": {"age": 400.0, "cause": "missing"},
    "thirst_input": {"age": 9 * DAY + 600},
    "watering_skips": {"n": 2, "reason": {"outcome": "skipped", "code": "thirst", "gate": "no_flow",
                                          "detail": {"de": "Erst den Durchfluss messen.", "en": "Measure the flow first."}}},
    "push_broken": {"cause": "none"},
    "hank_silent": {"age": 400.0, "cause": "beat"},
    "soil_uncalibrated": {"age": 9 * DAY + 600},
    "thirst_sensor_look": {"age": 1200.0},
    "ruler_silent": {"age": 28 * HOUR},
    "fan_stick_silent": {"age": 1000.0},
    "pest_watch_silent": {"age": 25 * HOUR},
    "store_silent": {"age": 3000.0, "cause": "collect"},
    "news_silent": {"age": 3 * DAY},
    "ollama_silent": {"age": 2000.0},
    "restarts": {"n": 23},
}
QUIET_PRIORITY = ("news_silent", "ollama_silent", "restarts")  # priority 2, no sound


class Pushes(Alerts):
    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[tuple[str, str, int]] = []
        self.answer = True

    async def push(self, title: str, message: str, *, priority: int = 3, click: str | None = None) -> bool:
        self.pushed.append((title, message, priority))
        return self.answer


def only(alerts: Alerts, *names: str) -> None:
    alerts.config = copy.deepcopy(alerts.config)
    alerts.config["rules"] = {name: alerts.config["rules"][name] for name in names}
    alerts.state = {}


def snap_of(name: str, state: str, params: dict[str, Any] | None = None, at: float = 1_000_000.0) -> dict[str, Any]:
    block = {"signal": name, "state": state, "at": at, "params": params or {}}
    return {"supervision": {"at": at, "signals": {name: block}}}


def check(alerts: Alerts, name: str, snap: dict[str, Any]) -> list[str]:
    watchdog = RULES[name].get("driver") == "watchdog"
    return asyncio.run(alerts.check(snap, only=alerts.watchdog_rules() if watchdog else None))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_000_000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: now[0])
    return now


def priority_of(name: str) -> int:
    if name == "watering_watchdog":
        return 5
    if name in QUIET_PRIORITY:
        return 2
    return 3 if RULES[name]["level"] == "yellow" else 4


# ---------- every rule through the engine ----------

@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("lang", ["de", "en"])
def test_each_rule_holds_pushes_reminds_and_recovers(name: str, lang: str, clock: list[float]) -> None:
    rule = RULES[name]
    shown = localize(rule, lang)
    alerts = Pushes()
    only(alerts, name)
    token = i18n.REQUEST_LANG.set(lang)
    try:
        silent = snap_of(name, "silent", SAMPLE[name])
        check(alerts, name, silent)  # the hold starts
        clock[0] += rule["for_minutes"] * 60 - 1
        check(alerts, name, silent)
        assert alerts.pushed == []  # not before its hold
        clock[0] += 2
        check(alerts, name, silent)
        assert len(alerts.pushed) == 1
        title, body, priority = alerts.pushed[0]
        now = supervision.reading(silent["supervision"]["signals"][name], lang)
        assert title == shown["label"] and body.startswith(shown["label"] + ". ")
        assert now in body and body.endswith(shown["hint"])  # label, what is wrong now, what to do
        assert "{" not in body and EM_DASH not in body and EN_DASH not in body and "..." not in body
        assert priority == priority_of(name)
        clock[0] += rule["remind_hours"] * 3600 - 10
        check(alerts, name, silent)
        assert len(alerts.pushed) == 1  # no repeat before remind_hours
        clock[0] += 20
        check(alerts, name, silent)
        again = "Immer noch: " if lang == "de" else "Still: "
        assert len(alerts.pushed) == 2 and alerts.pushed[1][1].startswith(again + shown["label"])
        check(alerts, name, snap_of(name, "ok", at=clock[0]))
        back = alerts.pushed[-1][1]
        assert len(alerts.pushed) == 3 and ("wieder im Rahmen" if lang == "de" else "back in range") in back
        assert shown["hint"] not in back and alerts.pushed[-1][2] == 2
    finally:
        i18n.REQUEST_LANG.reset(token)


def test_the_sentences_say_the_age_in_plain_words() -> None:
    def text(name: str, lang: str = "de", **params: Any) -> str:
        return supervision.reading({"signal": name, "state": "silent", "params": params}, lang)

    assert text("soil_uncalibrated", age=9 * DAY + 5) == ("Seit 9 Tagen liefert der Sensor Werte, ist aber nicht auf "
                                                          "deine Erde eingestellt.")
    assert supervision.age_text(59, "de") == "1 Minute" and supervision.age_text(300, "de") == "5 Minuten"
    assert supervision.age_text(HOUR, "de") == "1 Stunde" and supervision.age_text(3 * HOUR + 59, "de") == "3 Stunden"
    assert supervision.age_text(DAY, "de") == "1 Tag" and supervision.age_text(9 * DAY, "de") == "9 Tagen"
    assert supervision.age_text(HOUR, "en") == "1 hour" and supervision.age_text(2 * DAY, "en") == "2 days"
    # the parts by the names the page gives them, never a step's __qualname__, a task id or alerts.json (15.11.2026)
    assert text("poll_stalled", age=400, phase="Thirst.check") == (
        "Die Box wurde seit 6 Minuten nicht mehr abgefragt (hängt gerade: Durst-Index).")
    assert text("poll_stalled", age=400) == "Die Box wurde seit 6 Minuten nicht mehr abgefragt."
    assert text("poll_stalled", "en", age=400, phase="dead") == "The box has not been polled for 6 minutes (the polling has ended)."
    assert text("soil_silent", cause="sensor", age=700) == ("Das Gateway antwortet, aber der Bodenfühler sendet seit "
                                                           "11 Minuten nichts.")
    assert text("supervision_failing", cause="step", what=[["step", "Thirst.check"]], n=3, error="KeyError") == (
        "Durst-Index: bricht seit 3 Abfragen hintereinander ab.")
    assert text("supervision_failing", "en", cause="step", what=[["step", "AutopilotHub.check"],
                                                               ["step", "Thirst.check"]], n=4, error="KeyError") == (
        "Autopilot, Thirst index: failed on 4 polls in a row.")
    assert text("supervision_failing", cause="step", what=[["step", "Mystery.check"]], n=3) == (
        "Ein Teil des Servers: bricht seit 3 Abfragen hintereinander ab.")  # a step without words: never its name
    assert text("supervision_failing", cause="read", what=[["read", ""]], n=3) == (
        "Aufsicht: bricht seit 3 Abfragen hintereinander ab.")
    labels = {"tank_empty": {"label": "Tank leer", "label_en": "Tank empty"}}
    assert text("supervision_failing", cause="rule", what=[["rule", "tank_empty"]], n=1, error="KeyError",
                labels=labels) == "Alarmregel nicht auswertbar: Tank leer."
    assert text("supervision_failing", "en", cause="rule", what=[["rule", "tank_empty"]], n=1, labels=labels) == (
        "Alarm rule cannot be evaluated: Tank empty.")
    assert text("supervision_failing", cause="rule", what=[["rule", "bad"]], n=1) == (
        "Alarmregel nicht auswertbar: eine Regel ohne Namen.")
    assert text("tasks_dead", names=["soil_sensor"]) == "Nicht mehr aktiv: Bodensensor."
    assert text("tasks_dead", "en", names=["vgrow_supervise", "vgrow_shadow_refresh", "new_thing"]) == (
        "No longer active: box connection, a part of the server.")  # one part once
    reason = {"code": "setup"}
    assert text("watering_skips", n=2, reason=reason) == ("Die letzten 2 Gießtermine liefen nicht: Zuerst Laufzeit und "
                                                         "Pumpenschalter bestätigen.")
    unknown = supervision.reading({"signal": "probe_silent", "state": "unknown", "params": {"error": "KeyError"}}, "en")
    assert unknown == ("The supervision cannot check this part right now. This is no finding about the part itself. "
                       "Please restart VGrow on the Mac; if it happens again, read the log.")
    assert text("restarts", "en", n=21) == "VGrow restarted 21 times in the last 24 hours without a deploy."


def test_the_owner_reads_no_code_names() -> None:
    """Every task and every poll step has its words in both languages, the step names are the real __qualname__ the poll
    reports (server.poll_handler), and no rendered sentence carries a task id, a qualname, a file or a class."""
    from vgrow.autopilot import AutopilotHub
    from vgrow.automation import HumidifierAutomation

    steps = [Alerts.check, HumidifierAutomation.check, AutopilotHub.check, thirst.Thirst.check,
             fan_stick.FanStick.check, grow_plan.GrowPlan.check]
    for step in steps:
        assert f"sup.step.{step.__qualname__}" in supervision.DE, step.__qualname__  # a rename falls back silently
    ids = [*supervision.HANDLES, *supervision.REQUIRED]
    for name in ids:
        assert f"sup.task.{name}" in supervision.DE and f"sup.task.{name}" in supervision.EN, name
    for lang in ("de", "en"):
        said = " ".join([
            supervision.reading({"signal": "tasks_dead", "state": "silent", "params": {"names": ids}}, lang),
            supervision.reading({"signal": "supervision_failing", "state": "silent", "params": {
                "cause": "step", "what": [["step", s.__qualname__] for s in steps], "n": 3, "error": "KeyError"}},
                lang),
            supervision.reading({"signal": "supervision_failing", "state": "silent", "params": {
                "cause": "rule", "what": [["rule", "tank_empty"]], "n": 1, "error": "KeyError"}}, lang),
            *(supervision.reading({"signal": "poll_stalled", "state": "silent", "params": {
                "age": 400, "phase": s.__qualname__}}, lang) for s in steps)])
        for code in [*(n for n in ids if "_" in n), *(s.__qualname__ for s in steps), "tank_empty", "alerts.json",
                     "KeyError", ".check"]:
            assert code not in said, (lang, code)


def test_an_unknown_reader_pushes_as_unchecked_never_as_the_parts_fault(clock: list[float]) -> None:
    """A reader that raised pushed the part's own label at its priority, 5 for the pump watchdog, with a hint about what
    the silent part costs: a hardware fault nobody observed. Its push and Hank's row now say it cannot be checked."""
    alerts = Pushes()
    only(alerts, "watering_watchdog")
    unknown = snap_of("watering_watchdog", "unknown", {"error": "AttributeError"})
    check(alerts, "watering_watchdog", unknown)
    clock[0] += RULES["watering_watchdog"]["for_minutes"] * 60 + 1
    check(alerts, "watering_watchdog", unknown)
    title, body, priority = alerts.pushed[-1]
    assert title == "Pumpen-Wächter reagiert nicht (nicht prüfbar)" and priority == alerts_mod.UNCHECKED_PRIORITY == 4
    assert body.startswith(title + ". Die Aufsicht kann diesen Teil gerade nicht prüfen.")
    assert "kein Befund über den Teil selbst" in body and RULES["watering_watchdog"]["hint"] not in body
    assert "AttributeError" not in body
    row = next(r for r in alerts.view() if r["name"] == "watering_watchdog")
    assert row["state"] == "unknown" and row["active"]  # Hank reads it from the row
    check(alerts, "watering_watchdog", snap_of("watering_watchdog", "silent", SAMPLE["watering_watchdog"]))
    assert next(r for r in alerts.view() if r["name"] == "watering_watchdog")["state"] == "silent"
    assert Pushes().view()[0]["state"] is None  # a rule never checked, and every older rule: no state


def page_texts() -> dict[str, dict[str, str]]:
    text = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
    return json.loads(text.removeprefix("window.I18N = ").strip().removesuffix(";"))


def test_the_hints_name_the_page_as_it_reads() -> None:
    """The soil sensor's hint named a button "Einstellen" the page does not have and one tap where it takes two
    (review of 15.11.2026). Every hint that sends the owner to a page, card or button uses the page's own words, in
    both languages, so a renamed label fails here instead of in a push."""
    page = page_texts()
    quote = {"de": ("„", "“"), "en": ("“", "”")}
    for lang in ("de", "en"):
        rules = {name: localize(RULES[name], lang) for name in NAMES}
        words = {**page[lang], **(grow_plan.DE if lang == "de" else grow_plan.EN)}
        on, off = quote[lang]

        def quoted(key: str) -> str:
            return f"{on}{words[key]}{off}"

        soil = rules["soil_uncalibrated"]["hint"]
        assert words["waterPage.title"] in soil and quoted("water.setTitle") in soil and quoted("water.setWet") in soil
        assert ("noch einmal antippen" if lang == "de" else "tap it once more") in soil  # the save takes a second tap
        ruler = rules["ruler_silent"]["hint"]
        assert words["cockpit.page.automation"] in ruler and quoted("gp.title") in ruler
        assert quoted("gp.btn.measure") in ruler
        skips = rules["watering_skips"]["hint"]
        assert words["cockpit.page.automation"] in skips and words["watering.title"] in skips
        thirst_hint = rules["thirst_input"]["hint"]
        assert words["cockpit.page.automation"] in thirst_hint and words["thirst.title"] in thirst_hint
        assert all(words[f"thirst.{answer}"] in thirst_hint for answer in ("dry", "moist", "wet"))
        storage = {"state": "silent", "params": {"cause": "storage"}}
        light = supervision.reading({"signal": "light_timer", **storage}, lang)
        assert words["cockpit.page.devices"] in light and words["lightView.title"] in light
        assert quoted("lightTimer.save") in light
        plan = supervision.reading({"signal": "watering_skips", **storage}, lang)
        assert words["cockpit.page.automation"] in plan and quoted("watering.save") in plan
        # a hint stands alone: Hank shows it beside the label only ("Gießen von Hand kommt nicht an"), without the
        # sentence, so "erreicht ihn so nicht" had nothing to point at; and a step names who acts, never "bitte melden"
        assert words["thirst.title"].lower() in rules["thirst_sensor_look"]["hint"].lower()
        for name in NAMES:
            assert "melden" not in rules[name]["hint"] and "report it" not in rules[name]["hint"], name


# ---------- the states ----------

@pytest.mark.parametrize(("state", "expected"), [("silent", True), ("unknown", True), ("ok", False), ("starting", None),
                                                 ("waiting", None), ("off", None), ("unconfigured", None),
                                                 ("muted", None)])
def test_states_map_to_the_engine(state: str, expected: bool | None, clock: list[float]) -> None:
    rule = RULES["soil_silent"]
    assert breached(rule, snap_of("soil_silent", state)) is expected
    alerts = Pushes()
    only(alerts, "soil_silent")
    silent = snap_of("soil_silent", "silent", SAMPLE["soil_silent"])
    asyncio.run(alerts.check(silent))
    clock[0] += rule["for_minutes"] * 60 + 1
    asyncio.run(alerts.check(silent))
    assert alerts.state["soil_silent"]["active"] and len(alerts.pushed) == 1
    asyncio.run(alerts.check(snap_of("soil_silent", state)))
    if state in ("starting", "waiting"):  # decides nothing: the episode stays as it is
        assert alerts.state["soil_silent"]["active"] and len(alerts.pushed) == 1
    elif state in ("off", "unconfigured", "muted"):  # ends quietly, as a hidden camera does
        assert alerts.state["soil_silent"] == {"since": None, "active": False, "last_push": None, "source": None}
        assert len(alerts.pushed) == 1, "no 'back' push for a part that is only switched off"
    elif state == "ok":
        assert not alerts.state["soil_silent"]["active"] and "wieder im Rahmen" in alerts.pushed[-1][1]


def test_without_a_supervision_block_no_rule_decides() -> None:
    for name in NAMES:
        assert breached(RULES[name], {}) is None, name
        assert breached(RULES[name], {"supervision": {"signals": {}}}) is None, name
        assert breached(RULES[name], {"supervision": "garbage"}) is None, name


# ---------- the watchdog's scope ----------

def test_scope_driver(clock: list[float]) -> None:
    poll = Pushes()  # every rule of alerts.json, as the poll runs them
    assert poll.watchdog_rules() == {"poll_stalled", "supervision_failing"}
    stalled = snap_of("poll_stalled", "silent", SAMPLE["poll_stalled"])
    asyncio.run(poll.check(stalled))
    clock[0] += 2 * HOUR
    asyncio.run(poll.check(stalled))
    assert "poll_stalled" not in poll.state  # the poll path never evaluates the watchdog's rules
    assert "Abfrage der Box steht" not in [p[0] for p in poll.pushed]
    watchdog = Pushes()
    watchdog.state = {}
    meta = watchdog.watchdog_rules()
    asyncio.run(watchdog.check(stalled, only=meta))
    clock[0] += 2 * HOUR
    asyncio.run(watchdog.check(stalled, only=meta))
    assert [p[0] for p in watchdog.pushed] == ["Abfrage der Box steht"]
    # the empty-snapshot trap: the watchdog's snapshot holds nothing else, and "offline" must never read it
    assert set(watchdog.state) <= meta and "offline" not in watchdog.state


# ---------- isolation, doubles, failed pushes ----------

def test_one_bad_rule_does_not_silence_the_others(clock: list[float]) -> None:
    alerts = Pushes()
    good = copy.deepcopy(RULES["soil_silent"])
    bad = {k: v for k, v in good.items() if k != "for_minutes"}
    alerts.config = {"remind_hours": 6, "rules": {"bad": bad, "good": {**good, "for_minutes": 0}}}
    alerts.state = {}
    silent = snap_of("soil_silent", "silent", SAMPLE["soil_silent"])
    asyncio.run(alerts.check(silent))
    assert len(alerts.pushed) == 1 and alerts.pushed[0][0] == "Bodensensor meldet nichts"
    assert alerts.broken == {"bad": {"error": "KeyError", "n": 1, "since": clock[0]}}
    asyncio.run(alerts.check(silent))
    assert alerts.broken["bad"]["n"] == 2
    assert [row["name"] for row in alerts.view()] == ["good"]  # a broken entry costs its row, never the page
    alerts.config["rules"]["bad"]["for_minutes"] = 20  # fixed
    asyncio.run(alerts.check(silent))
    assert alerts.broken == {}
    alerts.config["rules"]["worse"] = "not a rule"
    asyncio.run(alerts.check(silent))
    assert set(alerts.broken) == {"worse"} and [row["name"] for row in alerts.view()] == ["bad", "good"]


class Recording(Alerts):
    """The pattern of tests/test_safety.py, test_vgrow.py and test_stage_rules.py: no __init__ call, push without click."""

    def __init__(self, rules: dict[str, Any]) -> None:
        self._session = None
        self.config = {"remind_hours": 6, "rules": rules}
        self._state_path = config.STATE_DIR / "alerts-state.json"
        self.state = {}
        self.target = ("https://ntfy.example", "t")
        self.pushed: list[str] = []

    async def push(self, title: str, message: str, *, priority: int = 3) -> bool:
        self.pushed.append(f"{title}|{message}|{priority}")
        return True


def test_doubles_that_skip_init_still_work(clock: list[float]) -> None:
    alerts = Recording({name: {**RULES[name], "for_minutes": 0} for name in ("soil_silent", "poll_stalled")})
    asyncio.run(alerts.check(snap_of("soil_silent", "silent", SAMPLE["soil_silent"])))
    asyncio.run(alerts.check(snap_of("poll_stalled", "silent", SAMPLE["poll_stalled"]),
                             only=alerts.watchdog_rules()))
    assert [p.split("|")[0] for p in alerts.pushed] == ["Bodensensor meldet nichts", "Abfrage der Box steht"]
    assert alerts.pushed[0].endswith("|4")
    assert alerts.push_health() == {"target": True, "failed_since": None, "failed_at": None, "ok_at": None}
    assert alerts.broken == {} and {r["kind"] for r in alerts.view()} == {"supervision"}


class Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> Resp:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class Session:
    def __init__(self) -> None:
        self.status = 500
        self.posts: list[str] = []

    def post(self, url: str, data: bytes, headers: dict[str, str], timeout: Any) -> Resp:
        self.posts.append(headers["Title"])
        return Resp(self.status)


def test_a_failed_push_stops_further_attempts_in_that_call_and_is_retried(clock: list[float]) -> None:
    session = Session()
    alerts = Alerts(session)  # type: ignore[arg-type]
    alerts.target = ("https://ntfy.example", "t")
    only(alerts, "soil_silent", "probe_silent")
    for name in ("soil_silent", "probe_silent"):
        alerts.config["rules"][name]["for_minutes"] = 0
    both = {"supervision": {"at": clock[0], "signals": {
        "soil_silent": {"signal": "soil_silent", "state": "silent", "params": SAMPLE["soil_silent"]},
        "probe_silent": {"signal": "probe_silent", "state": "silent", "params": SAMPLE["probe_silent"]}}}}
    asyncio.run(alerts.check(both))
    assert session.posts == ["VGrow: Bodensensor meldet nichts"]  # one failed push, no second try in this call
    assert alerts.state["probe_silent"]["last_push"] is None and alerts.state["soil_silent"]["last_push"] is None
    assert alerts.state["probe_silent"]["active"]  # the page and Hank show it although the phone does not
    assert alerts.push_health()["failed_since"] == clock[0]
    clock[0] += 90
    asyncio.run(alerts.check(both))
    assert len(session.posts) == 2 and alerts.push_health()["failed_since"] == clock[0] - 90  # still failing
    session.status = 200
    clock[0] += 90
    asyncio.run(alerts.check(both))
    assert session.posts[2:] == ["VGrow: Bodensensor meldet nichts", "VGrow: Fühler meldet nichts"]  # retried
    health = alerts.push_health()
    assert health["failed_since"] is None and health["ok_at"] == clock[0] and health["target"] is True


def test_a_failed_push_after_a_pause_starts_a_new_run(clock: list[float]) -> None:
    """failed_since is the start of the current run of failed sends: a failure after a pause with no send at all starts
    a new one, so a blip hours ago neither shortens the next outage's 900 s nor makes its "Seit 2 Stunden" text."""
    session = Session()
    alerts = Alerts(session)  # type: ignore[arg-type]
    alerts.target = ("https://ntfy.example", "t")
    start = clock[0]
    asyncio.run(alerts.push("x", "y"))
    assert alerts.push_health()["failed_since"] == alerts.push_health()["failed_at"] == start
    clock[0] += 90  # the next poll retries: one run
    asyncio.run(alerts.push("x", "y"))
    assert alerts.push_health()["failed_since"] == start and alerts.push_health()["failed_at"] == clock[0]
    clock[0] += alerts_mod.PUSH_RUN_GAP_S + 1  # nothing tried meanwhile: nothing proves ntfy was down
    asyncio.run(alerts.push("x", "y"))
    assert alerts.push_health()["failed_since"] == clock[0]
    session.status = 200
    asyncio.run(alerts.push("x", "y"))
    assert alerts.push_health()["failed_since"] is None


# ---------- the rule file and the table ----------

def test_alerts_json_and_signals_agree() -> None:
    assert list(RULES)[:10] == OLD  # the old rules stay first and in order (tests/test_i18n.py reads the first three)
    assert sorted(NAMES) == sorted(supervision.BY_ID) and len(NAMES) == 25  # restarts since the review of 15.11.2026
    for name in NAMES:
        rule = RULES[name]
        for key in ("label", "label_en", "hint", "hint_en", "source"):
            assert isinstance(rule.get(key), str) and rule[key].strip(), (name, key)
        assert rule["metric"] == "supervision" and rule["signal"] == name, name
        assert rule["level"] in ("red", "yellow") and rule["priority"] in (2, 3, 4, 5), name
        assert type(rule["for_minutes"]) is int and rule["for_minutes"] >= 1, name
        assert rule["remind_hours"] in (6, 12, 24), name
        assert rule.get("expect") in (None, "always", "seen"), name
        assert (rule.get("driver") == "watchdog") == (name in supervision.WATCHDOG_SIGNALS), name
        assert priority_of(name) == rule["priority"], name
    assert [n for n in NAMES if RULES[n]["level"] == "red"] == [
        "poll_stalled", "supervision_failing", "supervision_silent", "tasks_dead", "watering_watchdog", "light_timer",
        "device_link", "probe_silent", "tank_sensor_silent", "soil_silent", "thirst_engine", "thirst_input",
        "watering_skips", "push_broken", "hank_silent"]
    assert {n: RULES[n].get("expect") for n in NAMES if RULES[n].get("expect")} == {
        "probe_silent": "always", "tank_sensor_silent": "seen", "builtin_silent": "always"}
    text = (REPO / "alerts.json").read_text(encoding="utf-8")
    assert EM_DASH not in text and EN_DASH not in text and "..." not in text
    for word in ("Eugen", "Lukas", "MOC", "Gaertnern", "2000451117"):  # tests/test_safety.py
        assert word not in text
    for word in ("gurke", "erdbeer", "orchid", "hibiskus", "luftentfeuchter", "luftreiniger", "cucumber", "monstera",
                 "yucca", "air conditioner"):  # the names of Hank's unknown cases (tests/test_hank_eval_offline.py)
        assert word not in text.lower()
    # every sentence key the readers can ask for exists in both languages
    assert supervision.check_catalog() == []
    for key in supervision.DE:
        assert key in i18n.DE and key in i18n.EN


def test_limits_match_their_code_constants() -> None:
    limit = {sig.id: sig.limit for sig in supervision.SIGNALS}
    assert limit["poll_stalled"] == box.WATER_STALE_SECONDS
    assert limit["supervision_failing"] == supervision.FAIL_POLLS * box.POLL_SECONDS
    assert limit["supervision_silent"] == 3 * supervision.WATCH_S == 3 * hank_core.TICK_SECONDS
    assert limit["watering_watchdog"] == watering.CONFIRM_SECONDS
    assert limit["light_timer"] == light_schedule.CONFIRM_SECONDS
    assert limit["hank_silent"] == 5 * hank_core.TICK_SECONDS
    assert limit["device_link"] == 2 * box.SHADOW_REFRESH_SECONDS
    assert limit["probe_silent"] == limit["builtin_silent"] == automation.FAILSAFE_SECONDS
    assert limit["tank_sensor_silent"] == 3 * box.WATER_STALE_SECONDS == automation.FAILSAFE_SECONDS
    assert limit["soil_silent"] == soil.STALE_S
    assert limit["thirst_engine"] == box.WATER_STALE_SECONDS
    assert limit["thirst_input"] == thirst.load_model()("measured_valid_s")
    assert limit["thirst_sensor_look"] == 3 * (soil.POLL_S + thirst.SENSOR_WATER_LOOK_S) == 1080
    assert limit["push_broken"] == 10 * box.POLL_SECONDS
    assert limit["ruler_silent"] == grow_plan.UNREADABLE_PUSH_AFTER_S + grow_plan.HEIGHT_EVERY_S
    assert limit["fan_stick_silent"] == 3 * fan_stick.REFRESH_S
    assert limit["store_silent"] == 3 * measurements.COLLECT_SECONDS
    assert limit["news_silent"] == 2 * news.STALE_AFTER.total_seconds()
    assert limit["soil_uncalibrated"] == limit["pest_watch_silent"] == DAY and limit["ollama_silent"] == 1800
    assert supervision.GRACE_S == supervision.GAP_S == box.WATER_STALE_SECONDS
    assert supervision.LIT_HOURS_MIN * HOUR == grow_plan.HEIGHT_EVERY_S
    assert light_view.DAY_REFRESH == 600  # the measured day's own limit inside store_silent
    for sig in supervision.SIGNALS:
        assert sig.basis.strip(), sig.id
        assert all(p in supervision.BY_ID and list(supervision.BY_ID).index(p) < list(supervision.BY_ID).index(sig.id)
                   for p in sig.parents), sig.id  # a parent is read before its child


def test_the_view_rows_carry_kind_level_and_signal() -> None:
    rows = {row["name"]: row for row in Alerts().view()}
    assert rows["temp_low"]["kind"] is None and rows["temp_low"]["level"] == "red" and rows["temp_low"]["signal"] is None
    assert (rows["soil_uncalibrated"]["kind"], rows["soil_uncalibrated"]["level"],
            rows["soil_uncalibrated"]["signal"]) == ("supervision", "yellow", "soil_uncalibrated")
    assert rows["poll_stalled"]["op"] is None and rows["poll_stalled"]["limit"] is None
    assert json.dumps(rows)  # plain data for the page and Hank
