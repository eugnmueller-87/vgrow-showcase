"""The page's texts: web/i18n.js holds German and English, index.html and app.js only use its keys.

i18n.js is "window.I18N = " plus one strict JSON object {"de": {key: text}, "en": {...}} and ";".
Hank's texts sit in web/hank-i18n.js in the same format ("window.HANK_I18N = "); hank.js merges them into
window.I18N, so the checks below read both files as one dictionary and app.js plus hank.js as one script.
A {name} in a text is a value filled in by app.js; plurals are key.one / key.other.
app.js writes every key out in full (never assembled), so every key it names can be checked here.
Stdlib only.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

WEB = Path(__file__).resolve().parent.parent / "web"
PREFIX = "window.I18N = "
HANK_PREFIX = "window.HANK_I18N = "
PLACEHOLDER = re.compile(r"\{(\w+)\}")
KEY_LIKE = re.compile(r"[a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_]+)+")
# Words that are German and not English (umlauts are caught on their own): almost every German sentence has one.
GERMAN_WORDS = (
    "und", "nicht", "oder", "der", "die", "das", "mit", "für", "über", "ist", "ein", "eine", "einen", "kein",
    "keine", "noch", "nur", "bitte", "wird", "seit", "jetzt", "schon", "sind", "auf", "bei", "von", "vom", "zum",
    "zur", "aus", "nach", "wieder", "sich", "auch", "wenn", "als", "wie", "bis", "vor", "sie", "ich", "dein",
    "deine", "pausiert", "gesendet", "phase", "einschätzung", "abluft", "umluft", "pflanze", "kamera", "steuerung",
    "verlauf", "messwerte", "messung", "luftfeuchte", "temperatur", "wasser", "licht", "spektrum", "aufnahme",
    "stufe", "befeuchter", "luftbefeuchter", "zielbereich",
)
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(?:" + "|".join(GERMAN_WORDS) + r")\b", re.IGNORECASE)
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
# Language-neutral literals of app.js that the German scanner would flag. Keep it short and explain each.
ALLOWED_JS = {
    "kamera", "pflanze",  # ids of the section headings: anchors in links (#kamera), never shown as text
}
# Visible text index.html may keep: names, units, digits, symbols, language codes. "VGrow Cockpit (inoffiziell)"
# is the <title> until app.js sets document.title from meta.title in the page language.
ALLOWED_TEXT = {"VGrow Cockpit", "VGrow Cockpit (inoffiziell)", "GrowCam", "–", "°C", "%", "kPa", "−", "+", "3", "6",
                "DE", "EN"}
# The language switch names each language in that language, so it stays findable from the other one.
ALLOWED_ATTR = {"Deutsch", "English"}
TEXT_ATTRS = ("aria-label", "title", "alt", "placeholder")


# ---------- readers ----------

def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"duplicate keys in i18n.js: {dupes}"
    return dict(pairs)


def _texts_file(name: str, prefix: str) -> dict[str, dict[str, str]]:
    text = (WEB / name).read_text(encoding="utf-8")
    assert text.startswith(prefix), f"{name} must start with {prefix!r}"
    data, end = json.JSONDecoder(object_pairs_hook=_no_duplicates).raw_decode(text, len(prefix))
    assert text[end:].strip() == ";", f"{name} holds exactly one JSON object, then ';'"
    return data


def dictionary() -> dict[str, dict[str, str]]:
    """i18n.js plus hank-i18n.js, as hank.js merges them in the page. A key in both would overwrite one."""
    data = _texts_file("i18n.js", PREFIX)
    hank = _texts_file("hank-i18n.js", HANK_PREFIX)
    assert set(hank) == set(data), set(hank) ^ set(data)
    for lang, texts in hank.items():
        both = sorted(set(texts) & set(data[lang]))
        assert not both, f"keys in i18n.js and hank-i18n.js: {both}"
        data[lang] = {**data[lang], **texts}
    return data


def js_strings(src: str) -> list[tuple[str, int]]:
    """Every string literal of JavaScript source with its line: quoted strings and the literal parts of
    template literals (their ${...} parts are code and scanned as code). Comments and regexes are skipped."""
    out: list[tuple[str, int]] = []
    keywords = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case",
                "do", "else", "yield", "await"}

    def line(i: int) -> int:
        return src.count("\n", 0, i) + 1

    def regex_end(i: int) -> int:  # i at the opening slash
        j, in_class = i + 1, False
        while src[j] != "/" or in_class:
            if src[j] == "\\":
                j += 1
            elif src[j] == "[":
                in_class = True
            elif src[j] == "]":
                in_class = False
            elif src[j] == "\n":
                raise AssertionError(f"unterminated regex at line {line(i)}")
            j += 1
        j += 1
        while j < len(src) and src[j].isalpha():  # flags
            j += 1
        return j

    def template(i: int) -> int:  # i after the opening backtick
        start, buf = i, []
        while src[i] != "`":
            if src[i] == "\\":
                buf.append(src[i:i + 2])
                i += 2
            elif src.startswith("${", i):
                out.append(("".join(buf), line(start)))
                buf, i = [], code(i + 2, nested=True)
                start = i
            else:
                buf.append(src[i])
                i += 1
        out.append(("".join(buf), line(start)))
        return i + 1

    def code(i: int, nested: bool) -> int:
        depth, prev = 0, ""
        while i < len(src):
            c = src[i]
            if c.isspace():
                i += 1
            elif src.startswith("//", i):
                end = src.find("\n", i)
                i = len(src) if end < 0 else end
            elif src.startswith("/*", i):
                i = src.index("*/", i) + 2
            elif c in "'\"":
                j, buf = i + 1, []
                while src[j] != c:
                    if src[j] == "\\":
                        buf.append(src[j:j + 2])
                        j += 2
                    else:
                        buf.append(src[j])
                        j += 1
                out.append(("".join(buf), line(i)))
                i, prev = j + 1, "str"
            elif c == "`":
                i, prev = template(i + 1), "str"
            elif c == "/":
                word = prev.isidentifier() and prev not in keywords
                if word or prev in (")", "]", "}", "str", "num"):
                    i, prev = i + 1, "/"
                else:
                    i, prev = regex_end(i), "str"
            elif c == "{":
                depth, i, prev = depth + 1, i + 1, "{"
            elif c == "}":
                if nested and depth == 0:
                    return i + 1
                depth, i, prev = depth - 1, i + 1, "}"
            elif c.isalnum() or c in "_$":
                j = i
                while j < len(src) and (src[j].isalnum() or src[j] in "_$."):
                    if src[j] == "." and not src[i].isdigit():
                        break
                    j += 1
                prev = "num" if c.isdigit() else src[i:j]
                i = j
            else:
                i, prev = i + 1, c
        return i

    code(0, nested=False)
    return out


class _Page(HTMLParser):
    """Keys, visible text and text attributes of index.html (script, style and svg contents left out)."""

    def __init__(self) -> None:
        super().__init__()
        self.keys: list[str] = []
        self.texts: list[str] = []
        self.attrs: list[tuple[str, str, str]] = []
        self.skip = 0
        self.order: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag == "script" and "src" in values:
            self.order.append(values["src"])
        if tag in ("script", "style", "svg"):
            self.skip += 1
        if "data-i18n" in values:
            self.keys.append(values["data-i18n"])
        translated = set()
        for pair in values.get("data-i18n-attr", "").split(";"):
            if pair.strip():
                attr, _, key = pair.partition(":")
                self.keys.append(key.strip())
                translated.add(attr.strip())
        for attr in TEXT_ATTRS:
            if values.get(attr) and attr not in translated:
                self.attrs.append((tag, attr, values[attr]))
        if tag == "meta" and values.get("name") == "description" and "content" not in translated:
            self.attrs.append((tag, "content", values.get("content", "")))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "svg"):
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.texts.append(data.strip())


def page() -> _Page:
    parser = _Page()
    parser.feed((WEB / "index.html").read_text(encoding="utf-8"))
    return parser


def app_js() -> str:
    """app.js, hank.js, news.js and wiki.js: each writes its keys out in full, all are checked as the page's script."""
    return "\n".join((WEB / name).read_text(encoding="utf-8") for name in ("app.js", "cockpit.js", "hank.js", "news.js", "wiki.js", "wiki-tools.js", "workspace.js", "community.js", "light-view.js",
                                                                       "thirst.js", "protocol.js", "water.js", "garden-logic.js", "garden.js"))  # demand and issue/action/result views, Hank's garden


def known(key: str, keys: set[str]) -> bool:
    return key in keys or (f"{key}.one" in keys and f"{key}.other" in keys)


# ---------- the dictionary ----------

def test_dictionary_is_strict_json_in_two_languages() -> None:
    data = dictionary()
    assert set(data) == {"de", "en"}
    for lang, texts in data.items():
        assert texts, lang
        for key, text in texts.items():
            assert KEY_LIKE.fullmatch(key), f"{lang}: odd key {key!r}"
            assert isinstance(text, str) and text.strip(), f"{lang}: {key} is empty"


def test_languages_have_the_same_keys_and_placeholders() -> None:
    data = dictionary()
    assert set(data["de"]) == set(data["en"]), set(data["de"]) ^ set(data["en"])
    for key, de in data["de"].items():
        assert set(PLACEHOLDER.findall(de)) == set(PLACEHOLDER.findall(data["en"][key])), key


def test_plurals_come_in_pairs() -> None:
    keys = set(dictionary()["de"])
    for key in keys:
        base, _, form = key.rpartition(".")
        if form in ("one", "other"):
            assert {f"{base}.one", f"{base}.other"} <= keys, key


def test_english_has_no_german_and_no_text_has_an_em_dash() -> None:
    data = dictionary()
    german = {key: text for key, text in data["en"].items() if GERMAN.search(text)}
    assert not german, german
    for lang, texts in data.items():
        dashed = [key for key, text in texts.items() if EM_DASH in text]
        assert not dashed, (lang, dashed)


# ---------- index.html ----------

def test_index_html_keys_exist() -> None:
    keys = set(dictionary()["de"])
    missing = [k for k in page().keys if k not in keys]
    assert not missing, missing


def test_index_html_ids_are_unique_and_the_feeding_v2_ids_exist() -> None:
    """A second id="plant-start" (the start field next to the day count's span) made the field write into the span:
    every id once, and the ids app.js uses for the switch, the age line, the next change and the start field."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) > 100 and sorted({i for i in ids if ids.count(i) > 1}) == []
    for needed in ("feed-switch", "feed-choice-note", "fb-feed-switch", "feed-age", "feed-age-enter",
                   "feed-nextchange", "plant-start-date", "plant-age-weeks", "plant-start-save", "plant-start-note",
                   "plant-start-preview", "fb-plant-start"):
        assert needed in ids, needed


def test_index_html_has_no_text_of_its_own() -> None:
    p = page()
    literal = [text for text in p.texts if text not in ALLOWED_TEXT]
    assert not literal, f"visible text without data-i18n: {literal}"
    attrs = [a for a in p.attrs if a[2] not in ALLOWED_ATTR]
    assert not attrs, f"attributes without data-i18n-attr: {attrs}"


def test_index_html_loads_the_texts_before_the_app() -> None:
    order = page().order
    assert order.index("/static/i18n.js") < order.index("/static/app.js")
    # Hank uses app.js's t(), api() and LANG, and his texts must be in place before his script runs.
    assert order.index("/static/app.js") < order.index("/static/hank-i18n.js") < order.index("/static/hank.js")


# ---------- app.js ----------

def test_app_js_keys_exist() -> None:
    keys = set(dictionary()["de"])
    src = app_js()
    for key in re.findall(r"\bt\(\s*\"([^\"]+)\"", src):
        assert key in keys, f't("{key}") has no text'
    for key in re.findall(r"\btn\(\s*\"([^\"]+)\"", src):
        assert {f"{key}.one", f"{key}.other"} <= keys, f'tn("{key}") needs .one and .other'
    # Keys also sit in lookup tables (statuses, actions): every UI literal that looks like a key must be one.
    # Shadow field names in Hank's command watcher are protocol data. The direct t()/tn() checks above
    # still reject using these names as UI keys.
    protocol_fields = {"light.level", "light.spec", "cFan.level", "dFan.level"}
    spaces = {k.split(".")[0] for k in keys}
    for text, line in js_strings(src):
        if KEY_LIKE.fullmatch(text) and text.split(".")[0] in spaces and text not in protocol_fields:
            assert known(text, keys), f"app.js line {line}: {text!r} is not in i18n.js"


def test_every_text_is_used() -> None:
    keys = set(dictionary()["de"])
    used = {text for text, _ in js_strings(app_js())} | set(page().keys)
    unused = sorted(k for k in keys if k not in used and k.rpartition(".")[0] not in used)
    assert not unused, f"texts nobody shows: {unused}"


def test_app_js_has_no_german_in_its_strings() -> None:
    found = [(line, text) for text, line in js_strings(app_js())
             if text not in ALLOWED_JS and (GERMAN.search(text) or EM_DASH in text)]
    assert not found, found


def test_the_scanner_sees_template_literals_and_skips_comments_and_regexes() -> None:
    src = ('// Phase und Umluft im Kommentar\n'
           'const a = `Wert ${x ? "über" : `nested ${y}`} Rest`; /* die Box */\n'
           'const r = s.replace(/["\']/g, "x") / 2;\n'
           'const b = "ok"; const c = n / 2 / 3; return /a"b/.test(s) ? \'Abluft\' : "";\n')
    texts = [text for text, _ in js_strings(src)]
    assert texts == ["Wert ", "über", "nested ", "", " Rest", "x", "ok", "Abluft", ""]
    found = [text for text in texts if GERMAN.search(text)]
    assert found == ["über", "Abluft"]
