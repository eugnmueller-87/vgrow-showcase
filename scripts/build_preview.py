"""Builds a single-file preview of the page with example values, for a claude.ai artifact.

The preview cannot reach the box: it swaps the server API for scripts/preview_mock.js.
Both languages work: web/i18n.js is inlined, the preview's own texts are added to it here.
Hank comes along: web/hank-i18n.js, hank-scenes.js, hank.js, hank.css and hank-scenes.css are inlined after the
app, and preview_mock.js answers his /api/hank/* paths with sample answers.
The news list too: web/news.js and news.css, and preview_mock.js answers /api/news with the demo's fixed list
(vgrow/server.py demo_news(), made-up sources, example.org links), built here in both languages.
Private data stays out: the strain lexicon (kartei/strains/lexikon.json, private_only) is never part of a preview.
The preview carries no wiki, and private_markers() is checked against the finished page anyway: a key or a source
address of a private_only file in it stops the build (tests/test_strain_lexicon.py builds it and checks).
Usage: python scripts/build_preview.py <output.html>
"""

from __future__ import annotations

import json
import re
import sys
from urllib.parse import quote
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"
UPLOT = "https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.iife.min.js"  # cdnjs has no uPlot (404)
HANK_JS = ("hank-i18n.js", "hank-scenes.js", "hank.js")  # in the order index.html loads them
GUEST_CSS = ("hank.css", "hank-scenes.css", "news.css", "cockpit.css", "protocol.css")

# Texts only the preview shows, in the format of web/i18n.js.
PREVIEW_TEXT = {
    "de": {
        "preview.banner": "Vorschau mit Beispielwerten. Die echte Steuerung läuft über den Server auf deinem MacBook.",
        "preview.camNote": "Vorschau: Kamerabild, Live und Aufnahme gibt es nur im echten Cockpit, über den Link, "
                           "den der Server auf dem MacBook ausgibt.",
        "preview.camLine1": "Kamerabild nur über den Server",
        "preview.camLine2": "auf dem MacBook, im Heimnetz oder über Tailscale",
        "preview.noRecording": "In der Vorschau gibt es keine Aufnahme. Bitte das echte Cockpit öffnen",
        "preview.noPush": "In der Vorschau geht kein Push raus. Am echten Server funktioniert der Knopf",
    },
    "en": {
        "preview.banner": "Preview with sample values. The real controls run through the server on your MacBook.",
        "preview.camNote": "Preview: the camera picture, live view and recording only exist in the real cockpit, "
                           "through the link the server on the MacBook prints.",
        "preview.camLine1": "Camera picture only through the server",
        "preview.camLine2": "on the MacBook, at home or through Tailscale",
        "preview.noRecording": "The preview cannot record. Please open the real cockpit",
        "preview.noPush": "The preview sends no push. The button works on the real server",
    },
}


def cam_placeholder(lang: str) -> str:
    text = PREVIEW_TEXT[lang]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360">'
        '<rect width="640" height="360" fill="#14181f"/>'
        '<text x="320" y="170" fill="#c9d1dc" font-family="system-ui,sans-serif" font-size="22" text-anchor="middle">'
        f'{escape(text["preview.camLine1"])}</text>'
        '<text x="320" y="204" fill="#98a2b3" font-family="system-ui,sans-serif" font-size="16" text-anchor="middle">'
        f'{escape(text["preview.camLine2"])}</text></svg>'
    )
    return "data:image/svg+xml," + quote(svg)


# The artifact viewer pads :root by the safe areas itself: no own status-bar strip, the chip row sticks at the inset.
PREVIEW_CSS = """
.top { padding-top: 10px; }
body::before { display: none; }
.chipnav { top: env(safe-area-inset-top, 0px); }
"""


def demo_news() -> dict[str, object]:
    """The demo's news list in both languages, from the server's own demo_news() (one source for both)."""
    sys.path.insert(0, str(REPO))
    from vgrow import i18n, server

    out = {}
    for lang in PREVIEW_TEXT:
        token = i18n.REQUEST_LANG.set(lang)
        try:
            out[lang] = {**server.demo_news(), "private": False, "demo": True}
        finally:
            i18n.REQUEST_LANG.reset(token)
    return out


def private_markers() -> list[str]:
    """What would show that private_only data reached the page: the strain lexicon's entry keys and source addresses
    (vgrow/strains.py; a public build loads it as empty, the private load is what must stay out)."""
    sys.path.insert(0, str(REPO))
    from vgrow import strains

    lex = strains.load()
    if not lex.private:
        return []
    out = [s.key for s in lex.strains if not s.own]
    for s in lex.strains:
        if s.own:
            continue
        for value in s.sources.values():
            url = value.get("url") if isinstance(value, dict) else None
            if isinstance(url, str) and url.startswith("https://"):
                out.append(url.split("://", 1)[1])
    return sorted(set(out))


def check_public(page: str) -> None:
    found = [m for m in private_markers() if m in page]
    if found:
        raise SystemExit(f"the preview carries private_only data: {found[:5]}")


def replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def main(out: Path) -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    body = html[html.index("<body>") + len("<body>"):html.index("</body>")]
    body = re.sub(r"<script[^>]*></script>\s*", "", body)
    body = replace_once(body, 'data-i18n="banner.demo"', 'data-i18n="preview.banner"')
    # Camera buttons do nothing real in the preview, so they must not pretend to (Eugen saw a fake "Gespeichert").
    for button in ('id="cam-live"', 'id="cam-still"', 'id="cam-rec"'):
        body = replace_once(body, button, f"{button} disabled")
    body = replace_once(body, '<p class="hint" id="cam-hint"',
                        '<p class="note" data-i18n="preview.camNote"></p>\n        <p class="hint" id="cam-hint"')
    app = (WEB / "app.js").read_text(encoding="utf-8")
    start = app.index("async function api(path, body) {")
    end = app.index("\n}\n", start) + 3
    app = app[:start] + (REPO / "scripts" / "preview_mock.js").read_text(encoding="utf-8") + app[end:]
    # The camera picture only exists on the home network: the preview shows a placeholder instead
    # (the whole expression, so no ?lang= lands on the data: URL).
    for live_url in ("cameraUrl(`/camera/snapshot.jpg?t=${Date.now()}`, view)", "cameraUrl(`/camera/live.mjpg?t=${Date.now()}`, view)"):
        app = replace_once(app, live_url, "CAM_PLACEHOLDER[LANG]")
    app = replace_once(app, "const CAMERA_LIVE_DEFAULT = true;", "const CAMERA_LIVE_DEFAULT = false;")  # no fake LIVE in the preview
    texts = (WEB / "i18n.js").read_text(encoding="utf-8")
    extra = "".join(f"Object.assign(window.I18N.{lang}, {json.dumps(PREVIEW_TEXT[lang], ensure_ascii=False)});\n"
                    for lang in PREVIEW_TEXT)
    placeholders = json.dumps({lang: cam_placeholder(lang) for lang in PREVIEW_TEXT})
    news = json.dumps(demo_news(), ensure_ascii=False)
    # Hank after the app, in the order index.html loads them: his texts, his scenes, then his script; the news last.
    hank = "\n".join((WEB / name).read_text(encoding="utf-8") for name in HANK_JS)
    news_js = (WEB / "news.js").read_text(encoding="utf-8")
    # app.js's own "use strict" only counts at the very top of the script, which is the texts here.
    cockpit = (WEB / "cockpit.js").read_text(encoding="utf-8")
    light_view = (WEB / "light-view.js").read_text(encoding="utf-8")
    protocol_view = (WEB / "protocol.js").read_text(encoding="utf-8")
    script = (f'"use strict";\n{texts}\n{extra}const CAM_PLACEHOLDER = {placeholders};\nconst PREVIEW_NEWS = {news};\n'
              f"{app}\n{cockpit}\n{light_view}\n{protocol_view}\n{hank}\n{news_js}")
    css = (WEB / "vendor" / "uPlot.min.css").read_text(encoding="utf-8") + "\n"
    css += (REPO / "design-system" / "tokens.css").read_text(encoding="utf-8") + "\n"  # the colours, before style.css
    css += (WEB / "ds-components.css").read_text(encoding="utf-8") + "\n"  # the adopted components, as index.html
    css += (WEB / "style.css").read_text(encoding="utf-8")
    css += "".join("\n" + (WEB / name).read_text(encoding="utf-8") for name in GUEST_CSS)
    css += PREVIEW_CSS
    page = (
        "<title>VGrow Cockpit (inoffiziell)</title>\n"
        f"<style>\n{css}\n</style>\n"
        f"{body.strip()}\n"
        f'<script src="{UPLOT}"></script>\n'
        f"<script>\n{script}\n</script>\n"
    )
    check_public(page)
    out.write_bytes(page.encode("utf-8"))  # LF endings on every platform
    print(f"{out} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
