"""What Hank must never claim or say (spec 5.5, 5.6; addendum N5), pure code.

- switch_claim(): a sentence that says Hank switched, set or changed something ("ich habe ausgeschaltet", "ist jetzt
  auf", "I've turned", "is now at", also the future "ich schalte jetzt"). The validator replaces it with
  hank.reply.no_self (WP5's catalog), the roadmap's promise "wird ersetzt".
- forbidden(): consumption, effect, taste, selling, passing on, dosing for consumption, from never_say in
  vgrow/hank/data/chat_policy.json (owned by WP3, read here only).
- model_forbidden(): what only a model sentence may never say (model_never_say, acceptance 23.10.2026, review 3, H4):
  links, effects, medicine, law, passing on, the humidifier automation off, the full chart dose. The vetted card and
  code texts may say some of these (the automation is off, the dose factor), so the draft is not checked against it.
- vendors(): seed shop names in running text are replaced by the policy's rules, which keep the grammar (decision
  D3, review 3, L3); blocked_url() drops a link to a seed shop or a retailer, the source stays as text.

Switch claims are matched on folded text (text.fold), so umlauts and their ae/oe/ue spellings both match.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import text

_LOGGER = logging.getLogger(__name__)

POLICY_FILE = Path(__file__).resolve().parent / "data" / "chat_policy.json"
NO_SELF_KEY = "hank.reply.no_self"

# Participles of switching and setting, folded (bestätigt -> bestatigt, erhöht -> erhoht).
_DONE_DE = (
    "geschaltet", "ausgeschaltet", "eingeschaltet", "angeschaltet", "umgeschaltet", "abgeschaltet", "zugeschaltet",
    "eingestellt", "umgestellt", "hochgestellt", "runtergestellt", "heruntergestellt", "gestellt", "angepasst",
    "gestartet", "gestoppt", "bestatigt", "gespeichert", "eingetragen", "hochgedreht", "runtergedreht",
    "heruntergedreht", "aufgedreht", "zugedreht", "gedreht", "erhoht", "gesenkt", "geandert", "aktiviert",
    "deaktiviert", "angemacht", "ausgemacht", "gedimmt", "gesetzt",
    # acceptance 23.10.2026 (review 3, H4)
    "hochgefahren", "runtergefahren", "heruntergefahren", "hochgeregelt", "runtergeregelt", "geregelt", "erledigt",
    "umgesetzt", "ausgefuhrt", "ubernommen", "gemacht", "abgedreht", "zugemacht", "aufgemacht",
)
_DONE = "|".join(_DONE_DE)
_CLAIMS_DE = [
    re.compile(rf"\bich\s+(?:habe|hab)\b[^.!?]{{0,60}}?\b(?:{_DONE})\b"),
    re.compile(rf"\bhab(?:e)?\s+ich\b[^.!?]{{0,60}}?\b(?:{_DONE})\b"),
    re.compile(r"\bist\s+(?:jetzt|nun|ab\s+sofort)\s+(?:auf|an|aus|bei|eingeschaltet|ausgeschaltet)\b"),
    re.compile(r"\bich\s+(?:schalte|stelle|drehe|mache|starte|setze|bestatige|speichere|trage|erhohe|senke|dimme)\b"
               r"(?![^.!?]{0,12}\b(?:nichts|nie|nicht|niemals)\b)"
               r"[^.!?]{0,40}?\b(?:jetzt|gleich|sofort|nun|direkt)\b"),
    # acceptance 23.10.2026 (review 3, H4): the present without "jetzt", "Hab die Abluft hochgefahren", the passive
    # "wurde von mir ausgeschaltet", and "Erledigt, ..."
    re.compile(r"\bich\s+(?:schalte|stelle|drehe|mache|setze|fahre|erhohe|senke|dimme|aktiviere|deaktiviere|regle|"
               r"regele)\b(?![^.!?]{0,12}\b(?:nichts|nie|nicht|niemals)\b)"
               r"[^.!?]{0,40}?\b(?:aus|an|ein|ab|hoch|runter|herunter|auf|um|zu)\b"),
    re.compile(rf"^\W*hab(?:e)?\b[^.!?]{{0,60}}?\b(?:{_DONE})\b"),
    re.compile(rf"\bvon\s+mir\b|\bdurch\s+mich\b"),
    re.compile(r"^\W*(?:erledigt|gemacht|fertig|schon\s+passiert)\b"),
]
_VERBS_EN = ("switched", "turned", "set", "adjusted", "started", "stopped", "confirmed", "logged", "saved", "raised",
             "lowered", "changed", "increased", "decreased", "activated", "deactivated", "enabled", "disabled",
             "dimmed", "updated")
_NOW_EN = ("switch", "turn", "set", "adjust", "start", "stop", "raise", "lower", "change", "increase", "decrease",
           "dim", "enable", "disable")
_DOING_EN = ("switching", "turning", "setting", "adjusting", "starting", "stopping", "raising", "lowering", "changing",
             "increasing", "decreasing", "dimming", "enabling", "disabling")
_CLAIMS_EN = [
    re.compile(rf"\b(?:i\s+have|i\s*['’]?ve|i\s+just|i\s+already|i)\s+(?:just\s+|already\s+|now\s+)?"
               rf"(?:{'|'.join(_VERBS_EN)})\b"),
    re.compile(r"\bis\s+now\s+(?:at|on|off|set|running|switched|turned)\b"),
    re.compile(rf"\bi\s*(?:['’]ll|\s+will|\s+am\s+going\s+to|['’]m\s+going\s+to)\s+(?:now\s+|just\s+)?"
               rf"(?:{'|'.join(_NOW_EN)})\b"),
    re.compile(rf"\bi\s*(?:\s+am|['’]m)\s+(?:now\s+|just\s+)?(?:{'|'.join(_DOING_EN)})\b"),
    # acceptance 23.10.2026 (review 3, H4): "I've gone ahead and turned", "was switched off by me", "Done, ..."
    re.compile(rf"\bi\b[^.!?]{{0,30}}\b(?:{'|'.join(v for v in _VERBS_EN if v != 'set')}|took care of|handled|did it|did that)\b"),
    re.compile(r"\bby\s+me\b"),
    re.compile(r"^\W*(?:done|all\s+done|finished|taken\s+care\s+of)\b"),
]


def switch_claim(sentence: str, lang: str = "de") -> bool:
    """True when the sentence claims Hank switched or set something, now or in the future. Both languages are
    checked, because a model can slip into the other one."""
    folded = text.fold(sentence)
    patterns = (_CLAIMS_DE + _CLAIMS_EN) if lang == "de" else (_CLAIMS_EN + _CLAIMS_DE)
    return any(p.search(folded) for p in patterns)


# ---------- chat policy ----------

@lru_cache(maxsize=4)
def _policy_cached(path: str, mtime: float) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        _LOGGER.error("chat policy unreadable (%s): nothing is filtered by it", err)
        return {}
    return data if isinstance(data, dict) else {}


def policy(path: Path = POLICY_FILE) -> dict[str, Any]:
    """chat_policy.json, re-read when it changes. An unreadable file gives {} and an error in the log."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    return _policy_cached(str(path), mtime)


@lru_cache(maxsize=8)
def _never_say(raw: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    out = []
    for pattern in raw:
        try:
            out.append(re.compile(pattern, re.IGNORECASE))
        except re.error as err:
            _LOGGER.error("chat policy pattern %r does not compile: %s", pattern, err)
    return tuple(out)


def forbidden(sentence: str, lang: str = "de", pol: dict[str, Any] | None = None) -> str | None:
    """The never_say pattern a sentence matches (both languages), or None."""
    pol = policy() if pol is None else pol
    rules = (pol.get("never_say") or {})
    for code in (lang, "en" if lang == "de" else "de"):
        for pattern in _never_say(tuple(rules.get(code) or ())):
            if pattern.search(sentence):
                return pattern.pattern
    return None


def model_forbidden(sentence: str, lang: str = "de", pol: dict[str, Any] | None = None) -> str | None:
    """The model_never_say pattern a model sentence matches (both languages), or None (review 3, H4)."""
    pol = policy() if pol is None else pol
    rules = (pol.get("model_never_say") or {})
    for code in (lang, "en" if lang == "de" else "de"):
        for pattern in _never_say(tuple(rules.get(code) or ())):
            if pattern.search(sentence):
                return pattern.pattern
    return None


@lru_cache(maxsize=8)
def _vendor_rules(raw: tuple[tuple[str, str, str], ...]) -> tuple[tuple[str, re.Pattern[str], str], ...]:
    out = []
    for lang, pattern, replace in raw:
        try:
            out.append((lang, re.compile(pattern, re.IGNORECASE), replace))
        except re.error as err:
            _LOGGER.error("chat policy vendor rule %r does not compile: %s", pattern, err)
    return tuple(out)


def _capitalize_at_start(sentence: str, match: re.Match[str], replaced: str) -> str:
    """A replacement starts with a capital where the replaced text did, at the start of a sentence."""
    before = sentence[:match.start()].rstrip()
    if match.group(0)[:1].isupper() and (not before or before[-1] in ".!?"):
        return replaced[:1].upper() + replaced[1:]
    return replaced


def vendors(sentence: str, lang: str = "de", pol: dict[str, Any] | None = None) -> str:
    """Seed shop names in running text replaced (decision D3): first the policy's rules, which keep the grammar
    ("laut Hanfgarten" becomes "laut einem Samenhändler", review 3, L3), then the plain names. A replacement at the
    start of a sentence starts with a capital."""
    pol = policy() if pol is None else pol
    section = pol.get("vendor_names") or {}
    raw = tuple((str(r.get("lang") or ""), str(r.get("pattern") or ""), str(r.get("replace") or ""))
                for r in section.get("rules") or [] if isinstance(r, dict) and r.get("pattern"))
    for rule_lang, pattern, replace in _vendor_rules(raw):
        if rule_lang and rule_lang != lang:
            continue
        sentence = pattern.sub(lambda m, r=replace, s=sentence: _capitalize_at_start(s, m, m.expand(r)), sentence)
    names = section.get("names") or {}
    for name, words in names.items():
        word = (words or {}).get(lang) or (words or {}).get("de") or ""
        sentence = re.sub(rf"\b{re.escape(name)}\b",
                          lambda m, w=word, s=sentence: _capitalize_at_start(s, m, w), sentence,
                          flags=re.IGNORECASE)
    return sentence


def blocked_url(url: str | None, pol: dict[str, Any] | None = None) -> bool:
    """True for a link to one of the blocked hosts (seed shops), subdomains included."""
    if not url:
        return False
    pol = policy() if pol is None else pol
    hosts = [h.lower() for h in ((pol.get("blocked_source_hosts") or {}).get("hosts") or [])]
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)
