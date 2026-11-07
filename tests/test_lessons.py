"""A lesson exists only on the owner's tap, names its plant's variety, and he can change or delete it."""
from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import lessons, server, workspace as ws
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.hank.stores import ExperienceStore
from vgrow.plant import PlantLog

PROFILE = {"plant": {"strain": "Purple Punch", "start_date": "2026-11-06"}, "stages": []}


@pytest.fixture
def plant() -> str:
    ws.initialize("VGrow Y18", PROFILE, PlantLog())
    return ws.mutate({"revision": ws.view()["revision"], "action": "plant_create", "box": ws.NATIVE_BOX,
                      "name": "Erste", "variety": "Purple Punch", "phase": "Wachstum", "started": "2026-11-06"})


def test_a_lesson_is_named_after_the_variety_and_a_second_tap_adds_nothing(plant: str) -> None:
    lessons.act({"action": "add", "plant": plant, "text": "  Unter  die Blätter\nschauen. "})
    out = lessons.act({"action": "add", "plant": plant, "text": "Unter die Blätter schauen."})
    assert [(r["text"], r["run"], r["variety"], r["plant"]) for r in out] == [
        ("Unter die Blätter schauen.", "Purple Punch", "Purple Punch", plant)]


def test_the_newest_comes_first_and_change_and_delete_touch_only_their_own(plant: str) -> None:
    lessons.act({"action": "add", "plant": plant, "text": "Erste Lehre."})
    out = lessons.act({"action": "add", "plant": plant, "text": "Zweite Lehre."})
    assert [r["text"] for r in out] == ["Zweite Lehre.", "Erste Lehre."]
    out = lessons.act({"action": "update", "id": out[1]["id"], "text": "Erste, geändert."})
    assert [r["text"] for r in out] == ["Zweite Lehre.", "Erste, geändert."]
    out = lessons.act({"action": "delete", "id": out[0]["id"]})
    assert [r["text"] for r in out] == ["Erste, geändert."]


def test_a_note_from_the_command_line_stays_readable_and_is_left_alone(plant: str) -> None:
    ExperienceStore().add("Vom Terminal.", stage="veg", run=1)
    out = lessons.act({"action": "add", "plant": plant, "text": "Von der Seite."})
    assert [r["text"] for r in out] == ["Von der Seite.", "Vom Terminal."]
    assert ExperienceStore().items()[0]["stage"] == "veg"


@pytest.mark.parametrize(("body", "code"), [
    ({"action": "add", "plant": "nope", "text": "x"}, "lesson_plant"),
    ({"action": "add", "text": "x"}, "lesson_plant"),
    ({"action": "add", "plant": "p", "text": "   "}, "lesson_text"),
    ({"action": "add", "plant": "p", "text": "x" * 1001}, "lesson_text"),
    ({"action": "add", "plant": "p", "text": 5}, "lesson_text"),
    ({"action": "update", "id": "e-none", "text": "x"}, "lesson_missing"),
    ({"action": "delete", "id": "e-none"}, "lesson_missing"),
    ({"action": "drop"}, "lesson_text"),
    (["add"], "lesson_text"),
])
def test_what_is_refused_says_why_and_writes_nothing(plant: str, body: object, code: str) -> None:
    with pytest.raises(lessons.Invalid) as err:
        lessons.act(body)
    assert str(err.value) == code and ExperienceStore().items() == []


def test_the_route_needs_the_key_and_answers_the_whole_list(plant: str) -> None:
    async def go() -> None:
        app = server.make_app(DemoBox(), Alerts(), "k" * 32, plant_log=PlantLog())
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/lessons")).status == 403
            assert (await client.post("/api/lessons", json={"action": "add", "plant": plant, "text": "x"})).status == 403
            client.session.cookie_jar.update_cookies({server.COOKIE: "k" * 32})
            assert (await (await client.get("/api/lessons")).json()) == {"lessons": []}
            added = await client.post("/api/lessons", json={"action": "add", "plant": plant, "text": "Merken."})
            assert added.status == 200 and (await added.json())["lessons"][0]["text"] == "Merken."
            bad = await client.post("/api/lessons", json={"action": "add", "plant": plant, "text": ""})
            assert bad.status == 400 and (await bad.json())["error"] == "lesson_text"
            broken = await client.post("/api/lessons", data="kein json", headers={"Content-Type": "application/json"})
            assert broken.status == 400

    asyncio.run(go())
