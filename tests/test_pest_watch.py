"""The pest watch speaks on any sign the model does not rule out, pushes each sign once, and never moves a device."""
import asyncio
import base64
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, pest_watch, server, vision
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox, DemoCamera
from vgrow.server import COOKIE, make_app


class Camera(DemoCamera):
    def __init__(self, name, picture):
        self.name, self.picture_bytes, self.calls = name, picture, 0

    async def photo(self):
        self.calls += 1
        return self.picture_bytes


def checklist(**seen):
    """A model answer: every sign "no" unless given as key=(seen, where)."""
    out = {key: {"seen": "no", "where": ""} for key in pest_watch.SIGNS}
    for key, (state, where) in seen.items():
        out[key] = {"seen": state, "where": where}
    return {**out, "other_unusual": [], "image_quality": "good"}


def watch(answer, detail=None, overview=None):
    vis = vision.Vision(overview or Camera("GrowCam", b"growcam-jpeg"))
    calls = []

    async def chat(body):
        calls.append(body)
        return {"message": {"content": json.dumps(answer)}}

    vis._chat = chat
    return pest_watch.PestWatch(vis, detail=detail, overview=vis.camera), calls


def test_flags_keep_yes_and_unsure_and_drop_no():
    found = pest_watch.flags(checklist(white_powder=("yes", "top leaf"), curled=("unsure", "tip")))
    assert found == [{"key": "white_powder", "seen": "yes", "where": "top leaf"},
                     {"key": "curled", "seen": "unsure", "where": "tip"}]
    assert pest_watch.flags(checklist()) == []
    assert pest_watch.flags({**checklist(), "other_unusual": ["shiny spots", " "]}) == [
        {"key": "unusual", "seen": "unsure", "where": "shiny spots"}]
    assert pest_watch.flags(None) == []
    # Seen in the probe of 31.10.2026: "none" as an unusual item is nothing; an empty box (poor) is no finding.
    assert pest_watch.flags({**checklist(), "other_unusual": ["none", "None.", "keine"]}) == []
    assert pest_watch.flags({**checklist(curled=("yes", "tip")), "image_quality": "poor"}) == []


def test_run_prefers_the_close_camera_and_sends_the_checklist():
    close = Camera("HD", b"close-jpeg")
    pest, calls = watch(checklist(insects=("yes", "under a fan leaf")), detail=close)
    record = asyncio.run(pest.run("de"))
    assert close.calls == 1 and pest.overview.calls == 0
    assert record["camera_source"]["id"] == "usb" and vision.PHOTO_NAME.fullmatch(record["photo"])
    assert base64.b64decode(calls[0]["messages"][1]["images"][0]) == b"close-jpeg"
    assert calls[0]["format"]["required"][:len(pest_watch.SIGNS)] == list(pest_watch.SIGNS)
    assert calls[0]["messages"][0]["content"].endswith(pest_watch.LANGUAGE["de"])
    assert record["finding"]["status"] == "open"
    assert pest.view("de")["open"]["text"] == "Insekten, Milben, Eier oder Häutungsreste (under a fan leaf)"


def test_nothing_seen_opens_no_finding_and_an_unreadable_answer_is_a_failure():
    pest, _ = watch(checklist())
    record = asyncio.run(pest.run("de"))
    assert record["finding"] is None and pest.view()["open"] is None
    bad, _ = watch({"white_powder": "yes"})
    failed = asyncio.run(bad.run("de"))
    assert failed["result"] is None and failed["error"] and failed["finding"] is None


def test_each_sign_is_pushed_once_and_a_false_alarm_stays_quiet_for_a_day():
    pest, _ = watch(checklist(white_powder=("yes", "top")))
    first = asyncio.run(pest.run("de", now=1000.0))
    assert [f["key"] for f in pest.to_push(first, now=1000.0)] == ["white_powder"]
    pest.mark_pushed(first)
    pest.last_attempt = None
    second = asyncio.run(pest.run("de", now=2000.0))
    assert pest.to_push(second, now=2000.0) == []  # still open and already on the phone
    pest.answer(first["id"], "no", now=3000.0)
    pest.answer(second["id"], "no", now=3000.0)
    pest.last_attempt = None
    third = asyncio.run(pest.run("de", now=4000.0))
    assert pest.to_push(third, now=4000.0) == []
    assert [f["key"] for f in pest.to_push(third, now=3000.0 + pest_watch.QUIET_AFTER_NO + 1)] == ["white_powder"]


def test_answers_are_labels_and_bad_answers_are_refused():
    pest, _ = watch(checklist(bud_mold=("unsure", "main cola")))
    record = asyncio.run(pest.run("de"))
    with pytest.raises(ValueError):
        pest.answer(record["id"], "maybe")
    with pytest.raises(KeyError):
        pest.answer("nope", "yes")
    done = pest.answer(record["id"], "yes", "Läuse unter dem Blatt")
    assert done["finding"]["status"] == "confirmed" and done["finding"]["note"] == "Läuse unter dem Blatt"
    view = pest.view()
    assert view["open"] is None and view["confirmed"]["id"] == record["id"]
    assert view["stats"] == {"checks": 1, "flagged": 1, "confirmed": 1, "dismissed": 0, "unsure": 0, "unanswered": 0}


def test_due_is_hourly_and_a_failed_try_waits_an_hour_too():
    """The owner, 31.10.2026: every hour while the light is on (the loop checks light, plant and camera)."""
    pest, _ = watch(checklist())
    noon = 1_793_448_000.0
    assert pest.due(noon)
    asyncio.run(pest.run("de", now=noon))
    assert not pest.due(noon + 1800)
    assert pest.due(noon + pest_watch.CHECK_EVERY)
    bad, _ = watch({"white_powder": "yes"})
    asyncio.run(bad.run("de", now=noon))  # a failure: no result, retried an hour later
    assert not bad.due(noon + 1800) and bad.due(noon + pest_watch.RETRY_AFTER)


# The words in a card that back a sign; "insects" is backed by the card being one of the wiki's pests.
EVIDENCE = {"webbing": r"web|silk", "residue": r"honeydew|sticky|sooty", "speckles": r"stippl|speckl|silver|dots|flecks",
            "white_powder": r"powder", "curled": r"curl|twist|cup|distort|deform",
            "bud_mold": r"mold|mould|mycelium|spore", "chewed": r"chew|hole|tunnel|mine|frass|feeding"}


def test_every_sign_points_only_to_cards_whose_own_text_names_it():
    import re

    from vgrow import wiki
    for sign, refs in pest_watch.SIGN_CARDS.items():
        assert sign in pest_watch.SIGNS
        for ref in refs:
            folder, key = ref.split("/")
            entry = wiki.find(folder, key)
            assert entry is not None, ref
            raw = entry["raw"]
            if sign == "insects":
                assert raw.get("group") == "schaedlinge", ref
                continue
            text = " ".join([*raw.get("symptoms_en", []), raw.get("tile_en", ""), raw.get("summary_en", ""),
                             *(raw.get("facts_en") or {}).values()])
            assert re.search(EVIDENCE[sign], text, re.I), (sign, ref)


def test_candidates_rank_by_signs_and_bring_the_cards_own_steps():
    found = [{"key": "insects", "seen": "yes", "where": ""}, {"key": "residue", "seen": "unsure", "where": ""}]
    picks = pest_watch.candidates(found, "de", on=["cannabis"])
    cards = [p["card"] for p in picks]
    # the four cards both signs name come first, in the order the first sign names them
    assert cards[:4] == ["problems/aphid", "problems/whitefly", "health/mealybugs", "health/scale-insects"]
    assert set(cards[4:]) == {"problems/thrips", "problems/spider_mite", "problems/fungus_gnat", "problems/caterpillar"}
    aphid = picks[0]
    assert aphid["label"] == "Blattläuse" and aphid["signs"] == ["insects", "residue"]
    assert len(aphid["do_now"]) == 7 and aphid["do_now"][0].startswith("Aus der Praxis")
    assert picks[2]["label"] == "Wollläuse" and picks[2]["do_now"] == []  # a health card: the page links it
    # the cannabis package off: its problem cards stay out, as on the wiki
    assert [p["card"] for p in pest_watch.candidates(found, "en", on=[])] == ["health/mealybugs",
                                                                               "health/scale-insects"]
    assert pest_watch.candidates([{"key": "patches", "seen": "yes", "where": ""}], "de", on=["cannabis"]) == []


def test_identify_takes_only_a_card_of_the_confirmed_signs():
    pest, _ = watch(checklist(webbing=("yes", "between buds")))
    record = asyncio.run(pest.run("de"))
    with pytest.raises(KeyError):
        pest.identify(record["id"], "problems/spider_mite")  # not confirmed yet
    pest.answer(record["id"], "yes")
    with pytest.raises(ValueError):
        pest.identify(record["id"], "problems/aphid")  # webbing never points to aphids
    assert pest.identify(record["id"], "problems/spider_mite", now=5.0)["finding"]["identified"] == "problems/spider_mite"
    assert pest.identify(record["id"], "other")["finding"]["identified"] == "other"
    assert pest.identify(record["id"], "")["finding"]["identified"] is None
    view = pest.view("de")["confirmed"]
    assert [c["card"] for c in view["candidates"]] == ["problems/spider_mite"]


def test_a_yes_or_no_answers_the_older_findings_and_an_open_one_holds_a_day_only():
    pest, _ = watch(checklist(curled=("unsure", "tip")))
    first = asyncio.run(pest.run("de", now=1000.0))
    pest.mark_pushed(first)
    pest.last_attempt = None
    second = asyncio.run(pest.run("de", now=1000.0 + 3600))
    # nobody answered: the pushed sign waits, but a day, not for good
    assert pest.to_push(second, now=1000.0 + 3600) == []
    assert [f["key"] for f in pest.to_push(second, now=1001.0 + pest_watch.QUIET_WHILE_OPEN)] == ["curled"]
    pest.answer(second["id"], "no", now=9000.0)  # the page shows the newest; the look covers the first too
    first_now = next(r for r in pest.checks() if r["id"] == first["id"])
    assert first_now["finding"] == {"status": "dismissed", "answer": "no", "answered_at": 9000.0, "via": second["id"]}
    assert pest.view()["open"] is None
    assert pest.stats()["dismissed"] == 2 and pest.stats()["unanswered"] == 0


def test_a_photo_without_a_finding_goes_after_two_weeks():
    pest, _ = watch(checklist())
    old = asyncio.run(pest.run("de", now=1000.0))
    photo = vision.PHOTOS / old["photo"]
    assert photo.exists()
    flagged, _ = watch(checklist(insects=("yes", "leaf")))
    kept = asyncio.run(flagged.run("de", now=1000.0))
    pest.last_attempt = None
    asyncio.run(pest.run("de", now=1000.0 + pest_watch.KEEP_PHOTOS - 1))
    assert photo.exists()  # not two weeks yet
    pest.last_attempt = None
    asyncio.run(pest.run("de", now=1000.0 + pest_watch.KEEP_PHOTOS + 1))
    assert not photo.exists() and (vision.PHOTOS / kept["photo"]).exists()  # a finding keeps its photo
    pruned = next(r for r in pest.checks() if r["id"] == old["id"])
    assert pruned["photo"] is None and pruned["photo_deleted"] is True


def test_page_link_never_carries_the_key(monkeypatch, tmp_path):
    env = tmp_path / "vgrow.env"
    monkeypatch.setattr(config, "ENV_FILE", env)
    assert server.page_link("grow/insights") is None
    env.write_text("VGROW_PAGE_URL=http://100.1.2.3:8742/\n", encoding="utf-8")
    assert server.page_link("grow/insights") == "http://100.1.2.3:8742/#grow/insights"
    env.write_text("VGROW_PAGE_URL=http://100.1.2.3:8742/?key=secret\n", encoding="utf-8")
    assert server.page_link("grow/insights") is None


def test_the_answer_route_records_the_look_and_switches_nothing():
    pest, _ = watch(checklist(webbing=("yes", "between buds")))
    record = asyncio.run(pest.run("de"))
    box = DemoBox()

    async def go():
        app = make_app(box, Alerts(), "k" * 32, pest=pest)
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k" * 32})
            state = await (await client.get("/api/state")).json()
            assert state["pest"]["open"]["id"] == record["id"]
            resp = await client.post("/api/cmd", json={"action": "pest_answer", "id": record["id"], "answer": "no"})
            assert resp.status == 200 and (await resp.json())["finding"]["status"] == "dismissed"
            missing = await client.post("/api/cmd", json={"action": "pest_answer", "id": "x", "answer": "no"})
            assert missing.status == 404
            return (await (await client.get("/api/state")).json())["pest"]

    after = asyncio.run(go())
    assert after["open"] is None and after["stats"]["dismissed"] == 1


def test_the_identify_route_records_the_pick_and_the_state_carries_the_steps():
    pest, _ = watch(checklist(insects=("yes", "under a leaf")))
    record = asyncio.run(pest.run("de"))
    pest.answer(record["id"], "yes")
    box = DemoBox()

    async def go():
        app = make_app(box, Alerts(), "k" * 32, pest=pest)
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({COOKIE: "k" * 32})
            state = await (await client.get("/api/state")).json()
            cards = [c["card"] for c in state["pest"]["confirmed"]["candidates"]]
            bad = await client.post("/api/cmd", json={"action": "pest_identify", "id": record["id"],
                                                      "card": "problems/botrytis_bud_rot"})
            good = await client.post("/api/cmd", json={"action": "pest_identify", "id": record["id"],
                                                       "card": "problems/aphid"})
            return cards, bad.status, good.status, (await good.json())["finding"]["identified"]

    cards, bad, good, identified = asyncio.run(go())
    assert "problems/aphid" in cards and bad == 400 and good == 200 and identified == "problems/aphid"
