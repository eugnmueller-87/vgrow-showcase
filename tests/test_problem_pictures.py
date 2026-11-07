"""Pictures, tile lines and groups of the 27 problem cards (owner, 23.10.2026: "whats here", the Cannabis category was
27 green triangles in one long list). Every card shows a photo from Wikimedia Commons (credit, allowed licence, saved
once in web/img/wiki/) or one of the wiki's own drawings (alt text in both languages); every tile has a short line in
both languages; the Cannabis category is headed by groups in a fixed order."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import test_wiki as tw
from test_i18n import _numbers
from test_web_i18n import dictionary
from vgrow import wiki

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "web" / "img" / "wiki"
CARDS = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "kartei" / "problems").glob("*.json"))}
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
COMMONS = "https://commons.wikimedia.org/wiki/File:"
# plants, leaves, pests and damage only; word starts, so "Grauschimmel" (grey mould) is no hit for "rauch"
NOT_SHOWN = re.compile(r"THC|CBD|Wirkung|effect|\bRausch|\bjoint|smok|\brauch", re.I)
TILE_MAX = 60  # three lines of a 150 px tile on a phone


def credits() -> dict[str, dict[str, Any]]:
    return {e["file"]: e for e in json.loads((IMG / "credits.json").read_text(encoding="utf-8"))["images"]}


def alt_keys() -> dict[str, str]:
    src = tw.wiki_js()
    return dict(re.findall(r'"([a-z0-9-]+)": "(wiki\.art\.[a-z0-9_]+)"', src[src.index("const ART_ALT"):]))


def test_every_problem_card_has_a_picture_and_never_the_triangle() -> None:
    assert len(CARDS) == 27
    panels, alts, texts = tw.panel_ids(), alt_keys(), dictionary()
    photos = drawings = 0
    for stem, card in CARDS.items():
        raw = card["image"]
        for lang in ("de", "en"):
            view = wiki.card_view(wiki.find("problems", stem), lang, ["cannabis"])["image"]
            assert view and (view.get("photo") or view.get("drawing")), (stem, lang)  # a bad field would fall back
        if "file" in raw:
            photos += 1
            assert raw["alt"].strip() and raw["alt_en"].strip() and raw["alt"] != raw["alt_en"], stem
        else:
            drawings += 1
            fig = f"{raw['drawing']}-1"
            assert fig in panels and fig in alts, stem  # the panel exists and has its alt text
            for lang in ("de", "en"):
                assert texts[lang][alts[fig]].strip() and EM_DASH not in texts[lang][alts[fig]], (stem, lang)
            assert texts["de"][alts[fig]] != texts["en"][alts[fig]], stem
    assert (photos, drawings) == (18, 9)


def test_every_photo_has_its_credit_an_allowed_licence_and_says_when_it_shows_another_plant() -> None:
    entries = credits()
    for stem, card in CARDS.items():
        raw = card["image"]
        if "file" not in raw:
            continue
        e = entries[raw["file"]]
        assert f"problems/{stem}" in e["cards"], stem
        c = raw["credit"]
        who = {"author_url": e["author_url"]} if "author_url" in e else {}
        assert c == {"work": e["work"], "author": e["author"], **who, "license": e["license"],
                     "license_url": e["license_url"], "page_url": e["file_page"]}, stem
        assert wiki.LICENSE_RE.match(c["license"]) and c["author"].strip() and c["work"].startswith("File:"), stem
        assert c["page_url"].startswith(COMMONS) and c["license_url"].startswith("https://"), stem
        assert c["license_url"].startswith(("https://creativecommons.org/", "https://commons.wikimedia.org/wiki/Template:PD")), stem
        text = " ".join(str(raw.get(k, "")) for k in ("alt", "alt_en", "note", "note_en"))
        assert not NOT_SHOWN.search(text) and EM_DASH not in text, stem
        if raw.get("compare") is True:  # a comparison picture names the other plant in both languages
            assert raw["note"].strip() and raw["note_en"].strip() and raw["note"] != raw["note_en"], stem
            assert re.search(r"(?:kein|nicht auf) Hanf", raw["note"]) and "not cannabis" in raw["note_en"], stem
        if "note" in raw:
            assert _numbers(raw["note"]) == _numbers(raw["note_en"]), stem
    # a ported licence is still CC BY; anything without a free licence stays out
    assert wiki.LICENSE_RE.match("CC BY 3.0 US") and wiki.LICENSE_RE.match("CC BY-SA 2.5")
    for bad in ("CC BY-NC 4.0", "CC BY-ND 3.0", "All rights reserved", "CC BY 3.0 USA", "GFDL"):
        assert not wiki.LICENSE_RE.match(bad), bad


def test_problem_photos_are_local_small_and_inside_the_budget() -> None:
    for stem, card in CARDS.items():
        raw = card["image"]
        if "file" not in raw:
            continue
        for name in (raw["file"], raw["thumb"]):
            path = IMG / name
            assert path.suffix == ".webp" and path.stat().st_size <= 256_000, (stem, name)
        assert tw.webp_size(IMG / raw["file"]) == (raw["w"], raw["h"]) and max(raw["w"], raw["h"]) <= 1200, stem
        assert tw.webp_size(IMG / raw["thumb"]) == (480, 360), stem
        for lang in ("de", "en"):
            photo = wiki.card_view(wiki.find("problems", stem), lang, ["cannabis"])["image"]["photo"]
            assert photo["src"] == "/static/img/wiki/" + raw["file"], stem
            assert photo["thumb"] == "/static/img/wiki/" + raw["thumb"], stem
    total = sum(p.stat().st_size for p in IMG.iterdir() if p.is_file())
    assert total < 8 * 1024 * 1024, total
    # no picture or drawing fetched from the internet at runtime: no link in the cards' image fields beyond the
    # credit's own pages, and none in the drawings
    for stem, card in CARDS.items():
        raw = {k: v for k, v in card["image"].items() if k != "credit"}
        assert "http" not in json.dumps(raw), stem
    assert "http" not in tw.DRAWINGS.read_text(encoding="utf-8").replace('xmlns="http://www.w3.org/2000/svg"', "")


def test_the_new_drawings_use_only_what_the_page_copies() -> None:
    """wiki.js rebuilds a panel element by element through SVG_TAGS and SVG_ATTRS: a panel that relied on anything
    else (fill, stroke, defs, use, a clip path) would lose it on the page. Colour comes only from wa-* classes, and
    every class a problem panel uses is styled in wiki.css and in the drawings file's own fallback."""
    svg = tw.DRAWINGS.read_text(encoding="utf-8")
    css = (tw.WEB / "wiki.css").read_text(encoding="utf-8")
    style = svg[svg.index("<style>"):svg.index("</style>")]
    src = tw.wiki_js()
    tags = set(re.findall(r'"([a-z]+)"', src[src.index("const SVG_TAGS"):src.index("const SVG_ATTRS")]))
    attrs = set(re.findall(r'"([a-z-]+)"', src[src.index("const SVG_ATTRS"):src.index("const OWN_SRC")]))
    for stem, card in CARDS.items():
        if "drawing" not in card["image"]:
            continue
        fig = f"{card['image']['drawing']}-1"
        body = re.search(rf'<g id="{fig}">(.*?)</g></g>\n', svg).group(1)
        assert len(body) <= 3500, (fig, len(body))
        assert set(re.findall(r"<([a-z]+)", body)) <= tags, fig
        assert set(re.findall(r'\s([a-z-]+)="', body)) <= attrs, fig
        for cls in {c for v in re.findall(r'class="([^"]+)"', body) for c in v.split() if c.startswith("wa-")}:
            assert f".{cls}" in css and f".{cls}" in style, (fig, cls)


def test_every_tile_has_a_short_line_in_both_languages() -> None:
    for stem, card in CARDS.items():
        de, en = card["tile"], card["tile_en"]
        assert de.strip() and en.strip() and de != en, stem
        assert len(de) <= TILE_MAX and len(en) <= TILE_MAX, stem
        assert EM_DASH not in de + en and not wiki.CONSUMPTION_RE.search(de + en), stem
        assert _numbers(de) == _numbers(en) and not re.search(r"\bP\d", de + en), stem
    for lang in ("de", "en"):
        tiles = {c["key"]: c for cat in wiki.index_view(lang, ["cannabis"])["categories"] for c in cat["cards"]
                 if c["folder"] == "problems"}
        for stem, card in CARDS.items():
            assert tiles[stem]["blurb"] == card["tile_en" if lang == "en" else "tile"], (stem, lang)


def test_the_cannabis_category_is_grouped_in_a_fixed_order() -> None:
    order = ("stages_strains", "naehrstoffe", "schaedlinge", "krankheiten", "stress")
    assert wiki.GROUP_ORDER == order
    for lang in ("de", "en"):
        cat = next(c for c in wiki.index_view(lang, ["cannabis"])["categories"] if c["id"] == "cannabis")
        groups = [c["group"] for c in cat["cards"]]
        runs = [g for i, g in enumerate(groups) if i == 0 or groups[i - 1] != g]
        assert runs == list(order), runs  # each group once, in this order, nothing ungrouped
        counts = {g: groups.count(g) for g in order}
        assert counts == {"stages_strains": counts["stages_strains"], "naehrstoffe": 10, "schaedlinge": 8,
                          "krankheiten": 1, "stress": 8}, counts
        assert {c["folder"] for c in cat["cards"] if c["group"] == "stages_strains"} == {"stages", "strains"}
        for g in order[1:]:  # alphabetical inside a group
            labels = [wiki.fold(c["label"]) for c in cat["cards"] if c["group"] == g]
            assert labels == sorted(labels), (lang, g)
    # other categories keep one plain list: no card there carries a group
    for cat in wiki.index_view("de", ["cannabis"])["categories"]:
        if cat["id"] not in ("cannabis", "health"):
            assert not any(c["group"] for c in cat["cards"]), cat["id"]
    # the page names each group and its hint in both languages, and knows the same groups as the server
    src = tw.wiki_js()
    table = src[src.index("const GROUP = {"):src.index("const GROUP_HINT")]
    assert re.findall(r"(\w+): \"wiki\.group\.\w+\"", table) == list(order)
    texts = dictionary()
    for g in order:
        for lang in ("de", "en"):
            assert texts[lang][f"wiki.group.{g}"].strip(), (g, lang)
            if g != "stages_strains":
                assert texts[lang][f"wiki.group.{g}.hint"].strip(), (g, lang)
    assert texts["de"]["wiki.group.naehrstoffe"] == "Nährstoffprobleme"
    assert texts["en"]["wiki.group.schaedlinge"] == "Pests"


def test_the_switch_off_hides_the_problem_photos_with_their_cards() -> None:
    off = wiki.index_view("de", [])
    assert not any(c["folder"] == "problems" for cat in off["categories"] for c in cat["cards"])
    cards = {c["key"] for cat in off["categories"] for c in cat["cards"]}
    assert "spider_mite" not in cards and "aphid" not in cards


def test_review_fixes_of_23_10_2026() -> None:
    """The review of the problem pictures: a picture never contradicts its tile without a line saying so, a tile line
    belongs to the species in title and photo, a user name as author links to the profile, the root aphid tile has a
    short title (the full one with its caveat stands on the card)."""
    aphid = CARDS["root_aphid"]["image"]
    assert "nicht an der Wurzel" in aphid["note"] and "not on the roots" in aphid["note_en"]
    assert aphid["compare"] is False  # it is cannabis, only the place differs
    for lang, word in (("de", "Zum Bild"), ("en", "About the picture")):
        assert dictionary()[lang]["wiki.credit.note"].startswith(word), lang
    assert "Blüten" not in CARDS["caterpillar"]["tile"] and "flowers" not in CARDS["caterpillar"]["tile_en"]
    gnat = wiki.card_view(wiki.find("problems", "fungus_gnat"), "de", ["cannabis"])["image"]["photo"]["credit"]
    assert gnat["author"] == "User:Ich (Wikimedia Commons)"
    assert gnat["author_url"] == "https://commons.wikimedia.org/wiki/User:Ich"
    assert "author_url" not in wiki.card_view(wiki.find("problems", "whitefly"), "de", ["cannabis"])["image"]["photo"]["credit"]
    forged = {**CARDS["fungus_gnat"]["image"], "credit": {**CARDS["fungus_gnat"]["image"]["credit"],
                                                         "author_url": "https://example.com/User:Ich"}}
    assert "author_url" not in wiki.image_view(forged, "de", ["cannabis"])["photo"]["credit"]
    for name in ("botrytis-flower.webp", "magnesium-tomato-leaf.webp"):
        assert "migration=relicense" in credits()[name]["license_path"], name
    for lang, short, full in (("de", "Wurzelblattläuse", "Wurzelblattläuse (an Cannabis"),
                              ("en", "Root aphids", "Root aphids (on cannabis")):
        tiles = {c["key"]: c for cat in wiki.index_view(lang, ["cannabis"])["categories"] for c in cat["cards"]}
        assert tiles["root_aphid"]["label"] == short, lang
        assert wiki.card_view(wiki.find("problems", "root_aphid"), lang, ["cannabis"])["label"].startswith(full), lang
