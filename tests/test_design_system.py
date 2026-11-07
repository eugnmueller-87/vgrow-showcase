"""The design system (design-system/, mirrored from the Design System artifact) and what the page takes from it:
tokens.css, generated from tokens.json and loaded before every stylesheet of the page; style.css and cockpit.css
declare no token of their own; the adopted components; the Automation page in their terms. No network;
scripts/build-tokens.py is imported, not run; app.js functions run under node where node is installed."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import server
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, make_app

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DESIGN = ROOT / "design-system"
NODE = shutil.which("node")


def js_function(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    return src[start:src.index("\n}\n", start) + 3]


def js_const(src: str, name: str) -> str:
    start = src.index(f"const {name} = ")
    return src[start:src.index(";\n", start) + 2]


def build_tokens() -> ModuleType:
    """scripts/build-tokens.py; its name has a dash, so it is loaded by path."""
    spec = importlib.util.spec_from_file_location("build_tokens", ROOT / "scripts" / "build-tokens.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tokens() -> dict[str, Any]:
    return json.loads((DESIGN / "tokens.json").read_text(encoding="utf-8"))


def token_names() -> set[str]:
    data = tokens()
    names = {t["name"] for family in data.values()
             if isinstance(family, dict) and isinstance(family.get("tokens"), list) for t in family["tokens"]}
    return names | {f"font-{key}" for key in data["type"]["families"]}


# ---------- tokens.css ----------

def test_tokens_css_is_what_tokens_json_gives() -> None:
    assert build_tokens().build(tokens()) == (DESIGN / "tokens.css").read_text(encoding="utf-8")


def test_tokens_css_keeps_the_artifacts_layout_and_the_system_dark_mode() -> None:
    css = (DESIGN / "tokens.css").read_text(encoding="utf-8")
    assert css.startswith("/* VGrow Cockpit ")
    marks = (':root, [data-theme="light"] {', '[data-theme="dark"] {', ":root:not([data-theme]) {",
             '[data-theme="safe"] {', "\n:root {\n  --space-1: 4px;", "\n.display {")
    places = [css.index(mark) for mark in marks]
    assert places == sorted(places)
    # System follows the device's dark mode; the colour-blind-safe theme is a light theme and never turns dark
    assert "@media (prefers-color-scheme: dark) {\n  :root:not([data-theme]) {" in css
    assert ':not([data-theme="light"])' not in css


@pytest.mark.parametrize("family, token", [
    ("color", {"name": "bg", "value": {"light": "red; } body { background: url(https://example.org/x) }"}}),
    ("color", {"name": "bg", "value": {"light": "var(--ink)"}}),
    ("shadow", {"name": "shadow-card", "value": "0 2px 8px url(x)"}),
    ("spacing", {"name": "space-1", "value": "4px; }"}),
    ("size", {"name": "measure", "value": "68ch } * { color: red"}),
    ("color", {"name": "bad name;", "value": "#ffffff"}),
])
def test_a_value_that_would_break_out_of_its_declaration_is_refused(family: str, token: dict[str, Any]) -> None:
    """tokens.json comes from an artifact other people can edit: nothing in it may reach the page as CSS."""
    data = tokens()
    data[family]["tokens"] = [token]
    module = build_tokens()
    with pytest.raises(module.TokenError):
        module.build(data)


# ---------- the adopted components ----------

def test_ds_components_is_the_adopted_sections_of_bundle_css_in_one_layer() -> None:
    module = build_tokens()
    bundle = (DESIGN / "components" / "bundle.css").read_text(encoding="utf-8")
    css = (WEB / "ds-components.css").read_text(encoding="utf-8")
    assert module.components(bundle) == css
    assert css.count("@layer design-system {") == 1 and css.rstrip().endswith("}")
    assert re.findall(r"/\* ---- (\w+) ---- \*/", css) == [
        name for name in re.findall(r"/\* ---- (\w+) ---- \*/", bundle) if name in module.ADOPTED]
    # word for word: every rule of an adopted section is in the layer as bundle.css has it
    for name, text in zip(*[iter(re.split(r"^/\* ---- (\w+) ---- \*/\n", bundle, flags=re.M)[1:])] * 2):
        if name in module.ADOPTED:
            for line in filter(None, text.strip().split("\n")):
                assert f"  {line}\n" in css, (name, line)


def test_an_adopted_section_that_could_load_something_is_refused() -> None:
    module = build_tokens()
    bundle = (DESIGN / "components" / "bundle.css").read_text(encoding="utf-8")
    for bad in ("background: url(https://example.org/p.png);", "@import 'x.css';", "content: '</style>';"):
        with pytest.raises(module.TokenError):
            module.components(bundle.replace("/* ---- Button ---- */\n", f"/* ---- Button ---- */\n.btn {{ {bad} }}\n"))


def test_the_app_no_longer_carries_its_own_copy_of_an_adopted_component() -> None:
    style = (WEB / "style.css").read_text(encoding="utf-8")
    cockpit = (WEB / "cockpit.css").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for gone in (".btn.primary {", ".btn.sm {", ".seg button.on {", ".card-h h3 {", ".timeline li::before {",
                 ".banner.warn {", ".side-nav a.active {", ".card .seg button.on {", ".card .btn, .btn {"):
        # the rule itself, at the start of a line; a page's own contextual rule (.camera-card .card-h h3) stays
        assert not re.search(r"(?m)^\s*" + re.escape(gone), style + cockpit), gone
    # red banners say so in the Banner component's own words; the low-stakes switches are quiet
    assert 'class="banner warn' not in html and html.count('class="banner bad') == 3
    for seg in ('id="cam-mode"', 'id="range-seg"', 'aria-label:theme.title"', 'lang-seg"'):
        tag = next(t for t in re.findall(r"<div class=\"seg[^>]*>", html) if seg in t)
        assert " quiet" in tag, tag


# ---------- the page takes its tokens from there ----------

def test_the_page_loads_the_tokens_first_and_versions_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert sheets[:4] == ["/vendor/uPlot.min.css", "/design-system/tokens.css", "/static/ds-components.css",
                          "/static/style.css"]
    assert {s.removeprefix("/design-system/") for s in sheets if s.startswith("/design-system/")} == set(
        server.DESIGN_FILES)
    assert 'href="/design-system/tokens.css?v=42"' in server.page_html(html, "42", "de", "de")
    # a change to tokens.css alone reloads open pages
    web_dir, design = tmp_path / "web", tmp_path / "design-system"
    web_dir.mkdir()
    design.mkdir()
    (web_dir / "index.html").write_text("x", encoding="utf-8")
    os.utime(web_dir / "index.html", (1_000_000, 1_000_000))
    (design / "tokens.css").write_text("x", encoding="utf-8")
    os.utime(design / "tokens.css", (3_000_000, 3_000_000))
    monkeypatch.setattr(server, "WEB", web_dir)
    assert server.web_version() == "3000000"


def test_only_the_compiled_tokens_are_served_and_only_with_the_key() -> None:
    async def go() -> tuple[int, int, str, str, list[int]]:
        async with TestClient(TestServer(make_app(DemoBox(), Alerts(), "k"))) as client:
            anonymous = (await client.get("/design-system/tokens.css")).status
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            resp = await client.get("/design-system/tokens.css")
            body = await resp.text()
            # the mirror of the artifact (previews, scripts, sources) is never served from the cockpit's origin
            others = [(await client.get(f"/design-system/{path}")).status for path in (
                "tokens.json", "README.md", "components/bundle.js", "components/Banner/preview.html")]
            return anonymous, resp.status, resp.content_type, body, others

    anonymous, status, content_type, body, others = asyncio.run(go())
    assert anonymous == 403 and status == 200 and content_type == "text/css"
    assert body == (DESIGN / "tokens.css").read_text(encoding="utf-8")
    assert others == [404, 404, 404, 404]


def test_style_and_cockpit_declare_no_token_of_their_own() -> None:
    names = token_names()
    media = re.compile(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}")
    for name in ("style.css", "cockpit.css"):
        css = (WEB / name).read_text(encoding="utf-8")
        outside = set(re.findall(r"--([\w-]+)\s*:", media.sub("", css)))
        assert not outside & names, (name, sorted(outside & names))
        # inside a breakpoint only the shell's own measures change: the sidebar's width and the top bar's height
        inside = set(re.findall(r"--([\w-]+)\s*:", "".join(media.findall(css))))
        assert inside & names <= {"sidebar-w", "topbar-h"}, (name, sorted(inside & names))
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", (WEB / "cockpit.css").read_text(encoding="utf-8"))


def test_the_theme_switch_offers_the_colour_blind_safe_theme() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert set(re.findall(r'data-theme-set="(\w+)"', html)) == {"auto", "light", "dark", "safe"}
    assert 'm === "safe") document.documentElement.setAttribute("data-theme", m)' in html  # no light flash on load
    assert 'const THEMES = ["auto", "light", "dark", "safe"];' in js and 'safe: "theme.safe"' in js
    # a dark device never turns the safe page dark: every system-dark rule asks for "no theme chosen"
    for path in sorted(WEB.glob("*.css")):
        assert ':not([data-theme="light"])' not in path.read_text(encoding="utf-8"), path.name


def test_the_shell_keeps_to_the_type_scale_and_the_radii() -> None:
    for name in ("style.css", "cockpit.css"):
        css = (WEB / name).read_text(encoding="utf-8")
        sizes = {float(size) for size in re.findall(r"font-size:\s*([\d.]+)px", css)}
        assert min(sizes) >= 12, name  # nothing on the page is 11 or 10 px
        assert not {12.5, 13.5, 15.0, 15.5} & sizes, (name, sorted(sizes))
        assert set(re.findall(r"font-weight:\s*(\d+)", css)) <= {"400", "600", "700"}, name
        radii = [int(r) for r in re.findall(r"border-radius:\s*(\d+)px", css)]
        assert all(r <= 4 for r in radii), (name, radii)  # corners are tokens; only tiny marks keep a px value
    style = (WEB / "style.css").read_text(encoding="utf-8")
    body = re.search(r"\n\s*body \{[^}]*\}", style).group(0)
    assert "font: 400 14px/1.5 var(--font-sans);" in body  # 14/21, as a ratio so headings never inherit 21 px
    # the element defaults sit under the components: "button { color: inherit }" must not beat a primary's white
    base = re.search(r"@layer base \{(.*?)\n\}", style, flags=re.S).group(1)
    assert "button { font: inherit; color: inherit; cursor: pointer; }" in base and "a { color:" in base
    assert "@layer base, design-system;" in (WEB / "ds-components.css").read_text(encoding="utf-8").split("\n")[2]


def test_the_old_variable_names_are_gone_and_every_used_one_is_declared() -> None:
    old = re.compile(r"--(?:r-(?:sm|md|lg|pill)|radius|side-w)\b(?!-)")
    declared = token_names()
    for path in sorted(WEB.glob("*.css")):
        css = path.read_text(encoding="utf-8")
        assert not old.search(css), path.name
        declared |= set(re.findall(r"--([\w-]+)\s*:", css))
    for name in ("style.css", "cockpit.css"):
        used = set(re.findall(r"var\(--([\w-]+)", (WEB / name).read_text(encoding="utf-8")))
        assert used <= declared, (name, sorted(used - declared))


# ---------- the Automation page in the components' terms ----------

STATES = """
const devices = [
  { id: "cfan", control_state: "ready" },
  { id: "light", control_state: "paused", paused_until: 1 },
  { id: "dfan", control_state: "off" },
  { id: "humidifier", control_state: "needs_setup", reason_key: "ap.health.hum_implausible" },
  { id: "pump", control_state: "needs_setup", reason_key: "ap.hold.ports" },
];
const states = devices.map((d) => ({ id: d.id, ...apDeviceState(d) }));
const order = states.slice().sort((a, b) => a.rank - b.rank).map((s) => s.id);
const at = 1000000;
const log = [
  { kind: "manual", device: "light", source: "app", at },
  { kind: "manual", device: "light", source: "app", at: at - 120 },
  { kind: "manual", device: "light", source: "app", at: at - 300 },
  { kind: "manual", device: "light", source: "app", at: at - 360 },
  { kind: "manual", device: "light", source: "app", at: at - 2000 },
  { kind: "manual", device: "dfan", source: "app", at: at - 2010 },
  { kind: "manual", device: "dfan", at: at - 2015 },
  { kind: "step", device: "dfan", at: at - 2020 },
  { kind: "step", device: "dfan", at: at - 2030 },
];
const groups = apGroup(log).map((g) => [g.members.length, apDot(g.members[0])]);
console.log(JSON.stringify({ states, order, groups }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_device_cards_take_the_five_states_and_repeats_become_one_entry() -> None:
    src = (WEB / "app.js").read_text(encoding="utf-8")
    parts = [js_const(src, name) for name in ("AP_CHIP", "AP_DEVICE_TEXT", "AP_STOPPED", "AP_DOT")]
    parts += [js_function(src, name) for name in ("apPaused", "exhaustHolding", "apDeviceState", "apDot", "apGroup")]
    script = ("const t = (key, p) => p ? `${key} ${JSON.stringify(p)}` : key;\nconst clock = () => '9:37';\n"
              + "\n".join(parts) + STATES)
    run = subprocess.run([NODE or "node", "-"], input=script, capture_output=True, text=True, encoding="utf-8",
                         timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    chips = {s["id"]: (s["chip"], s["text"]) for s in out["states"]}
    assert chips == {"pump": ("warn", "pilot.device.needs"), "humidifier": ("bad", "pilot.device.stopped"),
                     "light": ("info", 'pilot.device.pausedUntil {"time":"9:37"}'),
                     "cfan": ("ok", "pilot.device.ready"), "dfan": ("", "pilot.device.off")}
    # needs you, stopped, paused, monitoring, off
    assert out["order"] == ["pump", "humidifier", "light", "cfan", "dfan"]
    # four app changes of the light within ten minutes are one entry; a fifth 27 minutes earlier is its own; another
    # device, a change in the cockpit and the autopilot's own steps never join a group
    assert out["groups"] == [[4, "hold"], [1, "hold"], [1, "hold"], [1, "manual"], [1, "step"], [1, "step"]]


COUNTS = """
const stopped = { readiness: { ready: 3, needs_setup: 1, waiting: 0, state: "attention" },
  devices: [{ id: "light", control_state: "ready" }, { id: "dfan", control_state: "ready" },
    { id: "cfan", control_state: "ready" }],
  humidifier: { control_state: "needs_setup", reason_key: "ap.health.hum_implausible" } };
const both = { readiness: { ready: 1, needs_setup: 2, waiting: 1, state: "attention" },
  devices: [{ id: "light", control_state: "needs_setup", reason_key: "ap.hold.lamp" },
    { id: "dfan", control_state: "waiting", value: 40, reason_key: "ap.hold.vpd_heat" }],
  humidifier: { control_state: "needs_setup", reason_key: "ap.health.hum_implausible" } };
console.log(JSON.stringify({ stopped: apCountChips(stopped, 0), both: apCountChips(both, 1) }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_health_line_counts_a_stopped_device_as_its_card_does() -> None:
    """02.11.2026 live: the humidifier's card said "Gestoppt" while the line beside it said "1 braucht dich"."""
    src = (WEB / "app.js").read_text(encoding="utf-8")
    parts = [js_const(src, name) for name in ("AP_STOPPED", "AP_COUNT_CHIP")] + [js_function(src, "apCountChips")]
    run = subprocess.run([NODE or "node", "-"], input="\n".join(parts) + COUNTS, capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["stopped"] == [["ok", "pilot.count.ready", 3], ["bad", "pilot.count.stopped", 1]]
    assert out["both"] == [["ok", "pilot.count.ready", 1], ["warn", "pilot.count.needs", 1],
                           ["bad", "pilot.count.stopped", 1], ["info", "pilot.count.holding", 1]]
    i18n = (WEB / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"pilot.count.stopped.one"') == 2 and i18n.count('"pilot.count.stopped.other"') == 2


def test_the_automation_page_is_drawn_with_the_components() -> None:
    js = (WEB / "app.js").read_text(encoding="utf-8")
    cockpit = (WEB / "cockpit.js").read_text(encoding="utf-8")
    thirst = (WEB / "thirst.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    page = html[html.index('id="page-automation"'):html.index('id="page-devices"')]
    # the chip is the status, in the card's header row; the 11 px coloured word is gone
    assert "ap-device-status" not in js and "ap-device-status" not in (WEB / "style.css").read_text(encoding="utf-8")
    card = js_function(js, "apDeviceCard")
    assert '"device-h"' in card and '"device compact"' in card and "dsChip(state.chip, state.text)" in card
    # the lamp distance is the Light card's primary button, no longer a step in the folded setup checks
    assert "apLampButton(box)" in card and "autopilot_lamp" not in js_function(js, "apNeeds")
    assert 'btn.dataset.apStep = "lamp";' in js_function(js, "apLampButton")
    # Off / Observe / Automatic: the filled segment (never quiet) with a note that says what the mode does
    assert '"seg block ap-mode"' in js_function(js, "apModeSwitch") and '"seg-note"' in js_function(js, "apBox")
    # no bare <details>: every one on the page is a Disclosure, chevron, title and a line naming what is inside
    tags = re.findall(r"<details[^>]*>", page)
    assert len(tags) == 5 and all('class="disclosure' in tag for tag in tags), tags  # thirst.js adds a sixth
    for summary in re.findall(r"<summary>(.*?)</summary>", page, flags=re.S):
        assert 'class="chev"' in summary and summary.count("data-i18n=") == 2, summary
    assert "apFold(" in js and "function apDetails(" not in js and 'el("details", "disclosure th-gloss")' in thirst
    # one Banner per card, at the top, with an icon and a bold lead
    for host in ('id="watering-warning"', 'id="tank-warning"'):
        tag = re.search(r"<(\w+) class=\"banner[^\"]*\" " + host, page)
        assert tag and tag.group(1) == "div", host
    assert 'dsBanner($("watering-warning")' in cockpit and 'dsBanner($("tank-warning")' in cockpit
    assert "dsBanner(built.pumpOn" in thirst and "body.append(b.pumpOn, b.setup" in thirst
    # the log says where a manual change came from, so an app change shows as a hold
    assert '"source": e.get("source")' in (ROOT / "vgrow" / "autopilot.py").read_text(encoding="utf-8")


LOG_ITEM = """
class El {
  constructor(tag) { this.tag = tag; this.className = ""; this.textContent = ""; this.kids = []; this.dataset = {}; }
  append(...xs) { for (const x of xs) this.kids.push(typeof x === "string" ? { tag: "#text", textContent: x } : x); }
  setAttribute() {}
}
const document = { createElement: (tag) => new El(tag) };
const flat = (el) => [el.tag + (el.className ? "." + el.className.split(" ").join(".") : ""),
  ...(el.kids || []).flatMap(flat)];
const at = 1000000;
const one = { kind: "manual", device: "light", source: "app", at, text: "Light 87% to 89%" };
const older = { ...one, at: at - 300, text: "Light 85% to 87%" };
console.log(JSON.stringify({ single: flat(apLogItem({ id: "b" }, one)),
  group: flat(apLogItem({ id: "b" }, one, [one, older])) }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_log_entry_draws_alone_and_as_a_group() -> None:
    """cockpit.js's activity list calls apLogItem(box, e) with one entry; the Automation log passes a group's
    members as the third argument (found by the PR's screenshot run: the list had thrown on the group shape)."""
    src = (WEB / "app.js").read_text(encoding="utf-8")
    assert "apLogItem(box, e)" in (WEB / "cockpit.js").read_text(encoding="utf-8")
    parts = [js_const(src, name) for name in ("AP_KIND", "AP_STATUS", "AP_DOT")]
    parts += [js_function(src, name) for name in ("apEl", "dsChip", "apDot", "apLogItem")]
    script = ("const t = (key) => key;\nconst clock = () => '9:37';\nconst when = () => '9:37';\n"
              "const keepUnits = (x) => String(x);\n" + "\n".join(parts) + LOG_ITEM)
    run = subprocess.run([NODE or "node", "-"], input=script, capture_output=True, text=True, encoding="utf-8",
                         timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["single"][0] == "li.ap-manual.hold" and "details.ap-grp" not in out["single"]
    assert out["group"][0] == "li.ap-manual.hold" and "details.ap-grp" in out["group"] and "span.status" in out["group"]
