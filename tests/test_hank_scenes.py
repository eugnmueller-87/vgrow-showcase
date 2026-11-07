"""Hank's scenes (web/hank-scenes.js, hank-scenes.css): the drawing is Hank's own, the scene ids are the server's.

- The faces and the body come from mascot() in web/hank.js, path for path (the prototype only swapped literal white
  and the tongue colour for tokens of the same value): a Hank redrawn in only one of the two files fails here.
- Every scene id the server can send (nudges.scene) has a state on the stage, and every chore has its label key
  written out in hank.js (the texts live in hank-i18n.js, whose checks are in test_hank_web.py).
- Same file rules as hank.js: no markup from data, no em dash, no German in the code's strings.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import test_web_i18n as web
from vgrow.hank import nudges

WEB = Path(__file__).resolve().parent.parent / "web"


def scenes_js() -> str:
    return (WEB / "hank-scenes.js").read_text(encoding="utf-8")


def hank_js() -> str:
    return (WEB / "hank.js").read_text(encoding="utf-8")


def test_scenes_draw_hank_from_his_own_mascot() -> None:
    src = hank_js()
    body = src[src.index("function mascot(expr, opts)"):src.index("// Small line icons")]
    parts = [p for p in re.findall(r"'([^']*)'", body) if len(p) > 24]
    assert len(parts) > 20, len(parts)
    # the prototype's tokens, put back to the values they stand for
    scenes = scenes_js().replace("var(--hi)", "#fff").replace("var(--alert-ink)", "#fff")
    missing = [p for p in parts if p.replace('fill="#e8826b"', 'fill="var(--blush)"') not in scenes]
    assert not missing, missing


def test_every_server_scene_has_a_state() -> None:
    ids = set(re.findall(r'return "(\w+)"', inspect.getsource(nudges.scene)))
    assert ids == {"point", "sleep", "idle", "umbrella", "fan", "scarf"}, ids
    states = scenes_js()[scenes_js().index("const STATES = {"):]
    states = states[:states.index("};")]
    assert ids <= set(re.findall(r"^\s+(\w+): \{", states, re.M))


def test_every_chore_has_a_label_in_hank_js() -> None:
    chores = re.search(r"const CHORES = \[([^\]]*)\]", scenes_js())
    assert chores
    names = re.findall(r'"(\w+)"', chores.group(1))
    table = re.search(r"const CHORE_TEXT = \{([^}]*)\}", hank_js())
    assert table
    labels = dict(re.findall(r'(\w+): "(hank\.chore\.\w+)"', table.group(1)))
    assert names and set(names) == set(labels), (names, labels)
    keys = set(web._texts_file("hank-i18n.js", web.HANK_PREFIX)["de"])
    assert set(labels.values()) <= keys


def test_scenes_file_rules() -> None:
    src = scenes_js()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in src, banned
    assert src.count("parseFromString(") == 1  # the fixed stage of this file, nothing else
    found = [(line, text) for text, line in web.js_strings(src) if web.GERMAN.search(text) or web.EM_DASH in text]
    assert not found, found
    for name in ("hank-scenes.js", "hank-scenes.css"):
        raw = (WEB / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), name
        assert web.EM_DASH not in raw.decode("utf-8"), name
    code = "\n".join(line for line in src.splitlines() if not line.startswith("//")).strip()
    assert code.startswith("(function () {") and code.endswith("})();")


def test_scenes_load_before_hank() -> None:
    order = web.page().order
    assert order.index("/static/hank-i18n.js") < order.index("/static/hank-scenes.js") < order.index("/static/hank.js")


def test_every_static_file_of_the_page_reloads_open_pages() -> None:
    """A change to any file index.html loads moves web_version(), so an open page and a phone's cache follow it
    (review 22.10.2026: the two scene files were missing and a scene change reached no open page)."""
    from vgrow import server

    html = (WEB / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'(?:src|href)="/static/([^"?]+)"', html))
    assert {"hank-scenes.js", "hank-scenes.css"} <= used, used
    missing = sorted(used - set(server.PAGE_FILES))
    assert not missing, missing


def _function_body(src: str, name: str, indent: str = "  ") -> str:
    start = src.index(f"function {name}(")
    end = src.index(f"\n{indent}}}\n", start)
    return src[start:end]


def test_a_broken_stage_costs_the_scenes_not_the_poll() -> None:
    """syncScenes runs inside the state poll: a stage that throws drops the scenes and Hank keeps his still faces."""
    body = _function_body(hank_js(), "syncScenes")
    assert "try {" in body and "dropScenes()" in body
    drop = _function_body(hank_js(), "dropScenes")
    assert "ui.lightScene = ui.headScene = null" in drop and 'face("hello")' in drop and 'H.lightSig = ""' in drop


def test_scenes_follow_the_mode_without_waiting_for_the_poll() -> None:
    body = _function_body(hank_js(), "syncScenes")
    assert 'const id = H.mode === "on" ? sc.id : null;' in body
    assert "setState(sc.id" not in body


def test_the_light_line_scene_runs_short_and_then_holds_still() -> None:
    """Spec 9.2: scenes run "selten und kurz". The mini in the always visible light line plays its loop a few times
    and then holds the still frame; its idle pose does not bob."""
    src = hank_js()
    light = int(re.search(r"const LOOPS_LIGHT = (\d+);", src).group(1))
    head = int(re.search(r"const LOOPS_HEAD = (\d+);", src).group(1))
    assert 1 <= light <= 2 and light <= head <= 5, (light, head)
    assert 'mountScene(lightArt, "mini", null, LOOPS_LIGHT)' in src
    assert 'mountScene(headFace, "head", sceneLabel, LOOPS_HEAD)' in src
    loop = _function_body(scenes_js(), "startLoop", "    ")
    assert "iterations: LOOPS" in loop and "holdStill(cur)" in loop
    css = (WEB / "hank-scenes.css").read_text(encoding="utf-8")
    assert ".hk-sc-mini .rig.rest .j-head { animation: none; }" in css
