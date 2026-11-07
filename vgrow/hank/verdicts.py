"""Verdicts from code, never from the model (spec 4.3, addendum N3).

One sentence per plant.evaluate() row (status ok, low, high, unknown, none) from catalog templates, for example
"Luftfeuchte am Fühler: 79 %, zu hoch (Ziel 45 bis 60 %, Frühe Blüte (Stretch), Phase nur geschätzt)."

Rules:
- Readings older than 600 s (automation.STALE_SECONDS) give no verdict at all, only the stale sentence (live-05).
- A stage the owner did not set (set_by other than "manual") adds "Phase nur geschätzt" (live-04).
- Verdicts hold for the current stage only; the caller asks for none when the question names another stage.
- A range the card review marked "partial" carries the note "allgemeiner Richtwert, nicht speziell für Cannabis
  belegt" (addendum N3), once per answer.
- The card's consequence (plant.evaluate passes it only inside consequence_<status>_from) becomes a Fact with its
  own sources (spec 6.4); whether Hank tells it is the ledger's rule (spec 6.3), not decided here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from vgrow import plant
from vgrow.automation import STALE_SECONDS
from vgrow.i18n import fmt_num, fmt_qty, localize, t

from . import text
from .livestate import LiveState, minutes, reading
from .types import Fact, SourceRef, Verdict

NEGATIVE = ("low", "high")
# v1.2 (owner, 23.10.2026): climate before light, light last. Humidity first (too humid in flower costs the yield,
# card S17), then temperature, then VPD; the light step waits anyway while it is too warm or too dry (fix.py).
URGENCY = {"probe_humidity": 0, "humidity": 0, "room_humidity": 0, "probe_temp": 1, "temp": 1, "room_temp": 1,
           "probe_vpd": 2, "vpd": 2, "light_percent": 9}
URGENCY_OTHER = 5  # every other metric: after the climate, before the light


def urgency(metric: str) -> int:
    """The rank of a metric in "most urgent first" (lower first): climate, then the rest, light last."""
    return URGENCY.get(metric, URGENCY_OTHER)
Resolve = Callable[[str, Iterable[str], str], tuple[SourceRef, ...]]  # (card, source ids, lang) -> refs


@dataclass
class VerdictSet:
    """The verdicts of one answer, or the stale sentence instead of them."""

    verdicts: list[Verdict] = field(default_factory=list)
    stale: bool = False
    stale_sentence: str | None = None
    sources: dict[int, tuple[SourceRef, ...]] = field(default_factory=dict)  # index in verdicts -> range sources
    partial: set[int] = field(default_factory=set)  # indexes whose range the review marked partial

    def sentences(self, lang: str) -> list[str]:
        if self.stale:
            return [s for s in (self.stale_sentence,) if s]
        return once_estimate([v.sentence for v in self.verdicts], lang)


def once_estimate(sentences: list[str], lang: str) -> list[str]:
    """"Phase nur geschätzt" in the first sentence that says it, not in every one (screens review, finding 10). Done
    on the shown sentences, so a verdict that a caller leaves out never takes the note with it."""
    marker = t("hank.verdict.estimate", lang, stage="\x00")
    suffix = marker.split("\x00", 1)[1] if "\x00" in marker else ""
    if not suffix.strip():
        return list(sentences)
    out: list[str] = []
    said = False
    for sentence in sentences:
        if suffix in sentence:
            if said:
                sentence = sentence.replace(suffix, "", 1)
            said = True
        out.append(sentence)
    return out


def by_urgency(found: VerdictSet) -> VerdictSet:
    """The same verdicts, climate first and light last (stable within a rank), their sources and partial marks
    moved along."""
    order = sorted(range(len(found.verdicts)), key=lambda i: urgency(found.verdicts[i].metric))
    out = VerdictSet(verdicts=[found.verdicts[i] for i in order], stale=found.stale,
                     stale_sentence=found.stale_sentence)
    for new, old in enumerate(order):
        if old in found.sources:
            out.sources[new] = found.sources[old]
        if old in found.partial:
            out.partial.add(new)
    return out


def label(metric: str, fallback: str, lang: str) -> str:
    key = f"hank.metric.{metric}"
    found = t(key, lang)
    return fallback if found == key else found


def target_text(lo: float | None, hi: float | None, unit: str, when: str | None, lang: str) -> str:
    if lo is not None and hi is not None and lo == hi:
        out = t("hank.verdict.exact", lang, value=fmt_qty(lo, unit, lang))
    elif lo is not None and hi is not None:
        out = t("hank.verdict.range", lang, lo=fmt_num(lo, lang), hi=fmt_qty(hi, unit, lang))
    elif lo is not None:
        out = t("hank.verdict.at_least", lang, lo=fmt_qty(lo, unit, lang))
    elif hi is not None:
        out = t("hank.verdict.at_most", lang, hi=fmt_qty(hi, unit, lang))
    else:
        out = ""
    if out and when in ("day", "night"):
        out += " " + t(f"hank.verdict.when.{when}", lang)
    return out


def stage_name(stage: dict, lang: str) -> str:
    """The stage's name in `lang` from the stage card itself (label, label_en), so a sentence in one language never
    carries the name of the other: the ticker builds both languages from one moment (acceptance 23.10.2026, screens
    review 2, finding 1). The moment's own label is the fallback."""
    key = stage.get("key")
    try:
        profile = plant.load_profile()
    except Exception:  # noqa: BLE001 (a broken profile never stops a verdict)
        profile = None
    for entry in (profile or {}).get("stages") or []:
        if isinstance(entry, dict) and entry.get("key") == key:
            name = localize(entry, lang).get("label")
            if name:
                return str(name)
    return str(stage.get("label") or key or "")


def stage_text(stage: dict | None, lang: str) -> str:
    if not stage:
        return ""
    name = stage_name(stage, lang)
    return name if stage.get("set_by") == "manual" else t("hank.verdict.estimate", lang, stage=name)


def stale_sentence(live: LiveState, lang: str) -> str:
    age = minutes(live.sensors_age)
    if age is None:
        age = minutes(STALE_SECONDS) or 0
    return t("hank.verdict.stale", lang, minutes=age)


def _value(row: dict, lang: str) -> str:
    actual = row.get("actual")
    if actual is None:
        return t("hank.digest.none", lang)
    if str(row.get("metric")) in ("probe_humidity", "humidity", "probe_temp", "temp", "probe_vpd", "vpd"):
        # as the tile shows it, with one decimal more when that alone shows it is off target ("60,4 %")
        lo, hi = (row.get("min"), row.get("max")) if row.get("status") in NEGATIVE else (None, None)
        return reading(actual, str(row["metric"]), row.get("unit", ""), lang, lo, hi)
    digits = None if float(actual).is_integer() else 1
    return fmt_qty(actual, row.get("unit", ""), lang, digits)


def value_text(verdict: Verdict, lang: str) -> str:
    """A verdict's reading as its sentence writes it (the same rounding and boundary decimals as _value)."""
    return _value({"actual": verdict.actual, "metric": verdict.metric, "unit": verdict.unit, "min": verdict.lo,
                   "max": verdict.hi, "status": verdict.status}, lang)


def build(live: LiveState, lang: str, *, metrics: Iterable[str] | None = None, statuses: Iterable[str] | None = None,
          card: str | None = None, resolve: Resolve | None = None, show: bool = True) -> VerdictSet:
    """The verdicts for the rows of `metrics` (None: every row) whose status is in `statuses` (None: every status).
    show=False gives an empty set (another stage was asked for). card is the stage card's id ("stages/autoflower")
    for resolving the range and consequence sources."""
    if not show:
        return VerdictSet()
    if live.stale:
        return VerdictSet(stale=True, stale_sentence=stale_sentence(live, lang))
    wanted = None if metrics is None else set(metrics)
    allowed = None if statuses is None else set(statuses)
    stage = live.stage
    out = VerdictSet()
    for row in live.checks:
        metric = row.get("metric")
        status = row.get("status")
        if wanted is not None and metric not in wanted or allowed is not None and status not in allowed:
            continue
        if status not in ("ok", "high", "low", "unknown", "none"):
            continue
        name = label(str(metric), str(row.get("label") or metric), lang)
        params = {"label": name, "value": _value(row, lang),
                  "target": target_text(row.get("min"), row.get("max"), row.get("unit", ""), row.get("when"), lang),
                  "stage": stage_text(stage, lang)}
        sentence = t(f"hank.verdict.{status}", lang, **params)
        consequence = None
        if status in NEGATIVE and row.get("consequence"):
            refs = resolve(card, row.get("consequence_sources") or [], lang) if resolve and card else ()
            consequence = Fact(id=f"C:{metric}", kind="consequence", text=str(row["consequence"]),
                               numbers=tuple(text.parse_numbers(str(row["consequence"]), lang)), sources=refs,
                               passage=None)
        verdict = Verdict(metric=str(metric), label=name, status=str(status), actual=row.get("actual"),
                          lo=row.get("min"), hi=row.get("max"), unit=str(row.get("unit", "")), when=row.get("when"),
                          stage_key=(stage or {}).get("key"), stage_set_by=(stage or {}).get("set_by"),
                          sentence=sentence, advice=row.get("advice") if status in NEGATIVE else None,
                          consequence=consequence)
        index = len(out.verdicts)
        out.verdicts.append(verdict)
        if status in ("ok", "high", "low") and resolve and card:
            out.sources[index] = resolve(card, row.get("sources") or [], lang)
        if row.get("verified") == "partial" and status in ("ok", "high", "low"):
            out.partial.add(index)
    return out
