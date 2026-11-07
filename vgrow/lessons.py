"""The owner's lessons (Tracker M5d; the owner, 23.10.2026: "worauf beim nächsten Grow achten", "so dass sich die KI
verbessert"): a line of the grow report, or his own words, kept as Hank's own experience (spec 8.4). No lesson comes
into being without his tap, and he can change or delete it.

They live in Hank's ExperienceStore, so Hank's index picks them up when the file changes and names them apart from the
sourced values. A lesson is named after the plant's variety (or its name), and Hank says so: what was learned on one
variety reads as that, it is not hidden on the next one (aphids do not ask for the variety).

GET /api/lessons: all of them, newest first. POST /api/lessons: {"action": "add", "plant", "text"},
{"action": "update", "id", "text"} or {"action": "delete", "id"}; answers with the whole list.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
from typing import Any

from aiohttp import web

from . import workspace
from .hank.stores import ExperienceStore

MAX_TEXT = 1000
_LOCK = threading.Lock()  # one change of the file at a time: the handlers run in worker threads


class Invalid(ValueError):
    """The code the page translates (web/workspace.js)."""


def clean(value: Any) -> str:
    """One line of plain words: Hank reads it as a passage."""
    text = re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""
    if not text or len(text) > MAX_TEXT:
        raise Invalid("lesson_text")
    return text


def view() -> list[dict[str, Any]]:
    rows = [e for e in ExperienceStore().items() if isinstance(e.get("text"), str)]
    return [{k: e.get(k) for k in ("id", "text", "time", "run", "variety", "plant")} for e in reversed(rows)]


def add(plant: Any, text: Any) -> None:
    note = clean(text)
    if not isinstance(plant, str) or not plant:
        raise Invalid("lesson_plant")
    with workspace.database() as db:
        try:
            record = workspace.record_data(db, plant)["record"]
        except workspace.Invalid as err:
            raise Invalid("lesson_plant") from err
    store = ExperienceStore()
    with _LOCK:
        if not any(e.get("plant") == plant and e.get("text") == note for e in store.items()):  # a second tap adds nothing
            store.add(note, stage=None, run=record.get("variety") or record.get("name"), plant=plant,
                      variety=record.get("variety"))


def update(ident: Any, text: Any) -> None:
    note = clean(text)
    with _LOCK:
        if ExperienceStore().change(str(ident), note) is None:
            raise Invalid("lesson_missing")


def delete(ident: Any) -> None:
    with _LOCK:
        if not ExperienceStore().remove(str(ident)):
            raise Invalid("lesson_missing")


def act(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        raise Invalid("lesson_text")
    action = body.get("action")
    if action == "add":
        add(body.get("plant"), body.get("text"))
    elif action == "update":
        update(body.get("id"), body.get("text"))
    elif action == "delete":
        delete(body.get("id"))
    else:
        raise Invalid("lesson_text")
    return view()


def register(app: web.Application) -> None:
    async def read(_request: web.Request) -> web.Response:
        return web.json_response({"lessons": await asyncio.to_thread(view)}, headers={"Cache-Control": "no-store"})

    async def write(request: web.Request) -> web.Response:
        try:
            out = await asyncio.to_thread(act, await request.json())
        except Invalid as err:
            return web.json_response({"error": str(err)}, status=400)
        except ValueError:  # not JSON
            return web.json_response({"error": "lesson_text"}, status=400)
        except (OSError, sqlite3.Error):
            return web.json_response({"error": "workspace_storage"}, status=503)
        return web.json_response({"lessons": out}, headers={"Cache-Control": "no-store"})

    app.router.add_get("/api/lessons", read)
    app.router.add_post("/api/lessons", write)
