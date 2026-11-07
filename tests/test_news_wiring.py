"""The news watcher wired into the cockpit: the private switch, GET /api/news, the demo list, the background task and
the page's entry. No network anywhere: the route only reads the store, the demo never fetches, the task's fetch is a
stand-in, and a real fetch would fail the test."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

import test_web_i18n as web
from vgrow import config, news, server
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, NewsWatcher, make_app

WEB = Path(__file__).resolve().parent.parent / "web"
FREE, PRIVATE = "bmg-pressemitteilungen", "bgh-pressemitteilungen"  # one of each in vgrow/news_sources.json


@pytest.fixture(autouse=True)
def no_network(monkeypatch):  # type: ignore[no-untyped-def]
    """Any fetch in these tests is a bug: the route and the demo read, they never ask a feed."""
    async def forbidden(*_: Any, **__: Any) -> list[dict[str, Any]]:
        raise AssertionError("a news fetch ran")

    monkeypatch.setattr(news, "fetch_all", forbidden)
    monkeypatch.setattr(config, "NEWS_PRIVATE", False)


class RealBox(DemoBox):
    """The demo box's readings without its demo flag: the route takes the real store."""
    demo = False


def item(source_id: str, number: int, *, rights: str, seen: datetime) -> dict[str, Any]:
    stamp = seen.isoformat(timespec="seconds")
    return {"id": f"{source_id}-{number}", "source_id": source_id, "source_name": source_id, "publisher_kind": "official",
            "rights": rights, "lang": "de", "title": f"Cannabis {source_id} {number}", "title_cut": False,
            "link": f"https://example.org/{source_id}/{number}", "published": stamp, "dated": "feed",
            "first_seen": stamp, "matched": ["cannabis"], "keys": [f"link:{source_id}/{number}"]}


def seed(now: datetime, free: int = 3, private: int = 2, old: int = 0) -> None:
    items = [item(FREE, n, rights="free", seen=now - timedelta(hours=n + 1)) for n in range(free)]
    items += [item(PRIVATE, n, rights="private", seen=now - timedelta(hours=n + 1)) for n in range(private)]
    items += [item(FREE, 100 + n, rights="free", seen=now - timedelta(days=8 + n)) for n in range(old)]
    news.save_items(items)
    state = news.load_state()
    state.update(last_run=now.isoformat(timespec="seconds"), first_run=(now - timedelta(days=30)).isoformat())
    state["sources"] = {FREE: {"last_ok": now.isoformat(timespec="seconds"), "gap": True}}
    news.save_state(state)


async def get_news(app: Any, *, cookie: bool = True, lang: str = "de") -> tuple[int, dict[str, Any]]:
    async with TestClient(TestServer(app)) as client:
        if cookie:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
        resp = await client.get("/api/news", headers={"X-Lang": lang})
        try:
            body = await resp.json()
        except Exception:
            body = {"text": await resp.text()}
        return resp.status, body


def key_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return sorted(value)
    return type(value).__name__


# ---------- the private switch ----------

@pytest.mark.parametrize(("line", "expected"), [
    ("VGROW_NEWS_PRIVATE=1", True),
    ('VGROW_NEWS_PRIVATE="1"', True),
    ("VGROW_NEWS_PRIVATE = 1", True),
    ("VGROW_NEWS_PRIVATE=true", False),
    ("VGROW_NEWS_PRIVATE=yes", False),
    ("VGROW_NEWS_PRIVATE=0", False),
    ("VGROW_NEWS_PRIVATE=", False),
    ("VGROW_NEWS_PRIVATE=11", False),
    ("# VGROW_NEWS_PRIVATE=1", False),
    ("VGROW_LANG=en", False),
])
def test_only_a_one_switches_private_on(tmp_path, monkeypatch, line: str, expected: bool) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / ".vgrow.env"
    env.write_text(f"VIVOSUN_EMAIL=a@b.c\n{line}\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)
    assert config.news_private() is expected


def test_private_is_off_without_a_file_and_by_default(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "missing.env")
    assert config.news_private() is False
    assert news.is_private() is False  # config.NEWS_PRIVATE is False until serve() reads the file


def test_an_unreadable_env_file_keeps_news_public(tmp_path, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    folder = tmp_path / "env-is-a-folder"
    folder.mkdir()
    monkeypatch.setattr(config, "ENV_FILE", folder)
    with caplog.at_level(logging.WARNING, logger="vgrow.config"):
        assert config.news_private() is False
    assert "news stay public" in caplog.text


def test_start_news_reads_the_switch_and_the_demo_starts_nothing(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / ".vgrow.env"
    env.write_text("VGROW_NEWS_PRIVATE=1\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)

    async def run() -> None:
        assert server.start_news(demo=True) is None
        assert config.NEWS_PRIVATE is False  # the demo does not even read it
        watcher = server.start_news(demo=False)
        assert isinstance(watcher, NewsWatcher) and watcher.task is not None and not watcher.task.done()
        assert config.NEWS_PRIVATE is True and news.is_private() is True
        await server.stop_news(watcher)
        assert watcher.task is None
        await server.stop_news(None)  # nothing started: nothing to stop

    asyncio.run(run())


# ---------- GET /api/news ----------

def test_route_sits_behind_the_key_cookie() -> None:
    status, _ = asyncio.run(get_news(make_app(RealBox(), Alerts(), "k"), cookie=False))
    assert status == 403


def test_route_answers_the_view_of_the_last_seven_days_without_private_sources() -> None:
    now = datetime.now().astimezone()
    seed(now, free=12, private=2, old=2)
    status, body = asyncio.run(get_news(make_app(RealBox(), Alerts(), "k")))
    assert status == 200
    assert set(body) == {"items", "last_fetch", "sources", "quiet_since", "private", "demo"}
    assert body["private"] is False and body["demo"] is False
    assert len(body["items"]) == 10  # limit 10
    assert {i["source_id"] for i in body["items"]} == {FREE}  # the private source stays out
    assert all(not i["title"].endswith((" 100", " 101")) for i in body["items"])  # older than 7 days: out
    assert all("keys" not in i for i in body["items"])
    assert PRIVATE not in {s["id"] for s in body["sources"]}
    assert any(s["id"] == FREE and s["gap"] for s in body["sources"])


def test_route_shows_private_sources_only_with_the_switch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now().astimezone()
    seed(now, free=1, private=2)
    monkeypatch.setattr(config, "NEWS_PRIVATE", True)
    status, body = asyncio.run(get_news(make_app(RealBox(), Alerts(), "k")))
    assert status == 200 and body["private"] is True
    assert {i["source_id"] for i in body["items"]} == {FREE, PRIVATE}
    assert PRIVATE in {s["id"] for s in body["sources"]}


def test_route_with_an_empty_store_says_nothing_new() -> None:
    status, body = asyncio.run(get_news(make_app(RealBox(), Alerts(), "k")))
    assert status == 200
    assert body["items"] == [] and body["last_fetch"] is None and body["quiet_since"] is None
    assert body["sources"] and not any(s["ok"] for s in body["sources"])  # "0 of N sources reached"


def test_a_broken_news_view_costs_the_list_not_the_cockpit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def broken(**_: Any) -> dict[str, Any]:
        raise RuntimeError("store exploded")

    monkeypatch.setattr(news, "view", broken)

    async def run() -> None:
        async with TestClient(TestServer(make_app(RealBox(), Alerts(), "k"))) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k"})
            resp = await client.get("/api/news", headers={"X-Lang": "en"})
            assert resp.status == 503
            assert (await resp.json()) == {"error": "The news cannot be read right now."}
            assert (await client.get("/api/state")).status == 200  # the cockpit answers on

    asyncio.run(run())


# ---------- the demo ----------

def test_demo_serves_its_fixed_list_and_never_reads_the_store(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def no_view(**_: Any) -> dict[str, Any]:
        raise AssertionError("the demo read the real store")

    monkeypatch.setattr(news, "view", no_view)
    status, de = asyncio.run(get_news(make_app(DemoBox(), Alerts(), "k"), lang="de"))
    _, en = asyncio.run(get_news(make_app(DemoBox(), Alerts(), "k"), lang="en"))
    assert status == 200 and de["demo"] is True and de["private"] is False
    assert [i["publisher_kind"] for i in de["items"]] == ["official", "court", "advocacy", "research"]
    assert all(i["link"].startswith(server.DEMO_NEWS_LINK) for i in de["items"])
    assert all("(Demo)" in i["source_name"] and i["title"].startswith("Beispiel:") for i in de["items"])
    assert all("(demo)" in i["source_name"] and i["title"].startswith("Example:") for i in en["items"])
    court = de["items"][1]
    assert court["attribution"].startswith("Pressemitteilung des Beispielgerichts vom ")
    assert re.search(r"\d{2}\.\d{2}\.\d{4}", court["attribution"])
    sources = de["sources"]
    assert sum(s["ok"] for s in sources) == len(sources) - 1  # one not reached
    assert [s["id"] for s in sources if s["gap"]] == ["demo-research"]
    assert len(de["items"]) <= server.NEWS_LIMIT


def test_demo_list_has_the_shape_of_the_real_view() -> None:
    now = datetime.now().astimezone()
    seed(now, free=1, private=0)
    real = news.view(days=7, limit=10)
    demo = server.demo_news(now)
    assert sorted(demo) == sorted(real)
    assert key_tree(demo["items"][0]) == key_tree(real["items"][0])
    assert key_tree(demo["sources"][0]) == key_tree(real["sources"][0])
    seen = [datetime.fromisoformat(i["first_seen"]) for i in demo["items"]]
    assert seen == sorted(seen, reverse=True) and all(now - s <= timedelta(days=7) for s in seen)


# ---------- the background task ----------

def test_tick_fetches_only_when_due(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def refresh(*_: Any, **__: Any) -> list[dict[str, Any]]:
        calls.append("refresh")
        return []

    monkeypatch.setattr(news, "refresh", refresh)
    monkeypatch.setattr(news, "due", lambda now, state: False)
    assert asyncio.run(NewsWatcher().tick()) is False and calls == []
    monkeypatch.setattr(news, "due", lambda now, state: True)
    assert asyncio.run(NewsWatcher().tick()) is True and calls == ["refresh"]


def test_tick_uses_the_real_due_rule_on_the_stored_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def refresh(*_: Any, **__: Any) -> list[dict[str, Any]]:
        calls.append("refresh")
        return []

    monkeypatch.setattr(news, "refresh", refresh)
    now = datetime.now().astimezone()
    state = news.load_state()
    state.update(last_run=(now - timedelta(minutes=10)).isoformat(), last_success=(now - timedelta(minutes=10)).isoformat())
    news.save_state(state)
    assert asyncio.run(NewsWatcher().tick()) is False  # ran ten minutes ago: not again
    state.update(last_run=(now - timedelta(days=2)).isoformat(), last_success=(now - timedelta(days=2)).isoformat())
    news.save_state(state)
    assert asyncio.run(NewsWatcher().tick()) is True and calls == ["refresh"]


def test_two_ticks_at_once_fetch_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def run() -> None:
        release = asyncio.Event()

        async def slow_refresh(*_: Any, **__: Any) -> list[dict[str, Any]]:
            calls.append("refresh")
            await release.wait()
            return []

        monkeypatch.setattr(news, "refresh", slow_refresh)
        monkeypatch.setattr(news, "due", lambda now, state: True)
        watcher = NewsWatcher()
        first = asyncio.create_task(watcher.tick())
        await asyncio.sleep(0)
        assert watcher.running is True
        assert await watcher.tick() is False  # the second look while the first fetch runs does nothing
        release.set()
        assert await first is True
        assert watcher.running is False

    asyncio.run(run())
    assert calls == ["refresh"]


def test_tick_catches_and_logs_every_error(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    async def failing(*_: Any, **__: Any) -> list[dict[str, Any]]:
        raise OSError("network down")

    monkeypatch.setattr(news, "refresh", failing)
    monkeypatch.setattr(news, "due", lambda now, state: True)
    watcher = NewsWatcher()
    with caplog.at_level(logging.ERROR, logger="vgrow.server"):
        assert asyncio.run(watcher.tick()) is False
    assert "News check failed" in caplog.text and watcher.running is False

    def broken_due(now: datetime, state: dict[str, Any]) -> bool:
        raise ValueError("bad state")

    monkeypatch.setattr(news, "due", broken_due)
    assert asyncio.run(watcher.tick()) is False and watcher.running is False


def test_loop_waits_first_then_checks_about_every_ten_minutes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    events: list[Any] = []

    async def fake_sleep(seconds: float) -> None:
        events.append(seconds)
        if len([e for e in events if e != "tick"]) > 3:
            raise asyncio.CancelledError

    async def tick(self: NewsWatcher) -> bool:
        events.append("tick")
        return False

    monkeypatch.setattr(NewsWatcher, "tick", tick)
    assert server.NEWS_CHECK_S == 600.0

    async def run() -> None:
        with pytest.raises(asyncio.CancelledError):
            await NewsWatcher(sleep=fake_sleep).loop()

    asyncio.run(run())
    assert events == [server.NEWS_FIRST_CHECK_S, "tick", 600.0, "tick", 600.0, "tick", 600.0]


def test_a_failing_tick_never_ends_the_loop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    looks: list[int] = []

    async def failing(*_: Any, **__: Any) -> list[dict[str, Any]]:
        looks.append(1)
        raise RuntimeError("boom")

    async def fake_sleep(_: float) -> None:
        if len(looks) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(news, "refresh", failing)
    monkeypatch.setattr(news, "due", lambda now, state: True)

    async def run() -> None:
        with pytest.raises(asyncio.CancelledError):
            await NewsWatcher(sleep=fake_sleep).loop()

    asyncio.run(run())
    assert len(looks) == 3


# ---------- the page ----------

def news_js() -> str:
    return (WEB / "news.js").read_text(encoding="utf-8")


def test_one_responsive_resource_entry_hidden_until_the_server_answers() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    resources = html[html.index('<div class="side-resources"'):html.index('<div class="side-foot"')]
    line = '<button type="button" class="news-open" data-news-open hidden data-i18n="news.title"></button>'
    assert resources.count("data-news-open") == 1
    assert html.count("data-news-open") == 1 and html.count(line) == 1
    news_lines = [x.strip() for x in html.splitlines() if "news" in x.lower()]
    assert news_lines == ['<link rel="stylesheet" href="/static/news.css">', line,
                          '<script src="/static/news.js"></script>']
    order = web.page().order
    assert order.index("/static/app.js") < order.index("/static/news.js")
    assert "news.js" in server.PAGE_FILES and "news.css" in server.PAGE_FILES


def test_news_js_builds_text_only_and_opens_links_safely() -> None:
    src = news_js()
    code_only = "\n".join(x for x in src.splitlines() if not x.lstrip().startswith("//"))
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                   "<img", "createElement(\"img\")", "description", "teaser", "summary"):
        assert banned not in code_only, banned
    assert src.count(".href =") == 1 and "a.href = url;" in src
    assert "/^https?:\\/\\//i.test(url)" in src
    assert 'a.target = "_blank";' in src and 'a.rel = "noopener noreferrer";' in src
    code = "\n".join(x for x in src.splitlines() if not x.startswith("//")).strip()
    assert code.startswith("(function () {") and code.endswith("})();")  # no globals that could clash with app.js


def test_every_publisher_kind_has_a_label_and_advocacy_says_so() -> None:
    kinds = dict(re.findall(r'^\s+(\w+): "(news\.kind\.\w+)",$', news_js(), flags=re.MULTILINE))
    assert set(kinds) == set(news.KINDS)
    texts = web.dictionary()
    assert texts["de"][kinds["advocacy"]] == "Interessenverband"
    assert texts["en"][kinds["advocacy"]] == "advocacy group"


def test_news_texts_exist_in_both_languages_and_files_are_clean() -> None:
    texts = web.dictionary()
    keys = {k for k in texts["de"] if k.startswith("news.")}
    assert keys and keys == {k for k in texts["en"] if k.startswith("news.")}
    assert "headlines and links" in texts["en"]["news.explain"]
    assert "Überschriften und Links" in texts["de"]["news.explain"]
    for name in ("news.js", "news.css"):
        raw = (WEB / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), name
        assert b"\r\n" not in raw, name
        assert web.EM_DASH not in raw.decode("utf-8"), name


def test_news_js_says_the_source_once_and_retries_a_failed_start() -> None:
    src = news_js()
    assert 'attr !== String(item.source_name || "")' in src  # no "Quelle: X" under the name X
    assert 'document.addEventListener("visibilitychange", retry)' in src  # a restart never hides the entry for good
    assert "if (N.dialog && N.dialog.open) load();" in src  # a language switch fetches the open list again


def test_preview_carries_the_scenes_and_the_news(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """The claude.ai preview inlines both guests and answers /api/news with the demo's list in both languages."""
    import test_hank_web

    page = test_hank_web._build_preview(tmp_path / "preview.html")
    capsys.readouterr()
    for name in ("hank-scenes.js", "news.js"):
        assert (WEB / name).read_text(encoding="utf-8").strip() in page, name
    for name in ("hank-scenes.css", "news.css"):
        assert (WEB / name).read_text(encoding="utf-8").strip() in page, name
    assert page.index("window.HANK_I18N = ") < page.index("window.HankScenes = api") < page.index("function mascot(")
    assert page.index("function mascot(") < page.index("function itemRow(")
    news = json.loads(re.search(r"const PREVIEW_NEWS = (\{.*?\});\n", page).group(1))
    assert set(news) == {"de", "en"} and all(news[lang]["demo"] and news[lang]["items"] for lang in news)
    assert all(i["link"].startswith(server.DEMO_NEWS_LINK) for lang in news for i in news[lang]["items"])
    mock = (WEB.parent / "scripts" / "preview_mock.js").read_text(encoding="utf-8")
    assert 'path === "/api/news"' in mock
