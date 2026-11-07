"""The camera suggests a stage only after three daily photos in a row show it, and never backwards (owner,
01.11.2026: the single reading jumped between early and mid flower from 24. to 30.10.2026, always "high")."""
import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from vgrow import vision
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.server import COOKIE, make_app

STAGES = [{"key": "veg", "label": "Wachstum"}, {"key": "early_flower", "label": "Frühe Blüte"},
          {"key": "mid_flower", "label": "Mittlere Blüte"}, {"key": "late_flower", "label": "Späte Blüte"}]


def reading(day: str, stage: str | None, *, at: str = "12-05-00-000000", photo: str | None = None,
            observation: bool = False) -> None:
    vision.ANALYSES.mkdir(parents=True, exist_ok=True)
    record = {"date": f"{day}T12:05", "photo": photo or f"{day}.jpg",
              "result": {"stage_guess": stage} if stage else None}
    if observation:
        record["observation_only"] = True
    (vision.ANALYSES / f"{day}_{at}.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_run_counts_calendar_days_in_a_row_with_the_same_stage() -> None:
    assert vision.stage_run() is None  # no readings yet
    reading("2026-10-24", "early_flower")
    reading("2026-10-25", "mid_flower")
    reading("2026-10-26", "mid_flower")
    reading("2026-10-27", "mid_flower")
    assert vision.stage_run() == {"stage": "mid_flower", "days": 3, "since": "2026-10-25"}
    # the day's last reading counts; an observation photo and a manual reading of another camera never do
    reading("2026-10-27", "early_flower", at="18-00-00-000000")
    reading("2026-10-27", "late_flower", at="19-00-00-000000", photo="2026-10-27_usb_19-00-00-000000.jpg")
    reading("2026-10-27", "late_flower", at="20-00-00-000000", observation=True)
    assert vision.stage_run() == {"stage": "early_flower", "days": 1, "since": "2026-10-27"}


def test_a_missing_day_ends_the_run_and_unclear_is_no_run() -> None:
    reading("2026-10-24", "mid_flower")
    reading("2026-10-26", "mid_flower")  # the 25th has no reading
    reading("2026-10-27", "mid_flower")
    assert vision.stage_run() == {"stage": "mid_flower", "days": 2, "since": "2026-10-26"}
    reading("2026-10-28", None)  # a failed reading is no reading: the run stands
    assert vision.stage_run()["days"] == 2
    reading("2026-10-29", "unclear")
    assert vision.stage_run() is None


def test_the_suggestion_needs_three_days_and_never_goes_backwards() -> None:
    plant = {"stages": STAGES, "stage": {"key": "early_flower"}}
    three = {"stage": "mid_flower", "days": 3, "since": "2026-10-25"}
    assert vision.stage_suggestion(three, plant) == {"key": "mid_flower", "label": "Mittlere Blüte", "days": 3,
                                                     "since": "2026-10-25"}
    assert vision.stage_suggestion({**three, "days": 2}, plant) is None
    assert vision.stage_suggestion({**three, "stage": "early_flower"}, plant) is None  # the stage it is in
    assert vision.stage_suggestion({**three, "stage": "veg"}, plant) is None  # backwards
    assert vision.stage_suggestion({**three, "stage": "harvest"}, plant) is None  # not one of the plant's stages
    assert vision.stage_suggestion(three, {"stages": STAGES, "stage": None})["key"] == "mid_flower"
    assert vision.stage_suggestion(None, plant) is None and vision.stage_suggestion(three, None) is None


def test_the_state_carries_the_run_and_the_suggestion() -> None:
    for day in ("2026-10-25", "2026-10-26", "2026-10-27"):
        reading(day, "late_flower")

    async def go():
        app = make_app(DemoBox(), Alerts(), "k" * 32, vision=vision.Vision(None))
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k" * 32})
            return (await (await client.get("/api/state")).json())["vision"]

    got = asyncio.run(go())
    assert got["stage_run"] == {"stage": "late_flower", "days": 3, "since": "2026-10-25"}
    assert "stage_suggest" in got  # None or the stage: the plant of the demo state decides
