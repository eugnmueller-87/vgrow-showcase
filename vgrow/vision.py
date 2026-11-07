"""Daily plant photo and a local model's reading of it: stage, visible signs, issues, next steps.

Runs locally through Ollama: no API, no cost, the photo never leaves the house.
Tested on a 2018 Intel MacBook Pro (i7-8559U, 16 GB) without GPU acceleration, measured on
22.10.2026 on that Mac: 163 s and 238 s per reading (gemma3:12b). The model is unloaded right after
(keep_alive 0) so it does not hold 8 GB of RAM all day. The reading never changes anything by itself:
the stage is only a suggestion until the owner taps it.
What a wide camera shot cannot show (trichome colour) the prompt makes the model say instead of guess.
Each reading is in one language and says which ("lang"): a manual one in the page's language, the
daily one in the server language.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config
from .hank.ollama import model_name, ollama_url
from .i18n import TextError, current_lang, fmt_num, normalize, t

_LOGGER = logging.getLogger(__name__)

# One model for the photo reading and Hank (spec 0.2 point 2, 4.9): two 8 GB models on 16 GB would unload each
# other. HANK_MODEL, else VISION_MODEL in ~/.vgrow.env override it (vgrow.hank.ollama.model_name).
MODEL = "gemma4:12b"
OLLAMA_URL = "http://127.0.0.1:11434"
TIMEOUT = 30 * 60  # seconds; CPU-only inference
PHOTO_HOUR = 12  # one photo a day from noon on, while the light is on
MAX_MANUAL_PER_DAY = 5
STAGES = ["seedling", "veg", "early_flower", "mid_flower", "late_flower", "harvest", "unclear"]

PHOTOS = config.STATE_DIR / "photos"
ANALYSES = config.STATE_DIR / "analysis"
PHOTO_NAME = re.compile(r"\d{4}-\d{2}-\d{2}(?:_(?:usb|growcam)_\d{2}-\d{2}-\d{2}-\d{6})?\.jpg", re.ASCII)
DAILY_PHOTO = re.compile(r"\d{4}-\d{2}-\d{2}\.jpg", re.ASCII)  # the noon photo; the others are observations
# The camera suggests a stage once this many daily photos in a row show it, never backwards (owner, 01.11.2026). One
# reading alone jumped: 24. to 30.10.2026 early, mid, early, mid flower, every time with confidence "high".
STAGE_RUN_DAYS = 3
_RUN_CACHE: dict[str, Any] = {}


def stage_run() -> dict[str, Any] | None:
    """The newest daily reading's stage and on how many calendar days in a row the daily readings said it:
    {"stage", "days", "since"}, or None without one or when the newest says "unclear". The last reading of a day
    counts; a day without one ends the run. The files are read again only when their names changed (every page poll
    asks; a reading is never rewritten, and a folder's time moves too coarsely on Windows to tell)."""
    paths = sorted(ANALYSES.glob("*.json")) if ANALYSES.exists() else []  # the names are times: a day's last comes last
    mark = (str(ANALYSES), tuple(p.name for p in paths))
    if _RUN_CACHE.get("mark") == mark:
        return _RUN_CACHE["run"]
    days: dict[str, str] = {}
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        photo = str(record.get("photo") or "")
        guess = (record.get("result") or {}).get("stage_guess")
        if DAILY_PHOTO.fullmatch(photo) and not record.get("observation_only") and guess in STAGES:
            days[photo[:10]] = guess
    run: dict[str, Any] | None = None
    for day in sorted(days, reverse=True):
        if run is None:
            if days[day] == "unclear":
                break
            run = {"stage": days[day], "days": 1, "since": day}
        elif days[day] == run["stage"] and date.fromisoformat(day) == date.fromisoformat(run["since"]) - timedelta(1):
            run.update(days=run["days"] + 1, since=day)
        else:
            break
    _RUN_CACHE.update(mark=mark, run=run)
    return run


def stage_suggestion(run: dict[str, Any] | None, plant: Any) -> dict[str, Any] | None:
    """The stage the camera suggests: STAGE_RUN_DAYS daily photos in a row said it, it is one of the plant's stages,
    and it comes after the stage the plant is in. {"key", "label", "days", "since"} or None."""
    if not run or run.get("days", 0) < STAGE_RUN_DAYS or not isinstance(plant, dict):
        return None
    stages = [s for s in plant.get("stages") or [] if isinstance(s, dict) and s.get("key")]
    keys = [s["key"] for s in stages]
    if run.get("stage") not in keys:
        return None
    current = (plant.get("stage") or {}).get("key")
    if current in keys and keys.index(run["stage"]) <= keys.index(current):
        return None  # the same stage, or an earlier one: never backwards
    found = stages[keys.index(run["stage"])]
    return {"key": found["key"], "label": found.get("label") or found["key"], "days": run["days"],
            "since": run.get("since")}

SCHEMA = {
    "type": "object",
    "properties": {
        "stage_guess": {"type": "string", "enum": STAGES},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "stage_reason": {"type": "string"},
        "visible_signs": {"type": "array", "items": {"type": "string"}},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "watch", "act"]},
                    "where": {"type": "string"},
                },
                "required": ["issue", "severity", "where"],
                "additionalProperties": False,
            },
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "not_assessable": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["stage_guess", "confidence", "stage_reason", "visible_signs", "issues", "next_steps",
                 "not_assessable"],
    "additionalProperties": False,
}

SYSTEM = {
    "de": (
        "Du schaust dir das Tagesfoto einer einzelnen Cannabispflanze an. Privater, legaler Eigenanbau in "
        "Deutschland (KCanG, eine Pflanze). Die Pflanze steht in einer VIVOSUN VGrow Grow-Box, Innenmaß "
        "46 x 46 x 122 cm, mit eingebauter LED oben; die Kamera hängt fest und schaut schräg von oben. "
        "Über der Pflanze liegt ein Netz zum Aufbinden.\n\n"
        "Deine Aufgabe: die Wuchsphase einschätzen und sichtbare Probleme melden, damit der Betreiber "
        "weiß, was als Nächstes zu tun ist. Regeln:\n"
        "- Beschreibe nur, was im Bild wirklich zu sehen ist. Wenn etwas nicht zu erkennen ist, schreib es "
        "unter not_assessable, statt zu raten.\n"
        "- Die Trichomfarbe (klar, milchig, bernstein) ist aus dieser Entfernung nicht sicher beurteilbar. "
        "Für den Erntezeitpunkt verweise dann auf eine Makroaufnahme.\n"
        "- Phasen: seedling (Keimling), veg (Wachstum, keine Blütenansätze), early_flower (erste weiße "
        "Härchen, Stretch), mid_flower (Blüten bilden sich deutlich), late_flower (Blüten dicht, Härchen "
        "färben sich), harvest (Erntefenster), unclear (nicht entscheidbar).\n"
        "- Bei Unsicherheit confidence low. Lieber unclear als eine falsche Phase.\n"
        "- severity: info (nur zur Kenntnis), watch (im Auge behalten), act (heute handeln).\n"
        "- next_steps: höchstens drei, konkret und machbar mit Licht, Umluft, Abluft, Luftbefeuchter und "
        "Gießen. Klimawerte im Kontext sind Messwerte; nutze sie.\n"
        "Antworte auf Deutsch, knapp."
    ),
    "en": (
        "You are looking at the daily photo of a single cannabis plant. Private, legal home grow in "
        "Germany (KCanG, one plant). The plant stands in a VIVOSUN VGrow grow box, inside dimensions "
        "46 x 46 x 122 cm, with a built-in LED at the top; the camera is fixed and looks down at an angle. "
        "A trellis net can lie above the plant.\n\n"
        "Your task: assess the growth stage and report visible problems, so the operator knows what to do "
        "next. Rules:\n"
        "- Describe only what is really visible in the picture. If something cannot be made out, list it "
        "under not_assessable instead of guessing.\n"
        "- Trichome color (clear, milky, amber) cannot be judged reliably from this distance. For the "
        "harvest time, point to a macro photo instead.\n"
        "- Stages: seedling (seedling), veg (vegetative, no flower sites), early_flower (first white "
        "pistils, stretch), mid_flower (buds clearly forming), late_flower (dense buds, pistils changing "
        "color), harvest (harvest window), unclear (cannot be decided).\n"
        "- When unsure, confidence low. Better unclear than a wrong stage.\n"
        "- severity: info (for information only), watch (keep an eye on it), act (act today).\n"
        "- next_steps: at most three, concrete and doable with light, circulation fan, exhaust fan, "
        "humidifier and watering. Climate values in the context are measurements; use them.\n"
        "Answer in English, briefly."
    ),
}

OBSERVATION_SYSTEM = {
    "de": "Beurteile ausschließlich die sichtbare Morphologie der Pflanze auf diesem frischen Foto. "
          "Dies ist eine beschreibende Beobachtung, keine Anbau- oder Pflegeanleitung. "
          "Ordne eine grobe sichtbare Entwicklungsphase zu: seedling, veg, early_flower, mid_flower, late_flower "
          "oder unclear. Beschreibe die sichtbaren Merkmale, die deine Einschätzung stützen. "
          "Bei unscharfem Bild, engem Ausschnitt oder unzureichenden Merkmalen confidence low oder unclear. "
          "Ein enges Blütendetail zeigt nicht den Zustand der gesamten Pflanze. "
          "Keine exakten Altersangaben, Phasenstartdaten, Größen oder Erntezeitpunkte aus dem Bild erraten. "
          "Gib keine Empfehlungen zu Licht, Bewässerung, Nährstoffen oder anderen Anbaumaßnahmen; next_steps bleibt leer. "
          "issues enthält nur direkt sichtbare Auffälligkeiten, not_assessable die nicht beurteilbaren Aspekte. "
          "Antworte auf Deutsch, knapp.",
    "en": "Assess only the visible plant morphology in this fresh photo. "
          "This is a descriptive observation, not cultivation or care guidance. "
          "Estimate a broad visible development stage: seedling, veg, early_flower, mid_flower, late_flower "
          "or unclear. Describe the visible features supporting that estimate. "
          "Use low confidence or unclear if blurred, tightly cropped or insufficient evidence is visible. "
          "A close-up of a flower does not establish the condition of the whole plant. "
          "Do not infer exact age, stage start dates, height or harvest dates from the picture. "
          "Do not recommend lighting, irrigation, nutrients or other cultivation actions; leave next_steps empty. "
          "issues contains only directly visible abnormalities; list limitations under not_assessable. "
          "Answer in English, briefly.",
}


def _explain(err: Exception, model: str) -> tuple[str, dict[str, Any]]:
    """Catalog key and values for a local-model error: a sentence that says what to do."""
    text = str(err)
    low = text.lower()
    if "cannot connect" in low or "connection refused" in low or "connect call failed" in low:
        return "vision.no_ollama", {}
    if "not found" in low and "model" in low:
        return "vision.no_model", {"model": model}
    if isinstance(err, TimeoutError) or "timeout" in low:
        return "vision.timeout", {"minutes": TIMEOUT // 60}
    return "vision.failed", {"reason": err if isinstance(err, TextError) else text[:160]}


def explain(err: Exception, model: str = MODEL, lang: str | None = None) -> str:
    """Turn a local-model error into a sentence that says what to do."""
    key, params = _explain(err, model)
    return t(key, lang, **params)


def context_text(snap: dict[str, Any], plant: dict[str, Any] | None, previous: dict[str, Any] | None,
                 lang: str | None = None) -> str:
    lang = normalize(lang) or current_lang()
    c = snap.get("climate") or {}

    def num(value: Any, digits: int = 1) -> str:
        return t("vision.unknown", lang) if value is None else fmt_num(value, lang, digits)

    def val(value: Any) -> Any:
        return t("vision.unknown", lang) if value is None else value

    when = datetime.now().strftime("%d.%m.%Y %H:%M" if lang == "de" else "%Y-%m-%d %H:%M")
    lines = [t("vision.ctx.photo", lang, when=when)]
    if plant and plant.get("configured"):
        p = plant.get("plant", {})
        lines.append(t("vision.ctx.plant", lang, strain=p.get("strain") or t("vision.unknown", lang),
                       day=val(plant.get("day"))))
        stage = plant.get("stage")
        if stage:
            # Only a stage the operator set is a fact; an estimate must not bias the model.
            key = "vision.ctx.stage_manual" if stage.get("set_by") == "manual" else "vision.ctx.stage_estimate"
            lines.append(t(key, lang, stage=stage.get("label") or stage.get("key")))
        if plant.get("height"):
            lines.append(t("vision.ctx.height", lang, cm=fmt_num(plant["height"]["cm"], lang),
                           date=plant["height"]["date"]))
    lines.append(t("vision.ctx.climate", lang, probe_temp=num(c.get("probe_temp")),
                   probe_humidity=num(c.get("probe_humidity"), 0), probe_vpd=num(c.get("probe_vpd"), 2),
                   temp=num(c.get("temp")), humidity=num(c.get("humidity"), 0)))
    light = snap.get("light") or {}
    lines.append(t("vision.ctx.controls", lang, light=val(light.get("level")), spectrum=val(light.get("spectrum")),
                   cfan=val((snap.get("cfan") or {}).get("step")), dfan=val((snap.get("dfan") or {}).get("step"))))
    previous = previous or {}
    last = previous.get("result") or {}  # a failed reading is stored with result None
    # Only a reading in the same language; readings before 22.10.2026 carry no lang and were all German.
    if last and previous.get("lang", "de") == lang:
        issues = "; ".join(str(i.get("issue")) for i in last.get("issues", []) if i.get("issue"))
        lines.append(t("vision.ctx.previous", lang, date=previous.get("date"), stage=last.get("stage_guess"),
                       issues=issues or t("vision.none", lang)))
    return "\n".join(lines)


class Vision:
    demo = False
    # The model lane shared with Hank (vgrow.hank.ollama.ModelLane), set by serve(). With it, the model call waits
    # behind a running chat answer (spec 4.9); None changes nothing.
    lane: Any = None
    # Optional: a callable that returns the keep_alive for the reading (for example "10m" while Hank is in use).
    # None, a failing hint or an unusable value means 0: unload right after (spec 4.9).
    keep_alive_hint: Callable[[], Any] | None = None

    def __init__(self, camera: Any) -> None:
        self.camera = camera
        env = config.read_env(config.ENV_FILE)
        self.model = model_name(env)  # the one name for photo and chat: HANK_MODEL, else VISION_MODEL, else MODEL
        self.url = ollama_url(env)  # OLLAMA_URL from ~/.vgrow.env, else the constant above
        self.lane = None
        self.keep_alive_hint = None
        self.busy = False
        self.started: float | None = None
        self.job: Any = None  # the running background reading, kept so it is not garbage-collected
        self._error: tuple[str, dict[str, Any]] | None = None  # catalog key and values of the last failure
        self.last_attempt: float | None = None

    @property
    def enabled(self) -> bool:
        return True

    # ---------- files ----------

    @staticmethod
    def photo_path(day: date) -> Path:
        return PHOTOS / f"{day.isoformat()}.jpg"

    @staticmethod
    def photo_names(limit: int = 21) -> list[str]:
        """The newest daily photos (date stems), oldest first, for the strip on the page."""
        if not PHOTOS.exists():
            return []
        return sorted(p.stem for p in PHOTOS.glob("*.jpg") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jpg", p.name))[-limit:]

    @staticmethod
    def photo_file(name: str) -> Path | tuple[bytes, str] | None:
        """A stored photo by file name, or None. The demo answers with (bytes, content type) instead."""
        if not PHOTO_NAME.fullmatch(name):
            return None
        path = PHOTOS / name
        return path if path.exists() else None

    def photo_taken_today(self) -> bool:
        return self.photo_path(date.today()).exists()

    @staticmethod
    def latest() -> dict[str, Any] | None:
        files = sorted(ANALYSES.glob("*.json")) if ANALYSES.exists() else []
        for path in reversed(files):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        return None

    def analysed_today(self) -> bool:
        today = date.today().isoformat()
        return ANALYSES.exists() and any(ANALYSES.glob(f"{today}*.json"))

    def due(self, retry_after: float = 2 * 3600) -> bool:
        """Today's reading is missing, and the last failed try is long enough ago."""
        if self.analysed_today():
            return False
        return self.last_attempt is None or time.time() - self.last_attempt > retry_after

    def manual_runs_today(self) -> int:
        today = date.today().isoformat()
        return sum(1 for p in ANALYSES.glob(f"{today}*.json")) if ANALYSES.exists() else 0

    # ---------- work ----------

    async def take_photo(self, *, camera: Any = None, source: dict[str, Any] | None = None) -> Path:
        if source and source.get("id") not in ("usb", "growcam"):
            raise ValueError("Unknown observation camera")
        PHOTOS.mkdir(parents=True, exist_ok=True)
        jpg = await (camera or self.camera).photo()
        path = (PHOTOS / f"{datetime.now():%Y-%m-%d}_{source['id']}_{datetime.now():%H-%M-%S-%f}.jpg"
                if source else self.photo_path(date.today()))
        path.write_bytes(jpg)
        return path

    def keep_alive(self) -> Any:
        """0 (unload right after the reading), unless keep_alive_hint returns a duration such as "10m" or a number of
        seconds. A hint that fails or returns anything else is 0."""
        hint = self.keep_alive_hint
        if hint is None:
            return 0
        try:
            value = hint()
        except Exception:  # a broken hint never stops the reading
            _LOGGER.exception("keep_alive hint failed; the model is unloaded right after")
            return 0
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            return 0
        if isinstance(value, str) and not re.fullmatch(r"\d+(?:\.\d+)?[smh]?", value.strip()):
            return 0
        if not isinstance(value, str) and value < 0:  # -1 would keep 8 GB loaded for good
            return 0
        return value.strip() if isinstance(value, str) else value

    async def _chat(self, body: dict[str, Any]) -> dict[str, Any]:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.url}/api/chat", json=body) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RuntimeError(data.get("error") or f"HTTP {resp.status}")
                return data

    async def analyze(self, photo: Path, snap: dict[str, Any], plant: dict[str, Any] | None,
                      lang: str | None = None, *, observation_only: bool = False,
                      source: dict[str, Any] | None = None) -> dict[str, Any]:
        lang = normalize(lang) or current_lang()
        text = (f"Fresh camera observation: {datetime.now().isoformat(timespec='seconds')}. "
                "Determine the visible stage from this image alone; describe uncertainty."
                if observation_only else context_text(snap, plant, self.latest(), lang))
        schema = {**SCHEMA, "properties": {**SCHEMA["properties"], "stage_guess": {
            "type": "string", "enum": [s for s in STAGES if s != "harvest"]}}} if observation_only else SCHEMA
        body = {
            "model": self.model,
            "stream": False,
            "format": schema,  # Ollama constrains the output to this JSON schema
            "keep_alive": self.keep_alive(),  # 0: unload right after, the Mac is a server first
            "think": False,  # no long reasoning before the answer: on the Mac's CPU that alone takes minutes
            "options": {"temperature": 0.2, "num_ctx": 4096},
            "messages": [
                {"role": "system", "content": (OBSERVATION_SYSTEM if observation_only else SYSTEM)[lang]},
                {"role": "user", "content": text,
                 "images": [base64.standard_b64encode(photo.read_bytes()).decode("utf-8")]},
            ],
        }
        # One model job at a time (spec 4.9): with Hank's lane the reading waits for a running chat answer. The wrap
        # sits here, outside _chat, which the tests replace. The time counts from the moment the model is ours.
        async with self.lane.hold("photo") if self.lane is not None else nullcontext():
            started = time.time()
            data = await self._chat(body)
        record: dict[str, Any] = {
            "date": datetime.now().isoformat(timespec="minutes"),
            "photo": photo.name,
            "model": data.get("model") or self.model,
            "local": True,
            "tokens": {"in": data.get("prompt_eval_count"), "out": data.get("eval_count")},
            "seconds": round(time.time() - started, 1),
            # Ollama reports nanoseconds; the split shows whether loading, reading or writing is slow.
            "timing": {key: round(data[f"{key}_duration"] / 1e9, 1) if data.get(f"{key}_duration") else None
                       for key in ("load", "prompt_eval", "eval")},
            "lang": lang,
            "context": text,
        }
        if source:
            record["camera_source"] = dict(source)
            record["photo_taken_at"] = datetime.fromtimestamp(photo.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        if observation_only:
            record["observation_only"] = True
        try:
            result = json.loads((data.get("message") or {}).get("content") or "")
        except ValueError:
            result = None
        if not isinstance(result, dict) or result.get("stage_guess") not in STAGES:
            record["result"] = None
            record["error"] = t("vision.unreadable", lang)
        else:
            if observation_only:
                result["next_steps"] = []
                if result.get("stage_guess") == "harvest":
                    result["stage_guess"], result["confidence"] = "unclear", "low"
            record["result"] = result
        ANALYSES.mkdir(parents=True, exist_ok=True)
        out = ANALYSES / f"{datetime.now():%Y-%m-%d_%H-%M-%S-%f}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        _LOGGER.info("Plant reading %s: %s (%s, %s s; tokens %s; timing %s)", out.name,
                     (record.get("result") or {}).get("stage_guess"), record["model"], record["seconds"],
                     record["tokens"], record["timing"])
        return record

    async def run(self, snap: dict[str, Any], plant: dict[str, Any] | None,
                  lang: str | None = None, *, camera: Any = None, source: dict[str, Any] | None = None,
                  observation_only: bool = False) -> dict[str, Any]:
        lang = normalize(lang) or current_lang()
        if self.busy:
            raise ValueError(t("vision.busy", lang))
        self.busy = True
        self.started = self.last_attempt = time.time()
        try:
            try:
                photo = await self.take_photo(camera=camera, source=source) if source else await self.take_photo()
            except Exception as err:  # camera trouble too must reach the page as a sentence
                # A catalog error stays one, so view() says it in the reader's language, inner part included.
                self._error = ("vision.photo_failed", {"reason": err if isinstance(err, TextError) else str(err)[:160]})
                _LOGGER.warning("Plant photo failed: %r", err)
                return {"photo": None, "result": None, "error": t(self._error[0], lang, **self._error[1]),
                        "lang": lang}
            try:
                record = (await self.analyze(photo, snap, plant, lang, observation_only=True, source=source)
                          if observation_only else await self.analyze(photo, snap, plant, lang))
            except Exception as err:  # model trouble must reach the page as a sentence, not as a 500
                self._error = _explain(err, self.model)
                _LOGGER.warning("Plant reading failed: %r", err)
                return {"photo": photo.name, "result": None, "error": explain(err, self.model, lang), "lang": lang}
            self._error = ("vision.unreadable", {}) if record.get("error") else None
            return record
        finally:
            self.busy = False
            self.started = None

    def view(self) -> dict[str, Any]:
        latest = self.latest()
        if latest:
            latest = {k: v for k, v in latest.items() if k != "context"}  # the prompt stays on the server
            latest.setdefault("lang", "de")  # readings before 22.10.2026 have no lang: all were German
        return {"enabled": self.enabled, "busy": self.busy,
                "error": t(self._error[0], **self._error[1]) if self._error else None, "latest": latest,
                "model": self.model, "running_s": round(time.time() - self.started) if self.started else None,
                "photo_today": self.photo_taken_today(), "demo": self.demo, "stage_run": stage_run()}
