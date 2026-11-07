"""Screenshots of the wiki for the layout check before acceptance (memory "seiten-headless-rendern-vor-abnahme").

Usage (PC with Edge): .venv/Scripts/python.exe scripts/wiki_screens.py <out-dir> [--lang de|en] [--port 9336]
                      [--server-port 8794] [--only list,crop] [--phone-width 320]

The demo cockpit with the real Hank and a fake Ollama (scripts/hank_screens.py LiveServer: made-up box, own state
folder in <out-dir>, never a VIVOSUN login), opened in headless Edge (phone 390x844 with touch, desktop 1280x800), in
light and dark: the sidebar with the entry, the picture index (tiles, the running strain, the glossary rows), a
method with its step drawings, a glossary term with its photo or drawing, a crop page, the stage card, a search with a typo, the
feeding chart, a card without "Hank fragen" (shown only once Hank can explain a card), a strain, curing, a deep
link opened by a reload, the list and the cloning card with the cannabis switch off, and the strain lexicon (its area,
the list filtered to "passt sicher", one entry, Hank's advice in the chat). Start it with USERPROFILE
pointing at an empty folder, so the demo finds no push settings, and HANK_EDGE_HOME at the real profile for Edge (see
hank_screens.py). Every picture also measures the page: wider than the screen (a sideways scroll on a phone) is
printed as "wide page" and listed again at the end. --phone-width 320 shoots a small phone (file prefix wiki320-).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hank_screens  # noqa: E402
import screenshots  # noqa: E402
from hank_screens import LiveServer, Step, shoot  # noqa: E402

# Sideways scroll: the page wider than the screen. A row that scrolls inside itself (the jump chips, a table) does
# not count; only the document does.
OVERFLOW_JS = """(() => {
  const w = document.documentElement.scrollWidth;
  return w <= innerWidth ? 'ok' : `${location.hash || '#'} at ${innerWidth}px: page ${w}px wide`;
})()"""
WIDE: list[str] = []
_plain_js = screenshots.Cdp.js


def _js(self: screenshots.Cdp, expr: str, *, wait: bool = False) -> object:
    value = _plain_js(self, expr, wait=wait)
    if expr == OVERFLOW_JS and value != "ok":
        print("wide page:", value)
        WIDE.append(str(value))
    return value


screenshots.Cdp.js = _js  # type: ignore[method-assign]


def _hash(target: str, ms: int = 1500) -> Step:
    return ("js", """(async () => {
  location.hash = %s;
  await new Promise((r) => setTimeout(r, %d));
  return true;
})()""" % (json.dumps(target), ms))


SIDEBAR_JS = """(async () => {
  const row = document.getElementById('chip-row');
  if (row) row.scrollLeft = row.scrollWidth;
  await new Promise((r) => setTimeout(r, 400));
  return !document.querySelector('[data-wiki-open]').hidden;
})()"""

SEARCH_JS = """(async (q) => {
  const box = document.querySelector('.wiki-q');
  if (!box) return false;
  box.value = q;
  box.dispatchEvent(new Event('input', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 1500));
  return true;
})(%s)"""

HANK_JS = """(async () => {
  const b = document.querySelector('.wiki-hank');
  if (b) b.click();
  await new Promise((r) => setTimeout(r, 2500));
  return !!b;
})()"""

SCROLL_JS = """(async (sel) => {
  const el = document.querySelector(sel);
  if (el) el.scrollIntoView({ block: 'start' });
  await new Promise((r) => setTimeout(r, 400));
  return !!el;
})(%s)"""

# Opening a deep link from the address bar: the hash first, then a fresh load of the page (the "wait" step after it
# gives the reload its time; the preferences script runs again on the new document).
RELOAD_JS = """(async (target) => {
  location.hash = target;
  setTimeout(() => location.reload(), 50);
  return true;
})(%s)"""

# The cannabis switch through the page's own api(), so the cookie and the language header are the page's.
PACKAGE_JS = """(async (on) => {
  await api('/api/wiki/packages', { package: 'cannabis', on });
  location.hash = '#wiki-x';
  await new Promise((r) => setTimeout(r, 200));
  location.hash = '#wiki';
  await new Promise((r) => setTimeout(r, 1500));
  return true;
})(%s)"""

TYPO = {"de": "Basilkum", "en": "basl"}

# The strain lexicon (#wiki/sorten): the box filter "passt sicher" tapped, through the page's own chip.
SURE_JS = """(async () => {
  const chip = [...document.querySelectorAll('.wiki-fchip')].find((b) => b.textContent.startsWith(t('wiki.strainFit.sure')));
  if (chip) chip.click();
  await new Promise((r) => setTimeout(r, 600));
  return !!chip;
})()"""

# Hank's strain advice in the chat: the question, his two short questions answered, then the start of his answer.
ADVICE = {"de": ["Welche Sorte passt in meine Box?", "Autoflower", "Erster Grow"],
          "en": ["What strain fits my grow box?", "Autoflower", "First grow"]}
ADVICE_END_JS = """(() => {
  const bots = document.querySelectorAll('.hk-msg.hk-bot');
  const last = bots[bots.length - 1];
  const log = document.querySelector('.hk-log');
  if (last && log) log.scrollTop = Math.max(0, last.offsetTop - log.offsetTop - 8);
  return !!last;
})()"""
ENTRY = "#wiki/strains/barney-s-farm-tangerine-dream-auto"


def states(lang: str) -> list[tuple[str, dict, list[Step]]]:
    plan = _states(lang)
    return [(name, pin, [*steps, ("js", OVERFLOW_JS)]) for name, pin, steps in plan]


def _states(lang: str) -> list[tuple[str, dict, list[Step]]]:
    return [
        ("sidebar", {}, [("js", SIDEBAR_JS)]),
        ("list", {}, [_hash("#wiki")]),
        ("list-methods", {}, [_hash("#wiki"), ("js", SCROLL_JS % json.dumps("#wiki-cat-methoden"))]),
        ("glossary", {}, [_hash("#wiki"), ("js", SCROLL_JS % json.dumps("#wiki-cat-glossar"))]),
        ("method", {}, [_hash("#wiki/methods/topping", 2500)]),
        ("method-steps", {}, [_hash("#wiki/methods/topping", 2500), ("js", SCROLL_JS % json.dumps(".wiki-numsteps"))]),
        ("term", {}, [_hash("#wiki/glossary/vpd")]),
        ("term-drawing", {}, [_hash("#wiki/glossary/photoperiod", 2500)]),
        ("term-compare", {}, [_hash("#wiki/glossary/dli", 2500)]),
        ("crop", {}, [_hash("#wiki/crops/basil")]),
        ("crop-stages", {}, [_hash("#wiki/crops/basil"), ("js", SCROLL_JS % json.dumps(".wiki-steps"))]),
        ("stage", {}, [_hash("#wiki/stages/autoflower")]),
        ("search", {}, [_hash("#wiki"), ("js", SEARCH_JS % json.dumps(TYPO[lang]))]),
        ("nutrient", {}, [_hash("#wiki/nutrients/biobizz-2026"), ("js", SCROLL_JS % json.dumps(".wiki-chart"))]),
        ("hank", {}, [_hash("#wiki/crops/chives"), ("js", HANK_JS)]),
        ("strain", {}, [_hash("#wiki/strains/ice-cream-cake-auto")]),
        ("curing", {}, [_hash("#wiki/processes/curing")]),
        ("reload", {}, [("js", RELOAD_JS % json.dumps("#wiki/crops/basil")), ("wait", 3.0)]),
        # switched off: the list without the cannabis cards; "after" switches it on again for the next picture
        ("off", {}, [_hash("#wiki"), ("js", PACKAGE_JS % "false"), ("after", PACKAGE_JS % "true")]),
        ("off-cloning", {}, [_hash("#wiki"), ("js", PACKAGE_JS % "false"), _hash("#wiki/processes/cloning"),
                             ("after", PACKAGE_JS % "true")]),
        # the strain lexicon (23.10.2026): the area in the index, the list filtered to "passt sicher", one entry,
        # and Hank's advice in the chat after his two short questions
        ("sorten-area", {}, [_hash("#wiki"), ("js", SCROLL_JS % json.dumps("#wiki-cat-sorten"))]),
        ("sorten-sure", {}, [_hash("#wiki/sorten", 2000), ("js", SURE_JS)]),
        ("sorten-entry", {}, [_hash(ENTRY, 2500)]),
        ("sorten-hank", {}, [("js", hank_screens.OPEN_JS),
                             *(hank_screens._ask(q, 1500) for q in ADVICE[lang]), ("js", ADVICE_END_JS)]),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--lang", choices=("de", "en"), default="de")
    parser.add_argument("--port", type=int, default=9336, help="DevTools port (default 9336)")
    parser.add_argument("--server-port", type=int, default=8794,
                        help="the cockpit's port; the fake Ollama takes the next one (default 8794)")
    parser.add_argument("--only", default="", help="comma-separated state names, e.g. list,crop")
    parser.add_argument("--phone-width", type=int, default=0, help="a smaller phone, e.g. 320 (prefix wiki320-)")
    args = parser.parse_args()
    prefix = "wiki-"
    if args.phone_width:
        hank_screens.PHONE = (args.phone_width, 700)
        prefix = f"wiki{args.phone_width}-"
    only = {name.strip() for name in args.only.split(",") if name.strip()} or None
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with LiveServer(args.out_dir / "live-state", args.server_port) as live:
        print("live server at", f"http://127.0.0.1:{args.server_port}/")
        shoot(args.out_dir, args.lang, args.port, live.url, states(args.lang), prefix=prefix, only=only)
    if WIDE:
        print(f"{len(WIDE)} pictures wider than the screen:")
        for line in WIDE:
            print("  " + line)
    else:
        print("no sideways scroll")


if __name__ == "__main__":
    main()
