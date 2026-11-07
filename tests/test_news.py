"""News watcher: parser, filter, dedupe, store, view, polite fetching. Offline: a local test server only."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from vgrow import config, news

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "news"
NOW = datetime(2026, 10, 22, 7, 30).astimezone()


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(autouse=True)
def fast(monkeypatch):  # type: ignore[no-untyped-def]
    """The test server is one host: no 5 s gap between its feeds here."""
    monkeypatch.setattr(news, "SAME_HOST_GAP_S", 0.0)


def source(sid: str, url: str, **extra: Any) -> dict[str, Any]:
    base = {"id": sid, "name": f"Quelle {sid}", "publisher_kind": "official", "lang": "de", "feed_url": url,
            "rights": "free", "filter": "cannabis", "crawl_delay_s": 0, "attribution": f"Quelle {sid}",
            "active": True}
    return {**base, **extra}


class Feeds:
    """A local feed server: routes answer from a table, every request is recorded."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        app = web.Application()
        app.router.add_route("GET", "/{tail:.*}", self.handle)
        self.server = TestServer(app)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append((request.path, dict(request.headers)))
        route = self.routes.get(request.path)
        if route is None:
            return web.Response(status=404)
        return await route(request)

    def url(self, path: str) -> str:
        return str(self.server.make_url(path))

    def hits(self, path: str) -> int:
        return sum(1 for p, _ in self.requests if p == path)

    async def __aenter__(self) -> Feeds:
        await self.server.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.server.close()


Route = Any  # async (request) -> response; a fresh response per request, aiohttp sends each one only once


def feed(body: bytes, **headers: str) -> Route:
    async def answer(request: web.Request) -> web.Response:
        return web.Response(body=body, content_type="application/rss+xml", headers=headers)
    return answer


def status(code: int, **headers: str) -> Route:
    async def answer(request: web.Request) -> web.Response:
        return web.Response(status=code, headers=headers)
    return answer


async def fetch(sources: list[dict[str, Any]], **kw: Any) -> list[dict[str, Any]]:
    async with aiohttp.ClientSession() as session:
        return await news.fetch_all(session, sources, **kw)


# ---------- parser ----------

def test_rss2_titles_links_dates_and_never_the_description() -> None:
    entries = news.parse(fixture("rss2.xml"), "https://www.example.org/pressemitteilungen.xml")
    assert [e["title"] for e in entries] == [
        "Zweiter Zwischenbericht zur Evaluation des Konsumcannabisgesetzes (KCanG)",
        "Kabinett beschließt Änderung des Medizinal-Cannabisgesetzes",  # CDATA, whitespace collapsed
        "Neue Pflegereform vorgestellt",
        "Growshop-Rabatt: Cannabis-Samen jetzt kaufen",
        "Cannabis-Rechtsprechung ohne Datum",
    ]  # the item without a link is gone
    assert all(set(e) == {"title", "link", "guid", "published"} for e in entries)
    assert "never be stored" not in json.dumps([{**e, "published": str(e["published"])} for e in entries])
    first = entries[0]
    assert first["link"] == "https://www.example.org/presse/ekocan-zwischenbericht.html"
    assert first["guid"] == "pm-2026-041"
    assert first["published"].isoformat() == "2026-04-01T00:00:00+01:00"  # CET understood
    assert entries[1]["published"].date().isoformat() == "2026-10-07"  # the BGBl form YYYY-MM-DD
    assert entries[4]["published"] is None


def test_rdf_feed_with_dc_date_and_rdf_about() -> None:
    entries = news.parse(fixture("rdf.xml"), "https://kammer.example.de/rss_gartenbau.rdf")
    assert [e["title"] for e in entries] == ["Schädlinge an Tomaten im Hausgarten erkennen",
                                             "Marktbericht Spargel und Erdbeeren", "Nutzhanf als Zwischenfrucht"]
    assert entries[0]["published"].isoformat() == "2026-09-15T09:30:00+02:00"
    assert entries[2]["link"] == "https://kammer.example.de/gartenbau/3"  # no <link>: rdf:about
    assert entries[2]["guid"] == "https://kammer.example.de/gartenbau/3"


def test_atom_feed_alternate_link_html_and_xhtml_titles() -> None:
    entries = news.parse(fixture("atom.xml"), "https://law.example.com/feed.atom")
    assert [e["title"] for e in entries] == ["BGH: Urteil zum Cannabis-Anbau in Wohnungen", "New hemp study",
                                             "Mietrecht: Neues zur Kaution"]
    assert entries[0]["link"] == "https://law.example.com/news/1"  # rel=alternate, not rel=self
    assert entries[1]["link"] == "https://law.example.com/news/2"  # no rel means alternate
    assert entries[0]["guid"] == "tag:law.example.com,2026:1"
    assert entries[0]["published"].isoformat() == "2026-10-22T14:55:51+00:00"  # published before updated
    assert "never be stored" not in repr(entries)


def test_malformed_feed_and_non_feed_xml_are_refused() -> None:
    with pytest.raises(news.FeedError):
        news.parse(fixture("malformed.xml"))
    with pytest.raises(news.FeedError):
        news.parse(b"<html><body>Not a feed</body></html>")
    with pytest.raises(news.FeedError):
        news.parse(b"")


def test_doctype_and_entity_declarations_are_refused_before_parsing() -> None:
    with pytest.raises(news.FeedError, match="DOCTYPE"):
        news.parse(fixture("entity_bomb.xml"))
    with pytest.raises(news.FeedError):  # also a harmless-looking one, and in any case
        news.parse(b'<?xml version="1.0"?><!doctype rss><rss><channel></channel></rss>')
    with pytest.raises(news.FeedError):
        news.parse("<rss>".encode("utf-16"))  # the check reads bytes, so UTF-16 is refused outright


def test_relative_links_resolve_against_the_feed_and_scripts_are_dropped() -> None:
    entries = news.parse(fixture("relative_links.xml"), "https://relative.example.org/rss/feed.xml")
    links = {e["title"]: e["link"] for e in entries}
    assert links["Cannabis: Pressemitteilung mit relativem Link"] == "https://relative.example.org/presse/2026/cannabis-1.html"
    assert links["Cannabis: eine Ebene hoeher"] == "https://relative.example.org/archiv/cannabis-2.html"
    assert links["Cannabis: Link nur in der guid"] == "https://relative.example.org/guid-link.html"
    assert links["Cannabis: mit Leerraum und Zeilenumbruch"] == "https://relative.example.org/leerraum.html"
    assert "Cannabis: Skript statt Link" not in links  # javascript: is no link


def test_html_is_stripped_from_titles() -> None:
    titles = [e["title"] for e in news.parse(fixture("html_titles.xml"), "https://html.example.org/")]
    assert titles == ["Cannabis und Recht", "KCanG Novelle", "Hanf & Recht doppelt", "THC-Grenzwert: 3 < 5 > 1"]


# ---------- filter ----------

@pytest.mark.parametrize(("title", "expected"), [
    ("Bundestag berät KCanG-Novelle", ["KCanG"]),
    ("Medizinalcannabis: neue Regeln für Telemedizin", ["Cannabis", "Medizinalcannabis"]),
    ("Anbauvereinigungen melden erste Ernte", ["Anbauvereinigung"]),
    ("THC-Grenzwert im Straßenverkehr", ["THC", "THC-Grenzwert"]),
    ("Deutscher Hanfverband fordert Nachbesserung", ["Hanf"]),
    ("Industriehanf als Rohstoff der Zukunft", ["Hanf"]),
    ("Study on hemp fibre", ["Hemp"]),
    ("Kabinett: Cannabis nur noch auf Rezept vom Arzt", ["Cannabis"]),  # prescription law stays
    ("Workshop Eigenanbau für Mitglieder", ["Eigenanbau"]),  # a workshop is no shop
    ("Anzeige gegen Cannabis Social Club erstattet", ["Cannabis", "Cannabis Social Club"]),
    ("Mathcamp in Bonn", None),  # thc inside a word does not count
    ("Shanghai Börse schließt", None),
    ("Neue Pflegereform vorgestellt", None),
])
def test_cannabis_keywords_with_word_boundaries(title: str, expected: list[str] | None) -> None:
    assert news.classify(title, "https://example.org/artikel/123") == expected


@pytest.mark.parametrize("title", [
    "Growshop-Rabatt: Cannabis-Samen jetzt kaufen", "Cannabis Sorte des Monats: Lemon", "Vaporizer im Testbericht",
    "Anzeige: Die besten Cannabis-Seeds", "[Werbung] Hanf-Gutschein", "CBD-Shop eröffnet", "Cannabis Rezept online",
    "Dosierung von Cannabis für Einsteiger", "Tipps zum Verdampfen von Cannabis", "Stecklinge abzugeben in Berlin",
    "Tickets für die Hanfparade", "Buy cannabis seeds with discount",
])
def test_consumption_and_sale_titles_are_dropped(title: str) -> None:
    assert news.classify(title, "https://example.org/a", "all") is None
    assert news.promotes(title)


def test_garden_filter_keeps_home_gardening_and_cannabis_drops_the_professional_rest() -> None:
    assert news.classify("Schädlinge an Tomaten im Hausgarten", "https://k.example/1", "garden") == [
        "Hausgarten", "Tomate", "Schädlinge"]
    assert news.classify("Kräuter auf dem Balkon", "https://k.example/2", "garden") == ["Kräuter", "Balkon"]
    assert news.classify("Nutzhanf als Zwischenfrucht", "https://k.example/3", "garden") == ["Hanf"]
    assert news.classify("Marktbericht Spargel und Erdbeeren", "https://k.example/4", "garden") is None
    assert news.classify("Kabinett beschließt Haushalt", "https://k.example/5", "all") == []  # all keeps it


def test_link_path_counts_for_topic_and_drop_link_contains_removes() -> None:
    assert news.classify("Fragen und Antworten", "https://bmg.example/themen/cannabis/faq.html") == ["Cannabis"]
    assert news.classify("Cannabis im Ausschuss", "https://bt.example/mediathek/123", "cannabis",
                         ["/mediathek"]) is None


# ---------- store, dedupe ----------

def test_normalize_link_ignores_scheme_www_slash_fragment_and_tracking() -> None:
    a = news.normalize_link("https://www.Example.org/presse/1/?utm_source=rss&b=2&a=1#top")
    b = news.normalize_link("http://example.org//presse/1?a=1&b=2")
    assert a == b == "example.org/presse/1?a=1&b=2"


def test_merge_dedupes_by_link_and_guid_and_files_land_in_the_state_dir(isolated_state) -> None:  # type: ignore[no-untyped-def]
    state = news.load_state()
    items: list[dict[str, Any]] = []
    src = source("s1", "https://example.org/feed.xml")
    first = [{"title": "Cannabis A", "link": "https://example.org/a", "guid": "g-a", "published": None},
             {"title": "Cannabis B", "link": "https://example.org/b", "guid": "", "published": None}]
    assert len(news.merge(items, state, src, first, NOW)) == 2
    again = [{"title": "Cannabis A neu", "link": "http://www.example.org/a/#x", "guid": "", "published": None},
             {"title": "Cannabis B anders", "link": "https://example.org/b-moved", "guid": "g-a", "published": None},
             {"title": "Cannabis C", "link": "https://example.org/c", "guid": "g-c", "published": None}]
    added = news.merge(items, state, src, again, NOW + timedelta(hours=1))
    assert [i["title"] for i in added] == ["Cannabis C"]
    item = added[0]
    assert set(item) >= {"id", "source_id", "source_name", "publisher_kind", "rights", "title", "link",
                         "published", "first_seen", "matched"}
    assert item["dated"] == "first_seen" and item["published"] == item["first_seen"]  # no date: first seen
    news.save_items(items)
    news.save_state(state)
    assert (isolated_state / "news" / "items.json").exists() and (isolated_state / "news" / "state.json").exists()
    raw = (isolated_state / "news" / "items.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw  # UTF-8 without BOM, LF
    assert [i["title"] for i in news.load_items()] == ["Cannabis C", "Cannabis A", "Cannabis B"]


def test_store_keeps_the_newest_200_and_pruned_items_stay_known(isolated_state) -> None:  # type: ignore[no-untyped-def]
    state = news.load_state()
    items: list[dict[str, Any]] = []
    src = source("s1", "https://example.org/feed.xml", filter="all")
    for n in range(230):
        entry = {"title": f"Meldung {n}", "link": f"https://example.org/{n}", "guid": "", "published": None}
        news.merge(items, state, src, [entry], NOW + timedelta(minutes=n))
    assert len(items) == news.MAX_ITEMS
    assert items[0]["title"] == "Meldung 229" and items[-1]["title"] == "Meldung 30"
    old = {"title": "Meldung 0", "link": "https://example.org/0", "guid": "", "published": None}
    assert news.merge(items, state, src, [old], NOW + timedelta(days=1)) == []  # pruned, but not new


def test_future_dates_fall_back_and_first_fetch_skips_old_items(isolated_state) -> None:  # type: ignore[no-untyped-def]
    state = news.load_state()
    items: list[dict[str, Any]] = []
    src = source("s1", "https://example.org/feed.xml")
    entries = [
        {"title": "Cannabis Zukunft", "link": "https://example.org/f", "guid": "", "published": NOW + timedelta(days=50)},
        {"title": "Cannabis alt", "link": "https://example.org/o", "guid": "", "published": NOW - timedelta(days=200)},
        {"title": "Cannabis frisch", "link": "https://example.org/n", "guid": "", "published": NOW - timedelta(days=2)},
    ]
    added = news.merge(items, state, src, entries, NOW, first_fetch=True)
    by_title = {i["title"]: i for i in added}
    assert set(by_title) == {"Cannabis Zukunft", "Cannabis frisch"}
    assert by_title["Cannabis Zukunft"]["dated"] == "first_seen"
    assert by_title["Cannabis frisch"]["dated"] == "feed"
    assert news.merge(items, state, src, entries, NOW + timedelta(days=1)) == []  # the old one is remembered


def test_a_corrupt_store_is_kept_aside_not_overwritten(isolated_state) -> None:  # type: ignore[no-untyped-def]
    path = news.items_path()
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    assert news.load_items() == []
    assert not path.exists()
    assert [p.read_text(encoding="utf-8") for p in path.parent.glob("items.json.broken-*")] == ["{ not json"]


# ---------- view ----------

def _write_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"sources": [source("amt", "https://amt.example/feed.xml"),
                        source("verband", "https://verband.example/feed/", rights="private",
                               publisher_kind="advocacy", filter="all"),
                        source("bgh", "https://bgh.example/feed.xml", publisher_kind="court",
                               attribution="Pressemitteilung des Bundesgerichtshofs vom TT.MM.JJJJ")]}
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(news, "SOURCES_FILE", path)


def test_view_private_on_and_off(isolated_state, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _write_sources(tmp_path, monkeypatch)
    state = news.load_state()
    items: list[dict[str, Any]] = []
    for sid, rights, title, when in [("amt", "free", "Cannabis amtlich", NOW),
                                     ("verband", "private", "Verband zur Teillegalisierung", NOW + timedelta(hours=1)),
                                     ("bgh", "free", "Urteil zu Cannabis", NOW - timedelta(hours=1))]:
        src = source(sid, f"https://{sid}.example/feed.xml", rights=rights)
        published = datetime(2026, 7, 28, 12, 0).astimezone() if sid == "bgh" else None
        news.merge(items, state, src, [{"title": title, "link": f"https://{sid}.example/1", "guid": "",
                                        "published": published}], when)
    state["last_run"] = news._iso(NOW)
    state["sources"] = {"amt": {"last_ok": news._iso(NOW), "last_error": None},
                        "verband": {"last_ok": None, "last_error": "FeedError: HTTP 429"}}
    news.save_items(items)
    news.save_state(state)

    private = news.view(private=True)
    assert [i["title"] for i in private["items"]] == ["Verband zur Teillegalisierung", "Cannabis amtlich",
                                                      "Urteil zu Cannabis"]  # newest first seen first
    assert private["items"][2]["attribution"] == "Pressemitteilung des Bundesgerichtshofs vom 28.07.2026"
    assert {s["id"]: s["ok"] for s in private["sources"]} == {"amt": True, "verband": False, "bgh": False}
    assert private["last_fetch"] == news._iso(NOW)
    assert private["quiet_since"] == private["items"][0]["first_seen"]

    public = news.view(private=False)
    assert [i["title"] for i in public["items"]] == ["Cannabis amtlich", "Urteil zu Cannabis"]
    assert all(i["rights"] == "free" for i in public["items"])
    assert [s["id"] for s in public["sources"]] == ["amt", "bgh"]
    assert len(news.view(limit=1)["items"]) == 1


def test_view_without_any_run_is_empty_and_quiet_since_is_none(isolated_state) -> None:  # type: ignore[no-untyped-def]
    out = news.view()
    assert out["items"] == [] and out["last_fetch"] is None and out["quiet_since"] is None
    assert out["sources"] and all(not s["ok"] for s in out["sources"])


# ---------- fetching ----------

def test_fetch_stores_matches_sends_user_agent_and_304_keeps_items(isolated_state) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async with Feeds() as srv:
            async def conditional(request: web.Request) -> web.Response:
                if request.headers.get("If-None-Match") == '"v1"':
                    return web.Response(status=304)
                return await feed(fixture("rss2.xml"), ETag='"v1"',
                                  **{"Last-Modified": "Wed, 22 Oct 2026 05:00:00 GMT"})(request)

            srv.routes["/rss.xml"] = conditional
            src = source("amt", srv.url("/rss.xml"), publisher_kind="press")  # press: the drop list applies
            new = await fetch([src], now=NOW)
            # Two cannabis items stay; the 01.04.2026 report is older than 30 days on the first fetch.
            assert sorted(i["title"] for i in new) == ["Cannabis-Rechtsprechung ohne Datum",
                                                       "Kabinett beschließt Änderung des Medizinal-Cannabisgesetzes"]
            assert srv.requests[0][1]["User-Agent"] == news.USER_AGENT
            state = news.load_state()["sources"]["amt"]
            assert state["etag"] == '"v1"' and state["last_modified"] == "Wed, 22 Oct 2026 05:00:00 GMT"
            assert state["count"] == 2 and state["feed_items"] == 5 and state["last_error"] is None

            later = NOW + timedelta(days=1)
            assert await fetch([src], now=later) == []
            headers = srv.requests[-1][1]
            assert headers["If-None-Match"] == '"v1"'
            assert headers["If-Modified-Since"] == "Wed, 22 Oct 2026 05:00:00 GMT"
            assert len(news.load_items()) == 2  # 304: nothing lost
            st = news.load_state()
            assert st["sources"]["amt"]["last_ok"] == news._iso(later) and st["last_success"] == news._iso(later)

    asyncio.run(run())


def test_429_pauses_the_source_for_six_hours_and_others_still_run(isolated_state) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async with Feeds() as srv:
            srv.routes["/busy"] = status(429)
            srv.routes["/ok"] = feed(fixture("atom.xml"))
            sources = [source("busy", srv.url("/busy")), source("ok", srv.url("/ok"))]
            new = await fetch(sources, now=NOW)
            assert [i["source_id"] for i in new] == ["ok", "ok"]
            st = news.load_state()["sources"]["busy"]
            assert st["backoff_until"] == news._iso(NOW + timedelta(hours=6)) and "429" in st["last_error"]
            await fetch(sources, now=NOW + timedelta(hours=5))
            assert srv.hits("/busy") == 1  # paused: not asked again
            srv.routes["/busy"] = status(503, **{"Retry-After": str(12 * 3600)})
            await fetch(sources, now=NOW + timedelta(hours=7))
            assert srv.hits("/busy") == 2
            st = news.load_state()["sources"]["busy"]
            assert st["backoff_until"] == news._iso(NOW + timedelta(hours=19))  # a longer Retry-After counts

    asyncio.run(run())


def test_size_cap_timeout_and_errors_are_skipped_never_raised(isolated_state, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(news, "MAX_BYTES", 4096)
    monkeypatch.setattr(news, "TIMEOUT", 0.3)

    async def run() -> None:
        async with Feeds() as srv:
            async def slow(request: web.Request) -> web.Response:
                await asyncio.sleep(2)
                return await feed(fixture("rss2.xml"))(request)

            async def streamed(request: web.Request) -> web.StreamResponse:
                resp = web.StreamResponse()  # no Content-Length: the cap must stop the reading itself
                await resp.prepare(request)
                for _ in range(64):
                    await resp.write(b"<!-- padding -->" * 64)
                await resp.write_eof()
                return resp

            srv.routes["/big"] = feed(b"<rss>" + b" " * 10_000 + b"</rss>")
            srv.routes["/stream"] = streamed
            srv.routes["/slow"] = slow
            srv.routes["/broken"] = feed(fixture("malformed.xml"))
            srv.routes["/bomb"] = feed(fixture("entity_bomb.xml"))
            srv.routes["/ok"] = feed(fixture("atom.xml"))
            names = ["big", "stream", "slow", "broken", "bomb", "missing", "ok"]
            new = await fetch([source(n, srv.url(f"/{n}")) for n in names], now=NOW)
            assert {i["source_id"] for i in new} == {"ok"}
            st = news.load_state()["sources"]
            assert "larger than" in st["big"]["last_error"] and "larger than" in st["stream"]["last_error"]
            assert "Timeout" in st["slow"]["last_error"]
            assert "XML" in st["broken"]["last_error"] and "DOCTYPE" in st["bomb"]["last_error"]
            assert "404" in st["missing"]["last_error"] and st["missing"]["backoff_until"]
            assert st["ok"]["last_error"] is None and not st["slow"].get("etag")

    asyncio.run(run())


def test_redirects_at_most_three_and_only_on_the_same_host(isolated_state) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async with Feeds() as srv:
            for n in range(1, 5):
                srv.routes[f"/r{n}"] = status(302, Location=f"/r{n + 1}")
            srv.routes["/r5"] = feed(fixture("atom.xml"))
            srv.routes["/away"] = status(301, Location="https://elsewhere.example/feed")
            new = await fetch([source("three", srv.url("/r2")), source("four", srv.url("/r1")),
                               source("away", srv.url("/away"))], now=NOW)
            assert {i["source_id"] for i in new} == {"three"}
            st = news.load_state()["sources"]
            assert "redirects" in st["four"]["last_error"]
            assert "another host" in st["away"]["last_error"]

    asyncio.run(run())


def test_private_sources_are_not_fetched_when_the_install_is_not_private(isolated_state) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async with Feeds() as srv:
            srv.routes["/amt"] = feed(fixture("atom.xml"))
            srv.routes["/verband"] = feed(fixture("rss2.xml"))
            sources = [source("amt", srv.url("/amt")), source("verband", srv.url("/verband"), rights="private")]
            await fetch(sources, private=False, now=NOW)
            assert srv.hits("/amt") == 1 and srv.hits("/verband") == 0
            await fetch(sources, private=True, now=NOW + timedelta(days=1))
            assert srv.hits("/verband") == 1

    asyncio.run(run())


def test_crawl_delay_between_requests_to_the_same_host(isolated_state) -> None:  # type: ignore[no-untyped-def]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async def run() -> None:
        async with Feeds() as srv:
            srv.routes["/a"] = feed(fixture("atom.xml"))
            srv.routes["/b"] = feed(fixture("atom.xml"))
            await fetch([source("a", srv.url("/a"), crawl_delay_s=30), source("b", srv.url("/b"))],
                        now=NOW, sleep=fake_sleep)

    asyncio.run(run())
    assert len(waits) == 1 and 29 < waits[0] <= 30  # the host's delay holds for every feed on it


def test_fetch_never_raises_even_with_a_broken_session(isolated_state) -> None:  # type: ignore[no-untyped-def]
    class Broken:
        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("socket gone")

    new = asyncio.run(news.fetch_all(Broken(), [source("x", "https://x.example/feed")], now=NOW))  # type: ignore[arg-type]
    assert new == []
    st = news.load_state()
    assert "socket gone" in st["sources"]["x"]["last_error"] and "last_success" not in st
    assert st["last_run"] == news._iso(NOW)


# ---------- review fixes: parser robustness ----------

def test_a_hostile_title_costs_linear_time() -> None:
    body = (b"<rss><channel><item><title>" + b"&lt;script&gt;" * 32000
            + b"</title><link>https://a.example/x</link></item></channel></rss>")
    started = time.monotonic()
    news.parse(body)
    assert time.monotonic() - started < 2.0  # was about 15 s, quadratic in the title length
    started = time.monotonic()
    assert news._drop_scripts("a<script>" * 200_000).startswith("a ")
    assert news._drop_scripts("x<style>p{}</style>y<SCRIPT src=1>z</script >w") == "x y w"
    assert time.monotonic() - started < 2.0


def test_parse_raises_only_feed_error_and_reads_undeclared_latin1() -> None:
    with pytest.raises(news.FeedError):
        news.parse(b'<?xml version="1.0" encoding="utf-7"?><rss><channel></channel></rss>')
    latin = b"<rss><channel><item><title>Cannabis f\xfcr Patienten</title><link>https://a.example/x</link></item></channel></rss>"
    assert [e["title"] for e in news.parse(latin)] == ["Cannabis für Patienten"]


def test_a_doctype_inside_a_title_is_text_not_a_declaration() -> None:
    body = (b"<?xml version='1.0'?><!-- feed --><rss><channel><item><title><![CDATA[Cannabis <!DOCTYPE html> Test]]>"
            b"</title><link>https://a.example/x</link></item></channel></rss>")
    assert [e["title"] for e in news.parse(body)] == ["Cannabis Test"]
    with pytest.raises(news.FeedError, match="DOCTYPE"):
        news.parse(b"\xef\xbb\xbf<?xml version='1.0'?>\n<!-- a -->\n<!DOCTYPE rss><rss/>")


def test_only_feed_namespaces_count_and_long_links_are_skipped() -> None:
    body = (b'<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>'
            b"<item><media:title>Bildtitel</media:title><title>Cannabis Gesetz</title>"
            b"<media:link>https://cdn.example/bild.jpg</media:link><link>https://a.example/1</link></item>"
            b"<item><title>Cannabis lang</title><link>https://a.example/" + b"x" * 3000 + b"</link></item>"
            b"</channel></rss>")
    entries = news.parse(body)
    assert [(e["title"], e["link"]) for e in entries] == [("Cannabis Gesetz", "https://a.example/1")]


def test_long_titles_are_filtered_whole_and_stored_cut_with_a_mark(isolated_state) -> None:  # type: ignore[no-untyped-def]
    title = "Wort " * 130 + "Cannabis"
    assert len(news.clean_title(title)) > news.TITLE_MAX  # the parser no longer cuts
    state, items = news.load_state(), []  # type: ignore[var-annotated]
    added = news.merge(items, state, source("s", "https://e.example/f"),
                       [{"title": title, "link": "https://e.example/1", "guid": "", "published": None}], NOW)
    assert len(added) == 1 and added[0]["title_cut"] is True
    assert len(added[0]["title"]) == news.TITLE_MAX and added[0]["title"].endswith("…")


# ---------- review fixes: filter ----------

@pytest.mark.parametrize(("title", "filter_kind", "kind", "keeps"), [
    ("Kabinett beschließt Änderung des Medizinal-Cannabisgesetzes", "cannabis", "press", True),
    ("Zweite Evaluation zur Cannabis-Teillegalisierung: EKOCAN-Zwischenbericht liegt vor", "cannabis", "press", True),
    ("BGH: Nicht geringe Menge bei Cannabis bleibt bei 7,5 Gramm THC", "cannabis", "press", True),
    ("THC-Grenzwert im Straßenverkehr: Bundestag stimmt Gesetzentwurf zu", "cannabis", "press", True),
    ("Anbauvereinigungen: Verwaltungsgericht lehnt Eilantrag gegen Erlaubnisentzug ab", "cannabis", "press", True),
    ("Eigenanbau: Wie viele Pflanzen sind erlaubt? OLG klärt Abstandsregel", "cannabis", "press", True),
    ("Study: light spectrum and yield in indoor cannabis cultivation", "cannabis", "press", True),
    ("Hemp regulation: EU debates THC limit for industrial hemp", "cannabis", "press", True),
    ("Cannabis-Samen kaufen: Die 10 besten Seedbanks 2026", "all", "advocacy", False),
    ("Growshop-Rabatt: 20 Prozent auf alle Cannabis-Seeds", "all", "advocacy", False),
    ("Anzeige: Neue LED-Growlampe jetzt erhältlich", "all", "advocacy", False),
    ("Vape pens, edibles and gummies: buy cannabis products online", "all", "press", False),
    ("Online-Rezept für Medizinalcannabis: Bundestag verschärft Regeln für Telemedizin", "cannabis", "press", True),
    ("BGH: Verkauf von Cannabis-Samen im Online-Shop ist keine verbotene Werbung", "cannabis", "press", True),
    ("Darf man Stecklinge tauschen? Gericht klärt Rechtslage zum Eigenanbau", "cannabis", "press", True),
    ("Nutzhanfanbau in Niedersachsen: Kammer informiert über Förderung", "garden", "horticulture", True),
    ("So dosierst du Cannabis richtig: Tipps für Einsteiger", "all", "advocacy", False),
    ("Cannabis rauchen: Die 5 besten Methoden im Vergleich", "all", "advocacy", False),
    ("The best cannabis strains for beginners", "all", "press", False),
    ("DHV-Ortsgruppe Halle verteilt erneut Stecklinge", "all", "advocacy", False),
    # law and research news about shops, seeds, vaporizers or dosage stays
    ("OLG: Werbung für CBD-Shop verstößt gegen KCanG", "cannabis", "press", True),
    ("Hanfverband kritisiert Shop-Verbot im KCanG-Entwurf", "all", "advocacy", True),
    ("Vaporizer-Verbot? Bundesregierung prüft Regeln für Cannabis", "cannabis", "press", True),
    ("EU-Kommission senkt THC-Grenzwert für Hanfsamen (hemp seeds)", "cannabis", "press", True),
    ("BfArM: Dosierungsempfehlungen für Medizinalcannabis", "cannabis", "press", True),
    ("Cannabis seeds germination under LED light: a study", "cannabis", "research", True),
    ("Cannabis Social Club: Tickets für Anhörung im Bundestag", "all", "advocacy", True),
])
def test_twenty_edge_titles_and_law_news_about_shops(title: str, filter_kind: str, kind: str, keeps: bool) -> None:
    assert (news.classify(title, "https://e.example/a", filter_kind, kind=kind) is not None) is keeps


def test_official_and_court_feeds_skip_the_drop_list_and_know_narcotics_law() -> None:
    assert news.classify("Growshop-Rabatt: Cannabis jetzt kaufen", "https://e.example/a", kind="official") == ["Cannabis"]
    assert news.classify("Growshop-Rabatt: Cannabis jetzt kaufen", "https://e.example/a", kind="press") is None
    assert news.classify("Änderung des Betäubungsmittelgesetzes", "https://e.example/a", kind="official") == [
        "Betäubungsmittel"]
    assert news.classify("BtMG-Novelle im Bundestag", "https://e.example/a", kind="court") == ["BtMG"]
    assert news.classify("Änderung des Betäubungsmittelgesetzes", "https://e.example/a", kind="advocacy") is None
    assert news.classify("Sorte des Monats nach dem Gesetz", "https://e.example/a", "all", kind="press") is None


def test_dropped_titles_are_counted_and_the_last_ones_kept(isolated_state) -> None:  # type: ignore[no-untyped-def]
    state, items = news.load_state(), []  # type: ignore[var-annotated]
    src = source("p", "https://p.example/f", publisher_kind="press", filter="all")
    entries = [{"title": f"Growshop-Rabatt {n}", "link": f"https://p.example/{n}", "guid": "", "published": None}
               for n in range(25)] + [{"title": "Cannabis Gesetz", "link": "https://p.example/g", "guid": "",
                                       "published": None}]
    assert len(news.merge(items, state, src, entries, NOW)) == 1
    st = state["sources"]["p"]
    assert st["dropped"] == 25 and st["kept"] == 1
    assert st["dropped_titles"] == [f"Growshop-Rabatt {n}" for n in range(5, 25)]


# ---------- review fixes: dedupe ----------

def test_a_lost_state_file_does_not_double_the_items(isolated_state) -> None:  # type: ignore[no-untyped-def]
    src = source("s", "https://e.example/f")
    entries = [{"title": "Cannabis A", "link": "https://e.example/a", "guid": "g", "published": None}]
    items: list[dict[str, Any]] = []
    news.merge(items, news.load_state(), src, entries, NOW)
    news.save_items(items)
    again = news.load_items()
    assert news.merge(again, {"sources": {}, "seen": []}, src, entries, NOW + timedelta(hours=1)) == []
    assert len(again) == 1


def test_equal_guids_of_two_sources_do_not_block_each_other(isolated_state) -> None:  # type: ignore[no-untyped-def]
    state, items = news.load_state(), []  # type: ignore[var-annotated]
    a = news.merge(items, state, source("a", "https://a.example/f"),
                   [{"title": "Cannabis A", "link": "https://a.example/1", "guid": "1", "published": None}], NOW)
    b = news.merge(items, state, source("b", "https://b.example/f"),
                   [{"title": "Cannabis B", "link": "https://b.example/1", "guid": "1", "published": None}], NOW)
    assert len(a) == len(b) == 1


def test_version_1_state_loses_its_unscoped_guid_keys(isolated_state) -> None:  # type: ignore[no-untyped-def]
    news.save_state({"version": 1, "sources": {}, "seen": ["link:e.example/a", "guid:1"]})
    state = news.load_state()
    assert state["version"] == news.STATE_VERSION and state["seen"] == ["link:e.example/a"]


def test_link_plus_date_brings_a_changed_page_back_once(isolated_state) -> None:  # type: ignore[no-untyped-def]
    state, items = news.load_state(), []  # type: ignore[var-annotated]
    src = source("seiten", "https://bmg.example/seiten.xml", dedupe="link+date")
    page = "https://bmg.example/themen/cannabis/faq.html"
    v1 = [{"title": "Fragen und Antworten", "link": page, "guid": page, "published": NOW - timedelta(days=3)}]
    v2 = [{"title": "Fragen und Antworten", "link": page, "guid": page, "published": NOW - timedelta(hours=2)}]
    assert len(news.merge(items, state, src, v1, NOW)) == 1
    assert news.merge(items, state, src, v1, NOW + timedelta(hours=1)) == []
    assert len(news.merge(items, state, src, v2, NOW + timedelta(hours=2))) == 1
    assert len(items) == 1 and items[0]["published"] == news._iso(NOW - timedelta(hours=2))  # replaced


def test_bgh_items_without_a_feed_date_wait_for_one(isolated_state) -> None:  # type: ignore[no-untyped-def]
    state, items = news.load_state(), []  # type: ignore[var-annotated]
    src = source("bgh", "https://bgh.example/f", publisher_kind="court",
                 attribution="Pressemitteilung des Bundesgerichtshofs vom TT.MM.JJJJ")
    entry = {"title": "Urteil zu Cannabis", "link": "https://bgh.example/1", "guid": "", "published": None}
    assert news.merge(items, state, src, [entry], NOW) == []
    dated = {**entry, "published": NOW - timedelta(days=1)}
    assert len(news.merge(items, state, src, [dated], NOW)) == 1  # not remembered while it had no date


# ---------- review fixes: fetching and view ----------

def test_a_garbage_retry_after_still_pauses_the_source(isolated_state) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async with Feeds() as srv:
            srv.routes["/busy"] = status(429, **{"Retry-After": "9" * 30})
            await fetch([source("busy", srv.url("/busy"))], now=NOW)
            st = news.load_state()["sources"]["busy"]
            assert st["backoff_until"] == news._iso(NOW + news.BACKOFF_MAX)

    asyncio.run(run())
    assert news._retry_after("²", NOW) == timedelta(0)  # a digit for isdigit, not for int


def test_sources_are_never_fetched_in_parallel(isolated_state) -> None:  # type: ignore[no-untyped-def]
    flight = {"now": 0, "max": 0}

    async def run() -> None:
        async with Feeds() as srv:
            async def counted(request: web.Request) -> web.Response:
                flight["now"] += 1
                flight["max"] = max(flight["max"], flight["now"])
                await asyncio.sleep(0.05)
                flight["now"] -= 1
                return await feed(fixture("atom.xml"))(request)

            for n in range(4):
                srv.routes[f"/f{n}"] = counted
            await fetch([source(f"s{n}", srv.url(f"/f{n}")) for n in range(4)], now=NOW)
            assert srv.hits("/f3") == 1

    asyncio.run(run())
    assert flight["max"] == 1


def test_a_pause_longer_than_the_window_is_shown_as_a_gap(isolated_state, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async with Feeds() as srv:
            srv.routes["/hib"] = feed(fixture("atom.xml"))
            src = source("hib", srv.url("/hib"), window_hours=8)
            path = tmp_path / "sources.json"
            listed = {**src, "feed_url": "https://hib.example/hib.rss"}  # the list only takes https
            path.write_text(json.dumps({"sources": [listed]}), encoding="utf-8")
            monkeypatch.setattr(news, "SOURCES_FILE", path)
            await fetch([src], now=NOW)
            assert news.view(private=True, now=NOW)["sources"][0]["gap"] is False  # first fetch: no gap
            await fetch([src], now=NOW + timedelta(hours=6))
            assert news.load_state()["sources"]["hib"]["gap"] is False
            await fetch([src], now=NOW + timedelta(hours=30))
            assert news.view(private=True, now=NOW)["sources"][0]["gap"] is True

    asyncio.run(run())


def test_not_private_any_more_removes_private_titles_from_the_store(isolated_state) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async with Feeds() as srv:
            srv.routes["/amt"] = feed(fixture("atom.xml"))
            srv.routes["/verband"] = feed(fixture("rss2.xml"))
            sources = [source("amt", srv.url("/amt")),
                       source("verband", srv.url("/verband"), rights="private", publisher_kind="advocacy")]
            await fetch(sources, private=True, now=NOW)
            assert {i["rights"] for i in news.load_items()} == {"free", "private"}
            await fetch(sources[:1], private=False, now=NOW + timedelta(days=1))
            assert {i["rights"] for i in news.load_items()} == {"free"}

    asyncio.run(run())


def test_view_days_window_link_target_recheck_and_no_internal_keys(isolated_state) -> None:  # type: ignore[no-untyped-def]
    state, items = news.load_state(), []  # type: ignore[var-annotated]
    src = source("bmg-pressemitteilungen", "https://bmg.example/f")
    for n, age in enumerate((1, 3, 9)):
        news.merge(items, state, src, [{"title": f"Cannabis {n}", "link": f"https://bmg.example/{n}", "guid": "",
                                        "published": None}], NOW - timedelta(days=age))
    news.save_items(items)
    news.save_state(state)
    out = news.view(days=7, limit=10, now=NOW)
    assert [i["title"] for i in out["items"]] == ["Cannabis 0", "Cannabis 1"]
    assert all("keys" not in i and i["link_target"] == "_blank" for i in out["items"])
    assert out["quiet_since"] == news._iso(NOW - timedelta(days=1))
    recheck = {s["id"]: s["recheck_due"] for s in out["sources"]}
    assert recheck["bmg-pressemitteilungen"] is False
    later = news.view(now=NOW + timedelta(days=100))
    assert all(s["recheck_due"] for s in later["sources"])


# ---------- schedule ----------

def test_due_once_a_day_after_six_or_when_stale() -> None:
    day = datetime(2026, 10, 22, 0, 0).astimezone()
    at = lambda h, m=0, d=0: (day + timedelta(days=d)).replace(hour=h, minute=m)  # noqa: E731
    assert news.due(at(3), {}) is True  # never run: at start, any hour
    ran = {"last_run": news._iso(at(6, 5)), "last_success": news._iso(at(6, 5))}
    assert news.due(at(9), ran) is False  # today's run is done
    assert news.due(at(5, 59, 1), ran) is False  # tomorrow, but before 06:00 and not yet 24 h
    assert news.due(at(6, 0, 1), ran) is True  # tomorrow after 06:00
    stale = {"last_run": news._iso(at(3, 0, -2)), "last_success": news._iso(at(3, 0, -2))}
    assert news.due(at(2), stale) is True  # started at night after two days off: at start
    failing = {"last_run": news._iso(at(2)), "last_success": news._iso(at(3, 0, -2))}
    assert news.due(at(2, 30), failing) is False  # nothing works, but not twice within an hour
    assert news.due(at(3, 1), failing) is True


# ---------- the source list ----------

def test_source_list_matches_the_check() -> None:
    data = json.loads(news.SOURCES_FILE.read_text(encoding="utf-8"))
    sources = news.load_sources(private=True)
    ids = [s["id"] for s in sources]
    assert len(sources) == len(data["sources"]) == 14
    # Bundesrat (personal use only), BGH and beck-aktuell (no commercial use) carry only a private install.
    assert {s["id"] for s in sources if s["rights"] == "private"} == {
        "dhv", "bundestag-hib", "bundestag-gesundheit", "lto", "bundesrat-kompakt", "bundesrat-neueingaenge",
        "bgh-pressemitteilungen", "beck-aktuell"}
    assert len(news.load_sources(private=False)) == 6
    assert len(news.load_sources()) == 6  # no switch set: closed, never open by default
    for sid in ("bgbl-1", "bgh-pressemitteilungen", "bundesrat-kompakt", "bundesrat-neueingaenge"):
        assert {s["id"]: s for s in sources}[sid]["crawl_delay_s"] == 30, sid  # robots.txt Crawl-delay: 30
    assert {d["id"]: d for d in data["disabled"]}["bverwg-pressemitteilungen"]["crawl_delay_s"] == 30
    assert {s["id"]: s for s in sources}["bmg-seiten"]["dedupe"] == "link+date"
    assert all(s.get("checked") for s in sources)
    assert {d["id"] for d in data["disabled"]} == {"biorxiv-plant-biology", "bverwg-pressemitteilungen"}
    assert all(d["reason"] for d in data["disabled"])
    by_id = {s["id"]: s for s in sources}
    assert by_id["bgh-pressemitteilungen"]["attribution"] == "Pressemitteilung des Bundesgerichtshofs vom TT.MM.JJJJ"
    assert by_id["beck-aktuell"]["attribution"] == "beck-aktuell. HEUTE IM RECHT"
    assert by_id["bundesrat-kompakt"]["crawl_delay_s"] == by_id["bundesrat-neueingaenge"]["crawl_delay_s"] == 30
    assert by_id["lto"]["feed_url"] == "https://www.lto.de/rss/feed.xml"
    assert by_id["jki-kulturpflanzen"]["filter"] == by_id["lwk-gartenbau"]["filter"] == "garden"
    assert by_id["dhv"]["publisher_kind"] == "advocacy"
    banned = ("pharmazeutische-zeitung", "krautinvest", "g-ba.de", "bioeg", "zdf", "frontiersin", "ncsu", "umd.edu",
              "illinois", "biorxiv", "bverwg", "taz.de", "hanfjournal", "leafly")
    assert not [s["feed_url"] for s in sources if any(b in s["feed_url"] for b in banned)]
    assert len(set(ids)) == len(ids)


def test_the_install_switch_opens_private_sources_only_when_set_on_purpose(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert news.is_private() is False
    monkeypatch.setattr(config, "NEWS_PRIVATE", "yes", raising=False)  # only True itself counts
    assert news.is_private() is False
    monkeypatch.setattr(config, "NEWS_PRIVATE", True, raising=False)
    assert news.is_private() is True and len(news.load_sources()) == 14
    assert news.is_private(False) is False


def test_a_broken_source_entry_is_skipped_not_fatal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": [source("ok", "https://ok.example/feed"),
                                            source("plain", "http://plain.example/feed"),
                                            source("odd", "https://odd.example/feed", rights="maybe"),
                                            source("ok", "https://dupe.example/feed"),
                                            source("off", "https://off.example/feed", active=False),
                                            source("dd", "https://dd.example/feed", dedupe="guid")]}),
                    encoding="utf-8")
    assert [s["id"] for s in news.load_sources(path=path)] == ["ok"]


def test_new_files_are_utf8_lf_without_bom_or_em_dash() -> None:
    repo = Path(__file__).resolve().parent.parent
    files = [repo / "vgrow" / "news.py", repo / "vgrow" / "news_sources.json", Path(__file__),
             *sorted(FIXTURES.glob("*.xml"))]
    for path in files:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path.name
        # LF as committed; a checkout with core.autocrlf may turn every line into CRLF, never only some.
        assert b"\r\n" not in raw or raw.count(b"\n") == raw.count(b"\r\n"), path.name
        text = raw.decode("utf-8")
        assert chr(0x2014) not in text, path.name  # no em dash
    assert config.STATE_DIR.name == "state"  # the conftest isolation is active for this module too
