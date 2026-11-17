"""Hank's garden, the idle screen (web/garden-logic.js, garden-art.js, garden.js): what he does comes from the box.

- decide() runs in node on small states: every trigger the owner named, and the order between them.
- The whole scene runs in node on a canvas that draws nothing: through every job for simulated minutes, so a job that leads
  nowhere, a loop that hangs or a pose that throws shows here. Whether it looks right is judged on rendered pictures.
- Beer and cigarette are for the owner's own box, the demo never shows them. This public version has no joint (owner, 07.11.2026).
- The garden only looks: it reads two answers and sends no command, also when the owner plays with it.
- It stays until its button or Esc closes it (owner, 06.11.2026: "if I move the mouse, it disappears"), and nothing keeps Hank stuck on one spot.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import test_web_i18n as web

WEB = Path(__file__).resolve().parent.parent / "web"
NODE = shutil.which("node")
FILES = ("garden-logic.js", "garden-art.js", "garden.js")
needs_node = pytest.mark.skipif(not NODE, reason="node is not installed")

STUBS = r"""
let seed = 20261106;
Math.random = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };  // the same day every run
const noop = () => {};
const ctx = new Proxy({}, { get(target, name) {
  if (name === "getImageData") return (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) });
  if (name in target) return target[name];
  return noop;
}, set(target, name, value) { target[name] = value; return true; } });
const created = [];
const fakeCanvas = () => {
  const el = { width: 300, height: 150, style: {}, on: {}, attrs: {}, getContext: () => ctx, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(name, fn) { this.on[name] = fn; }, append: noop, replaceChildren: noop,
    getBoundingClientRect() { return this.className === "garden-bar" ? { left: 400, top: 6, bottom: 46, width: 394, height: 40 } : { left: 0, top: 0, bottom: 600, width: 800, height: 600 }; }, focus: noop, blur: noop };
  created.push(el);
  return el;
};
const docOn = {};
const fire = (name, event) => { for (const fn of docOn[name] || []) fn({ preventDefault: noop, stopPropagation: noop, ...event }); };
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
global.window = { addEventListener: noop, matchMedia: () => ({ matches: false }), innerWidth: 800, innerHeight: 600, devicePixelRatio: 1 };
global.document = { createElement: fakeCanvas, addEventListener: (name, fn) => { (docOn[name] = docOn[name] || []).push(fn); }, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], body: { append: noop }, hidden: false, activeElement: null };
global.setInterval = () => 0;
global.requestAnimationFrame = () => 1;
global.cancelAnimationFrame = noop;
global.t = (key, params) => key + (params ? JSON.stringify(params) : "");
global.fmt = (value) => String(value);
global.when = () => "12:00";
global.api = async () => ({});
"""


def run(body: str) -> Any:
    files = ",".join(json.dumps(str(WEB / name)) for name in FILES)
    script = f"{STUBS}\nfor (const f of [{files}]) require(f);\nconst G = window.GardenLogic, H = window.HankGarden;\n{body}\n"
    out = subprocess.run([NODE or "node", "-"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


GOOD: dict[str, Any] = {
    "online": True, "mqtt": True, "sensors_age": 60, "error": None, "light": {"level": 60}, "cfan": {"step": 5}, "dfan": {"step": 5},
    "climate": {"probe_temp": 24.0, "probe_humidity": 60.0, "probe_vpd": 1.1, "water_state": "wet"},
    "ports": {"usb1": {"on": False}, "usb2": {"on": True}},
    "thirst": {"decision": {"kind": "WAIT", "reason": "not_due"}, "pump": {"port": "usb1", "flow": {"calibrated": True}}},
    "watering": {"active": None}, "automation": {"port": "usb2", "tank": {"warning": None}},
    "soil": {"fresh": True, "percent": 31.0, "relative": None, "state": None}, "alerts": [],
    "plant": {"checks": [{"metric": "probe_temp", "status": "ok"}, {"metric": "probe_humidity", "status": "ok"}, {"metric": "probe_vpd", "status": "ok"},
                         {"metric": "light_percent", "status": "low"}], "height": {"cm": 15}},
    "grow_plan": {"todo": [], "stage": {"key": "veg"}},
    "autopilot": {"boxes": [{"plant": "Purple Punch", "height_cm": 15.1}]},
}


def state(**changes: Any) -> dict[str, Any]:
    s = json.loads(json.dumps(GOOD))
    for path, value in changes.items():
        cur = s
        parts = path.split("__")
        for part in parts[:-1]:
            cur = cur[part]
        cur[parts[-1]] = value
    return s


def decide(s: dict[str, Any], hank: Any = None) -> dict[str, Any]:
    return run(f"console.log(JSON.stringify(G.decide({json.dumps(s)}, {json.dumps(hank)})));")


def checks(**status: str) -> list[dict[str, str]]:
    base = {"probe_temp": "ok", "probe_humidity": "ok", "probe_vpd": "ok", "light_percent": "ok", **status}
    return [{"metric": k, "status": v} for k, v in base.items()]


@needs_node
def test_nothing_to_do_means_free_time() -> None:
    found = decide(state())
    assert found["job"] == "chill" and not found["urgent"]


@needs_node
def test_a_light_held_below_the_card_is_not_a_job() -> None:
    assert decide(state(plant__checks=checks(light_percent="low")))["job"] == "chill"


@needs_node
def test_an_empty_tank_makes_him_say_the_pump_cannot_pump() -> None:
    found = decide(state(climate__water_state="dry"))
    assert found["job"] == "tank" and found["say"] == "garden.say.tank" and found["urgent"]


@needs_node
def test_the_pump_makes_him_water() -> None:
    found = decide(state(ports__usb1__on=True))
    assert found["job"] == "water" and found["say"] == "garden.say.pumpSoil" and found["params"]["soil"] == 31
    assert decide(state(ports__usb1__on=True, soil__fresh=False))["say"] == "garden.say.pump"


@needs_node
def test_a_pump_on_a_dry_tank_is_the_tank_first() -> None:
    found = decide(state(ports__usb1__on=True, climate__water_state="dry"))
    assert found["job"] == "tank" and found["say"] == "garden.say.dryRun"


@needs_node
def test_the_pump_port_comes_from_the_server() -> None:
    s = state(ports__usb2__on=True)
    s["thirst"]["pump"]["port"] = "usb2"
    assert decide(s)["job"] == "water"


@needs_node
def test_a_box_that_does_not_answer() -> None:
    assert decide(state(online=False))["job"] == "lost"
    assert decide(state(sensors_age=901))["job"] == "lost"
    assert decide(state(sensors_age=900))["job"] == "chill"
    assert decide(state(online=False, demo=True))["job"] == "chill"


@needs_node
@pytest.mark.parametrize(("decision", "job", "say"), [
    ({"kind": "PROPOSE", "reason": "hand", "dose": {"litres": 0.7}}, "thirsty", "garden.say.dose"),
    ({"kind": "ALERT", "reason": "ti_high"}, "thirsty", "garden.say.thirsty"),
    ({"kind": "BLOCKED", "reason": "blocked"}, "thirsty", "garden.say.thirsty"),
    ({"kind": "ALERT", "reason": "wet_72h"}, "soggy", "garden.say.soggy"),
    ({"kind": "ASK", "reason": "check", "check": {"how": "finger", "depth_cm": 5}}, "poke", "garden.say.checkDepth"),
    ({"kind": "WAIT", "reason": "hold"}, "chill", None),
])
def test_the_watering_decision(decision: dict[str, Any], job: str, say: str | None) -> None:
    found = decide(state(thirst__decision=decision))
    assert found["job"] == job and found["say"] == say


@needs_node
def test_thirsty_with_an_unmeasured_pump_says_why_it_cannot_pump() -> None:
    found = decide(state(thirst__decision={"kind": "ALERT", "reason": "ti_high"}, thirst__pump__flow__calibrated=False))
    assert found["job"] == "thirsty" and found["say"] == "garden.say.thirstyNoPump"


@needs_node
def test_a_calibrated_soil_reading_below_target_is_thirst() -> None:
    assert decide(state(soil__state="low"))["job"] == "thirsty"
    assert decide(state(soil__state="low", soil__fresh=False))["job"] == "chill"


@needs_node
@pytest.mark.parametrize(("status", "job"), [
    ({"probe_temp": "high"}, "hot"), ({"probe_temp": "low"}, "cold"), ({"probe_vpd": "high"}, "dry"), ({"probe_humidity": "low"}, "dry"),
    ({"probe_humidity": "high"}, "humid"), ({"probe_vpd": "low"}, "humid"),
])
def test_the_climate_off_target_is_soft_work(status: dict[str, str], job: str) -> None:
    found = decide(state(plant__checks=checks(**status)))
    assert found["job"] == job and found["soft"] and not found["urgent"]


@needs_node
def test_the_vpd_in_the_dark_is_no_job() -> None:
    """Seen 06.11.2026: 30 minutes after lights off the probe read VPD 0.81 and falling, with temperature and humidity on target."""
    for status in ("low", "high"):
        assert decide(state(light__level=0, plant__checks=checks(probe_vpd=status)))["job"] == "chill"
    assert decide(state(light__level=0, plant__checks=checks(probe_humidity="high")))["job"] == "humid"


@needs_node
def test_an_alarm_about_the_air_is_urgent() -> None:
    hot = decide(state(alerts=[{"name": "temp_high", "active": True}]))
    assert hot["job"] == "hot" and hot["urgent"]
    assert decide(state(alerts=[{"name": "temp_high", "active": False}]))["job"] == "chill"
    assert decide(state(alerts=[{"name": "tank_empty", "active": True}]))["job"] == "tank"


@needs_node
@pytest.mark.parametrize("name", ["usb_camera_offline", "camera_offline", "dark_period", "short_day", "something_new"])
def test_an_alarm_nobody_can_act_on_now_is_a_note_on_the_board_not_a_job(name: str) -> None:
    """Seen 06.11.2026: "close camera offline" kept him at the board, and the adapter is in the post."""
    s = state(alerts=[{"name": name, "label": "Label", "active": True}])
    assert decide(s)["job"] == "chill"
    assert run(f"console.log(JSON.stringify(G.notices({json.dumps(s)})));") == ["Label"]


@needs_node
def test_the_tank_and_the_air_are_not_notes() -> None:
    s = state(alerts=[{"name": n, "active": True} for n in ("temp_high", "temp_low", "humidity_high", "water_low", "tank_empty")])
    assert run(f"console.log(JSON.stringify(G.notices({json.dumps(s)})));") == []


@needs_node
def test_the_humidifier_running_out() -> None:
    assert decide(state(automation__tank__warning="low"))["job"] == "mist"


@needs_node
def test_free_time_fits_the_box_and_the_lamp() -> None:
    pools = run("""
      console.log(JSON.stringify({
        day: G.chillPool({}, true), night: G.chillPool({}, false), demoDay: G.chillPool({ demo: true }, true), demoNight: G.chillPool({ demo: true }, false),
      }));""")
    assert {"beer", "cig", "game", "juice"} <= set(pools["day"]) and "joint" not in pools["day"]
    assert {"nap", "game"} <= set(pools["night"]) and "joint" not in pools["night"] and "nap" not in pools["day"]
    for pool in (pools["demoDay"], pools["demoNight"]):
        assert not {"beer", "joint", "cig"} & set(pool)


SCENE = """
function play(s, hank, seconds, watch) {
  const scene = new H.Scene(fakeCanvas(), {});
  scene.resize(384, 216);
  scene.feed(s, hank);
  const seen = { poses: new Set(), props: new Set(), jobs: new Set(), said: new Set(), spots: [] };
  for (let i = 0; i < seconds * 15; i++) {
    scene.step(1 / 15);
    if (i % 3 === 0) scene.draw();
    seen.poses.add(scene.a.pose);
    if (scene.a.prop) seen.props.add(scene.a.prop);
    if (scene.saying) seen.said.add(scene.saying.text);
    if (scene.doing) seen.jobs.add(scene.doing);
    if (watch) watch(scene);
  }
  return { poses: [...seen.poses], props: [...seen.props], doing: [...seen.jobs], said: [...seen.said], gx: scene.a.gx, gy: scene.a.gy };
}
"""


def scene(s: dict[str, Any], seconds: int, hank: Any = None) -> dict[str, Any]:
    return run(f"{SCENE}\nconsole.log(JSON.stringify(play({json.dumps(s)}, {json.dumps(hank)}, {seconds})));")


@needs_node
def test_the_pump_sends_him_to_the_barrel_and_then_pours_on_the_plants() -> None:
    seen = scene(state(ports__usb1__on=True), 40)
    assert {"fill", "pour", "walk"} <= set(seen["poses"]) and "can" in seen["props"]
    assert seen["doing"] == ["garden.do.water"]
    assert any("garden.say.pumpSoil" in text for text in seen["said"])


@needs_node
def test_an_empty_tank_keeps_him_at_the_barrel_and_the_bubble_stays() -> None:
    result = run(f"""{SCENE}
      let always = true, ticks = 0;
      const out = play({json.dumps(state(climate__water_state="dry"))}, null, 60, (scene) => {{ ticks++; if (ticks > 15 && !scene.saying) always = false; }});
      out.always = always;
      console.log(JSON.stringify(out));""")
    assert result["always"] and {"lookin", "scratch"} <= set(result["poses"])


@needs_node
def test_free_time_runs_for_minutes_and_uses_the_fridge_the_chair_and_the_pc() -> None:
    seen = scene(state(), 1800)
    assert {"reach", "sip", "hold", "type"} <= set(seen["poses"])
    assert "beer" in seen["props"] and "joint" not in seen["props"]
    assert "garden.do.game" in seen["doing"]


@needs_node
def test_the_demo_never_smokes_or_drinks_beer() -> None:
    seen = scene(state(demo=True), 900)
    assert not {"beer", "joint", "cig"} & set(seen["props"])
    assert not {"garden.do.beer", "garden.do.joint", "garden.do.cig"} & set(seen["doing"])
    assert "juice" in seen["props"]


@needs_node
def test_lights_out_is_night_and_he_can_nap() -> None:
    result = run(f"""{SCENE}
      let darkest = 1;
      const out = play({json.dumps(state(light__level=0))}, null, 900, (scene) => {{ darkest = Math.min(darkest, scene.d); }});
      out.darkest = darkest;
      console.log(JSON.stringify(out));""")
    assert result["darkest"] == 0 and "nap" in result["poses"]


@needs_node
@pytest.mark.parametrize("change", [
    {"plant__checks": checks(probe_temp="high")}, {"plant__checks": checks(probe_temp="low")}, {"plant__checks": checks(probe_vpd="high")},
    {"plant__checks": checks(probe_humidity="high")}, {"thirst__decision": {"kind": "ALERT", "reason": "ti_high"}},
    {"thirst__decision": {"kind": "ALERT", "reason": "wet_72h"}}, {"thirst__decision": {"kind": "ASK", "reason": "check", "check": {"how": "finger", "depth_cm": 5}}},
    {"online": False}, {"alerts": [{"name": "camera_offline", "label": "Camera", "active": True}]}, {"automation__tank__warning": "low"},
    {"grow_plan__todo": [{"id": "probe", "title": "Hang the probe", "done": False}]},
])
def test_every_job_keeps_going_without_an_error(change: dict[str, Any]) -> None:
    seen = scene(state(**change), 300)
    assert seen["doing"], seen


@needs_node
def test_a_soft_job_takes_breaks_and_never_says_all_is_on_target_meanwhile() -> None:
    seen = scene(state(plant__checks=checks(probe_vpd="high")), 900)
    assert "spray" in seen["poses"] and "garden.do.dry" in seen["doing"] and len(seen["doing"]) > 1
    assert not any(key in text for text in seen["said"] for key in ("garden.say.beer", "garden.say.juice", "garden.say.joint"))
    assert any("garden.say.break" in text for text in seen["said"])


@needs_node
def test_a_todo_shows_up_between_the_free_time() -> None:
    seen = scene(state(grow_plan__todo=[{"id": "probe", "title": "Hang the probe", "done": False}]), 600)
    assert any("garden.say.todo" in text and "Hang the probe" in text for text in seen["said"]) and "point" in seen["poses"]


@needs_node
def test_a_value_on_the_edge_does_not_make_him_run_back_and_forth() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      const good = {json.dumps(state())}, off = {json.dumps(state(plant__checks=checks(probe_vpd="high")))};
      scene.feed(good, null);
      let switched = 0, job = scene.decision.job;
      for (let i = 0; i < 300; i++) {{
        scene.feed(i % 2 ? good : off);
        for (let k = 0; k < 75; k++) scene.step(1 / 15);
        if (scene.decision.job !== job) {{ switched++; job = scene.decision.job; }}
      }}
      console.log(JSON.stringify({{ switched }}));""")
    assert result["switched"] == 0


@needs_node
def test_pump_on_interrupts_at_once_and_pump_off_ends_it() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state())}, null);
      for (let i = 0; i < 150; i++) scene.step(1 / 15);
      scene.feed({json.dumps(state(ports__usb1__on=True))});
      const during = scene.decision.job;
      for (let i = 0; i < 150; i++) scene.step(1 / 15);
      scene.feed({json.dumps(state())});
      console.log(JSON.stringify({{ during, after: scene.decision.job }}));""")
    assert result == {"during": "water", "after": "chill"}


@needs_node
def test_a_camera_alarm_leaves_him_his_free_time_and_he_mentions_it_at_the_board() -> None:
    seen = scene(state(alerts=[{"name": "usb_camera_offline", "label": "Close camera offline", "active": True}]), 900)
    assert "garden.do.todo" in seen["doing"] and len(seen["doing"]) >= 3, seen["doing"]  # the board once in a while, and his day around it
    assert any("garden.say.notice" in text and "Close camera offline" in text for text in seen["said"])


@needs_node
def test_a_camera_alarm_does_not_fill_the_line_under_his_job() -> None:
    advice = "VPD too high: the air is too dry or too warm."
    rows = checks(probe_vpd="high")
    rows[2]["advice"] = advice
    dry = state(plant__checks=rows, alerts=[{"name": "usb_camera_offline", "label": "Close-up camera offline", "active": True}])
    light = lambda text, *fix: {"light": {"level": "red", "text": text, "fix": list(fix)}}
    cases = {"camera": light("Act now: Close-up camera offline.", "alert:usb_camera_offline"),
             "both": light("Act now: Close-up camera offline, Too hot.", "alert:temp_high", "alert:usb_camera_offline", "probe_temp:high"),
             "air": light("Act now: Too hot.", "alert:temp_high", "probe_temp:high"),
             "check": light("VPD is off.", "probe_vpd:high")}
    result = run(f"""{SCENE}
      const out = {{}}, cases = {json.dumps(cases)};
      for (const name of Object.keys(cases)) {{
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed({json.dumps(dry)}, cases[name]);
        out[name] = [scene.decision.job, scene.detail()];
      }}
      console.log(JSON.stringify(out));""")
    assert result == {"camera": ["dry", advice], "both": ["dry", advice], "air": ["dry", "Act now: Too hot."], "check": ["dry", "VPD is off."]}


def piloted(mode: str = "auto", log: list[dict[str, Any]] | None = None, **fan: Any) -> dict[str, Any]:
    device = {"id": "dfan", "name": "Exhaust fan", "level": "auto", "value": 8, "app_mode": None, "paused_until": None,
              "status": WATCH, **fan}
    return {"boxes": [{"plant": "Purple Punch", "height_cm": 15.1, "mode": mode, "devices": [device], "log": log or []}]}


WATCH = "watching: the temperature is 28.1 °C, above 28.0 °C. One step up once it stays above 28.5 °C for 10 min"


@needs_node
def test_on_auto_the_line_under_his_job_says_what_the_autopilot_does_never_the_cards_order() -> None:
    """Owner, 07.11.2026, on "Too warm: turn the exhaust fan up." under "Fanning himself" while the exhaust ran on
    Automatik: "it's on auto, he should do everything himself, not telling me to do it"."""
    order = "Too warm: turn the exhaust fan up."
    rows = checks(probe_temp="high")
    rows[0]["advice"] = order
    step = "Autopilot: Exhaust fan 8 to 9, because the probe temperature is 28.6 °C, above 28.0 °C."
    stepped = lambda ago: piloted(log=[{"kind": "event", "at": "AGO", "ago": 60, "text": "mist"},
                                       {"kind": "step", "device": "dfan", "ago": ago, "text": step}])
    cases = {"auto": piloted(), "stepped": stepped(600), "old step": stepped(3600), "observe": piloted("observe"),
             "manual": piloted(level="manual"), "paused": piloted(paused_until=1_794_040_000), "app": piloted(app_mode="auto")}
    camera = {"light": {"level": "red", "text": "Act now: Close-up camera offline.", "fix": ["alert:usb_camera_offline"]}}
    result = run(f"""{SCENE}
      const out = {{}}, cases = {json.dumps(cases)}, base = {json.dumps(state(plant__checks=rows))};
      for (const name of Object.keys(cases)) {{
        for (const e of cases[name].boxes[0].log) e.at = Date.now() / 1000 - e.ago;
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed({{ ...base, autopilot: cases[name] }}, {json.dumps(camera)});
        out[name] = [scene.decision.job, scene.detail()];
      }}
      console.log(JSON.stringify(out));""")
    line = 'garden.auto.device{"device":"Exhaust fan","status":"' + WATCH + '"}'
    assert result == {"auto": ["hot", line], "stepped": ["hot", step], "old step": ["hot", line], "observe": ["hot", order],
                      "manual": ["hot", order], "paused": ["hot", order], "app": ["hot", order]}


@needs_node
def test_a_long_line_under_his_job_is_never_cut_the_third_row_takes_the_hints_place() -> None:
    """The autopilot's status runs to three rows below Full HD, and "holding: VPD ... above its band, but less exhaust
    only once ..." at every width (07.11.2026). The wide window has two rows and the hint: a third row takes the hint's
    place, so the sentence is never cut off. Short lines keep the hint."""
    long = ("Exhaust fan: holding: VPD 1.28 kPa above its band, but less exhaust only once the probe shows at most "
            "27.0 °C (otherwise it gets too warm).")
    out = run(f"""{SCENE}
      const out = {{}}, art = window.GardenArt;
      for (const [name, text, w, h] of [["long", {json.dumps(long)}, 384, 216], ["narrow", {json.dumps(long)}, 340, 216],
                                        ["short", "Too warm.", 384, 216], ["phone", {json.dumps(long)}, 292, 380],
                                        ["phone short", "Too warm.", 292, 380]]) {{
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(w, h);
        scene.feed({json.dumps(state())}, null);
        scene.detail = () => text;
        const drawn = [], real = art.text;
        art.text = (c, s, x, y, col) => {{ drawn.push(s); return real(c, s, x, y, col); }};
        scene.drawDialog(ctx);
        art.text = real;
        const box = scene.dialog;
        out[name] = {{ drawn, rows: art.wrap(text, box.w - 56).length, room: box.h > 54 ? 3 : 2, end: text.split(" ").pop() }};
      }}
      console.log(JSON.stringify(out));""")
    assert out["long"]["rows"] == 3 and out["narrow"]["rows"] == 3  # Full HD and below: three rows, the window has two
    for name, case in out.items():
        assert any(s.endswith(case["end"]) for s in case["drawn"]), (name, case)  # the end of the sentence is drawn
        assert ("garden.back" in case["drawn"]) is (case["rows"] <= case["room"]), (name, case)


@needs_node
def test_on_auto_against_dry_air_the_running_humidifier_comes_first() -> None:
    rows = checks(probe_vpd="high")
    rows[2]["advice"] = "VPD too high: the air is too dry or too warm."
    mist = "holding: mist at the probe, humidity and VPD do not count, only the temperature"
    found = run(f"""{SCENE}
      const out = {{}};
      for (const on of [true, false]) {{
        const s = {json.dumps(state(plant__checks=rows, autopilot=piloted(status=mist)))};
        s.ports.usb2.on = on;
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed(s, null);
        out[on ? "on" : "off"] = [scene.decision.job, scene.detail()];
      }}
      console.log(JSON.stringify(out));""")
    fan = 'garden.auto.device{"device":"Exhaust fan","status":"' + mist + '"}'
    assert found == {"on": ["dry", "garden.auto.humidifier " + fan], "off": ["dry", fan]}


@needs_node
def test_an_alarm_that_stays_gets_breaks_and_his_bubble_keeps_saying_it() -> None:
    seen = scene(state(alerts=[{"name": "temp_high", "active": True}]), 1200)
    assert "garden.do.hot" in seen["doing"] and len(seen["doing"]) > 1
    assert any("garden.say.hot" in text for text in seen["said"])
    assert not any("garden.say.beer" in text or "garden.say.joint" in text for text in seen["said"])  # never "all on target" meanwhile


@needs_node
def test_as_long_as_the_pump_runs_he_waters() -> None:
    assert scene(state(ports__usb1__on=True), 1200)["doing"] == ["garden.do.water"]


# ---------- the owner plays ----------

PLAY = SCENE + """
const A = window.GardenArt;
function fresh(s) { const scene = new H.Scene(fakeCanvas(), {}); scene.resize(384, 216); scene.feed(s, null); scene.a.gx = 1; scene.a.gy = 8; return scene; }  // away from everything: his own box counts first
function spot(scene, gx, gy) { return [scene.ox + A.X(gx, gy), scene.oy + A.Y(gx, gy)]; }
function middle(scene, row) { const b = scene.boxOf(row); return [scene.ox + b[0] + b[2] / 2, scene.oy + b[1] + b[3] / 2]; }
function play2(scene, seconds, seen) {
  for (let i = 0; i < seconds * 15; i++) {
    scene.step(1 / 15);
    if (i % 5 === 0) scene.draw();
    if (seen) { seen.poses.add(scene.a.pose); if (scene.a.prop) seen.props.add(scene.a.prop); if (scene.saying) seen.said.add(scene.saying.text); }
  }
}
const log = () => ({ poses: new Set(), props: new Set(), said: new Set() });
const plain = (seen) => ({ poses: [...seen.poses], props: [...seen.props], said: [...seen.said] });
"""


def play(body: str, s: dict[str, Any] | None = None) -> Any:
    return run(f"{PLAY}\nconst S = {json.dumps(s or state())};\n{body}")


@needs_node
def test_a_click_on_the_grass_walks_him_there_and_after_a_while_he_goes_back_to_his_day() -> None:
    result = play("""
      const scene = fresh(S);
      play2(scene, 5);
      const hit = scene.click(...spot(scene, 7, 6.5));
      const told = scene.doing;
      play2(scene, 22);
      const there = [scene.a.gx, scene.a.gy], still = scene.order !== null;
      play2(scene, 60);
      console.log(JSON.stringify({ hit, told, there, still, after: scene.order }));""")
    assert result["hit"] == "ground" and result["told"] == "garden.do.walk" and result["still"]
    assert result["there"] == pytest.approx([7, 6.5], abs=0.05)
    assert result["after"] is None


@needs_node
def test_every_thing_can_be_clicked_and_sends_him_somewhere() -> None:
    result = play("""
      const out = {};
      for (const row of H.THINGS) {
        const scene = fresh(S);
        const hit = scene.click(...middle(scene, row));
        const seen = log();
        play2(scene, 45, seen);
        out[row.name] = { hit, said: plain(seen).said };
      }
      console.log(JSON.stringify(out));""")
    assert set(result) == {"hank", "board", "table", "chair", "lantern", "thermo", "barrel", "pc", "fridge", "plants", "fountain", "windmill", "tree"}
    for name, row in result.items():
        assert row["hit"] == name, (name, row)
        assert row["said"], name


@needs_node
def test_what_he_says_about_a_thing_is_the_real_value() -> None:
    s = state(light_schedule={"settings": {"on_time": "20:00", "off_time": "16:00"}}, dfan__step=7, cfan__step=3)
    result = play("""
      const said = (name) => { const scene = fresh(S); const row = H.THINGS.find((r) => r.name === name); scene.click(...middle(scene, row)); const seen = log(); play2(scene, 30, seen); return plain(seen).said.join("|"); };
      console.log(JSON.stringify({ thermo: said("thermo"), mill: said("windmill"), lamp: said("lantern"), plants: said("plants") }));""", s)
    assert "24" in result["thermo"] and "60" in result["thermo"] and "1.1" in result["thermo"]
    assert '"dfan":"7"' in result["mill"] and '"cfan":"3"' in result["mill"]
    assert '"level":"60"' in result["lamp"] and '"off":"16:00"' in result["lamp"]
    assert "Purple Punch" in result["plants"] and "15.1" in result["plants"] and '"soil":"31"' in result["plants"]


@needs_node
def test_the_sun_tells_the_lamp() -> None:
    result = play("""
      const scene = fresh(S);
      scene.step(1 / 15); scene.draw();
      const hit = scene.click(scene.sunAt[0], scene.sunAt[1]);
      console.log(JSON.stringify({ hit, key: scene.talkKey }));""")
    assert result == {"hit": "sun", "key": "garden.act.lampPlain"}


@needs_node
def test_the_fridge_gives_a_beer_on_his_own_box_and_never_in_the_demo() -> None:
    body = """
      const seen = log();
      for (const name of ["fridge", "fridge", "chair", "table", "pc"]) {
        const scene = fresh(S);
        scene.fridgeNext = seen.props.size ? 1 : 0;
        scene.click(...middle(scene, H.THINGS.find((r) => r.name === name)));
        play2(scene, 50, seen);
      }
      console.log(JSON.stringify(plain(seen)));"""
    own = play(body)
    assert {"beer", "cig"} <= set(own["props"]) and "joint" not in own["props"]
    demo = play(body, state(demo=True))
    assert not {"beer", "joint", "cig"} & set(demo["props"]) and "juice" in demo["props"]


@needs_node
def test_a_click_on_his_windows_does_nothing_and_the_sky_neither() -> None:
    result = play("""
      const scene = fresh(S);
      const before = scene.order;
      const hits = [scene.click(scene.stats.x + 4, scene.stats.y + 4), scene.click(scene.dialog.x + 30, scene.dialog.y + 10), scene.click(10, 150)];
      console.log(JSON.stringify({ hits, same: scene.order === before }));""")
    assert result == {"hits": [None, None, None], "same": True}


@needs_node
def test_a_pump_that_starts_takes_him_away_from_what_he_was_told() -> None:
    result = play(f"""
      const scene = fresh(S);
      scene.click(...spot(scene, 7, 6.5));
      play2(scene, 2);
      scene.feed({json.dumps(state(ports__usb1__on=True))});
      console.log(JSON.stringify({{ job: scene.decision.job, order: scene.order, doing: scene.doing }}));""")
    assert result == {"job": "water", "order": None, "doing": "garden.do.water"}


@needs_node
def test_what_he_was_told_runs_on_when_the_pump_stops_or_the_climate_drifts() -> None:
    result = play(f"""
      const wet = {json.dumps(state(ports__usb1__on=True))}, off = {json.dumps(state(plant__checks=checks(probe_vpd="high")))};
      const scene = fresh(wet);
      scene.click(...spot(scene, 7, 6.5));
      scene.feed(S);
      const afterPump = scene.order !== null;
      for (let i = 0; i < 40; i++) {{ scene.feed(off); scene.step(0.1); scene.pending && (scene.pending.at -= 5); }}
      console.log(JSON.stringify({{ afterPump, stillTold: scene.order !== null, job: scene.decision.job }}));""")
    assert result == {"afterPump": True, "stillTold": True, "job": "chill"}


@needs_node
def test_an_alarm_can_be_walked_away_from_and_he_comes_back_to_it() -> None:
    result = play(f"""
      const scene = fresh({json.dumps(state(climate__water_state="dry"))});
      play2(scene, 2);
      const said = scene.saying && scene.saying.text;
      scene.click(...spot(scene, 7, 6.5));
      scene.feed({json.dumps(state(climate__water_state="dry"))});
      const quiet = !scene.saying;
      play2(scene, 60);
      console.log(JSON.stringify({{ said, quiet, order: scene.order, back: scene.saying && scene.saying.text, job: scene.decision.job }}));""")
    assert "garden.say.tank" in result["said"] and result["quiet"] and result["order"] is None
    assert result["job"] == "tank" and "garden.say.tank" in result["back"]


@needs_node
def test_the_windmill_turns_slowly_and_still_shows_the_exhaust_step() -> None:
    result = play(f"""
      const turn = (s) => {{ const scene = fresh(s); const start = scene.rotor; for (let i = 0; i < 150; i++) scene.step(1 / 15); return (scene.rotor - start) / 10; }};
      console.log(JSON.stringify({{ high: turn({json.dumps(state(dfan__step=10))}), low: turn({json.dumps(state(dfan__step=2))}) }}));""")
    assert result["high"] < 1.0, result  # under one radian a second: a full turn takes more than six seconds
    assert result["high"] > result["low"] * 2


# ---------- it stays ----------

STICKY = """
const S = %s;
fire("vgrow:state", { detail: S });
"""


def sticky(body: str, s: dict[str, Any] | None = None) -> Any:
    return run(STICKY % json.dumps(s or state()) + body)


@needs_node
def test_neither_the_mouse_nor_a_click_nor_a_key_closes_it_only_esc_and_its_button() -> None:
    result = sticky("""
      H.open();
      const canvas = created.find((el) => el.on.pointerleave);
      const buttons = created.filter((el) => el.type === "button");
      const open0 = H.isOpen();
      for (let i = 0; i < 20; i++) fire("pointermove", { pointerType: "mouse", clientX: i * 50, clientY: i * 30 });
      const afterMove = H.isOpen();
      canvas.on.pointermove({ pointerType: "mouse", clientX: 400, clientY: 300 });
      canvas.on.click({ clientX: 400, clientY: 300 });
      const afterClick = H.isOpen();
      fire("keydown", { key: "a" });
      fire("keydown", { key: " " });
      const afterKey = H.isOpen();
      fire("pointerdown", {}); fire("wheel", {}); fire("scroll", {});
      const afterWheel = H.isOpen();
      fire("keydown", { key: "Escape" });
      const afterEsc = H.isOpen();
      H.open();
      const again = H.isOpen();
      buttons[1].on.click({});
      console.log(JSON.stringify({ open0, afterMove, afterClick, afterKey, afterWheel, afterEsc, again, afterButton: H.isOpen(), count: buttons.length }));""")
    assert result == {"open0": True, "afterMove": True, "afterClick": True, "afterKey": True, "afterWheel": True,
                      "afterEsc": False, "again": True, "afterButton": False, "count": 2}


@needs_node
def test_the_page_below_gets_no_key_while_it_is_open() -> None:
    result = sticky("""
      let stopped = 0;
      fire("keydown", { key: "a", stopPropagation: () => { stopped++; } });
      const closed = stopped;
      H.open();
      fire("keydown", { key: "a", stopPropagation: () => { stopped++; } });
      console.log(JSON.stringify({ closed, open: stopped }));""")
    assert result == {"closed": 0, "open": 1}


@needs_node
def test_the_pin_opens_the_page_in_the_garden() -> None:
    result = run("""
      store["vgrow.garden.pin"] = "1";
      const before = H.isOpen();
      fire("vgrow:state", { detail: %s });
      const after = H.isOpen();
      fire("keydown", { key: "Escape" });
      fire("vgrow:state", { detail: %s });
      console.log(JSON.stringify({ before, after, closed: H.isOpen(), stays: store["vgrow.garden.pin"] }));""" % (json.dumps(state()), json.dumps(state())))
    assert result == {"before": False, "after": True, "closed": False, "stays": "1"}


@needs_node
def test_the_pin_button_switches_and_says_so() -> None:
    result = sticky("""
      H.open();
      const pin = created.filter((el) => el.type === "button")[0];
      const first = pin.attrs["aria-pressed"];
      pin.on.click({});
      const second = [pin.attrs["aria-pressed"], store["vgrow.garden.pin"]];
      pin.on.click({});
      console.log(JSON.stringify({ first, second, third: [pin.attrs["aria-pressed"], store["vgrow.garden.pin"] || null], open: H.isOpen() }));""")
    assert result == {"first": "false", "second": ["true", "1"], "third": ["false", None], "open": True}


# ---------- nothing under the two buttons: they lie on top of the canvas ----------

BUBBLES = """
const A = window.GardenArt;
const WORDS = ["VPD 1.23. The air is too dry.", "Thermometer: 24.6 degrees and 58 percent. VPD 1.23. The air is too dry, so he sprays now.",
               "Three long lines of what he says about the plants, about the soil and about the tank, because the owner clicked on all of it and wants to read it."];
function placed(W, Ht, bar, head, words) {
  const scene = new H.Scene(fakeCanvas(), {});
  scene.resize(W, Ht);
  scene.bar = bar;
  scene.fx = { head };
  scene.say(words, 10);
  return { scene, box: scene.drawBubble(ctx) };
}
"""
BIG_SCREEN = "427, 240, { left: 313, bottom: 11 }"  # 2560 x 1440, the owner's picture of 07.11.2026


@needs_node
def test_his_bubble_is_never_under_the_buttons_nor_across_the_status_window() -> None:
    result = run(BUBBLES + """
      const SCREENS = [[427, 240, 313, 11], [384, 216, 302, 14], [340, 216, 237, 14], [456, 217, 357, 14], [412, 223, 311, 12], [293, 633, 157, 26], [292, 380, 156, 35], [400, 430, 250, 22]];
      const bad = [];
      let seen = 0;
      for (const [W, Ht, left, bottom] of SCREENS) for (const words of WORDS) {
        const { scene } = placed(W, Ht, { left, bottom }, [0, 0], words);
        for (let x = -150; x <= 150; x += 5) for (let y = -45; y <= 170; y += 3) {
          scene.fx = { head: [x, y] };
          const b = scene.drawBubble(ctx);
          seen++;
          const overBar = b.x + b.w > left && b.y < bottom;
          const overStats = !scene.tall && b.x < scene.stats.x + scene.stats.w && b.y < scene.stats.y + scene.stats.h;
          if (overBar || overStats || b.x < 0 || b.y < 0 || b.x + b.w > W) bad.push({ W, Ht, x, y, b });
        }
      }
      console.log(JSON.stringify({ seen, bad: bad.slice(0, 5) }));""")
    assert result["seen"] > 10000 and result["bad"] == []


@needs_node
def test_the_bubble_from_the_owners_picture_moves_beside_the_buttons_and_still_points_at_him() -> None:
    result = run(BUBBLES + f"""
      const {{ scene, box }} = placed({BIG_SCREEN}, [45, -3], WORDS[0]);
      console.log(JSON.stringify({{ box, tipX: scene.ox + 45, tipY: scene.oy - 3 - 4 }}));""")
    box = result["box"]
    assert box["x"] + box["w"] <= 313
    assert box["x"] + 4 <= result["tipX"] <= box["x"] + box["w"] - 6
    assert box["y"] + box["h"] + 5 == result["tipY"]  # above his head, not across it


@needs_node
def test_away_from_the_buttons_the_bubble_stays_centred_over_his_head() -> None:
    result = run(BUBBLES + f"""
      const {{ scene, box }} = placed({BIG_SCREEN}, [45, 60], WORDS[0]);
      console.log(JSON.stringify({{ box, tipX: scene.ox + 45 }}));""")
    assert abs(result["box"]["x"] + result["box"]["w"] / 2 - result["tipX"]) <= 1


@needs_node
def test_at_the_raised_bed_two_lines_fit_above_his_head() -> None:
    # the spot of the jobs "too dry" and "too damp", grid (3.55, 1.08): his hat starts 3 pixels above the island's origin
    result = run(BUBBLES + """
      const out = [];
      for (const [W, Ht] of [[384, 216], [427, 240], [480, 235]]) {
        const { scene, box } = placed(W, Ht, { left: W - 100, bottom: 16 }, [40, -3], WORDS[1].slice(0, 58));
        out.push({ lines: scene.saying.lines.length, tail: box.y + box.h + 5, hat: scene.oy - 3 });
      }
      console.log(JSON.stringify(out));""")
    for row in result:
        assert row["lines"] == 2 and row["tail"] <= row["hat"], row


@needs_node
def test_his_bobbing_head_does_not_make_the_bubble_jump() -> None:
    result = run(BUBBLES + f"""
      const {{ scene }} = placed({BIG_SCREEN}, [45, 0], WORDS[0]);
      const edge = 39 - scene.oy;  // with his head here, the bubble runs just along the bottom of the buttons
      const at = (y) => {{ scene.fx = {{ head: [45, y] }}; return scene.drawBubble(ctx).x; }};
      const bobbing = [-1, 0, 1, 0, -1, 0, 1, 0].map((d) => at(edge + d));
      console.log(JSON.stringify({{ bobbing, away: at(edge + 12) }}));""")
    assert len(set(result["bobbing"])) == 1
    assert result["away"] > result["bobbing"][0]


@needs_node
def test_the_name_under_the_mouse_is_not_under_the_buttons_either() -> None:
    result = run(BUBBLES + f"""
      const {{ scene }} = placed({BIG_SCREEN}, [0, 0], WORDS[0]);
      const boxes = [], real = A.windowBox;
      A.windowBox = (c, x, y, w, h) => {{ boxes.push({{ x, y, w, h }}); return real(c, x, y, w, h); }};
      scene.hover = {{ label: "garden.thing.lamp", x: 401, y: 20 }};
      scene.drawHover(ctx);
      console.log(JSON.stringify(boxes));""")
    assert len(result) == 1 and result[0]["y"] >= 11


@needs_node
def test_the_garden_measures_its_two_buttons_with_their_outline() -> None:
    result = sticky("""
      let scene = null;
      const resize = H.Scene.prototype.resize;
      H.Scene.prototype.resize = function (w, h) { scene = this; return resize.call(this, w, h); };
      H.open();
      console.log(JSON.stringify({ W: scene.W, H: scene.H, bar: scene.bar }));""")
    assert result == {"W": 400, "H": 300, "bar": {"left": 199, "bottom": 24}}  # 800 x 600 CSS pixels at two per scene pixel


# ---------- his helper robot: gets every signal, runs to Hank with a new one, otherwise keeps him company ----------

# Rows as the supervision build publishes them (vgrow/alerts.py rows(), its alerts.json): a silent soil sensor is red, a soil
# sensor not yet set to the soil is yellow, and today's climate rows carry no level at all.
SILENT = {"name": "soil_silent", "label": "Soil sensor reports nothing", "metric": "supervision", "active": True, "kind": "supervision",
          "level": "red", "signal": "soil_silent"}
UNSET = {"name": "soil_uncalibrated", "label": "Soil sensor not set", "metric": "supervision", "active": True, "kind": "supervision",
         "level": "yellow", "signal": "soil_uncalibrated"}
WARM = {"name": "temp_high", "label": "Too warm (probe)", "metric": "probe_temp", "active": True}


@needs_node
def test_the_robot_reads_every_signal_in_one_place() -> None:
    cases = {
        "calm": [state()], "nothing": [{}], "odd": [{"alerts": None, "light": {}}], "off rows": [state(alerts=[{**SILENT, "active": False}])],
        "alert": [state(alerts=[UNSET, SILENT, WARM])], "twice": [state(alerts=[SILENT, dict(SILENT)])], "bare": [state(alerts=[{"active": True}])],
        "night": [state(light__level=0)], "night alert": [state(light__level=0, alerts=[SILENT])],
        "offline": [state(), 100_000, 88_000, True], "offline blip": [state(), 100_000, 97_000, True],
        "stale": [state(), 100_000, 69_000, False], "fresh": [state(), 100_000, 80_000, False], "lost and alert": [state(alerts=[SILENT]), 100_000, 50_000],
    }
    out = run(f"const c = {json.dumps(cases)}, o = {{}}; for (const k of Object.keys(c)) o[k] = G.buddy(...c[k]); console.log(JSON.stringify(o));")
    calm = {"status": "calm", "items": []}
    for name in ("calm", "nothing", "odd", "off rows", "offline blip", "fresh"):
        assert out[name] == calm, (name, out[name])
    assert out["alert"] == {"status": "alert", "items": [
        {"id": "alert:soil_uncalibrated", "text": "Soil sensor not set", "level": "yellow", "key": "garden.robot.tell"},
        {"id": "alert:soil_silent", "text": "Soil sensor reports nothing", "level": "red", "key": "garden.robot.tell"},
        {"id": "alert:temp_high", "text": "Too warm (probe)", "level": "red", "key": "garden.robot.tell"}]}  # no level means red
    assert len(out["twice"]["items"]) == 1
    assert out["bare"]["items"] == [{"id": "alert:?", "text": None, "level": "red", "key": "garden.robot.tellPlain"}]
    assert out["night"] == {"status": "night", "items": []} and out["night alert"]["status"] == "alert"
    link = {"id": "link", "text": None, "level": "red", "key": "garden.robot.link"}
    assert out["offline"] == {"status": "alert", "items": [link]} and out["stale"] == {"status": "alert", "items": [link]}
    assert [i["id"] for i in out["lost and alert"]["items"]] == ["link", "alert:soil_silent"]


@needs_node
def test_the_page_tells_the_robot_when_it_loses_the_server() -> None:
    result = run("""
      fire("vgrow:state", { detail: %s });
      const before = H.link();
      fire("vgrow:offline", {});
      const lost = H.link();
      fire("vgrow:state", { detail: %s });
      console.log(JSON.stringify({ before, lost, back: H.link() }));""" % (json.dumps(state()), json.dumps(state())))
    assert result["before"]["offline"] is False and result["lost"]["offline"] is True and result["back"]["offline"] is False
    assert result["before"]["at"] and result["before"]["now"] >= result["before"]["at"]


@needs_node
def test_a_lost_server_sends_him_to_hank_with_its_own_line() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state())}, null);
      scene.link = {{ now: 100000, at: 60000, offline: true }};
      let said = null;
      for (let i = 0; i < 15 * 20 && !said; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); said = scene.robot.saying && scene.robot.saying.text; }}
      console.log(JSON.stringify({{ said }}));""")
    assert result["said"].startswith("garden.robot.link")


@needs_node
def test_a_new_problem_sends_him_running_to_hank_from_every_spot_and_hank_answers() -> None:
    result = run(f"""{SCENE}
      const out = {{}};
      for (const name of Object.keys(H.NODES)) {{
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed({json.dumps(state())}, null);
        for (let i = 0; i < 30; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); }}
        const r = scene.robot, decision = scene.decision;
        r.gx = H.NODES[name][0]; r.gy = H.NODES[name][1]; r.path = []; r.dest = null;
        scene.feed({json.dumps(state(alerts=[SILENT]))});
        let told = null, reply = null, said = "", line = "", fastest = 0;
        for (let i = 0; i < 15 * 30 && !reply; i++) {{
          const was = [r.gx, r.gy];
          scene.step(1 / 15);
          if (i % 3 === 0) scene.draw();
          if (!told) fastest = Math.max(fastest, Math.hypot(r.gx - was[0], r.gy - was[1]));
          if (!told && r.saying && r.saying.kind === "tell") {{
            told = {{ seconds: (i + 1) / 15, gap: Math.hypot(r.gx - scene.a.gx, r.gy - scene.a.gy), text: r.saying.text, bulb: r.look().bulb }};
            said = scene.robotLine();
          }}
          reply = scene.robotReply();
          if (reply) line = scene.robotLine();
        }}
        out[name] = {{ told, reply: reply && reply.text, said, line, fastest, sameDecision: scene.decision.job === decision.job }};
      }}
      console.log(JSON.stringify(out));""")
    hank_per_frame = 1.5 * 0.65 / 15  # WALK_SPEED tiles per world second, PACE, 15 frames a second
    for name, row in result.items():
        told = row["told"]
        assert told and told["seconds"] <= 12 and told["gap"] < 1.3, (name, row)
        assert "garden.robot.tell" in told["text"] and "Soil sensor reports nothing" in told["text"] and told["bulb"] == "alert", (name, row)
        assert "Soil sensor reports nothing" in row["said"], (name, row)  # the screen reader's line has his words, then Hank's answer
        assert row["reply"] == "garden.robot.thanks" and "Hank: garden.robot.thanks" in row["line"], (name, row)
        assert row["sameDecision"], name
        if name not in ("hub", "deck", "thermo"):  # from far away he runs, faster than Hank walks
            assert row["fastest"] > hank_per_frame * 1.5, (name, row)


@needs_node
def test_he_tells_once_then_again_only_after_a_long_while_one_trip_per_change_and_all_clear_once() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(alerts=[SILENT]))}, null);
      const r = scene.robot, starts = [];
      let was = null, bulb = null;
      for (let i = 0; i < 15 * 1520; i++) {{
        if (i === 15 * 1300) scene.feed({json.dumps(state(alerts=[SILENT, WARM]))});
        if (i === 15 * 1500) {{ bulb = r.look().bulb; scene.feed({json.dumps(state())}); }}
        scene.step(1 / 15);
        if (i % 3 === 0) scene.draw();
        const said = r.saying;
        if (said && said !== was) starts.push([said.kind, Math.round(scene.time)]);
        was = said;
      }}
      console.log(JSON.stringify({{ starts, trips: r.trips, bulb, after: r.look().bulb }}));""")
    kinds = [kind for kind, _ in result["starts"] if kind != "joke"]
    assert kinds == ["tell", "remind", "tell", "clear"], result
    times = [at for kind, at in result["starts"] if kind in ("tell", "remind")]
    assert all(b - a >= 300 for a, b in zip(times, times[1:])), times  # never every minute: a reminder after eight world minutes
    assert result["trips"] == 2 and result["bulb"] == "alert" and result["after"] is None


@needs_node
@pytest.mark.parametrize("change", [{}, {"alerts": [SILENT]}, {"alerts": [WARM]}, {"plant__checks": checks(probe_vpd="high")},
                                    {"light__level": 0, "alerts": [SILENT]}, {"ports__usb1__on": True}])
def test_the_robot_changes_nothing_of_hanks_own_day(change: dict[str, Any]) -> None:
    """Same seed, same state, with and without the robot: Hank's jobs, poses, ways and words stay the same to the frame."""
    result = run(f"""{SCENE}
      const trace = (robot) => {{
        seed = 20261106;
        const scene = new H.Scene(fakeCanvas(), {{ robot }});
        scene.resize(384, 216);
        scene.feed({json.dumps(state(**change))}, null);
        const out = [];
        for (let i = 0; i < 15 * 600; i++) {{
          scene.step(1 / 15);
          if (i % 3 === 0) scene.draw();
          if (i % 10 === 0) out.push([scene.decision.job, scene.doing, scene.a.pose, scene.a.prop, +scene.a.gx.toFixed(4), +scene.a.gy.toFixed(4),
            scene.saying && scene.saying.text, scene.queue.length, scene.mode]);
        }}
        return {{ out, trips: scene.robot ? scene.robot.trips : null }};
      }};
      const a = trace(true), b = trace(false);
      const first = a.out.findIndex((row, i) => JSON.stringify(row) !== JSON.stringify(b.out[i]));
      console.log(JSON.stringify({{ first, a: a.out[first], b: b.out[first], trips: a.trips, without: b.trips }}));""")
    assert result["first"] == -1, result
    assert result["without"] is None and result["trips"] == (1 if change.get("alerts") else 0)


@needs_node
def test_lamp_off_he_sleeps_on_his_charger_and_a_problem_wakes_him_at_once() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(light__level=0))}, null);
      const r = scene.robot;
      for (let i = 0; i < 15 * 40; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); }}
      const asleep = {{ mood: r.mood, status: r.status, at: [r.gx, r.gy], zzz: r.zAt > 0, jokes: Boolean(r.saying), battery: Boolean(r.look().battery && r.fx.battery) }};
      scene.feed({json.dumps(state(light__level=0, alerts=[SILENT]))});
      scene.step(1 / 15);
      scene.draw();
      const woke = {{ mood: r.mood, trip: r.trip, bulb: r.look().bulb }};
      scene.feed({json.dumps(state(light__level=0))});
      for (let i = 0; i < 15 * 60; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); }}
      console.log(JSON.stringify({{ asleep, woke, again: r.mood }}));""")
    assert result["asleep"] == {"mood": "sleep", "status": "night", "at": [5.7, 7.15], "zzz": True, "jokes": False, "battery": True}
    assert result["woke"] == {"mood": "alert", "trip": True, "bulb": "alert"}
    assert result["again"] == "sleep"


@needs_node
def test_while_hank_naps_and_all_is_calm_he_charges_and_comes_back_when_hank_wakes() -> None:
    """The owner, 16.11.2026: nothing going on and Hank taking a nap, he goes to a charging station and charges, a battery over
    his head. By day the nap comes from a click on the armchair (the demo's chair is a nap)."""
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(demo=True))}, null);
      const r = scene.robot, frame = () => {{ scene.step(1 / 15); scene.draw(); }};
      for (let i = 0; i < 15 * 5; i++) frame();
      scene.command(scene.orderFor("chair"));
      let i = 0;
      for (; i < 15 * 40 && !r.docked(); i++) frame();
      const arrived = {{ seconds: Math.round(i / 15), at: [r.gx, r.gy], doing: scene.doing }};
      const first = r.charge, looks = [];
      let dropped = 0, was = r.charge;
      for (let k = 0; k < 15 * 20; k++) {{
        frame();
        if (r.charge < was) dropped++;
        was = r.charge;
        if (k % 15 === 0) looks.push([r.mood, Boolean(r.look().battery && r.look().battery.charging), Boolean(r.fx && r.fx.battery), scene.dockLamp ? "lamp" : null]);
      }}
      const charged = +(r.charge - first).toFixed(3);
      for (let k = 0; k < 15 * 240 && scene.doing === "garden.do.nap"; k++) frame();
      let left = 0;  // Hank first walks to his next thing; the robot comes over once he is there
      for (; left < 15 * 30 && r.docked(); left++) frame();
      for (let k = 0; k < 15 * 2; k++) frame();
      console.log(JSON.stringify({{ arrived, charged, dropped, looks: [...new Set(looks.map((l) => JSON.stringify(l)))],
        after: {{ docked: r.docked(), battery: r.look().battery, doing: scene.doing, near: r.near() || r.path.length > 0, left: Math.round(left / 15) }} }}));""")
    assert result["arrived"]["at"] == [5.7, 7.15] and result["arrived"]["seconds"] <= 15 and result["arrived"]["doing"] == "garden.do.nap", result
    assert abs(result["charged"] - 20 * 0.65 / 90) < 0.005 and result["dropped"] == 0, result  # 20 s at PACE 0.65, 90 world s for a full battery
    assert result["looks"] == [json.dumps(["charge", True, True, "lamp"], separators=(",", ":"))], result
    after = result["after"]
    assert after["docked"] is False and after["battery"] is None and after["doing"] != "garden.do.nap", after
    assert after["near"] and after["left"] <= 20, after  # back with Hank, or on his way


@needs_node
def test_full_he_stays_on_the_pad_awake_and_the_battery_stops_blinking() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(demo=True))}, null);
      const r = scene.robot;
      scene.command(scene.orderFor("chair"));
      for (let i = 0; i < 15 * 30 && !r.docked(); i++) {{ scene.step(1 / 15); scene.draw(); }}
      r.charge = 0.995;
      for (let i = 0; i < 15 * 2; i++) {{ scene.step(1 / 15); scene.draw(); }}
      console.log(JSON.stringify({{ docked: r.docked(), charge: r.charge, mood: r.mood, battery: r.look().battery, lamp: scene.dockLamp.on }}));""")
    assert result == {"docked": True, "charge": 1, "mood": "chill", "battery": {"level": 1, "charging": False}, "lamp": True}


@needs_node
def test_a_problem_that_stays_keeps_him_with_hank_through_the_nap() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(demo=True, alerts=[SILENT]))}, null);
      const r = scene.robot;
      for (let i = 0; i < 15 * 20; i++) {{ scene.step(1 / 15); scene.draw(); }}
      scene.command(scene.orderFor("chair"));
      let docked = 0;
      for (let i = 0; i < 15 * 60; i++) {{ scene.step(1 / 15); scene.draw(); if (r.docked()) docked++; }}
      console.log(JSON.stringify({{ docked, near: r.near(), doing: scene.doing, battery: r.look().battery }}));""")
    assert result == {"docked": 0, "near": True, "doing": "garden.do.nap", "battery": None}


@needs_node
def test_his_battery_runs_down_while_he_is_about_and_never_below_its_floor() -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(demo=True))}, null);
      const r = scene.robot, start = r.charge;
      let shown = 0, low = 1;
      for (let i = 0; i < 15 * 3000; i++) {{
        scene.step(1 / 15);
        if (i % 3 === 0) scene.draw();
        if (r.look().battery && scene.doing !== "garden.do.nap") shown++;
        low = Math.min(low, r.charge);
      }}
      console.log(JSON.stringify({{ start, end: r.charge, low, shown }}));""")
    assert result["end"] < result["start"] and result["low"] >= 0.15 and result["shown"] == 0, result


@needs_node
def test_from_every_spot_at_night_he_finds_his_way_onto_the_charger() -> None:
    result = run(f"""{SCENE}
      const out = {{}};
      for (const name of Object.keys(H.NODES)) {{
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed({json.dumps(state(light__level=0))}, null);
        scene.step(1 / 15);
        const r = scene.robot;
        r.gx = H.NODES[name][0]; r.gy = H.NODES[name][1]; r.path = []; r.dest = null; r.planAt = -Infinity;
        let i = 0;
        for (; i < 15 * 30 && !r.docked(); i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); }}
        out[name] = r.docked() ? Math.round(i / 15) : null;
      }}
      console.log(JSON.stringify(out));""")
    assert all(seconds is not None and seconds <= 12 for seconds in result.values()), result


@needs_node
@pytest.mark.parametrize(("change", "hank", "jokes"), [
    ({"demo": True}, None, True), ({}, None, True), ({"demo": True, "alerts": [SILENT]}, None, False),
    ({"demo": True}, {"light": {"level": "red"}}, False), ({"demo": True, "climate__water_state": "dry"}, None, False),
    ({"demo": True, "light__level": 0}, None, False),
])
def test_jokes_only_when_all_is_calm_and_never_on_top_of_hanks_line(change: dict[str, Any], hank: Any, jokes: bool) -> None:
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(**change))}, {json.dumps(hank)});
      const r = scene.robot, told = new Set();
      let stacked = 0, was = null;
      for (let i = 0; i < 15 * 1500; i++) {{
        scene.step(1 / 15);
        if (i % 3 === 0) scene.draw();
        const joke = r.saying && r.saying.kind === "joke";
        if (joke && r.saying !== was) told.add(r.saying.text);
        if (joke && scene.saying) stacked++;
        was = r.saying;
      }}
      console.log(JSON.stringify({{ told: [...told], stacked }}));""")
    assert bool(result["told"]) is jokes, result
    assert all(text.startswith("garden.joke.") for text in result["told"]) and result["stacked"] == 0


@needs_node
def test_he_never_joins_hanks_beer_or_smoke_and_never_holds_a_thing() -> None:
    result = run(f"""{SCENE}
      const A = window.GardenArt, real = A.robot, keys = new Set();
      A.robot = (c, x, y, o) => {{ for (const k of Object.keys(o)) keys.add(k); return real(c, x, y, o); }};
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state())}, null);
      const r = scene.robot, props = ["beer", "cig", "joint"];
      let held = 0, joined = 0, near = 0, company = 0;
      for (let i = 0; i < 15 * 1800; i++) {{
        scene.step(1 / 15);
        if (i % 3 === 0) scene.draw();
        if (props.includes(scene.a.prop)) {{
          held++;
          if (r.company() !== "own") joined++;
          const hankStays = !(scene.a.step && scene.a.step.go);  // Hank walking past him with his bottle is no company
          if (hankStays && !r.path.length && !r.trip && Math.hypot(r.gx - scene.a.gx, r.gy - scene.a.gy) < 1.6) near++;
        }} else if (r.company() !== "own" && !r.path.length && Math.hypot(r.gx - scene.a.gx, r.gy - scene.a.gy) < 1.3) company++;
      }}
      console.log(JSON.stringify({{ held, joined, near, company, keys: [...keys].sort() }}));""")
    assert result["held"] > 1000 and result["joined"] == 0 and result["near"] == 0, result
    assert result["company"] > 1000  # the rest of the day he is beside Hank
    assert result["keys"] == ["battery", "bulb", "flip", "mood", "t", "view", "walk"]  # no prop, ever


@needs_node
def test_reduced_motion_no_walking_no_particles_and_his_bubble_still_comes() -> None:
    result = run(f"""{SCENE}
      const A = window.GardenArt, real = A.robot;
      let walked = 0;
      A.robot = (c, x, y, o) => {{ if (o.walk !== null) walked++; return real(c, x, y, o); }};
      const scene = new H.Scene(fakeCanvas(), {{ still: true }});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(demo=True))}, null);
      const r = scene.robot;
      let parts = 0, told = null;
      for (let i = 0; i < 120; i++) {{ scene.step(1); scene.draw(); parts = Math.max(parts, scene.parts.length); }}
      scene.feed({json.dumps(state(demo=True, alerts=[SILENT]))});
      for (let i = 0; i < 10 && !told; i++) {{
        scene.step(1); scene.draw();
        if (r.saying) told = {{ text: r.saying.text, gap: Math.hypot(r.gx - scene.a.gx, r.gy - scene.a.gy) }};
      }}
      scene.feed({json.dumps(state(demo=True, light__level=0))});
      for (let i = 0; i < 60; i++) {{ scene.step(1); scene.draw(); parts = Math.max(parts, scene.parts.length); }}
      console.log(JSON.stringify({{ walked, parts, told, night: r.mood }}));""")
    assert result["walked"] == 0 and result["parts"] == 0, result
    assert result["told"] and result["told"]["gap"] < 1.3 and "Soil sensor reports nothing" in result["told"]["text"]
    assert result["night"] == "sleep"


@needs_node
@pytest.mark.parametrize("where", ["art", "logic", "step"])
def test_a_robot_that_throws_never_stops_the_garden(where: str) -> None:
    result = run(f"""{SCENE}
      const boom = () => {{ throw new Error("robot"); }};
      if ({json.dumps(where)} === "art") window.GardenArt.robot = boom;
      if ({json.dumps(where)} === "logic") G.buddy = boom;
      const scene = new H.Scene(fakeCanvas(), {{}});
      if ({json.dumps(where)} === "step") scene.robot.step = boom;
      scene.resize(384, 216);
      scene.feed({json.dumps(state(alerts=[SILENT]))}, null);
      const errors = [], real = console.error;
      console.error = (e) => errors.push(String(e && e.message));
      let threw = null;
      try {{ for (let i = 0; i < 15 * 60; i++) {{ scene.step(1 / 15); scene.draw(); }} }} catch (e) {{ threw = String(e); }}
      console.error = real;
      console.log(JSON.stringify({{ threw, robot: scene.robot, errors, doing: scene.doing, line: scene.robotLine() }}));""")
    assert result["threw"] is None and result["robot"] is None and result["errors"] == ["robot"], result
    assert result["doing"] and result["line"] == ""


@needs_node
def test_his_bubble_never_covers_hanks_bubble_hank_the_status_window_or_the_buttons() -> None:
    result = run(BUBBLES + """
      const SCREENS = [[427, 240, 313, 11], [384, 216, 302, 14], [340, 216, 237, 14], [293, 633, 157, 26], [292, 380, 156, 35]];
      const HIS = ["Hank! Soil sensor reports nothing!", "Hank! Eingebauter Sensor meldet nichts!", "Hank! Part of the server is not running!",
                   "I told the soil sensor a joke. Too dry, it said."];  // the real labels, the longest ones of the supervision build included
      const cut = (a, b) => a[0] < b[0] + b[2] && a[0] + a[2] > b[0] && a[1] < b[1] + b[3] && a[1] + a[3] > b[1];
      const bad = [];
      let seen = 0, both = 0, turns = 0;
      for (const [W, Ht, left, bottom] of SCREENS) {
        const scene = new H.Scene(fakeCanvas(), {});
        scene.resize(W, Ht);
        scene.bar = { left, bottom };
        const r = scene.robot;
        r.gx = 1; r.gy = 1;
        for (let hx = -130; hx <= 130; hx += 20) for (const hy of [-10, 10, 40, 80, 120]) for (const side of [-17, 17]) for (const hank of [null, ...WORDS.slice(0, 2)]) for (const his of HIS) {
          scene.fx = { head: [hx, hy] };
          r.fx = { head: [hx + side, hy + 12] };
          scene.saying = hank ? { lines: A.wrap(hank, 168), until: 1e9, text: hank } : null;
          r.saying = { lines: A.wrapEven(his, r.width()), until: 1e9, text: his, kind: "tell" };
          const boxes = [], real = A.bubble;
          A.bubble = (c, ...args) => { const b = real(c, ...args); boxes.push(b); return b; };
          scene.drawBubbles(ctx);
          A.bubble = real;
          seen++;
          const mine = boxes[boxes.length - 1], R = [mine.x, mine.y, mine.w, mine.h + 5];
          if (boxes.length === 2) {
            both++;
            const h = boxes[0];
            if (cut(R, [h.x - 1, h.y - 1, h.w + 2, h.h + 7])) bad.push({ W, hx, hy, side, why: "over Hank's bubble" });
          } else if (hank) turns++;
          if (R[0] < 0 || R[1] < 0 || R[0] + R[2] > W) bad.push({ W, hx, hy, side, why: "off the screen" });
          if (R[0] + R[2] > left && R[1] < bottom) bad.push({ W, hx, hy, side, why: "under the buttons" });
          if (!scene.tall && cut(R, [scene.stats.x, scene.stats.y, scene.stats.w, scene.stats.h])) bad.push({ W, hx, hy, side, why: "over the status window" });
        }
      }
      console.log(JSON.stringify({ seen, both, turns, bad: bad.slice(0, 5), count: bad.length }));""")
    assert result["seen"] > 3000 and result["count"] == 0, result
    assert result["both"] > result["turns"] > 0  # mostly side by side or stacked; where the sky is full they take turns


def test_the_robots_words_are_in_both_languages_short_and_without_consumption() -> None:
    data = web.dictionary()
    keys = ["garden.robot.tell", "garden.robot.tellPlain", "garden.robot.link", "garden.robot.thanks", "garden.robot.clear",
            *[f"garden.joke.{n}" for n in range(1, 15)]]
    src = (WEB / "garden.js").read_text(encoding="utf-8") + (WEB / "garden-logic.js").read_text(encoding="utf-8")
    for key in keys:
        assert data["de"].get(key) and data["en"].get(key), key
        assert f'"{key}"' in src, key  # written out in full where it is used
    jokes = [data[lang][f"garden.joke.{n}"] for lang in ("de", "en") for n in range(1, 15)]
    assert len(set(jokes)) == 28
    banned = re.compile(r"\b(?:bier|beer|wein|wine|joint|rauch|smok|zigar|cigar|weed|kiff|stoned|high|drunk|betrunk|gras|pot|alkohol|alcohol)", re.I)
    for text in jokes:
        assert not banned.search(text) and len(text) <= 80, text
    assert "{what}" in data["de"]["garden.robot.tell"] and "{what}" in data["en"]["garden.robot.tell"]


def test_the_robot_runs_faster_than_hank_walks() -> None:
    src = (WEB / "garden.js").read_text(encoding="utf-8")
    walk = re.search(r"const WALK_SPEED = ([\d.]+);", src)
    robot = re.search(r"const ROBOT_WALK = ([\d.]+), ROBOT_RUN = ([\d.]+);", src)
    assert walk and robot and float(robot.group(2)) > float(robot.group(1)) >= float(walk.group(1))


def supervised(name: str, label: str, level: str = "red") -> dict[str, Any]:
    return {"name": name, "label": label, "metric": "supervision", "active": True, "kind": "supervision", "level": level, "signal": name}


# A box that went dark: five rules of the supervision build at once (their English labels)
BURST = [supervised("poll_stalled", "Box polling stalled"), supervised("device_link", "Device status not arriving"),
         supervised("probe_silent", "Probe reports nothing"), supervised("tank_sensor_silent", "Tank sensor reports nothing"), SILENT]


@needs_node
def test_a_burst_of_new_problems_is_told_in_full_on_one_trip() -> None:
    """Every new problem is said, also the fourth and the fifth of one moment: in turn on the same trip, then Hank's answer."""
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state())}, null);
      for (let i = 0; i < 30; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); }}
      scene.feed({json.dumps(state(alerts=BURST))});
      const r = scene.robot, told = [];
      let was = null, replyAfter = null;
      for (let i = 0; i < 15 * 120; i++) {{
        scene.step(1 / 15);
        if (i % 3 === 0) scene.draw();
        if (r.saying && r.saying !== was && r.saying.kind === "tell") told.push(r.saying.text);
        was = r.saying;
        if (replyAfter === null && scene.robotReply()) replyAfter = told.length;
      }}
      console.log(JSON.stringify({{ told, trips: r.trips, replyAfter }}));""")
    labels = [row["label"] for row in BURST]
    assert [next((label for label in labels if label in text), text) for text in result["told"]] == labels, result
    assert result["trips"] == 1 and result["replyAfter"] == len(labels), result


@needs_node
def test_a_problem_over_before_he_says_it_is_left_out_and_new_again_when_it_comes_back() -> None:
    three = BURST[:3]
    result = run(f"""{SCENE}
      const make = () => {{
        const scene = new H.Scene(fakeCanvas(), {{}});
        scene.resize(384, 216);
        scene.feed({json.dumps(state())}, null);
        for (let i = 0; i < 30; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); }}
        return scene;
      }};
      const said = [], all = {json.dumps(state(alerts=three))}, two = {json.dumps(state(alerts=three[:2]))};
      const listen = (scene, seconds, until) => {{
        const r = scene.robot;
        for (let i = 0; i < seconds * 15; i++) {{
          const was = r.saying;
          scene.step(1 / 15);
          if (i % 3 === 0) scene.draw();
          if (r.saying && r.saying !== was) said.push([r.saying.kind, r.saying.text]);
          if (until && until(r)) return;
        }}
      }};
      const scene = make(), r = scene.robot;
      scene.feed(all);
      listen(scene, 30, (robot) => robot.saying && robot.saying.kind === "tell");
      scene.feed(two);  // the third is over before he got to it
      listen(scene, 60);
      const first = {{ trips: r.trips, trip: r.trip, said: said.splice(0) }};
      scene.feed(all);  // and it is back: news again
      listen(scene, 40);
      const again = {{ trips: r.trips, said: said.splice(0) }};
      // a single one that is over while he is still running: not a word, and no all clear for it
      const other = make(), q = other.robot, a = other.a;
      const far = Object.keys(H.NODES).sort((m, n) => Math.hypot(H.NODES[n][0] - a.gx, H.NODES[n][1] - a.gy) - Math.hypot(H.NODES[m][0] - a.gx, H.NODES[m][1] - a.gy))[0];
      q.gx = H.NODES[far][0]; q.gy = H.NODES[far][1]; q.path = []; q.dest = null;
      other.feed({json.dumps(state(alerts=[SILENT]))});
      listen(other, 0.3);
      const running = {{ trip: q.trip, moving: q.path.length > 0 }};
      other.feed({json.dumps(state())});
      listen(other, 20);
      const after = {{ trip: q.trip, said: said.splice(0) }};
      // one he told stays, a new one is over while he runs to say it: the run ends at once, not at Hank
      other.feed({json.dumps(state(alerts=[SILENT]))});
      listen(other, 30, (robot) => robot.trips === 2 && !robot.trip);
      q.gx = H.NODES[far][0]; q.gy = H.NODES[far][1]; q.path = []; q.dest = null;
      other.feed({json.dumps(state(alerts=[SILENT, BURST[0]]))});
      listen(other, 0.3);
      const second = {{ trip: q.trip, moving: q.path.length > 0 }};
      other.feed({json.dumps(state(alerts=[SILENT]))});
      listen(other, 1 / 15);
      const stopped = q.trip;
      listen(other, 20);
      console.log(JSON.stringify({{ first, again, running, after, second, stopped, lastly: said.filter((row) => row[0] === "tell") }}));""")
    labels = [row["label"] for row in three]
    first = [text for kind, text in result["first"]["said"] if kind == "tell"]
    assert len(first) == 2 and labels[0] in first[0] and labels[1] in first[1], result["first"]
    assert not any(kind == "clear" for kind, _ in result["first"]["said"]) and result["first"]["trips"] == 1
    again = [text for kind, text in result["again"]["said"] if kind == "tell"]
    assert len(again) == 1 and labels[2] in again[0] and result["again"]["trips"] == 2, result["again"]
    assert result["running"] == {"trip": True, "moving": True}
    assert result["after"] == {"trip": False, "said": []}
    assert result["second"] == {"trip": True, "moving": True} and result["stopped"] is False, result
    assert len(result["lastly"]) == 1 and BURST[0]["label"] not in result["lastly"][0][1], result["lastly"]  # only the old one, told once


@needs_node
def test_hank_turns_to_the_robot_only_while_he_stands_and_turns_back_after() -> None:
    """The one thing the robot changes in Hank: his facing, for the moment of his answer. Seated or walking, never."""
    result = run(f"""{SCENE}
      const A = window.GardenArt, alert = {json.dumps(state(demo=True, alerts=[SILENT]))};
      const make = (robot) => {{
        const scene = new H.Scene(fakeCanvas(), {{ robot }});
        scene.resize(384, 216);
        scene.feed({json.dumps(state(demo=True))}, null);
        for (let i = 0; i < 30; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); }}
        return scene;
      }};
      const facing = (scene) => [scene.a.view, scene.a.flip];
      const frames = (scene, seconds, until) => {{
        for (let i = 0; i < seconds * 15; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); if (until && until()) return true; }}
        return false;
      }};
      // standing at the hub, facing SW, the robot on his right: he turns to him while he answers, and back after it
      const standing = (() => {{
        const scene = make(true), a = scene.a, r = scene.robot;
        a.gx = 5.6; a.gy = 3.3;
        scene.command({{ steps: [{{ pose: "hip", left: 60, face: "SW" }}], doing: "garden.do.read" }});
        r.gx = a.gx + 0.55; r.gy = a.gy - 0.55; r.path = []; r.dest = null;
        scene.step(1 / 15);
        const before = facing(scene);
        scene.feed(alert);
        frames(scene, 30, () => scene.robotReply());
        const during = facing(scene), right = A.X(r.gx, r.gy) > A.X(a.gx, a.gy);
        frames(scene, 30, () => !scene.robotReply());
        return {{ before, during, right, after: facing(scene), pose: a.pose }};
      }})();
      // seated in the armchair, his back to us: he answers without turning
      const seated = (() => {{
        const scene = make(true), a = scene.a;
        scene.command({{ steps: [{{ seat: "chair", face: "NE" }}, {{ pose: "hold", left: 90 }}], doing: "garden.do.juice" }});
        frames(scene, 1);
        const before = facing(scene), during = new Set();
        scene.feed(alert);
        let seen = 0;
        for (let i = 0; i < 15 * 40; i++) {{
          scene.step(1 / 15);
          if (i % 3 === 0) scene.draw();
          if (scene.robotReply()) {{ seen++; during.add(facing(scene).join()); }}
        }}
        return {{ before, seen, during: [...during], pose: a.pose }};
      }})();
      // his own next step comes while he faces the robot: its own facing stays, nothing turns him back over it
      const stepped = (() => {{
        const scene = make(true), a = scene.a, r = scene.robot;
        a.gx = 5.6; a.gy = 3.3;
        scene.command({{ steps: [{{ pose: "hip", left: 60, face: "SW" }}, {{ pose: "stand", left: 60, face: "NW" }}], doing: "garden.do.read" }});
        r.gx = a.gx + 0.55; r.gy = a.gy - 0.55; r.path = []; r.dest = null;
        scene.step(1 / 15);
        scene.feed(alert);
        frames(scene, 30, () => scene.robotReply());
        const turned = facing(scene);
        a.step.left = 0;
        scene.step(1 / 15);
        const own = facing(scene);
        frames(scene, 30, () => !scene.robotReply());
        frames(scene, 1);
        return {{ turned, own, after: facing(scene), pose: a.pose }};
      }})();
      // he has turned to listen, and all is clear before his answer shows: he turns back, the answer is dropped
      const cleared = (() => {{
        const scene = make(true), a = scene.a, r = scene.robot;
        a.gx = 5.6; a.gy = 3.3;
        scene.command({{ steps: [{{ pose: "hip", left: 60, face: "SW" }}], doing: "garden.do.read" }});
        r.gx = a.gx + 0.55; r.gy = a.gy - 0.55; r.path = []; r.dest = null;
        scene.step(1 / 15);
        const before = facing(scene);
        scene.feed(alert);
        for (let i = 0; i < 15 * 30 && !(r.turned && !scene.robotReply()); i++) scene.step(1 / 15);
        const turned = facing(scene);
        scene.feed({json.dumps(state(demo=True))});
        scene.step(1 / 15);
        scene.draw();
        return {{ before, turned, after: facing(scene), reply: Boolean(r.reply) }};
      }})();
      // walking: frame for frame the same facing as without the robot while he answers on the way
      const walking = (() => {{
        const trace = (robot) => {{
          seed = 20261106;
          const scene = make(robot), a = scene.a;
          a.gx = H.NODES.desk[0]; a.gy = H.NODES.desk[1];
          scene.command({{ steps: [{{ go: "barrel" }}, {{ go: "desk" }}, {{ pose: "stand", left: 5, face: "SE" }}], doing: "garden.do.walk" }});
          scene.feed(alert);
          const out = [];
          let start = null;
          for (let i = 0; i < 15 * 25; i++) {{
            scene.step(1 / 15);
            if (i % 3 === 0) scene.draw();
            const answering = Boolean(scene.robot && scene.robotReply());
            if (answering && !start) start = {{ walking: Boolean(a.step && a.step.go), turned: Boolean(scene.robot.turned && scene.robot.turned.done) }};
            out.push([answering, Boolean(a.step && a.step.go), a.view, a.flip]);
          }}
          return {{ out, start }};
        }};
        const a = trace(true), b = trace(false);
        let answering = 0, differs = 0;
        a.out.forEach((row, i) => {{ if (row[0] && row[1]) {{ answering++; if (row[2] !== b.out[i][2] || row[3] !== b.out[i][3]) differs++; }} }});
        return {{ answering, differs, start: a.start }};
      }})();
      console.log(JSON.stringify({{ standing, seated, stepped, cleared, walking }}));""")
    standing = result["standing"]
    assert standing["right"] and standing["before"] == ["F", True] and standing["during"] == ["F", False], standing
    assert standing["after"] == ["F", True] and standing["pose"] == "hip", standing
    seated = result["seated"]
    assert seated["seen"] > 0 and seated["before"] == ["B", False] and seated["during"] == ["B,false"] and seated["pose"] == "hold", seated
    stepped = result["stepped"]
    assert stepped["turned"] == ["F", False] and stepped["own"] == ["B", True] and stepped["after"] == ["B", True], stepped
    assert result["walking"]["answering"] > 0 and result["walking"]["differs"] == 0, result["walking"]
    cleared = result["cleared"]
    assert cleared["turned"] == ["F", False] and cleared["after"] == cleared["before"] == ["F", True] and not cleared["reply"], cleared
    assert result["walking"]["start"] == {"walking": True, "turned": False}, result["walking"]


FRAMES = """
let clock = 1800000000000, rafFn = null, at = 0, scene = null;
Date.now = () => clock;
global.requestAnimationFrame = (fn) => { rafFn = fn; return 1; };
const resize = H.Scene.prototype.resize;
H.Scene.prototype.resize = function (w, h) { scene = this; return resize.call(this, w, h); };
const frame = (gap) => { at += gap || 70; clock += gap || 70; rafFn(at); };  // the page's own frame loop, tick(), with its clock
const live = () => created.find((el) => el.className === "garden-sr");
"""


@needs_node
def test_the_frame_loop_tells_the_robot_the_server_is_lost_and_the_screen_reader_hears_both() -> None:
    """The glue in tick(): the page's link state reaches the robot, and his line and Hank's answer reach the live region."""
    result = run(FRAMES + """
      fire("vgrow:state", { detail: %s });
      H.open();
      at = performance.now();
      for (let i = 0; i < 30; i++) frame();
      const calm = { status: scene.robot.status, line: live().textContent };
      fire("vgrow:offline", {});
      clock += 12000;  // a failed poll and no new state for 12 s
      let told = null, answered = null;
      for (let i = 0; i < 600 && !(told && answered); i++) {
        frame();
        const text = live().textContent;
        if (!told && text.includes("garden.robot.link")) told = text;
        if (!answered && text.includes("Hank: garden.robot.thanks")) answered = text;
      }
      console.log(JSON.stringify({ calm, told, answered, link: scene.link, status: scene.robot.status }));""" % json.dumps(state()))
    assert result["calm"]["status"] == "calm" and "garden.robot" not in (result["calm"]["line"] or ""), result["calm"]
    assert result["link"]["offline"] is True and result["status"] == "alert"
    assert result["told"] and result["answered"], result


@needs_node
def test_a_computer_that_slept_does_not_make_him_report_a_lost_server() -> None:
    """No frame for hours (the computer slept, no visibilitychange): the age of the last state counts from the return."""
    result = run(FRAMES + """
      fire("vgrow:state", { detail: %s });
      H.open();
      at = performance.now();
      for (let i = 0; i < 30; i++) frame();
      frame(3 * 3600 * 1000);  // three hours later the next frame
      const woke = { status: scene.robot.status, items: scene.robot.items.map((item) => item.id) };
      for (let i = 0; i < 100; i++) frame();  // 7 s: the polls come back
      const soon = scene.robot.status;
      for (let i = 0; i < 400; i++) frame();  // and if no state comes for half a minute, the server is lost after all
      console.log(JSON.stringify({ woke, soon, later: scene.robot.items.map((item) => item.id) }));""" % json.dumps(state()))
    assert result["woke"] == {"status": "calm", "items": []} and result["soon"] == "calm", result
    assert result["later"] == ["link"], result


@needs_node
def test_beside_the_thermometer_he_stands_in_front_of_the_plants_and_in_his_corner_behind_them() -> None:
    """The plants stand in the raised bed: for the drawing order they count as the bed (seen 15.11.2026: the right-hand plant
    was drawn across his raised arm beside the thermometer, while his legs stood in front of the bed's plank). The stones lie
    flat and come last: they never put him in front of the armchair he stands behind (seen 16.11.2026 beside Hank at the PC).
    In front of the armchair, past its front side, he is in front of it and of Hank sitting in it (seen the same day)."""
    result = run(f"""{SCENE}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.feed({json.dumps(state(autopilot__boxes=[{"plant": "Purple Punch", "height_cm": 43}]))}, null);
      scene.step(1 / 15);
      const objs = scene.world.objs, r = scene.robot, plantPx = Math.min(72, 6 + 43 * 0.8);
      const index = (name) => objs.findIndex((o) => o.name === name);
      const at = (gx, gy) => {{ r.gx = gx; r.gy = gy; return scene.robotSpot(objs, plantPx).insert; }};
      // beside Hank at the PC, coming from the deck on the left: a spot in sight, not squeezed in behind the armchair
      scene.a.gx = 7.85; scene.a.gy = 1.62; r.gx = 6.3; r.gy = 3.56;
      const spot = r.beside(), besidePc = at(...spot);
      // the two of them: in front of Hank he is drawn after him, whatever Hank's own place among the things; behind
      // Hank he keeps his own place, in front of the plants (beside Hank on his way to the barrel they covered his face)
      const A = window.GardenArt, plant = A.plant;
      const pair = (hankAt, robotAt) => {{
        const order = [], drawHank = scene.drawHank.bind(scene), drawRobot = scene.drawRobot.bind(scene);
        scene.drawHank = (...args) => {{ order.push("hank"); return drawHank(...args); }};
        scene.drawRobot = (...args) => {{ order.push("robot"); return drawRobot(...args); }};
        A.plant = (...args) => {{ order.push("plant"); return plant(...args); }};
        [scene.a.gx, scene.a.gy] = hankAt; [r.gx, r.gy] = robotAt;
        scene.draw();
        delete scene.drawHank; delete scene.drawRobot; A.plant = plant;
        return order.join(" ");
      }};
      const pcPair = pair([7.85, 1.62], [8.6, 1.77]), thermoPair = pair([3.3, 3.3], [3.0, 2.95]), barrelPair = pair([1.9, 4.09], [2.45, 3.02]);
      console.log(JSON.stringify({{ thermo: at(3.85, 2.75), front: at(2.45, 2.75), right: at(5.05, 1.63), corner: at(4.35, 1.08), behind: at(2.75, 1.28),
        pc: at(7.0, 2.47), park: at(3.95, 4.35), armchair: at(8.17, 3.67), bed: index("bed"), plantB: index("plantB"), plantC: index("plantC"),
        chair: index("chairRear"), besidePc, spot, pcPair, thermoPair, barrelPair, all: objs.length }}));""")
    assert result["thermo"] > result["plantC"] and result["right"] > result["plantC"], result  # in front of all three plants
    assert result["front"] > result["plantB"], result
    assert result["corner"] <= result["bed"] and result["behind"] <= result["bed"], result  # behind the bed, its plants over him
    assert result["pc"] <= result["chair"] and result["park"] == result["all"], result  # behind the armchair; by the thermometer in front of all
    assert result["armchair"] == result["all"], result  # in front of the armchair: drawn with Hank, the one further front after
    assert result["besidePc"] > result["chair"], result  # at the PC he stands where he is seen
    pc, thermo, barrel = (result[k].split() for k in ("pcPair", "thermoPair", "barrelPair"))
    assert pc.index("hank") < pc.index("robot") and thermo.index("robot") < thermo.index("hank"), result  # in front of Hank at the PC, behind him there
    # behind Hank, still in front of the two plants he overlaps there (A and B; the third stands clear to his right)
    assert barrel[:barrel.index("robot")].count("plant") >= 2, result


@needs_node
def test_his_bubble_wears_his_bulbs_colour_and_hanks_does_not() -> None:
    result = run(BUBBLES + """
      const scene = new H.Scene(fakeCanvas(), {});
      scene.resize(384, 216);
      const r = scene.robot;
      r.gx = 1; r.gy = 1;
      scene.fx = { head: [-20, 70] };
      r.fx = { head: [-3, 82] };
      const bands = (status) => {
        r.status = status;
        r.saying = { lines: A.wrapEven("Hank! Tank empty!", r.width()), until: 1e9, text: "Hank! Tank empty!", kind: "tell" };
        scene.saying = { lines: A.wrap("Thanks, I'll have a look.", 168), until: 1e9, text: "Thanks, I'll have a look." };
        const calls = [], boxes = [], rect = A.rect, bubble = A.bubble;
        A.rect = (c, x, y, w, h, col) => { calls.push({ x, y, w, h, col }); return rect(c, x, y, w, h, col); };
        A.bubble = (c, ...args) => { const b = bubble(c, ...args); boxes.push(b); return b; };
        scene.drawBubbles(ctx);
        A.rect = rect; A.bubble = bubble;
        const strip = (b) => calls.filter((k) => k.x === b.x + 1 && k.y === b.y + 1 && k.w === b.w - 2 && k.h === 1).map((k) => k.col);
        return { bubbles: boxes.length, his: strip(boxes[boxes.length - 1]), hank: boxes.length > 1 ? strip(boxes[0]) : null };
      };
      console.log(JSON.stringify({ alert: bands("alert"), calm: bands("calm") }));""")
    assert result["alert"]["his"] == ["#d4503f"] and result["calm"]["his"] == ["#f2b632"], result
    assert result["alert"]["bubbles"] == 2 and result["alert"]["hank"] == [], result


DESKTOP, PHONE, BIG = [384, 216, 295, 11], [293, 633, 157, 26], [427, 240, 313, 11]  # scene size and the buttons' left and bottom


@needs_node
@pytest.mark.parametrize(("lang", "label", "screen"), [
    ("de", "Bodensensor meldet nichts", DESKTOP), ("en", "Soil sensor reports nothing", DESKTOP), ("de", "Eingebauter Sensor meldet nichts", DESKTOP),
    ("en", "Tank empty", DESKTOP), ("de", "Bodensensor meldet nichts", PHONE), ("en", "Part of the server is not running", BIG),
])
def test_hanks_answer_once_shown_stays_in_sight_and_never_under_his_line(lang: str, label: str, screen: list[int]) -> None:
    """Seen 16.11.2026 with a real two-line label: whether both fit was judged before Hank turned to the robot, the turn moved his
    head 2 pixels, and his answer stayed hidden behind the robot's line. With the real texts, frame by frame: once his answer
    shows it is drawn every frame, the two never cross, and a line of the robot's that gave way does not come back."""
    words = web.dictionary()[lang]
    result = run(f"""
      const words = {json.dumps(words, ensure_ascii=False)};
      global.t = (key, p) => String(words[key] || key).replace(/[{{](\\w+)[}}]/g, (m, k) => (p && p[k] !== undefined ? p[k] : m));
      {BUBBLES}
      const [W, Ht, left, bottom] = {json.dumps(screen)};
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(W, Ht);
      scene.bar = {{ left, bottom }};
      scene.feed({json.dumps(state(demo=True))}, null);
      for (let i = 0; i < 30; i++) {{ scene.step(1 / 15); if (i % 3 === 0) scene.draw(); }}
      scene.command(scene.orderFor("thermo"));
      for (let i = 0; i < 15 * 14 && !(scene.a.pose === "hip" && !scene.robot.path.length); i++) {{ scene.step(1 / 15); scene.draw(); }}
      const r = scene.robot;
      r.gx = H.NODES.desk[0]; r.gy = H.NODES.desk[1]; r.path = []; r.dest = null;
      scene.feed({json.dumps(state(demo=True, alerts=[{**SILENT, "label": label}]))});
      const real = A.bubble, drawBubble = scene.drawBubble.bind(scene);
      const cross = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h + 5 && a.y + a.h + 5 > b.y;
      let answered = 0, hidden = 0, both = 0, overlaps = 0, comeback = 0, cut = 0, was = null;
      for (let i = 0; i < 15 * 16; i++) {{
        scene.step(1 / 15);
        const boxes = [];
        let hank = null;
        A.bubble = (c, ...args) => {{ const b = real(c, ...args); boxes.push(b); return b; }};
        scene.drawBubble = (c) => {{ const b = drawBubble(c); if (b) hank = b; return b; }};
        scene.draw();
        A.bubble = real;
        delete scene.drawBubble;
        const his = boxes.some((b) => b !== hank);
        if (scene.robotReply()) {{
          answered++;
          if (!hank) hidden++;
          if (his) both++;
          if (r.saying && !his) cut++;  // his line still running, but not drawn: it had to give way
          if (was === false && his) comeback++;
          was = his;
        }}
        if (boxes.length === 2 && cross(boxes[0], boxes[1])) overlaps++;
      }}
      console.log(JSON.stringify({{ answered, hidden, both, overlaps, comeback, cut }}));""")
    assert result["answered"] > 30 and result["hidden"] == 0, result
    assert result["overlaps"] == 0 and result["comeback"] == 0, result
    assert result["cut"] == 0, result  # judged with Hank already turned: both from the start, or his line first and then the answer


@needs_node
def test_once_hanks_answer_shows_the_robots_line_gives_way_for_good_when_room_runs_out() -> None:
    result = run(BUBBLES + """
      const scene = new H.Scene(fakeCanvas(), {});
      scene.resize(384, 216);
      scene.bar = { left: 295, bottom: 11 };
      scene.time = 10;
      const r = scene.robot;
      r.gx = 1; r.gy = 1;
      r.fx = { head: [17, 22] };
      r.saying = { lines: A.wrapEven("Hank! Bodensensor meldet nichts!", r.width()), until: 1e9, text: "Hank! Bodensensor meldet nichts!", kind: "tell" };
      r.reply = { lines: A.wrap("Danke, ich schau nach.", 168), text: "Danke, ich schau nach.", from: 0, until: 1e9, placed: true };
      const real = A.bubble, drawBubble = scene.drawBubble.bind(scene);
      const picture = (head) => {
        scene.fx = { head };
        let all = 0, hank = 0;
        A.bubble = (c, ...args) => { all++; return real(c, ...args); };
        scene.drawBubble = (c) => { const b = drawBubble(c); if (b) hank++; return b; };
        scene.drawBubbles(ctx);
        A.bubble = real;
        delete scene.drawBubble;
        return [hank, all - hank];
      };
      // far apart both fit; Hank turned to him at the thermometer they do not (his line is two rows); then far apart again
      console.log(JSON.stringify([picture([-120, 60]), picture([1, 13]), picture([-120, 60])]));""")
    assert result == [[1, 1], [1, 0], [1, 0]], result  # Hank's answer every time, the robot's line gone once it gave way


@needs_node
def test_beside_a_wide_bubble_of_hanks_his_own_goes_where_he_is() -> None:
    """Seen 16.11.2026 at night: Hank napping in the armchair with his line, the robot in front of him with his. Above Hank's
    bubble was free, yet his went to its far left, the tail over the thermometer. Of the places around Hank's bubble the
    free one nearest above him wins."""
    words = web.dictionary()["de"]
    result = run(f"""
      const words = {json.dumps(words, ensure_ascii=False)};
      global.t = (key, p) => String(words[key] || key).replace(/[{{](\\w+)[}}]/g, (m, k) => (p && p[k] !== undefined ? p[k] : m));
      {BUBBLES}
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.bar = {{ left: 295, bottom: 11 }};
      scene.feed({json.dumps(state(demo=True, light__level=0))}, null);
      scene.command(scene.orderFor("chair"));
      for (let i = 0; i < 15 * 30 && scene.a.pose !== "nap"; i++) {{ scene.step(1 / 15); scene.draw(); }}
      const r = scene.robot;
      r.gx = 8.17; r.gy = 3.67; r.path = []; r.dest = null;
      scene.draw();
      r.saying = {{ lines: A.wrapEven("Hank! Bodensensor meldet nichts!", r.width()), until: 1e9, text: "Hank! Bodensensor meldet nichts!", kind: "tell" }};
      const boxes = [], real = A.bubble;
      A.bubble = (c, ...args) => {{ const b = real(c, ...args); boxes.push(b); return b; }};
      scene.draw();
      A.bubble = real;
      const his = boxes[boxes.length - 1], headX = scene.ox + r.fx.head[0];
      console.log(JSON.stringify({{ bubbles: boxes.length, hank: Boolean(scene.saying), his, headX }}));""")
    his, head = result["his"], result["headX"]
    assert result["bubbles"] == 2 and result["hank"], result  # Hank's line and his, together
    assert his["x"] <= head <= his["x"] + his["w"], result  # his bubble above him


# Real labels of the supervision build (its alerts.json), the longest ones included, as his line puts them
REAL_LINES = ["Hank! Tank leer!", "Hank! Zu kalt (Fühler)!", "Hank! Bodensensor meldet nichts!", "Hank! Eingebauter Sensor meldet nichts!",
              "Hank! Messwertspeicher schreibt nicht!", "Hank! Licht läuft auf 12/12!", "Hank! Tank empty!", "Hank! Soil sensor reports nothing!",
              "Hank! Part of the server is not running!", "Hank! Measurement store is not writing!", "Hank! Hand watering does not arrive!"]


@needs_node
def test_his_lines_break_where_a_sentence_ends_and_no_punchline_word_hangs_alone() -> None:
    data = web.dictionary()
    jokes = [data[lang][f"garden.joke.{n}"] for lang in ("de", "en") for n in range(1, 15)]
    result = run(f"""
      const A = window.GardenArt, out = [];
      for (const text of {json.dumps(jokes + REAL_LINES, ensure_ascii=False)}) out.push({{ text, even: A.wrapEven(text, 140), greedy: A.wrap(text, 140),
        widths: A.wrapEven(text, 140).map(A.textWidth) }});
      console.log(JSON.stringify(out));""")
    for row in result:
        lines = row["even"]
        assert len(lines) == len(row["greedy"]) and " ".join(lines) == row["text"], row  # never taller, never a word lost
        assert all(w <= 140 or " " not in line for w, line in zip(row["widths"], lines)), row
        for i, line in enumerate(lines):  # a line of one word is a sentence of its own ("Hank!", "Langsam.")
            if " " not in line:
                assert re.search(r"[.!?]$", line) and (i == 0 or re.search(r"[.!?:]$", lines[i - 1])), row
    by_text = {row["text"]: row["even"] for row in result}
    spoken = run(f"""
      const words = {json.dumps(data["de"], ensure_ascii=False)};
      global.t = (key) => words[key];
      const scene = new H.Scene(fakeCanvas(), {{}});
      scene.resize(384, 216);
      scene.robot.say("garden.joke.7", null, 5, "joke");
      console.log(JSON.stringify(scene.robot.saying.lines));""")
    assert spoken == ["Ich hab keine Gefühle.", "Nur Fühler.", "Kommt aufs Gleiche raus."]  # his own bubble wraps that way
    assert by_text[data["de"]["garden.joke.7"]] == ["Ich hab keine Gefühle.", "Nur Fühler.", "Kommt aufs Gleiche raus."]
    assert by_text[data["en"]["garden.joke.7"]] == ["I don't have feelings.", "I have sensors.", "Close enough."]


# ---------- the files ----------

def test_the_top_bar_has_the_button_on_every_page() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    top = html[html.index('<header class="app-topbar">'):html.index("</header>")]
    assert 'data-garden-open' in top and 'data-i18n="garden.open"' in top


def test_the_garden_runs_slower_than_real_time() -> None:
    pace = re.search(r"const PACE = ([\d.]+);", (WEB / "garden.js").read_text(encoding="utf-8"))
    assert pace and 0.4 <= float(pace.group(1)) <= 0.8


def test_the_garden_waits_a_minute() -> None:
    assert "const IDLE_MS = 60 * 1000;" in (WEB / "garden.js").read_text(encoding="utf-8")


def test_the_garden_only_looks() -> None:
    for name in FILES:
        src = (WEB / name).read_text(encoding="utf-8")
        assert "/api/cmd" not in src and "fetch(" not in src and "XMLHttpRequest" not in src, name
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//"))
        assert not re.search(r"\bapi\((?!\"/api/hank/state\"\))", code), name


def test_the_page_loads_the_files_in_order_after_the_app() -> None:
    order = web.page().order
    positions = [order.index(f"/static/{name}") for name in FILES]
    assert positions == sorted(positions) and order.index("/static/app.js") < positions[0]


def test_every_garden_file_reloads_open_pages() -> None:
    from vgrow import server

    assert set(FILES) | {"garden.css"} <= set(server.PAGE_FILES)


def test_the_file_rules() -> None:
    for name in (*FILES, "garden.css"):
        raw = (WEB / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), name
        text = raw.decode("utf-8")
        assert web.EM_DASH not in text, name
    for name in FILES:
        src = (WEB / name).read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert banned not in src, (name, banned)
    # the art file holds no language: its only words are pixel rows and colours
    art = (WEB / "garden-art.js").read_text(encoding="utf-8")
    assert not re.search(r"\bt\(\"", art)


def test_the_look_is_our_own_no_borrowed_names() -> None:
    """The owner asked for a retro feel; the world, the man and the font are drawn here, and no other game's name sits in the code."""
    for name in (*FILES, "garden.css"):
        text = (WEB / name).read_text(encoding="utf-8").lower()
        assert "ragnarok" not in text and "poring" not in text, name
