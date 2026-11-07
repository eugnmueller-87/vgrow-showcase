"""Which plant the cockpit runs on, and what a variety from the wiki brings to it.

The automation reads one run: plant.json in the repository until the owner chooses a plant here, then plant-run.json in
the state folder (plant.run_path). The chosen run keeps the repository's medium, water targets and feeding setups and
names the plant record it belongs to. Choosing another plant sets the old plant's log aside and starts a new one, so a
stage or a height of the last plant never counts for this one. A plant record alone still changes nothing: only this
step does, on the owner's own choice (workspace.js, "Das Cockpit regelt nach dieser Pflanze").

What a variety brings (owner, 06.11.2026): autoflower or photoperiodic, which decides the light plan; the flowering time
and the time from seed to harvest; the height against the box, as a warning. Climate, water and feed stay with the stage
card: the lexicon and the encyclopedia hold no such figures per strain, and none are made up. THC never leaves the
reference. Whether a plant is an autoflower is the owner's word: the data only suggests it.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from . import config, i18n, plant

_LOGGER = logging.getLogger(__name__)

SOURCES = ("lexicon", "encyclopedia")
FLOWERING = ("auto", "photoperiod")
KEPT = ("medium", "water_targets", "feeding", "nutrient_card", "sources")  # what a new run takes from plant.json
LOG_ARCHIVE = "plant-logs"
LAMP_FREE_CM = 35  # autopilot.LAMP_MIN_CM; the general span for 100 to 300 W LEDs on the stage card, S7
PHOTOPERIOD_DOSE_NOTE = {
    "dose_factor_note": "Faktor 0,5 zum Start: 420seedz beginnt in Light-Mix-Erde mit der halben Dosis (N6). Nie mehr als die Tabelle.",
    "dose_factor_note_en": "Factor 0.5 to start: 420seedz starts with half the dose in light-mix soil (N6). Never more than the chart.",
    "dose_factor_sources": ["N6"],
}
_CARDS: dict[str, dict[str, Any]] = {}

DE = {
    "run.no_stage_card": "Die Phasenkarte fehlt, deshalb kann das Cockpit diese Pflanze nicht übernehmen.",
    "run.unknown_stage": "Diese Phase kennt die Phasenkarte nicht.",
    "run.not_this_box": "Das Cockpit regelt nur die verbundene Box.",
}
EN = {
    "run.no_stage_card": "The stage card is missing, so the cockpit cannot take this plant on.",
    "run.unknown_stage": "The stage card does not know this stage.",
    "run.not_this_box": "The cockpit only runs the connected box.",
}
i18n.DE.update(DE)
i18n.EN.update(EN)

WARN = {
    "tall": ("Wird laut {by} {height} hoch, die Box hat {box} cm. Sie wächst aus der Box: Netz nutzen, Triebe flach halten, "
             "Abstand zur Lampe im Blick behalten.",
             "According to {by} it grows {height}, the box has {box} cm. It outgrows the box: use the net, keep the shoots "
             "low, watch the distance to the lamp."),
    "lst": ("Wird laut {by} {height} hoch, die Box hat {box} cm. Kann höher werden als die Box: Netz nutzen, Triebe flach "
            "halten, Abstand zur Lampe im Blick behalten.",
            "According to {by} it grows {height}, the box has {box} cm. It can outgrow the box: use the net, keep the "
            "shoots low, watch the distance to the lamp."),
    "photoperiod": ("Photoperiodisch: Sie blüht erst, wenn das Licht auf 12 Stunden steht, und verdoppelt in der Blüte ihre "
                    "Größe (Grow Weed Easy). Die Box hat {box} cm, {lamp} cm bleiben zur Lampe frei, also bei etwa {half} cm "
                    "die Phase auf Blüte setzen. Von selbst schaltet das Cockpit das nicht um.",
                    "Photoperiodic: it flowers only when the lamp is on 12 hours a day, and it doubles in size in flower (Grow "
                    "Weed Easy). The box has {box} cm, {lamp} cm stay free to the lamp, so set the stage to flower at about "
                    "{half} cm. The cockpit does not switch that by itself."),
    "unknown": ("Noch offen, ob Autoflower oder photoperiodisch: Ohne das gibt es keinen automatischen Lichtplan.",
                "Still open whether it is an autoflower or photoperiodic: without that there is no automatic light plan."),
}


# ---------- what the databases know ----------

def _span(value: Any) -> list[int | None] | None:
    return list(value) if value else None


def _lexicon_rows(lang: str, on: Iterable[str], library: Path | None) -> list[dict[str, Any]]:
    from . import strains

    lex = strains.load(library or plant.LIBRARY)
    if not lex.visible(on):
        return []
    return [{"source": "lexicon", "key": s.key, "label": lex.text(s.label, lang), "brand": s.brand, "by": s.brand,
             "flowering": s.flowering if s.flowering in FLOWERING else None, "bloom": _span(s.bloom_days),
             "height": _span(s.height), "height_word": None, "harvest": _span(s.harvest), "difficulty": s.difficulty,
             "fit": s.fit(lex.box_cm), "box_cm": lex.box_cm} for s in lex.strains]


def _encyclopedia_rows(lang: str, on: Iterable[str], library: Path | None) -> list[dict[str, Any]]:
    from . import strains, wiki

    box = strains.load(library or plant.LIBRARY).box_cm
    rows = []
    for entry in wiki._entries(library or wiki.LIBRARY):
        if entry.get("folder") != "varieties" or not wiki.visible(entry, on):
            continue
        raw = entry["raw"]
        reference = raw.get("reference") if isinstance(raw.get("reference"), Mapping) else {}
        grow = wiki.grow_view(reference.get("grow"), reference.get("seed_facts"), box) or {}
        origin = grow.get("from") or {}
        height = (origin.get("height") or {}).get("cm")
        days = (origin.get("flowering_days") or {}).get("days")
        by = next((o.get("by") for o in origin.values() if o.get("by")), None)
        rows.append({"source": "encyclopedia", "key": raw.get("key") or entry.get("key"),
                     "label": (raw.get("label_en") if lang == "en" else None) or raw.get("label") or entry.get("key"),
                     "brand": "", "by": by, "flowering": None,  # the reference does not say which seed it is
                     "bloom": list(days) if days else [grow["flowering_days"], grow["flowering_days"]]
                     if grow.get("flowering_days") else None,
                     "height": list(height) if height else None, "height_word": None if height else grow.get("height"),
                     "harvest": None, "difficulty": grow.get("difficulty"),
                     "fit": strains.fit_of(tuple(height), box) if height else "unknown", "box_cm": box})
    return rows


def options(lang: str | None = None, on: Iterable[str] | None = None, library: Path | None = None) -> list[dict[str, Any]]:
    """Every variety the owner can pick when adding a plant, sorted by name; empty while the cannabis package is off."""
    from . import wiki

    lang = i18n.normalize(lang) or i18n.current_lang()
    on = wiki.packages() if on is None else on
    rows = [*_lexicon_rows(lang, on, library), *_encyclopedia_rows(lang, on, library)]
    rows.sort(key=lambda r: (str(r["label"]).casefold(), r["source"] != "lexicon", str(r["brand"]).casefold()))
    return rows


def find(ref: Mapping[str, Any] | None, lang: str | None = None, on: Iterable[str] | None = None,
         library: Path | None = None) -> dict[str, Any] | None:
    """One variety by its {"source", "key"}; None when it is not there (any more) or the package is off."""
    if not isinstance(ref, Mapping):
        return None
    return next((r for r in options(lang, on, library) if r["source"] == ref.get("source") and r["key"] == ref.get("key")),
                None)


# ---------- the run ----------

def run_file() -> Path:
    return config.STATE_DIR / plant.RUN_FILE


def _read_chosen() -> dict[str, Any]:
    try:
        data = json.loads(run_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def active_record() -> str | None:
    """The plant record the chosen run belongs to; None while the repository's plant.json runs."""
    found = (_read_chosen().get("plant") or {}).get("record")
    return found if isinstance(found, str) else None


def _height_text(lo: int | None, hi: int | None, both: str, up_to: str, from_: str) -> str:
    if lo is not None and hi is not None:
        return both.format(lo, hi)
    return up_to.format(hi) if hi is not None else from_.format(lo)


def strain_card(run: Mapping[str, Any], library: Path | None = None) -> dict[str, Any]:
    """The run's strain as plant.load_profile hands it on, kept per run: every poll and page load reads the profile, and
    building it walks both databases. The databases only change with a deploy, which restarts the server."""
    key = json.dumps([run.get("strain_ref"), run.get("flowering"), (run.get("plant") or {}).get("strain"), str(library)],
                     sort_keys=True, default=str)
    if key not in _CARDS:
        if len(_CARDS) >= 16:
            _CARDS.clear()
        _CARDS[key] = _build_card(run, library)
    return copy.deepcopy(_CARDS[key])


def _build_card(run: Mapping[str, Any], library: Path | None) -> dict[str, Any]:
    """The same fields as a card in kartei/strains/, built from the lexicon or the encyclopedia. The warnings come from the
    height against the box and from the owner's flowering type."""
    from . import strains

    ref = run.get("strain_ref")
    flowering = run.get("flowering") if run.get("flowering") in FLOWERING else None
    name = str((run.get("plant") or {}).get("strain") or "")
    card: dict[str, Any] = {"name": name, "grow": {"flowering": flowering, "sources": []}}
    row_de = find(ref, "de", library=library)
    row_en = find(ref, "en", library=library)
    if row_de is not None:
        card["name"], card["name_en"] = row_de["label"], (row_en or row_de)["label"]
        grow = card["grow"]
        grow["height_indoor_cm"] = row_de["height"]
        if row_de["bloom"]:
            grow["flowering_days"] = row_de["bloom"]
        if row_de["harvest"]:
            grow["seed_to_harvest_days"] = row_de["harvest"]
        if ref.get("source") == "lexicon":
            found = strains.load(library or plant.LIBRARY).get(ref.get("key"))
            if found is not None:
                card.update(facts=dict(found.facts.get("de") or {}), facts_en=dict(found.facts.get("en") or {}),
                            fact_labels=dict(found.labels.get("de") or {}), fact_labels_en=dict(found.labels.get("en") or {}))
                grow["genotype"] = found.genotype
                grow["sources"] = list(found.sources)
    de, en = [], []
    fit = row_de["fit"] if row_de is not None else "unknown"
    if fit in ("tall", "lst"):
        lo, hi = row_de["height"]
        de.append(WARN[fit][0].format(by=row_de["by"] or "Züchter", box=row_de["box_cm"],
                                      height=_height_text(lo, hi, "{} bis {} cm", "bis {} cm", "ab {} cm")))
        en.append(WARN[fit][1].format(by=row_de["by"] or "the breeder", box=row_de["box_cm"],
                                      height=_height_text(lo, hi, "{} to {} cm", "up to {} cm", "from {} cm")))
    if flowering != "auto":
        which = "photoperiod" if flowering == "photoperiod" else "unknown"
        box = strains.load(library or plant.LIBRARY).box_cm or 92
        half = (box - LAMP_FREE_CM) // 2  # it doubles after the switch, and the canopy must stay this far from the lamp
        de.append(WARN[which][0].format(half=half, box=box, lamp=LAMP_FREE_CM))
        en.append(WARN[which][1].format(half=half, box=box, lamp=LAMP_FREE_CM))
    if de:
        card.update(warnings=de, warnings_en=en)
    return card


def _stage_keys(card: str | None) -> list[str] | None:
    try:
        data = json.loads((plant.LIBRARY / "stages" / f"{card}.json").read_text(encoding="utf-8"))
        return [s["key"] for s in data["stages"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_run(record: Mapping[str, Any]) -> dict[str, Any]:
    """The run for a plant record: the plant, its variety and its flowering type on top of what plant.json keeps."""
    try:
        base = json.loads(plant.PLANT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        base = {}
    base = base if isinstance(base, dict) else {}
    run = {key: base[key] for key in KEPT if key in base}
    if isinstance(run.get("feeding"), dict):  # plant.json's dates and notes belong to its own plant
        run["feeding"] = {k: v for k, v in run["feeding"].items() if not k.startswith("bloom_start_estimate")}
        run["feeding"]["bloom_start"] = None
    run["stages_card"] = base.get("stages_card") or "autoflower"
    run["plant"] = {"strain": record.get("name") or record.get("variety") or "", "start_date": record.get("started"),
                    "start_approx": bool(record.get("approximate")), "record": record["id"]}
    flowering = record.get("flowering") if record.get("flowering") in FLOWERING else None
    run["flowering"] = flowering
    if flowering == "photoperiod":
        run["stages_variant"] = "photoperiod"
        if isinstance(run.get("feeding"), dict):
            run["feeding"].update(PHOTOPERIOD_DOSE_NOTE)
    if flowering != "auto":
        run["light_schedule"] = None  # the card's cycle is for autoflowers; a photoperiodic plant gets its own per stage
    if record.get("variety_ref"):
        run["strain_ref"] = dict(record["variety_ref"])
    return run


def activate(record: Mapping[str, Any], log: Any, stage: str | None = None, today: date | None = None) -> dict[str, Any]:
    """Make this plant record the cockpit's run. A new plant sets the last plant's log aside and starts its own; the same
    plant again only takes the record's new name, date or flowering type and keeps its log. stage: the owner's stage."""
    from .box import CommandRefused

    run = build_run(record)
    keys = _stage_keys(run["stages_card"])
    if keys is None:
        raise CommandRefused(i18n.t("run.no_stage_card"))
    if stage is not None and stage not in keys:
        raise CommandRefused(i18n.t("run.unknown_stage"))
    day = today or date.today()
    previous = active_record()
    new_plant = previous != record["id"]
    config.atomic_write(run_file(), json.dumps(run, ensure_ascii=False, indent=2) + "\n")
    if new_plant:
        log.start_new_run(config.STATE_DIR / LOG_ARCHIVE / f"{previous or 'plant-json'}-{day.isoformat()}.json")
    if stage is not None:
        log.set_stage(stage, day.isoformat())
    return {"ok": True, "new_plant": new_plant, "plant": run["plant"]}
