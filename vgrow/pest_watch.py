"""Pest watch: a fixed checklist of early pest and disease signs, asked of the local model on a fresh photo.

The owner (31.10.2026): watch the flowers for pests from the start, any kind; when the model is not sure, it should
still say "there might be something" and ask the owner to look, so an infestation is not caught too late. Aphids had
just destroyed a harvest that the daily reading never flagged: from 22. to 30.10.2026 it named heat and leaf tips only.

The model only answers the checklist: yes, unsure or no per sign, and where. The code decides: one "yes" or "unsure"
opens a finding, which goes to the phone once and waits for the owner's answer (yes, no, unsure). A false alarm costs
one look, a missed sign costs the harvest, so the bar is low on purpose. The answers are the labels that measure the
hit rate later. Nothing here switches a device or names a pest: identifying it is the owner's look and the wiki's
cards. The photo never leaves the house; the push carries words and, if VGROW_PAGE_URL is set, a link to the page.

After the owner's "yes" the page asks what it was: the wiki's cards the confirmed signs point to (SIGN_CARDS, code,
never the model), and for the one the owner picks, the card's own "do now" steps. Hourly while the light is on
(the owner, 31.10.2026); a photo of a check that found nothing is deleted after KEEP_PHOTOS.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from collections.abc import Iterable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, wiki
from .i18n import current_lang, normalize, t

_LOGGER = logging.getLogger(__name__)

# The checklist, in the order the page shows it. The English text is what the model reads; the page shows the
# catalog's pest.sign.<key>. Kept to what a camera can show; it never names a pest, so the model cannot guess one.
SIGNS = {
    "white_powder": "white powdery or flour-like coating on leaves or buds",
    "speckles": "many tiny pale, white or yellow dots or speckles on leaves",
    "webbing": "fine webbing or silk threads between leaves or buds",
    "insects": "visible insects, mites, larvae, eggs or shed white skins",
    "residue": "sticky shiny residue on leaves, or a black sooty coating",
    "curled": "curled, twisted, cupped or deformed leaves",
    "bud_mold": "grey, brown or fuzzy mold, or dark mushy spots in buds",
    "chewed": "holes, chewed edges or tunnels in leaves",
    "patches": "yellow, brown or dead patches away from the leaf tips",
}
SEEN = ("yes", "unsure", "no")
ANSWERS = ("yes", "no", "unsure")  # the owner's: there is something, false alarm, looked and cannot tell

# What a confirmed sign can be: the wiki cards whose own text names the sign, checked sentence by sentence on
# 02.11.2026 (tests/test_pest_watch.py holds every pair to its card's words). "patches" and "unusual" point to too many
# cards to list; the page sends the owner to the wiki's pests instead. Problem cards carry "do now" steps, the
# general health cards only signs and context, so the page links those.
SIGN_CARDS = {
    "webbing": ("problems/spider_mite",),
    "residue": ("problems/aphid", "problems/whitefly", "health/mealybugs", "health/scale-insects"),
    "speckles": ("problems/spider_mite", "problems/thrips"),
    "white_powder": ("health/powdery-mildew", "problems/whitefly"),
    "insects": ("problems/aphid", "problems/whitefly", "problems/thrips", "problems/spider_mite",
                "problems/fungus_gnat", "problems/caterpillar", "health/mealybugs", "health/scale-insects"),
    "curled": ("problems/russet_mite", "health/broad-mites"),
    "bud_mold": ("problems/botrytis_bud_rot", "problems/high_humidity_flower"),
    "chewed": ("problems/caterpillar", "health/leafminers", "health/slugs-snails"),
}
OTHER = "other"  # the owner's pick when none of the cards fits

CHECK_EVERY = 3600  # seconds: hourly while the light is on (the owner, 31.10.2026); until 02.11. twice a day
RETRY_AFTER = CHECK_EVERY  # after a failed check
QUIET_AFTER_NO = 24 * 3600  # a sign the owner called a false alarm does not push again for a day (still shown)
QUIET_AFTER_YES = 48 * 3600  # a confirmed sign is being handled: no push for two days, the page keeps showing it
QUIET_WHILE_OPEN = 24 * 3600  # a pushed sign nobody answered yet does not push again for a day, then it may
KEEP_PHOTOS = 14 * 86400  # a photo of a check that found nothing; the two weeks before a finding show what was missed
TIMEOUT = 30 * 60  # seconds; CPU-only inference, as in vision.py

_SIGN_SCHEMA = {
    "type": "object",
    "properties": {"seen": {"type": "string", "enum": list(SEEN)}, "where": {"type": "string"}},
    "required": ["seen", "where"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {**{key: _SIGN_SCHEMA for key in SIGNS},
                   "other_unusual": {"type": "array", "items": {"type": "string"}},
                   "image_quality": {"type": "string", "enum": ["good", "limited", "poor"]}},
    "required": [*SIGNS, "other_unusual", "image_quality"],
    "additionalProperties": False,
}
SYSTEM = (
    "You inspect a photo of a single plant in a small grow box for early signs of pests and disease. "
    "Missing an early sign costs the whole harvest; a false alarm costs the owner one look. So: answer 'yes' "
    "when the sign is visible, 'unsure' when something might be it, 'no' only when the visible parts clearly "
    "do not show it. 'where' names the spot (for example 'top cola, left fan leaf'), or is empty for 'no'. "
    "Do not name a pest or disease; describe only what is visible. other_unusual lists anything else that "
    "looks abnormal, and stays empty when there is nothing. If no plant is visible, image_quality is 'poor' "
    "and every sign 'no'. Checklist:\n" + "\n".join(f"- {key}: {text}" for key, text in SIGNS.items())
)
LANGUAGE = {"de": "Write 'where' and other_unusual in German.", "en": "Write 'where' and other_unusual in English."}
# The probe of 31.10.2026 on ten stored photos: the model wrote "none" as an unusual item on two of them.
NOTHING = {"none", "nothing", "no", "n/a", "na", "-", "keine", "nichts", "kein"}


def store() -> Path:
    return config.STATE_DIR / "pest"


def flags(result: dict[str, Any] | None) -> list[dict[str, str]]:
    """The signs the model did not rule out, each {"key", "seen", "where"}; anything else as key "unusual"."""
    if not isinstance(result, dict) or result.get("image_quality") == "poor":  # an empty box or a dark frame
        return []
    out = []
    for key in SIGNS:
        item = result.get(key)
        if isinstance(item, dict) and item.get("seen") in ("yes", "unsure"):
            out.append({"key": key, "seen": item["seen"], "where": str(item.get("where") or "").strip()})
    for text in result.get("other_unusual") or []:
        if isinstance(text, str) and text.strip() and text.strip().strip(".").lower() not in NOTHING:
            out.append({"key": "unusual", "seen": "unsure", "where": text.strip()})
    return out


def describe(found: list[dict[str, str]], lang: str | None = None) -> str:
    """One line for the push and the page: "white coating (top cola); curled leaves (?)"."""
    parts = []
    for item in found:
        label = t(f"pest.sign.{item['key']}", lang)
        where = item["where"] or t("pest.where_unknown", lang)
        mark = "" if item["seen"] == "yes" else t("pest.maybe", lang)
        parts.append(f"{label}{mark} ({where})")
    return "; ".join(parts)


def short_label(label: str) -> str:
    """A card's name for a button: "Blattläuse, vor allem Hanfblattlaus (Phorodon cannabis)" is "Blattläuse"."""
    return re.split(r"\s*[,(]", label, maxsplit=1)[0].strip() or label


def candidates(found: list[dict[str, str]], lang: str | None = None, on: Iterable[str] | None = None,
               library: Path | None = None) -> list[dict[str, Any]]:
    """The cards the signs point to, those most signs name first: {"card", "label", "signs", "do_now"}. A card the
    package switch hides stays out, as on the wiki; "do_now" is the card's own list, empty for a health card."""
    lang = normalize(lang) or current_lang()
    on = wiki.packages() if on is None else list(on)
    named: dict[str, list[str]] = {}
    for item in found:
        for ref in SIGN_CARDS.get(item.get("key", ""), ()):
            signs = named.setdefault(ref, [])
            if item["key"] not in signs:
                signs.append(item["key"])
    out = []
    for ref, signs in sorted(named.items(), key=lambda pair: -len(pair[1])):  # stable: among equals the first named
        folder, key = ref.split("/", 1)
        entry = wiki.find(folder, key, library)
        if entry is None or not wiki.visible(entry, on):
            continue
        card = wiki.card_view(entry, lang, on)
        steps = [s.strip() for s in card["card"].get("do_now") or [] if isinstance(s, str) and s.strip()]
        out.append({"card": ref, "label": short_label(card["label"]), "signs": signs, "do_now": steps})
    return out


class PestWatch:
    """Shares the model, its address and its lane with the daily reading (vision.Vision): one model job at a time."""

    def __init__(self, vision: Any, detail: Any = None, overview: Any = None) -> None:
        self.vision = vision
        self.detail = detail  # the close camera (usb), preferred: aphids and mildew need the detail
        self.overview = overview  # the GrowCam, when there is no close camera
        self.busy = False
        self.last_attempt: float | None = None
        self._error: str | None = None
        self._cache: tuple[tuple[Path, int | None], list[dict[str, Any]]] | None = None
        self._picks: tuple[tuple[Any, ...], float, list[dict[str, Any]]] | None = None

    # ---------- files ----------

    def checks(self) -> list[dict[str, Any]]:
        """All stored checks, oldest first. Read from disk once and then kept, as _write keeps it: hourly checks add
        some 18 files a day, and every page poll asks for the view."""
        folder = store() / "checks"
        try:
            mark = (folder, folder.stat().st_mtime_ns)
        except OSError:
            mark = (folder, None)
        if self._cache is None or self._cache[0] != mark:
            out = []
            for path in sorted(folder.glob("*.json")) if folder.exists() else []:
                try:
                    out.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
            self._cache = (mark, out)
        return self._cache[1]

    def _write(self, record: dict[str, Any]) -> None:
        folder = store() / "checks"
        records = self.checks()  # the folder as it was before this write
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{record['id']}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False),
                                                     encoding="utf-8")
        for i, old in enumerate(records):
            if old.get("id") == record["id"]:
                records[i] = record
                break
        else:
            records.append(record)
            records.sort(key=lambda r: str(r.get("id", "")))  # the ids start with the time, as the file names
        self._cache = ((folder, folder.stat().st_mtime_ns), records)

    def due(self, now: float | None = None) -> bool:
        """CHECK_EVERY since the last check, RETRY_AFTER since a failed try. Light, plant and camera are the loop's."""
        now = time.time() if now is None else now
        if self.last_attempt is not None and now - self.last_attempt < RETRY_AFTER:
            return False
        done = [c for c in self.checks() if c.get("result")]
        return not done or now - float(done[-1].get("at") or 0) >= CHECK_EVERY

    def camera(self) -> tuple[Any, dict[str, Any]] | None:
        for cam, source in ((self.detail, "usb"), (self.overview, "growcam")):
            if cam is not None and getattr(cam, "configured", True) and getattr(cam, "reachable", True):
                return cam, {"id": source, "name": getattr(cam, "name", source)}
        return None

    # ---------- work ----------

    async def run(self, lang: str | None = None, now: float | None = None) -> dict[str, Any]:
        """Photo, checklist, finding. Returns the stored record; a failure is a record without result."""
        lang = normalize(lang) or current_lang()
        if self.busy:
            raise ValueError(t("vision.busy", lang))
        picked = self.camera()
        if picked is None:
            raise ValueError(t("pest.no_camera", lang))
        cam, source = picked
        self.busy = True
        self.last_attempt = time.time() if now is None else now
        record: dict[str, Any] = {"id": f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:6]}",
                                  "date": datetime.now().isoformat(timespec="minutes"), "at": self.last_attempt,
                                  "camera_source": source, "model": self.vision.model, "result": None}
        try:
            photo = await self.vision.take_photo(camera=cam, source=source)
            record["photo"] = photo.name
            started = time.time()
            data = await self._ask(photo, lang)
            record["seconds"] = round(time.time() - started, 1)
            try:
                result = json.loads((data.get("message") or {}).get("content") or "")
            except ValueError:
                result = None
            if isinstance(result, dict) and all(isinstance(result.get(k), dict) for k in SIGNS):
                record["result"] = result
                self._error = None
            else:
                record["error"] = t("vision.unreadable", lang)
                self._error = record["error"]
        except Exception as err:  # camera or model trouble reaches the page as a sentence, the loop keeps going
            _LOGGER.warning("Pest check failed: %r", err)
            record["error"] = t("pest.failed", lang, reason=str(err)[:160])
            self._error = record["error"]
        finally:
            self.busy = False
        found = flags(record["result"])
        record["flags"] = found
        record["finding"] = {"status": "open", "answer": None, "answered_at": None} if found else None
        self._write(record)
        _LOGGER.info("Pest check %s: %s", record["id"], [f["key"] for f in found] or "nothing")
        try:
            self._prune(self.last_attempt)
        except OSError:  # a photo that cannot be deleted stays; the check itself is done
            _LOGGER.exception("Pruning old pest photos failed")
        return record

    def _prune(self, now: float) -> None:
        """The photo of a check that found nothing goes after KEEP_PHOTOS (hourly that is some 18 a day, and the
        close camera sees the room when the box is open). A check with a finding keeps its photo for the hit rate."""
        for record in self.checks():
            name = record.get("photo")
            if not name or record.get("finding") or now - float(record.get("at") or now) < KEEP_PHOTOS:
                continue
            path = self.vision.photo_file(name)
            if isinstance(path, Path):
                path.unlink(missing_ok=True)
            record["photo"], record["photo_deleted"] = None, True
            self._write(record)

    async def _ask(self, photo: Path, lang: str) -> dict[str, Any]:
        body = {
            "model": self.vision.model,
            "stream": False,
            "format": SCHEMA,
            "keep_alive": self.vision.keep_alive(),
            "think": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
            "messages": [
                {"role": "system", "content": f"{SYSTEM}\n{LANGUAGE[lang]}"},
                {"role": "user", "content": "Check this photo against the checklist.",
                 "images": [base64.standard_b64encode(photo.read_bytes()).decode("utf-8")]},
            ],
        }
        lane = getattr(self.vision, "lane", None)
        async with lane.hold("photo") if lane is not None else nullcontext():
            return await self.vision._chat(body)

    def to_push(self, record: dict[str, Any], now: float | None = None) -> list[dict[str, str]]:
        """The flags of a new record worth a push: not answered "no" within QUIET_AFTER_NO, nor "yes" within
        QUIET_AFTER_YES, for the same sign. A sign an open finding already pushed waits QUIET_WHILE_OPEN from that
        check; it used to wait for good, and only the newest open finding can be answered on the page."""
        now = time.time() if now is None else now
        quiet: set[str] = set()
        pushed: set[str] = set()
        for old in self.checks():
            if old.get("id") == record.get("id") or not old.get("finding"):
                continue
            finding = old["finding"]
            keys = {f["key"] for f in old.get("flags") or []}
            answered = finding.get("answered_at")
            if finding.get("answer") == "no" and answered and now - answered < QUIET_AFTER_NO:
                quiet |= keys
            elif finding.get("answer") == "yes" and answered and now - answered < QUIET_AFTER_YES:
                quiet |= keys
            elif (finding.get("status") == "open" and old.get("pushed")
                  and now - float(old.get("at") or 0) < QUIET_WHILE_OPEN):
                pushed |= keys
        return [f for f in record.get("flags") or [] if f["key"] not in quiet and f["key"] not in pushed]

    def mark_pushed(self, record: dict[str, Any]) -> None:
        record["pushed"] = True
        self._write(record)

    def answer(self, check_id: str, answer: str, note: str = "", now: float | None = None) -> dict[str, Any]:
        """The owner's look: yes (there is something), no (false alarm) or unsure. Never changes a device. A yes or a
        no also answers the older findings nobody answered: hourly checks open one an hour while nobody looks, and
        the page shows only the newest."""
        if answer not in ANSWERS:
            raise ValueError(t("pest.bad_answer"))
        now = time.time() if now is None else now
        status = {"yes": "confirmed", "no": "dismissed", "unsure": "open"}[answer]
        records = self.checks()
        target = next((r for r in records if r.get("id") == check_id and r.get("finding")), None)
        if target is None:
            raise KeyError(check_id)
        target["finding"] = {"status": status, "answer": answer, "answered_at": now, "note": str(note)[:500]}
        self._write(target)
        if answer != "unsure":
            for record in list(records):
                finding = record.get("finding") or {}
                if (record is not target and finding.get("status") == "open" and not finding.get("answer")
                        and float(record.get("at") or 0) < float(target.get("at") or 0)):
                    record["finding"] = {"status": status, "answer": answer, "answered_at": now, "via": check_id}
                    self._write(record)
        return target

    def identify(self, check_id: str, card: str, now: float | None = None) -> dict[str, Any]:
        """What the owner found it to be after his yes: a card its signs point to, OTHER when none fits, or "" to
        choose again. A label for the hit rate; never changes a device."""
        for record in self.checks():
            if record.get("id") == check_id and (record.get("finding") or {}).get("status") == "confirmed":
                allowed = {ref for f in record.get("flags") or [] for ref in SIGN_CARDS.get(f.get("key", ""), ())}
                if card and card != OTHER and card not in allowed:
                    raise ValueError(t("pest.bad_card"))
                when = (time.time() if now is None else now) if card else None
                record["finding"].update(identified=card or None, identified_at=when)
                self._write(record)
                return record
        raise KeyError(check_id)

    def _candidates(self, record: dict[str, Any], lang: str | None) -> list[dict[str, Any]]:
        """candidates() of the confirmed finding, kept a minute: every page poll asks, and each card is a wiki read."""
        lang = normalize(lang) or current_lang()
        on = wiki.packages()
        key = (record["id"], lang, tuple(on))
        if self._picks is None or self._picks[0] != key or time.time() - self._picks[1] > 60:
            self._picks = (key, time.time(), candidates(record.get("flags") or [], lang, on))
        return self._picks[2]

    def stats(self) -> dict[str, int]:
        """How often the watch spoke and what the owner said: the hit rate, measured on this box."""
        out = {"checks": 0, "flagged": 0, "confirmed": 0, "dismissed": 0, "unsure": 0, "unanswered": 0}
        for record in self.checks():
            if not record.get("result"):
                continue
            out["checks"] += 1
            finding = record.get("finding")
            if not finding:
                continue
            out["flagged"] += 1
            out[{"yes": "confirmed", "no": "dismissed", "unsure": "unsure"}.get(finding.get("answer"), "unanswered")] += 1
        return out

    def view(self, lang: str | None = None) -> dict[str, Any]:
        records = self.checks()
        latest = next((r for r in reversed(records) if r.get("result") or r.get("error")), None)
        open_ = next((r for r in reversed(records) if (r.get("finding") or {}).get("status") == "open"), None)
        confirmed = next((r for r in reversed(records)
                          if (r.get("finding") or {}).get("status") == "confirmed"
                          and time.time() - float(r["finding"].get("answered_at") or 0) < QUIET_AFTER_YES), None)

        def lite(record: dict[str, Any] | None) -> dict[str, Any] | None:
            if record is None:
                return None
            return {"id": record["id"], "date": record.get("date"), "photo": record.get("photo"),
                    "camera": (record.get("camera_source") or {}).get("id"), "flags": record.get("flags") or [],
                    "text": describe(record.get("flags") or [], lang), "finding": record.get("finding"),
                    "error": record.get("error")}

        out = {"busy": self.busy, "error": self._error, "latest": lite(latest), "open": lite(open_),
               "confirmed": lite(confirmed), "stats": self.stats(), "signs": list(SIGNS)}
        if confirmed is not None:  # what it can be, and the steps of the card the owner picks (the page asks)
            out["confirmed"]["candidates"] = self._candidates(confirmed, lang)
        return out
