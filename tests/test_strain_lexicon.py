"""The strain lexicon (kartei/strains/lexikon.json, vgrow/strains.py): the data, the box groups, the wiki's Sorten
area (GET /api/wiki/strains, the index's areas, one entry), private_only out of every public build, and the page's
list in node. Hank's advice has its own file (tests/test_hank_strains.py). No network anywhere."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import test_wiki as tw
from vgrow import config, strains, wiki

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.append(str(ROOT / "scripts"))
LEXICON = ROOT / "kartei" / "strains" / "lexikon.json"
OWNER = ROOT / "kartei" / "strains" / "ice-cream-cake-auto.json"
EM_DASH = chr(0x2014)  # written as a code, so this file holds no em dash itself
SID = re.compile(r"(?<![\w])([A-Z]{1,2}\d{1,3})(?![\w])")
# never in the data: cannabinoid content, effects, taste, prices (owner's rule: growing data only)
FORBIDDEN = re.compile(r"\bTHC\b|\bCBD\b|cannabinoid|Cannabinoid|Wirkung|\beffects?\b|Geschmack|\btaste\b|\baroma\b|"
                       r"Terpen|terpen|€|\bEUR\b|\bPreis|\bprice", re.I)


def data() -> dict[str, Any]:
    return json.loads(LEXICON.read_text(encoding="utf-8"))


def own_entries() -> list[dict[str, Any]]:
    return [e for e in data()["entries"] if "same_as" not in e]


# ---------- the data ----------

def test_the_file_is_a_private_draft_collection_of_the_cannabis_package() -> None:
    raw = LEXICON.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw and EM_DASH not in raw.decode("utf-8")
    d = data()
    assert (d["private_only"], d["package"], d["status"], d["area"]) == (True, "cannabis", "draft", "sorten")
    assert d["box"] == {"growing_height_cm": 92, "plants": 1, "card": "stages/autoflower", "source": "S1"}
    assert len(d["entries"]) == 205 and len({e["key"] for e in d["entries"]}) == 205
    assert sum(e.get("grow", {}).get("flowering") == "auto" for e in d["entries"]) == 79  # + the owner's card
    assert d["name_en"] and d["summary_en"] and d["note"] == "Herstellerangabe, ungeprüft" and d["note_en"]


def test_no_cannabinoid_effect_taste_or_price_anywhere_in_the_data() -> None:
    assert not FORBIDDEN.search(LEXICON.read_text(encoding="utf-8"))
    owner = json.loads(OWNER.read_text(encoding="utf-8"))
    assert "THC" not in owner["facts"] and not FORBIDDEN.search(json.dumps(owner["facts"], ensure_ascii=False))
    assert not FORBIDDEN.search(json.dumps(owner["facts_en"], ensure_ascii=False))


# a name that names a cannabinoid, a health word, a drug or a state of intoxication stays out (review 23.10.2026,
# SHOULD 4): the names are what the wiki lists and Hank suggests, so the labels are checked, not only the facts
NAME_WORDS = re.compile(r"\b(?:thc|cbd|cbg|cannabinoid\w*|medical|medizin\w*|lsd\w*|ayahuasca|crack|kokain\w*|"
                        r"cocain\w*|heroin|opium|mdma|ecstasy|ketamin\w*|meth|dmt|psilo\w*|shrooms?|mescal\w*|peyote|"
                        r"euforia|euphori\w*|laughing|cough\w*|sleep\w*|relax\w*|heal\w*|pain\w*)\b", re.I)


def test_no_name_names_a_drug_an_effect_or_a_health_word() -> None:
    d = data()
    names = [str(e.get(k) or "") for e in d["entries"] for k in ("label", "label_en", "key")]
    assert names and not [n for n in names if NAME_WORDS.search(n.replace("-", " "))]
    titles = [str(v.get(k) or "") for v in d["sources"].values() for k in ("title", "title_en")]
    assert not [x for x in titles if NAME_WORDS.search(x)]
    assert d["_ausgelassen"].startswith("11 Sorten ausgelassen")
    assert not [s for s in strains.load().strains if NAME_WORDS.search(" ".join(s.names))]


def test_every_fact_carries_its_own_source_and_an_english_twin() -> None:
    d = data()
    table = d["sources"]
    cited: set[str] = set()
    for e in own_entries():
        assert e["kind"] == "strain" and e["label"] and e["label_en"] and e["brand"], e["key"]
        assert set(e["facts"]) == set(e["facts_en"]) == set(e["fact_labels"]) == set(e["fact_labels_en"]), e["key"]
        ids = set()
        for key, text in e["facts"].items():
            found = [sid for sid in SID.findall(text) if sid in table]  # "G13 Haze" names a strain, no source
            assert found and found == [sid for sid in SID.findall(e["facts_en"][key]) if sid in table], (e["key"], key)
            assert re.findall(r"\d+", SID.sub("", text)) == re.findall(r"\d+", SID.sub("", e["facts_en"][key])), \
                (e["key"], key)  # the twins carry the same numbers
            ids.update(found)
        assert len(ids) == 1, e["key"]  # one product page per entry
        cited |= ids | set(e.get("also_sources", []))
    assert set(table) == cited  # no source in the table that nobody cites
    for sid, src in table.items():
        assert src["url"].startswith("https://www.") and src["retrieved"] == "2026-10-23", sid
        assert re.match(r"https://www\.(hanfgarten\.at|barneysfarm\.de)/", src["url"]), sid
        assert src["note"].startswith("Herstellerangabe, ungeprüft") and src["title_en"], sid


def test_grow_numbers_match_the_texts() -> None:
    for e in own_entries():
        g, f = e["grow"], e["facts"]
        assert g["flowering"] in ("auto", "photoperiod") and g["sex"] in ("feminized", "regular"), e["key"]
        assert ("Autoflower" in f["type"]) == (g["flowering"] == "auto"), e["key"]
        lo, hi = g["height_indoor_cm"]
        nums = [int(n) for n in re.findall(r"\d+", SID.sub("", f["h_in"]))]
        assert nums == [n for n in (lo, hi) if n is not None][: len(nums)] or nums == [hi], e["key"]
        if g.get("difficulty"):
            assert g["difficulty"] == "easy" and f["difficulty"].startswith("einfach"), e["key"]


def test_the_owners_card_stays_his_and_the_lexicon_points_to_it() -> None:
    pointer = [e for e in data()["entries"] if "same_as" in e]
    assert pointer == [{"key": "hanfgarten-ice-cream-cake-auto", "kind": "strain", "label": "Ice Cream Cake Auto",
                        "label_en": "Ice Cream Cake Auto", "brand": "Hanfgarten",
                        "same_as": "strains/ice-cream-cake-auto"}]
    owner = json.loads(OWNER.read_text(encoding="utf-8"))
    assert owner["name"] == "Ice Cream Cake Automatic" and owner["grow"]["height_indoor_cm"] == [None, 120]
    assert owner["grow"]["sources"] == ["S37"] and set(owner["sources"]) == {"S1", "S37", "S40"}  # nothing new
    lex = strains.load()
    s = lex.get("hanfgarten-ice-cream-cake-auto")
    assert s is not None and s.own and s.card == "strains/ice-cream-cake-auto" and s.fit(lex.box_cm) == "unknown"
    assert set(s.sources) == {"S1", "S37", "S40"}  # the owner's card's own table, nothing of the lexicon's
    # the wiki shows no second copy: the pointer has no card of its own
    assert wiki.find("strains", "hanfgarten-ice-cream-cake-auto") is None
    assert wiki.find("strains", "ice-cream-cake-auto")["raw"]["name"] == "Ice Cream Cake Automatic"


# ---------- the box groups ----------

@pytest.mark.parametrize(("height", "fit"), [
    ((None, 60), "sure"), ((60, 60), "sure"), ((70, 90), "sure"), ((92, 92), "sure"), ((80, 120), "lst"),
    ((92, 100), "lst"), ((93, 110), "tall"), ((100, 150), "tall"), ((None, 120), "unknown"), (None, "unknown"),
])
def test_fit_follows_the_owners_four_groups(height: Any, fit: str) -> None:
    assert strains.fit_of(height, 92) == fit


def test_the_lexicon_counts() -> None:
    lex = strains.load()
    assert lex.box_cm == 92 and lex.box_source[0] == "S1" and "VGrow" in lex.box_source[1]
    fits = [s.fit(lex.box_cm) for s in lex.strains]
    assert {f: fits.count(f) for f in strains.FITS} == {"sure": 9, "lst": 71, "unknown": 55, "tall": 70}


# ---------- private_only: never in a public build ----------

def test_a_public_build_carries_no_lexicon() -> None:
    assert strains.load().private and len(strains.load().strains) == 205
    assert strains.load(public=True).strains == ()
    assert wiki.strains_view("de", ["cannabis"], public=True) is None
    assert [a for a in wiki.areas_view(["cannabis"], public=True) if a["id"] == "sorten"] == []  # Dünger stays


def test_the_preview_build_leaves_private_data_out(tmp_path: Path) -> None:
    import build_preview  # scripts/ (appended to the path above)

    markers = build_preview.private_markers()
    assert len(markers) > 400 and "barney-s-farm-northern-lights-photo" in markers
    out = tmp_path / "preview.html"
    build_preview.main(out)
    page = out.read_text(encoding="utf-8")
    assert not [m for m in markers if m in page]
    with pytest.raises(SystemExit):
        build_preview.check_public(page + "www.hanfgarten.at/northern-lights-samen-feminisiert.html")


def test_the_library_index_lists_no_collection() -> None:
    from vgrow.plant import library_index

    assert [c["key"] for c in library_index()["strains"]] == ["ice-cream-cake-auto"]


# ---------- the wiki ----------

def test_the_index_shows_the_area_not_one_tile_per_strain() -> None:
    _, index = tw.get("/api/wiki")
    assert next(a for a in index["areas"] if a["id"] == "sorten") == {"id": "sorten", "count": 205, "auto": 80, "sure": 9}
    keys = {c["key"] for cat in index["categories"] for c in cat["cards"]}
    assert "ice-cream-cake-auto" in keys and not any(k.startswith(("barney-s-farm-", "hanfgarten-")) for k in keys)


def test_the_strain_list_comes_sorted_by_fit_with_numbers_and_no_shop_text() -> None:
    status, d = tw.get("/api/wiki/strains", lang="en")
    assert status == 200 and d["count"] == 205 == len(d["strains"]) and d["status"] == "draft"
    assert d["box"]["cm"] == 92 and d["box"]["source"]["id"] == "S1" and d["note"] == "Maker's figure, unverified"
    order = [strains.FITS.index(r["fit"]) for r in d["strains"]]
    assert order == sorted(order)
    assert {b["name"] for b in d["brands"]} >= {"Barney's Farm", "Hanfgarten", "Royal Queen Seeds"}
    assert sum(b["count"] for b in d["brands"]) == 205
    own = [r for r in d["strains"] if r.get("own")]
    assert len(own) == 1 and own[0]["card"] == "ice-cream-cake-auto" and own[0]["current"] is True
    text = json.dumps(d, ensure_ascii=False)
    assert not FORBIDDEN.search(text) and "http" not in text.replace(d["box"]["source"]["url"], "")
    assert len(text.encode("utf-8")) < 90_000  # 205 rows stay light on a phone


def test_the_package_switch_takes_the_lexicon_away() -> None:
    config.atomic_write(config.STATE_DIR / "settings.json", json.dumps({"packages": []}))
    status, body = tw.get("/api/wiki/strains")
    assert status == 404 and body["hidden"] is True
    _, index = tw.get("/api/wiki")
    assert [a["id"] for a in index["areas"]] == ["duenger"]  # the fertilizer list is no cannabis package
    _, hits = tw.get("/api/wiki/search?q=Northern%20Lights")
    assert not [h for h in hits["results"] if h["folder"] == "strains"]
    status, _ = tw.get("/api/wiki/card/strains/barney-s-farm-northern-lights-photo")
    assert status == 404


def test_one_entry_cites_only_its_own_sources_without_a_shop_link() -> None:
    for lang in ("de", "en"):
        status, card = tw.get("/api/wiki/card/strains/barney-s-farm-tangerine-dream-auto", lang=lang)
        assert status == 200 and card["category"] == "sorten" and card["kind"] == "strain"
        assert [s["id"] for s in card["sources"]] == ["B97", "S1"]
        shop = card["sources"][0]
        assert shop["url"] is None and shop["host"] == "barneysfarm.de" and shop["retrieved"] == "2026-10-23"
        assert card["lexicon"]["fit"] == "sure" and card["lexicon"]["height"] == [70, 90]
        assert card["lexicon"]["note"] == ("Herstellerangabe, ungeprüft" if lang == "de" else "Maker's figure, unverified")
        assert card["image"] == {"drawing": {"id": "strain-auto", "steps": []}}
        assert "grow" not in card["card"] and not FORBIDDEN.search(json.dumps(card, ensure_ascii=False))
    # the owner's card gets the lexicon block too, and keeps its own sources with their links
    _, own = tw.get("/api/wiki/card/strains/ice-cream-cake-auto")
    assert own["lexicon"]["own"] is True and own["lexicon"]["fit"] == "unknown" and "grow" not in own["card"]
    assert "THC" not in own["card"]["facts"]


def test_the_search_finds_lexicon_entries_under_their_area() -> None:
    _, hits = tw.get("/api/wiki/search?q=Northern%20Lights")
    first = hits["results"][:3]
    assert {h["key"] for h in first} == {"barney-s-farm-northern-lights-photo", "barney-s-farm-northern-lights-photo-2",
                                         "hanfgarten-northern-lights-photo"}
    assert all(h["category"] == "sorten" for h in first)


def test_hank_answers_the_lexicon_himself_not_through_the_card_button() -> None:
    from vgrow.hank import kartei_index, wikicards

    assert not [e for e in wikicards.entries() if e.get("area") == "sorten"]  # the Dünger area's cards are answered
    index = kartei_index.Index.load()
    assert not [p for p in index.passages if p.card == "lexikon"]


# ---------- the page (node, no browser) ----------

LIST_RUN = r"""
const tick = () => new Promise((r) => setTimeout(r, 30));
const click = async (node) => { for (const f of node.listeners.click || []) await f({ preventDefault() {} }); await tick(); };
(async () => {
  await tick();
  location.hash = "#wiki/sorten";
  for (const f of listeners.hashchange || []) await f();
  await tick();
  const view = main.childNodes[0];
  const out = {};
  out.first = view.textContent;
  out.rows = view.querySelectorAll(".wiki-strain").length;
  out.more = !!view.querySelector(".wiki-more-btn");
  const chip = view.querySelectorAll(".wiki-fchip").find((b) => b.textContent.startsWith("wiki.strainFit.sure"));
  await click(chip);
  const after = main.childNodes[0];
  out.sure = after.querySelectorAll(".wiki-strain").length;
  out.sureText = after.textContent;
  out.pressed = after.querySelectorAll(".wiki-fchip").filter((b) => b.attrs["aria-pressed"] === "true").map((b) => b.textContent);
  out.links = after.querySelectorAll(".wiki-strain").map((a) => a.href);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(tw.NODE is None, reason="node is not installed")
def test_the_sorten_list_pages_filters_and_links_each_entry() -> None:
    lex = wiki.strains_view("de", ["cannabis"])
    dom = tw.DOM % json.dumps({}, ensure_ascii=False)
    dom = dom.replace('  if (path === "/api/wiki") return',
                      f'  if (path === "/api/wiki/strains") return {json.dumps(lex, ensure_ascii=False)};\n'
                      '  if (path === "/api/wiki") return')
    run = subprocess.run([tw.NODE or "node", "-"], input=dom + tw.wiki_js() + LIST_RUN, capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["rows"] == 24 and out["more"] is True  # one page of 24, then "Mehr laden"
    assert 'wiki.strains.shown{"shown":205,"total":205}' in out["first"]
    assert out["sure"] == 9 and 'wiki.strains.shown{"shown":9,"total":205}' in out["sureText"]
    assert out["pressed"] and out["pressed"][0].startswith("wiki.strainFit.sure")
    assert all(h.startswith("#wiki/strains/") for h in out["links"]) and len(out["links"]) == 9
    assert "Herstellerangabe" in out["first"]  # the server's note in the explanation line


@pytest.mark.skipif(tw.NODE is None, reason="node is not installed")
def test_an_entry_renders_its_box_block_and_each_source_under_its_fact() -> None:
    cards = {}
    for key in ("barney-s-farm-tangerine-dream-auto", "hanfgarten-zkittlez-photo", "ice-cream-cake-auto"):
        cards[f"strains/{key}"] = wiki.card_view(wiki.find("strains", key), "de", ["cannabis"])
    out, err = tw.run_page(cards)
    assert not err, err
    td = out["strains/barney-s-farm-tangerine-dream-auto"]["text"]
    assert "wiki.sec.box" in td and "wiki.strainFit.sure.text" in td and "wiki.strains.boxHeight" in td
    assert 'wiki.strains.source{"id":"B97"} · Herstellerangabe, ungeprüft' in td
    assert "wiki.src.read" in td and [a for a in out["strains/barney-s-farm-tangerine-dream-auto"]["links"] if not a["href"].startswith("#wiki")] == [
        {"href": "https://vivosun.com/support/guide/vgrow-smart-grow-box.md", "target": "_blank",
         "rel": "noopener noreferrer"}]  # the box manual links, the shop page does not
    assert "wiki.strainFit.unknown.text" in out["strains/hanfgarten-zkittlez-photo"]["text"]
    assert "THC" not in out["strains/ice-cream-cake-auto"]["text"]


def test_the_wiki_css_keeps_the_filter_rows_inside_the_screen() -> None:
    css = (ROOT / "web" / "wiki.css").read_text(encoding="utf-8")
    assert ".wiki-fchips { display: flex; gap: 6px; overflow-x: auto;" in css
    assert ".wiki-fchip.on { background: var(--sel-bg)" in css and EM_DASH not in css


def test_strain_texts_exist_in_both_languages() -> None:
    import test_web_i18n as web

    d = web.dictionary()
    used = set(re.findall(r'"(wiki\.(?:strains|strainFit|area\.sorten)[\w.]*)"', tw.wiki_js()))
    assert len(used) > 30
    for key in used:
        if key.endswith((".count", ".active")):  # plurals (tn): .one and .other
            assert {f"{key}.one", f"{key}.other"} <= set(d["de"]) & set(d["en"]), key
        else:
            assert key in d["de"] and key in d["en"], key
