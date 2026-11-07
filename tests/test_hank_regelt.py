"""Hank regelt (owner, 23.10.2026: "Instead of telling me, he should regulate it on autopilot. I cannot set Hank to
auto."): the switch in Hank's panel is the autopilot's mode, its blocker list, and what his warnings say he does.

Three parts:
- the binding: one state. Hank's switch sends the card's own action (autopilot_mode) and reads the card's own answer
  (/api/state autopilot.boxes[].hank); the mode the panel shows is the mode the autopilot holds, whichever side
  switched it, and the panel adds no mode, no store and no rule (vgrow/hankpilot.py, web/hank.js);
- the blocker list: the stage, USB 2, the lamp distance, the humidifier rule, each with its step, in the live
  situation of 23.10.2026 (stage only estimated, USB 2 unnamed, rule off, lamp unconfirmed) and with nothing left;
- the warning texts: for every problem key a device can act on, Hank says he regulates it, or what he would do in
  observe, or which step blocks him, in German and English.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

import test_web_i18n as web
from test_autopilot import EM_DASH, REPO, Rig, stage
from vgrow import automation as automation_mod
from vgrow import hankpilot
from vgrow.alerts import Alerts
from vgrow.automation import HumidifierAutomation
from vgrow.autopilot import Autopilot, AutopilotHub
from vgrow.plant import PlantLog
from vgrow.portguard import PortGuard
from vgrow.server import COOKIE, make_app

WEB = REPO / "web"


def hum_view(**over: Any) -> dict[str, Any]:
    """The humidifier rule's view as automation.view gives it, all clear unless told otherwise."""
    base = {"port": "usb2", "enabled": True, "band": [45, 60], "paused_until": None, "last_action": None,
            "recent": [], "stale": False, "missing": False, "needs_stage_confirmation": False, "estimate": None,
            "profile_problem": None, "port_hold": None, "port_device": "humidifier", "port_choices": [],
            "implausible": False}
    return {**base, **over}


def panel(rig: Rig, auto: dict[str, Any] | None, lang: str = "de") -> dict[str, Any]:
    view = rig.pilot.view(lang=lang)
    return hankpilot.panel(rig.pilot, view, auto, lang, now=rig.clock[0])


def ids(found: dict[str, Any]) -> list[str]:
    return [b["id"] for b in found["blockers"]]


# ---------- the binding: one state ----------

def _server_rig() -> tuple[Any, Autopilot, HumidifierAutomation, PlantLog]:
    from vgrow.demo import DemoBox

    box = DemoBox()
    log = PlantLog()
    guard = PortGuard("box-1")
    hum = HumidifierAutomation(box, None, log, guard=guard)
    pilot = Autopilot(box, box_id="box-1", plant_log=log, humidifier=hum, guard=guard)
    return box, pilot, hum, log


def test_the_switch_is_the_autopilots_mode_one_state_both_ways() -> None:
    box, pilot, hum, log = _server_rig()
    hub = AutopilotHub([pilot])

    async def go() -> None:
        client = TestClient(TestServer(make_app(box, Alerts(), "k", plant_log=log, automation=hum, autopilot=hub)))
        await client.start_server()
        client.session.cookie_jar.update_cookies({COOKIE: "k"})

        async def seen() -> tuple[str, str, str]:
            state = await (await client.get("/api/state", headers={"X-Lang": "de"})).json()
            card = state["autopilot"]["boxes"][0]
            return card["mode"], card["hank"]["mode"], pilot.state["mode"]

        try:
            assert await seen() == ("off", "off", "off")
            # what Hank's switch sends (web/hank.js sendPilot): the card's own action, nothing else
            for mode in ("observe", "auto", "off"):
                r = await client.post("/api/cmd", json={"action": "autopilot_mode", "mode": mode, "box": "box-1"})
                assert r.status == 200, await r.text()
                assert await seen() == (mode, mode, mode)
            # the other way: switched on the card (or anywhere), the panel shows it on the very next answer
            pilot.set_mode("observe")
            assert await seen() == ("observe", "observe", "observe")
            # the same safety rules: VGROW_AUTOPILOT=0 refuses switching on, for Hank's switch as for the card
            pilot.set_mode("off")
            pilot.forced_off = True
            r = await client.post("/api/cmd", json={"action": "autopilot_mode", "mode": "auto", "box": "box-1"})
            assert r.status != 200
            state = await (await client.get("/api/state")).json()
            found = state["autopilot"]["boxes"][0]["hank"]
            assert found["mode"] == "off" and found["forced_off"] is True and "forced" in ids(found)
        finally:
            await client.close()

    asyncio.run(go())


def test_the_panel_adds_no_mode_no_store_and_no_rule() -> None:
    src = (REPO / "vgrow" / "hankpilot.py").read_text(encoding="utf-8")
    # it never switches, never writes, never keeps a state of its own
    for banned in ("set_mode(", "set_device(", "confirm_lamp(", "set_enabled(", "atomic_write", "write_text",
                   ".open(", "STATE_DIR", "_save(", "await "):
        assert banned not in src, banned
    # Hank's package and this module never import each other (Hank only reads, tests/test_autopilot.py)
    assert not re.search(r"^from \.hank|^import vgrow\.hank|^from vgrow\.hank", src, re.M)
    for path in (REPO / "vgrow" / "hank").rglob("*.py"):
        assert "hankpilot" not in path.read_text(encoding="utf-8"), path.name
    js = (WEB / "hank.js").read_text(encoding="utf-8")
    # the switch sends the autopilot card's action to the cockpit's command route, and reads the app's own answer
    assert 'api("/api/cmd", { action: "autopilot_mode", mode: m, box: p.box })' in js
    assert 'document.addEventListener("vgrow:state"' in js and "box.hank" in js
    assert "/api/hank/pilot" not in js and "localStorage.setItem(CLIENT_KEY" in js
    assert js.count("localStorage.setItem(") == 1  # only the chat's client id, never a mode
    # Automatik takes two taps here too; Off and Observe one, as on the card
    assert 'if (m === "auto" && !pilotArmed())' in js
    # the same refusal as the card: only VGROW_AUTOPILOT=0 (forced_off) greys Observe and Auto, never a lock
    assert 'b.disabled = b.dataset.pilot !== "off" && !!p.forced_off;' in js and "p.locked_out" not in js
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'document.dispatchEvent(new CustomEvent("vgrow:state", { detail: s }))' in app
    assert 'apTwoTap(key, () => apSend(box, { action: "autopilot_mode", mode }))' in app  # the card stays as it is


def test_a_step_from_hanks_panel_is_the_first_of_its_two_taps_never_the_second() -> None:
    """pilotStep (web/app.js) arms the existing confirmation where it lives and sends nothing itself."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    body = app[app.index("function pilotStep("):]
    body = body[:body.index("\n}\n")]
    assert "send(" not in body and "api(" not in body and "apSend(" not in body
    assert "apArmNow(" in body and "stageArm.pilot = Date.now() + 4000" in body
    for step, hook in (("usb2", 'btn.dataset.apStep = "usb2"'), ("hum", 'tog.dataset.apStep = "hum"'),
                       ("lamp/ports", "btn.dataset.apStep = what;")):
        assert hook in app, step
    # every step id the server can hand out has its place on the page
    steps = {s for s in hankpilot.STEP.values() if s} | {"stage_none"}
    for step in steps:
        assert f'"{step}"' in app or f"{step}:" in app, step


NODE = shutil.which("node")
STEP_HARNESS = r"""
const calls = { send: [], fetch: [], render: 0, query: [] };
function fake() {
  return { className: "", textContent: "", dataset: {}, children: [], attrs: {}, onclick: null,
    append(...n) { this.children.push(...n); }, setAttribute(k, v) { this.attrs[k] = v; },
    scrollIntoView() {}, focus() {}, classList: { add() {}, remove() {} } };
}
const document = { createElement: () => fake(), querySelector: (sel) => { calls.query.push(sel); return fake(); } };
const CSS = { escape: (s) => s };
const REDUCED = { matches: true };
const stageArm = { pilot: 0 };
let stageBusy = false;
function setTimeout() {}
function fetch(...a) { calls.fetch.push(a); return Promise.resolve({}); }
function api(...a) { calls.fetch.push(a); return Promise.resolve({}); }
function send(body) { calls.send.push(body); }
function render() { calls.render += 1; }
function renderPilot() { calls.render += 1; }
function $() { return fake(); }
function t(key) { return key; }
function portName(p) { return p.toUpperCase(); }
function humState() { return ["pill", ""]; }
function humAction() { return ""; }
function humBand() { return ""; }  // the band line (vpd-regel): its texts are tested in test_vpd_rule.py
function humNeed() { return ""; }
function qty(v) { return String(v); }
function num(v) { return v; }
function again() { return "again"; }
%s
const a = %s;
let state = { automation: a, autopilot: { boxes: [{ id: "box-1", needs: {} }] } };
const out = {};
out.found = pilotStep("box-1", "usb2");
out.armed = apArmed("box-1|port|humidifier");
out.sentByStep = calls.send.length + calls.fetch.length;
const row = apHumRow(state.autopilot.boxes[0], a);
const all = [];
(function walk(n) { all.push(n); for (const c of n.children || []) if (typeof c === "object") walk(c); })(row);
const hum = all.find((n) => n.dataset && n.dataset.apStep === "usb2");
out.humClass = hum.className;
out.humPressed = hum.attrs["aria-pressed"];
hum.onclick();
out.sentAfterOne = calls.send.length;
const again2 = apHumRow(state.autopilot.boxes[0], a);
const all2 = [];
(function walk(n) { all2.push(n); for (const c of n.children || []) if (typeof c === "object") walk(c); })(again2);
all2.find((n) => n.dataset && n.dataset.apStep === "usb2").onclick();
out.sent = calls.send;
console.log(JSON.stringify(out));
"""


def _slice(source: str, start: str, end: str) -> str:
    i = source.index(start)
    return source[i:source.index(end, i)]


def run_step(auto: dict[str, Any]) -> dict[str, Any]:
    """pilotStep and apHumRow from web/app.js in node, with a small stand-in for the DOM: what Hank's step arms, what
    the USB 2 row then shows, and what is sent (only the owner's own taps at the control may send)."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    code = "\n".join((_slice(app, "const AP_PORT_DEVICES", "let apDrawn"),
                      _slice(app, "function apArmed(", "function apHead("),
                      _slice(app, "function apHumRow(", "function humState(")))
    run = subprocess.run([NODE or "node", "-"], input=STEP_HARNESS % (code, json.dumps(auto)), capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_after_the_plausibility_stop_the_usb2_step_arms_the_humidifier_again() -> None:
    """The implausible blocker's step "USB 2 neu benennen": the rule only resumes after the owner names the device
    again (automation.py _plausible), so the humidifier button must be a live choice there, armed by Hank's tap and
    confirmed by the owner's tap at the control, never pressed already."""
    out = run_step(hum_view(implausible=True))
    assert out["found"] is True and out["armed"] is True and out["sentByStep"] == 0
    assert "on" not in out["humClass"].split() and out["humPressed"] == "false"
    # Hank's tap was the first of the two: the owner's tap at the control is the second and names the humidifier
    # again; a further tap only arms anew
    assert out["sentAfterOne"] == 1
    assert out["sent"] == [{"action": "port_assign", "port": "usb2", "device": "humidifier", "box": "box-1"}]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_named_humidifier_is_not_armed_again_and_the_step_sends_nothing() -> None:
    out = run_step(hum_view())
    assert out["found"] is True and out["armed"] is False and out["sentByStep"] == 0
    assert "on" in out["humClass"].split() and out["humPressed"] == "true"
    out = run_step(hum_view(port_device=None))
    assert out["armed"] is True and out["sentByStep"] == 0


# ---------- the blocker list ----------

def test_the_live_situation_of_the_evening_lists_four_steps() -> None:
    """23.10.2026, evening: autopilot off, stage only estimated (early flower), USB 2 unnamed, the humidifier rule
    held by both, the lamp distance never confirmed while the light is below the preset."""
    rig = Rig(st=stage("early_flower", set_by="estimate"), mode="off")
    rig.box.light["level"] = 50  # the early flower preset is higher
    auto = hum_view(port_device=None, needs_stage_confirmation=True,
                    estimate={"key": "early_flower", "label": "Frühe Blüte (Stretch)", "set_by": "estimate"})
    found = panel(rig, auto)
    assert ids(found) == ["stage", "usb2", "lamp"]
    first = found["blockers"][0]
    assert first["text"] == "Phase nicht bestätigt (geschätzt: Frühe Blüte (Stretch))"
    assert (first["step"], first["label"], first["holds"]) == ("stage", "Phase bestätigen", "hält alles an")
    usb2 = found["blockers"][1]
    assert (usb2["step"], usb2["label"], usb2["holds"]) == ("usb2", "USB 2 benennen", "hält den Befeuchter an")
    lamp = found["blockers"][2]
    assert lamp["text"] == "Lampenabstand nicht bestätigt (mindestens 35 cm)" and lamp["step"] == "lamp"
    assert lamp["holds"] == "hält den Lichtanstieg an"
    # the rule switched off as well: its own step
    found = panel(rig, {**auto, "enabled": False})
    assert ids(found) == ["stage", "usb2", "hum_off", "lamp"]
    hum_off = found["blockers"][2]
    assert (hum_off["step"], hum_off["label"]) == ("hum", "Regel einschalten")
    assert found["regulates"] == "Hank regelt gerade nichts."
    en = panel(rig, {**auto, "enabled": False}, "en")
    assert [b["label"] for b in en["blockers"]] == ["Confirm the stage", "Name USB 2", "Switch the rule on",
                                                    "Confirm the distance"]


def test_nothing_in_the_way_says_what_hank_regulates_and_his_last_action() -> None:
    rig = Rig(lamp=True)  # Automatik, stage confirmed, lamp confirmed
    rig.box.set_climate(23.0, 66.0)  # too humid: the exhaust steps up
    rig.run(30)
    found = panel(rig, hum_view())
    assert found["blockers"] == [] and found["mode"] == "auto"
    # the circulation fan runs natural wind (the test box's default): the autopilot never touches it
    assert found["regulates"] == "Hank regelt jetzt: Befeuchter. Hank wartet bei: Licht und Abluft."
    last = found["last"]
    assert last["text"].startswith("Autopilot: Abluft 5 auf 6, weil") and re.fullmatch(r"\d\d:\d\d", last["when"])
    # a newer switch of the humidifier rule is the last action then
    newer = hum_view(recent=[{"time": rig.clock[0] + 5, "action": "humidifier_on", "value": 44.6}])
    assert panel(rig, newer)["last"]["text"] == "Befeuchter an bei 44,6 %"
    assert panel(rig, newer, "en")["last"]["text"] == "Humidifier on at 44.6%"
    # observing: the devices are watched, not switched; the humidifier rule switches in every mode
    rig.pilot.set_mode("observe")
    assert panel(rig, hum_view())["regulates"] == ("Hank regelt jetzt: Befeuchter. Hank beobachtet Licht und "
                                                   "Abluft und schaltet sie nicht.")


def test_a_device_on_manual_or_paused_is_not_said_to_be_regulated() -> None:
    rig = Rig(lamp=True)
    rig.pilot.set_device("cfan", level="manual")
    rig.pilot.manual("dfan", 4)
    found = panel(rig, hum_view(enabled=False))
    assert ids(found) == ["hum_off"]
    assert found["regulates"] == "Hank wartet bei: Licht."
    line = found["about"]["probe_humidity:high"]
    assert line["text"].startswith("Hank regelt das gerade nicht: du hast Abluft bis ") and line["step"] is None


def test_ports_changed_profile_and_other_holds() -> None:
    rig = Rig(lamp=True)
    view = rig.pilot.view(lang="de")
    view["needs"]["ports"] = {"confirmed": {}, "now": {}}
    found = hankpilot.panel(rig.pilot, view, hum_view(port_device="pump"), "de", now=rig.clock[0])
    assert ids(found) == ["ports", "usb2_other"]
    assert [b["step"] for b in found["blockers"]] == ["ports", "usb2"]
    # a hold of the autopilot that no step settles (here: stale readings) is said, with nothing to tap
    rig.box.sensors_age = 4000
    rig.run(10)
    found = panel(rig, hum_view())
    hold = next(b for b in found["blockers"] if b["id"] == "hold")
    assert hold["step"] is None and hold["text"] == "Wartet: Messwerte älter als 10 Minuten"
    # implausible stop of the humidifier: name USB 2 again
    found = panel(Rig(lamp=True, box_id="box-2"), hum_view(implausible=True))
    assert ids(found) == ["implausible"] and found["blockers"][0]["step"] == "usb2"


# ---------- the warning texts ----------

def test_the_owners_case_humidity_low_says_what_blocks_and_where_to_tap() -> None:
    rig = Rig(st=stage("early_flower", set_by="estimate"), mode="off")
    auto = hum_view(port_device=None, needs_stage_confirmation=True,
                    estimate={"key": "early_flower", "label": "Frühe Blüte (Stretch)", "set_by": "estimate"})
    line = panel(rig, auto)["about"]["probe_humidity:low"]
    assert line == {"text": "Hank regelt das noch nicht: Phase nicht bestätigt (geschätzt: Frühe Blüte (Stretch)).",
                    "step": "stage", "label": "Phase bestätigen"}
    en = panel(rig, auto, "en")["about"]["probe_humidity:low"]
    # the estimate's label in the page's language, from the autopilot's own view
    assert en["text"] == "Hank does not regulate this yet: Stage not confirmed (estimate: Early flower (stretch))."
    # the stage confirmed, USB 2 still unnamed: that is the step now
    rig.stage = stage("early_flower")
    line = panel(rig, hum_view(port_device=None))["about"]["probe_humidity:low"]
    assert line == {"text": "Hank regelt das noch nicht: Nicht gesagt, was an USB 2 hängt.", "step": "usb2",
                    "label": "USB 2 benennen"}
    # everything done: Hank regulates it, the humidifier switches on by its own rule
    line = panel(rig, hum_view())["about"]["probe_humidity:low"]
    assert line == {"text": "Hank regelt das: Befeuchter.", "step": None, "label": None}
    assert panel(rig, hum_view(), "en")["about"]["probe_humidity:low"]["text"] == "Hank regulates this: Humidifier."


def test_fan_light_and_temperature_lines_in_every_mode() -> None:
    rig = Rig(mode="off", lamp=True)
    off = panel(rig, hum_view())["about"]
    for key in ("probe_humidity:high", "probe_temp:high", "probe_temp:low", "probe_vpd:low", "light_percent:low"):
        assert off[key] == {"text": "Hank regelt das nicht: \u201eHank regelt\u201c steht auf Aus.", "step": "mode",
                            "label": "Hank regelt einschalten"}, key
    rig.pilot.set_mode("observe")
    obs = panel(rig, hum_view())["about"]
    assert obs["probe_temp:high"] == {"text": "Hank beobachtet nur: auf Automatik würde er das über Abluft regeln.",
                                      "step": "mode", "label": "Auf Automatik stellen"}
    assert obs["light_percent:low"]["text"] == "Hank beobachtet nur: auf Automatik würde er das über Licht regeln."
    rig.pilot.set_mode("auto")
    rig.run(5)
    auto = panel(rig, hum_view())["about"]
    assert auto["probe_temp:high"]["text"].startswith("Hank regelt das: Abluft (")  # with its own status line
    assert auto["probe_temp:high"]["step"] is None
    # the light ramp waits for the lamp distance: that blocks the light line only, never the fans
    fresh = Rig(box_id="box-2")  # a box of its own: box-1's state already has the confirmed distance
    fresh.box.light["level"] = 50
    lines = panel(fresh, hum_view())["about"]
    assert lines["light_percent:low"]["step"] == "lamp"
    assert lines["light_percent:low"]["text"] == ("Hank regelt das noch nicht: Lampenabstand nicht bestätigt "
                                                  "(mindestens 35 cm).")
    assert lines["probe_humidity:high"]["step"] is None
    en = panel(Rig(mode="off", box_id="box-3"), hum_view(), "en")["about"]["probe_temp:low"]
    assert en["text"] == "Hank does not regulate this: \u201cHank controls\u201d is set to Off."


def test_vpd_too_high_is_the_humidifiers_only_where_its_rule_has_the_vpd_floor(monkeypatch: Any) -> None:
    rig = Rig(lamp=True)
    monkeypatch.delattr(automation_mod, "humidity_floor", raising=False)
    assert "probe_vpd:high" not in panel(rig, hum_view())["about"]  # never claimed while nothing acts on it
    monkeypatch.setattr(automation_mod, "humidity_floor", lambda stage, snap: {}, raising=False)
    assert panel(rig, hum_view())["about"]["probe_vpd:high"]["text"] == "Hank regelt das: Befeuchter."


def test_the_device_table_follows_the_autopilots_own_votes() -> None:
    """KEY_DEVICES says which device acts on which problem; the exhaust's votes in vgrow/autopilot.py must still be
    there for every line that names the exhaust, and the light ramp for the light."""
    source = (REPO / "vgrow" / "autopilot.py").read_text(encoding="utf-8")
    votes = {"probe_humidity:high": '"hum_high"', "probe_temp:high": '"temp_high"', "probe_temp:low": '"temp_low"',
             "probe_vpd:low": '"vpd_low"'}
    for key, devices in hankpilot.BASE_KEY_DEVICES.items():
        if devices == ("dfan",):
            assert votes[key] in source, key
    assert hankpilot.BASE_KEY_DEVICES["light_percent:low"] == ("light",) and '"kind": "ramp"' in source
    assert hankpilot.BASE_KEY_DEVICES["probe_humidity:low"] == ("humidifier",)
    assert "value < lo" in (REPO / "vgrow" / "automation.py").read_text(encoding="utf-8")  # the rule's "too dry"


def test_hank_js_shows_the_line_under_notes_and_on_the_light() -> None:
    js = (WEB / "hank.js").read_text(encoding="utf-8")
    assert "pilotNote(row);" in js and 'el("span", "hk-light-p", doing)' in js
    assert 'go.onclick = () => openStep(a.step);' in js and 'go.onclick = () => openStep(b.step);' in js
    # the existing two-tap proposals stay untouched: the line adds no card and no command of its own
    note = js[js.index("function pilotNote("):js.index("function pilotArmed(")]
    assert "api(" not in note and "proposalCard" not in note and "runButton" not in note


# ---------- texts ----------

def test_texts_have_twins_no_em_dash_and_english_without_german() -> None:
    assert hankpilot.check() == []
    for key, text in hankpilot.EN.items():
        assert not web.GERMAN.search(text.replace("plant.json", "")), (key, text)
    for name in ("vgrow/hankpilot.py", "tests/test_hank_regelt.py", "web/hank.js", "web/hank-i18n.js",
                 "web/hank.css", "web/app.js", "web/style.css", "vgrow/server.py"):
        raw = (REPO / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw, name
        assert EM_DASH not in raw.decode("utf-8"), name
    texts = web._texts_file("hank-i18n.js", web.HANK_PREFIX)
    for key in ("hank.ctl.label", "hank.ctl.off", "hank.ctl.observe", "hank.ctl.auto", "hank.ctl.need",
                "hank.ctl.armAuto", "hank.ctl.blocked", "hank.ctl.last"):
        assert texts["de"][key] and texts["en"][key], key
    assert texts["de"]["hank.ctl.label"] == "Hank regelt" and texts["en"]["hank.ctl.label"] == "Hank controls"
    assert [texts["de"][f"hank.ctl.{m}"] for m in ("off", "observe", "auto")] == ["Aus", "Beobachten", "Automatik"]
    assert [texts["en"][f"hank.ctl.{m}"] for m in ("off", "observe", "auto")] == ["Off", "Observe", "Auto"]
    # the one line on what the mode needs: two taps for Automatik, observing first
    assert "zwei Tipps" in texts["de"]["hank.ctl.need"] and "24 h Beobachten" in texts["de"]["hank.ctl.need"]
    assert json.dumps(hankpilot.DE, ensure_ascii=False).count("{") == json.dumps(hankpilot.EN).count("{")


def test_nothing_is_written_into_the_repository() -> None:
    before = sorted(p.name for p in Path(REPO).iterdir())
    rig = Rig(lamp=True)
    panel(rig, hum_view())
    assert sorted(p.name for p in Path(REPO).iterdir()) == before
