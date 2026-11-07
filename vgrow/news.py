"""A quiet news watcher: what happens in the cannabis world (law, research, growing), read once a day.

Not a news stream. The legally clean feeds are mostly official and rarely mention cannabis, so the page will
often say "nothing new"; that is the product ("nicht ueberladen"). Sources, rights and attribution live in
news_sources.json, checked in docs/news-quellen.md.

Rules from that check, kept for every source:
- titles and links only: no teaser, no description, no content:encoded, no images, no authors;
- one request at a time, crawl delay per host, an honest User-Agent, conditional GET;
- HTTP 429 or 503 pauses a source for 6 hours, 403/404/410 for 7 days;
- "private" sources (with care) are fetched and shown only while the install is private, and that has to
  be said on purpose (private=True or config.NEWS_PRIVATE = True); otherwise their titles leave the store;
- items that promote consumption or sale are dropped before they are stored (section 6 KCanG spirit).
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit

import aiohttp

from . import config

_LOGGER = logging.getLogger(__name__)

SOURCES_FILE = Path(__file__).resolve().parent / "news_sources.json"
USER_AGENT = "VGrowCockpit/1.0 (privater Feedreader, lokal)"
ACCEPT = ("application/rss+xml, application/atom+xml, application/rdf+xml, application/xml;q=0.9, "
          "text/xml;q=0.9, */*;q=0.1")
TIMEOUT = 20.0  # seconds per feed, redirects included, polite waiting excluded
MAX_BYTES = 2 * 1024 * 1024  # a feed is a few hundred KB; beyond this the source is skipped
MAX_REDIRECTS = 3
SAME_HOST_GAP_S = 5.0  # at least this between two requests to one host (docs/news-quellen.md, section 5)
BACKOFF_THROTTLED = timedelta(hours=6)  # HTTP 429 or 503
BACKOFF_GONE = timedelta(days=7)  # HTTP 403, 404, 410: blocked or moved, look again next week
BACKOFF_MAX = timedelta(days=7)  # a Retry-After beyond this is not believed
MAX_ITEMS = 200
MAX_SEEN = 3000  # dedupe memory beyond the stored items, so a pruned item does not come back as new
FIRST_FETCH_WINDOW = timedelta(days=30)  # on a source's first fetch older items are marked seen, not shown
FUTURE_SLACK = timedelta(days=1)  # a feed date further ahead than this is wrong (Bundesrat 444/26)
DAILY_HOUR = 6
STALE_AFTER = timedelta(hours=24)
RETRY_GUARD = timedelta(hours=1)  # never twice within an hour, also when everything failed
TITLE_MAX = 600  # stored titles are cut here, marked with an ellipsis and "title_cut"; the filter reads them whole
RAW_TITLE_MAX = 4 * TITLE_MAX  # raw title text beyond this is never cleaned: a hostile title costs linear time
LINK_MAX = 2000  # an entry with a longer link is skipped
DROPPED_KEEP = 20  # the last dropped titles per source, so a wrong drop is visible
RECHECK_AFTER = timedelta(days=90)  # terms and robots.txt are read again every three months
ELLIPSIS = "…"
STATE_VERSION = 2  # 2: guid keys carry the source id

RIGHTS = ("free", "private")
FILTERS = ("cannabis", "garden", "all")
KINDS = ("official", "court", "press", "advocacy", "research", "horticulture")
DEDUPE = ("link+guid", "link+date")
REDIRECTS = (301, 302, 303, 307, 308)
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "wt_mc", "wt.mc_id", "wt_cc"}
NO_DROP_KINDS = ("official", "court")  # authorities and courts do not advertise: the drop list can only harm there
BROAD_LAW_KINDS = ("official", "court", "press")  # Betaeubungsmittel and BtMG count as topic words here

Sleep = Callable[[float], Awaitable[Any]]


class FeedError(ValueError):
    """A feed that cannot be read: refused, malformed, too large, wrong status."""


# ---------- keywords ----------

def _rx(pattern: str) -> re.Pattern[str]:
    """Case-insensitive, whole words: no letter or digit may touch the match on either side."""
    return re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE)


# German compounds carry the keyword inside a longer word (Cannabisgesetz, Medizinalcannabis), so some
# patterns allow letters around the stem. Each stays a whole word.
CANNABIS: list[tuple[str, re.Pattern[str]]] = [(label, _rx(p)) for label, p in [
    ("Cannabis", r"\w*cannabis\w*"),
    ("Konsumcannabis", r"konsumcannabis\w*"),
    ("Medizinalcannabis", r"medizinal-?cannabis\w*"),
    ("KCanG", r"kcang"),
    ("MedCanG", r"medcang"),
    ("Cannabinoid", r"\w*cannabinoid\w*"),
    ("THC", r"thc"),
    ("THC-Grenzwert", r"thc-grenzwert\w*"),
    ("Hanf", r"\w*hanf\w*"),
    ("Eigenanbau", r"eigenanbau\w*"),
    ("Anbauvereinigung", r"anbauverein\w*"),
    ("Cannabis Social Club", r"cannabis[- ]social[- ]clubs?"),
    ("EKOCAN", r"ekocan"),
    ("Teillegalisierung", r"teillegalisierung\w*"),
    ("Marihuana", r"marihuana|marijuana"),
    ("Kiffen", r"kiff\w*"),
    ("Hemp", r"hemp"),
]]
# The narcotics law around cannabis; on official, court and press feeds only, where it means law news.
CANNABIS_LAW: list[tuple[str, re.Pattern[str]]] = [(label, _rx(p)) for label, p in [
    ("Betäubungsmittel", r"\w*bet(?:ä|ae)ubungsmittel\w*"),
    ("BtMG", r"btmg"),
]]

# A small home-gardening set: the gardening feeds are professional, this keeps what a hobby grower can use.
GARDEN: list[tuple[str, re.Pattern[str]]] = [(label, _rx(p)) for label, p in [
    ("Hobbygarten", r"hobbyg(?:a|ä|ae)rt\w*|freizeitg(?:a|ä|ae)rt\w*"),
    ("Hausgarten", r"hausg(?:a|ä|ae)rt\w*|haus- und kleing(?:a|ä|ae)rt\w*"),
    ("Kleingarten", r"kleing(?:a|ä|ae)rt\w*"),
    ("Kräuter", r"\w*kr(?:ä|ae)uter\w*"),
    ("Balkon", r"balkon\w*"),
    ("Topf", r"(?:gem(?:ü|ue)se )?im topf|in t(?:ö|oe)pfen|topfpflanz\w*|k(?:ü|ue)belpflanz\w*"),
    ("Tomate", r"\w*tomate\w*|tomato(?:es)?"),
    ("Pflanzenschutz im Garten", r"pflanzenschutz im (?:haus|klein|hobby)?garten"),
    ("Schädlinge", r"\w*sch(?:ä|ae)dling\w*"),
    ("Nützlinge", r"n(?:ü|ue)tzling\w*"),
    ("Hochbeet", r"hochbeet\w*"),
    ("Kompost", r"kompost\w*"),
    ("Zimmerpflanze", r"zimmerpflanz\w*"),
    ("Gartenboden", r"gartenb(?:o|ö|oe)den"),
    ("Microgreens", r"microgreens|sprossen"),
    ("Home garden", r"home gard\w*|houseplants?|container gard\w*"),
]]

# Titles that sell or promote consumption (section 6 KCanG spirit). Three tiers, so law news about shops,
# seeds or vaporizers is never thrown out with the advertising:
# - official and court feeds skip the drop list completely (NO_DROP_KINDS);
# - AD_MARK drops always: paid content, raffles, "strain of the month";
# - DROP drops only when the title carries no law or research word (CONTEXT): "BGH: Verkauf von Samen im
#   Online-Shop ..." stays, "Growshop-Rabatt: 20 Prozent auf Seeds" goes.
# Deliberate deviation from the word list in docs/news-quellen.md (section 4.3): bare "samen", "preis",
# "versand", "angebot", "rauchen", "verdampfen" and "stecklinge" are not drop words. They hit policy and
# growing news far more often than sales in the checked feeds; the intent forms below catch the sales.
AD_MARK = _rx("|".join([
    r"\w*gutschein\w*", r"coupons?", r"\w*gewinnspiel\w*", r"giveaway",
    r"sorte des monats", r"strain des monats", r"strain of the month",
    r"advertorial", r"sponsored", r"in kooperation mit",
]))
DROP = _rx("|".join([
    r"(?:online|grow|head|hanf|cbd|web)?-?shops?",
    r"\w*rabatt\w*", r"discounts?", r"for sale", r"sale",
    r"(?:jetzt |online )?(?:kaufen|bestellen)", r"kauf(?:tipps?|ratgeber|beratung|empfehlung\w*)",
    r"buy(?:ing)?", r"jetzt erh(?:ä|ae)ltlich", r"neu im sortiment",
    r"sortenbericht\w*", r"sortentest\w*", r"strain[- ]reviews?", r"strains?\W+(?:guide|reviews?|ranking)",
    r"(?:best|beste[nr]?|top \d+)\W+(?:\w+\W+){0,3}?strains?",
    r"testberichte?",
    r"rezept online", r"online-?rezepte?", r"\w*kochrezept\w*", r"backrezept\w*", r"rezeptideen?",
    r"dosierung zum konsum", r"dosiertipps?", r"dosage",
    r"dosier\w*\W+(?:\w+\W+){0,4}?(?:einsteiger\w*|anf(?:ä|ae)nger\w*|beginners?|tipps?)",
    r"so (?:dosierst|rauchst|kiffst|verdampfst|vapest)",
    r"(?:richtig|besser) (?:rauchen|kiffen|dosieren|verdampfen|vapen)",
    r"tipps? (?:zum|f(?:ü|ue)rs) (?:rauchen|kiffen|dosieren|verdampfen|konsum\w*)",
    r"(?:rauchen|kiffen|verdampfen|dosieren|vapen)\W+(?:\w+\W+){0,4}?(?:tipps?|methoden|anleitung|guide|vergleich)",
    r"how to (?:smoke|vape|dose)",
    r"seedbanks?", r"seeds", r"samen (?:kaufen|bestellen|shop)",
    r"(?:verteil\w*|verkauf\w*|tausch\w*|verschenk\w*)\W+(?:\w+\W+){0,3}?stecklinge?",
    r"stecklinge?\W+(?:\w+\W+){0,3}?(?:verteil\w*|verkauf\w*|tausch\w*|kaufen|bestellen|abzugeben)",
    r"stecklings?(?:verkauf|tausch|verteilung|abgabe|b(?:ö|oe)rse)\w*",
    r"vaporizer\w*", r"vapes?", r"bongs?", r"edibles",
    r"tickets?", r"ticketverkauf",
]))
# Law and research words: a title with one of these is news about the subject, not an advert for it.
CONTEXT = _rx("|".join([
    r"\w*gesetz\w*", r"\w*verordnung\w*", r"novelle\w*", r"\w*entwurf\w*", r"\w*verbot\w*", r"verboten",
    r"\w*urteil\w*", r"beschluss\w*", r"beschlie(?:ß|ss)\w*", r"\w*gericht\w*", r"bgh", r"bverfg", r"bverwg",
    r"olg", r"lg", r"vg", r"ovg", r"bundestag\w*", r"bundesrat\w*", r"bundesregierung", r"kabinett\w*",
    r"ministeri\w*", r"eu-kommission", r"kommission", r"\w*grenzwert\w*", r"bfarm", r"kcang", r"medcang",
    r"btmg", r"rechtslage", r"rechtsprechung", r"strafbar\w*", r"(?:an|verfassungs|eil|sammel)?klagen?",
    r"stud(?:ie|ien|y|ies)", r"forschung\w*", r"research", r"evaluation\w*", r"preprint",
]))
# Paid-content markers ("Anzeige:", "[Werbung]"); a bare "Anzeige" is also a criminal complaint, which stays.
PAID_MARK = re.compile(r"^\W*(?:anzeige|werbung|sponsored|promotion)\s*[:|\]\)-]|[\[(]\s*(?:anzeige|werbung|sponsored)"
                       r"\s*[\])]", re.IGNORECASE)


def _labels(text: str, sets: Iterable[list[tuple[str, re.Pattern[str]]]]) -> list[str]:
    found: list[str] = []
    for keywords in sets:
        for label, pattern in keywords:
            if label not in found and pattern.search(text):
                found.append(label)
    return found


def _link_words(link: str) -> str:
    """The link path as words: BMG page links often name the topic (/themen/cannabis/...)."""
    try:
        path = unquote(urlsplit(link).path)
    except ValueError:
        return ""
    return re.sub(r"[/_.\-+]+", " ", path)


def promotes(title: str, kind: str = "") -> bool:
    """True for a title that sells or promotes consumption. Never for official or court feeds."""
    if kind in NO_DROP_KINDS:
        return False
    if PAID_MARK.search(title) or AD_MARK.search(title):
        return True
    return bool(DROP.search(title)) and not CONTEXT.search(title)


def judge(title: str, link: str, filter_kind: str = "cannabis", drop_link_contains: Iterable[str] = (),
          kind: str = "") -> tuple[str, list[str]]:
    """("keep", keywords), ("promo", []), ("link", []) or ("off_topic", []).

    The drop list comes first and reads the title only; the topic filter reads title and link path.
    """
    if any(part and part in link for part in drop_link_contains):
        return "link", []
    if promotes(title, kind):
        return "promo", []
    text = f"{title} {_link_words(link)}"
    topic = [CANNABIS, CANNABIS_LAW] if kind in BROAD_LAW_KINDS else [CANNABIS]
    if filter_kind == "all":
        return "keep", _labels(text, topic)
    found = _labels(text, [GARDEN, *topic] if filter_kind == "garden" else topic)
    return ("keep", found) if found else ("off_topic", [])


def classify(title: str, link: str, filter_kind: str = "cannabis",
             drop_link_contains: Iterable[str] = (), kind: str = "") -> list[str] | None:
    """Matched keywords for an item that stays, None for one that goes."""
    verdict, found = judge(title, link, filter_kind, drop_link_contains, kind)
    return found if verdict == "keep" else None


# ---------- parsing ----------

_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_ENCODING_DECL = re.compile(rb"^\s*<\?xml[^>]*encoding\s*=", re.IGNORECASE)
_TAG = re.compile(r"</?[A-Za-z!?][^<>]*>")
_SCRIPT_OPEN = re.compile(r"<\s*(script|style)\b", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TZ_NAMES = {"CET": "+0100", "MEZ": "+0100", "CEST": "+0200", "MESZ": "+0200"}

# Feed fields are read only from these namespaces: no namespace (RSS 2.0), Atom (1.0 and 0.3), RSS 1.0 and 0.9,
# Dublin Core. A media:title or a foreign <link> never stands in for the entry's own.
FEED_NS = frozenset({
    "", "http://www.w3.org/2005/Atom", "http://purl.org/atom/ns#", "http://purl.org/rss/1.0/",
    "http://my.netscape.com/rdf/simple/0.9/", "http://purl.org/dc/elements/1.1/", "http://purl.org/dc/terms/",
})


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ns(tag: Any) -> str:
    return tag[1:tag.index("}")] if isinstance(tag, str) and tag.startswith("{") and "}" in tag else ""


def _children(el: ET.Element, *names: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) in names and _ns(c.tag) in FEED_NS]


def _first_text(el: ET.Element, *names: str) -> str:
    """Text of the first child with one of the names, in the order given."""
    for name in names:
        for child in _children(el, name):
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


_INLINE = {"a", "abbr", "b", "code", "em", "i", "mark", "q", "s", "small", "span", "strong", "sub", "sup", "u"}


def _drop_scripts(text: str) -> str:
    """Script and style blocks out, in one pass (linear time). An unclosed block takes the rest with it."""
    low = text.lower()
    out: list[str] = []
    pos = 0
    while (m := _SCRIPT_OPEN.search(text, pos)) is not None:
        out.append(text[pos:m.start()])
        end = low.find(f"</{m.group(1).lower()}", m.end())
        close = low.find(">", end) if end >= 0 else -1
        pos = close + 1 if close >= 0 else len(text)
        out.append(" ")
    out.append(text[pos:])
    return "".join(out)


def _untag(text: str) -> str:
    """Inline tags vanish (<b>BGH</b>: stays "BGH:"), every other tag becomes a space."""
    def swap(m: re.Match[str]) -> str:
        name = re.match(r"</?\s*([A-Za-z]+)", m.group(0))
        return "" if name and name.group(1).lower() in _INLINE else " "
    return _TAG.sub(swap, _drop_scripts(text))


def clean_title(raw: str) -> str:
    """Plain text: tags out, entities decoded (twice, for double-escaped feeds), whitespace collapsed.

    Not cut to TITLE_MAX: the filter reads the whole title, the store cuts it (with a mark).
    """
    text = html.unescape(_untag(raw[:RAW_TITLE_MAX]))
    text = html.unescape(_untag(text))
    return _SPACE.sub(" ", _CONTROL.sub("", text)).strip()


def stored_title(title: str) -> tuple[str, bool]:
    """The title as stored: unchanged up to TITLE_MAX, beyond that cut and marked with an ellipsis."""
    if len(title) <= TITLE_MAX:
        return title, False
    return title[:TITLE_MAX - 1].rstrip() + ELLIPSIS, True


def parse_date(text: str) -> datetime | None:
    """RFC 822, ISO 8601 (also date only, as the BGBl writes it) or DD.MM.YYYY; None when unreadable."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].upper() in _TZ_NAMES:  # email.utils knows US zones only
        text = f"{parts[0]} {_TZ_NAMES[parts[1].upper()]}"
    dt: datetime | None = None
    try:
        dt = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.strptime(text, "%d.%m.%Y")
            except ValueError:
                return None
    if dt.year < 1990:
        return None
    return dt if dt.tzinfo else dt.astimezone()  # no zone: local time


def _web_link(raw: str, base: str) -> str:
    """An absolute http(s) link, relative ones resolved against the feed; anything else (javascript:) is ''."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        link = urljoin(base, raw) if base else raw
        parts = urlsplit(link)
    except ValueError:
        return ""
    return link if parts.scheme in ("http", "https") and parts.hostname else ""


def _entry_link(el: ET.Element, base: str) -> str:
    alternate, other = "", ""
    for child in _children(el, "link"):
        href = child.get("href")
        if href is None:  # RSS: the link is the text
            link = _web_link("".join(child.itertext()), base)
            alternate = alternate or link
            continue
        rel = child.get("rel", "alternate")
        link = _web_link(href, base)
        if rel == "alternate" and link:
            alternate = alternate or link
        elif link and rel not in ("self", "edit", "enclosure", "replies"):
            other = other or link
    if alternate or other:
        return alternate or other
    about = next((v for k, v in el.attrib.items() if _local(k) == "about"), "")  # RSS 1.0 rdf:about
    if about:
        return _web_link(about, base)
    for guid in _children(el, "guid"):
        if guid.get("isPermaLink", "true").lower() != "false":
            return _web_link("".join(guid.itertext()), base)
    return ""


def _prolog_end(body: bytes) -> int:
    """Where the prolog ends: the first markup that is not the XML declaration, a processing instruction,
    a comment or whitespace. A DOCTYPE can only stand there; anywhere later expat refuses it anyway."""
    pos = 3 if body.startswith(b"\xef\xbb\xbf") else 0
    while True:
        while pos < len(body) and body[pos:pos + 1].isspace():
            pos += 1
        if body.startswith(b"<?", pos):
            end = body.find(b"?>", pos + 2)
        elif body.startswith(b"<!--", pos):
            end = body.find(b"-->", pos + 4)
        else:
            return pos
        if end < 0:
            return pos
        pos = end + (2 if body.startswith(b"?>", end) else 3)


def _xml(body: bytes) -> ET.Element:
    """The parsed root. A feed without an encoding declaration that is not UTF-8 gets one retry as cp1252."""
    try:
        return ET.fromstring(body)
    except (ET.ParseError, ValueError, LookupError) as err:
        first = err
    if not _ENCODING_DECL.search(body[:200]):
        try:
            return ET.fromstring(body.decode("cp1252", errors="replace").encode("utf-8"))
        except (ET.ParseError, ValueError, LookupError):
            pass
    raise FeedError(f"not well-formed XML: {first}") from None


def parse(body: bytes, base_url: str = "") -> list[dict[str, Any]]:
    """Entries of an RSS 2.0, RSS 1.0 (RDF) or Atom feed: title, link, guid, published (datetime or None).

    Descriptions, summaries and content:encoded are never read. A DOCTYPE or ENTITY declaration in the
    prolog is refused before parsing, so no entity can expand. Raises FeedError, and only FeedError, for
    anything that is not a readable feed.
    """
    head = body[:64]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in head:
        raise FeedError("UTF-16 or UTF-32 feeds are not read")
    if _DECLARATION.match(body, _prolog_end(body)):
        raise FeedError("DOCTYPE or ENTITY declaration refused")
    root = _xml(body)
    kind = _local(root.tag)
    if kind == "rss":
        channels = _children(root, "channel")
        entries = [i for c in channels for i in _children(c, "item")] or _children(root, "item")
    elif kind == "RDF":
        entries = _children(root, "item")
    elif kind == "feed":
        entries = _children(root, "entry")
    else:
        raise FeedError(f"not a feed: root element <{kind}>")
    out: list[dict[str, Any]] = []
    for el in entries:
        title = clean_title(_first_text(el, "title"))
        link = _entry_link(el, base_url)
        if not title or not link or len(link) > LINK_MAX:
            continue
        guid = _first_text(el, "guid", "id", "identifier")
        if not guid:
            guid = next((v for k, v in el.attrib.items() if _local(k) == "about"), "")
        published = parse_date(_first_text(el, "pubDate", "published", "date", "issued", "updated", "modified",
                                            "created"))
        out.append({"title": title, "link": link, "guid": guid.strip()[:500], "published": published})
    return out


# ---------- store ----------

def news_dir() -> Path:
    """Read at call time: tests and the demo point config.STATE_DIR elsewhere."""
    return config.STATE_DIR / "news"


def items_path() -> Path:
    return news_dir() / "items.json"


def state_path() -> Path:
    return news_dir() / "state.json"


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.astimezone()


def _load(path: Path) -> dict[str, Any] | None:
    """The file as a dict, None if missing. A corrupt file is moved aside (never overwritten unseen)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as err:
        data, problem = None, f"{type(err).__name__}: {err}"
    else:
        problem = None if isinstance(data, dict) else "the top level is not an object"
    if problem:
        aside = path.with_name(f"{path.name}.broken-{datetime.now():%Y%m%d-%H%M%S}")
        _LOGGER.warning("%s unreadable (%s): kept as %s, starting empty", path, problem, aside.name)
        try:
            path.replace(aside)
        except OSError as err:
            _LOGGER.warning("could not move %s aside: %s", path, err)
        return None
    return data


def load_state() -> dict[str, Any]:
    data = _load(state_path()) or {"version": STATE_VERSION}
    if not isinstance(data.get("sources"), dict):
        data["sources"] = {}
    if not isinstance(data.get("seen"), list):
        data["seen"] = []
    if data.get("version") != STATE_VERSION:
        # Version 1 kept guids without their source; those keys cannot be told apart, so they go. The link
        # keys stay, and every stored item still counts as known.
        data["seen"] = [k for k in data["seen"] if isinstance(k, str) and not k.startswith("guid:")]
        data["version"] = STATE_VERSION
    return data


def load_items() -> list[dict[str, Any]]:
    data = _load(items_path()) or {}
    items = data.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def save_state(state: dict[str, Any]) -> None:
    config.atomic_write(state_path(), json.dumps(state, indent=1, ensure_ascii=False) + "\n")


def save_items(items: list[dict[str, Any]]) -> None:
    config.atomic_write(items_path(), json.dumps({"version": 1, "items": items}, indent=1, ensure_ascii=False)
                        + "\n")


def normalize_link(link: str) -> str:
    """One key per article: no scheme, no www., no fragment, no trailing slash, no tracking parameters."""
    try:
        parts = urlsplit(link.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return link.strip()
    if host.startswith("www."):
        host = host[4:]
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", re.sub(r";jsessionid=[^/?#]*", "", parts.path or "/", flags=re.IGNORECASE))
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS)
    return host + path + (f"?{urlencode(query)}" if query else "")


def _keys(entry: dict[str, Any], source: dict[str, Any]) -> list[str]:
    """Dedupe keys of a feed entry; the first one also makes the item id.

    "link+guid" (default): the normalised link, plus the guid scoped to the source (short numeric guids of
    two feeds must not block each other). "link+date": link plus feed date, no guid, for feeds whose guid is
    a page that comes back changed with a new date (BMG page changes).
    """
    link = f"link:{normalize_link(str(entry.get('link') or ''))}"
    if source.get("dedupe") == "link+date":
        published = entry.get("published")
        return [f"{link}@{_iso(published)}" if isinstance(published, datetime) else link]
    keys = [link]
    if entry.get("guid"):
        keys.append(f"guid:{source.get('id', '')}:{entry['guid']}")
    return keys


def _item_keys(item: dict[str, Any]) -> list[str]:
    """The keys a stored item was filed under (older items without them: the link key)."""
    keys = item.get("keys")
    if isinstance(keys, list) and keys:
        return [k for k in keys if isinstance(k, str)]
    return [f"link:{normalize_link(str(item.get('link') or ''))}"]


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _sort_key(item: dict[str, Any]) -> tuple[datetime, datetime]:
    """First seen, then the feed date: the feed date alone is unreliable (Bundesrat, LWK order)."""
    return _time(item.get("first_seen")) or _EPOCH, _time(item.get("published")) or _EPOCH


def merge(items: list[dict[str, Any]], state: dict[str, Any], source: dict[str, Any],
          entries: list[dict[str, Any]], now: datetime, first_fetch: bool = False) -> list[dict[str, Any]]:
    """Add the entries that pass the filter and are not known yet; returns the new items.

    Known means: same normalised link or same guid of this source, also for items pruned from the store long
    ago, and every key of an item still in the store (a lost state.json must not double the list). On a
    source's first fetch, entries older than FIRST_FETCH_WINDOW are only remembered, not shown. Dropped
    promotion titles are counted per source, the last DROPPED_KEEP of them kept (titles only).
    """
    seen: list[str] = state.setdefault("seen", [])
    st = state.setdefault("sources", {}).setdefault(source["id"], {})
    known = set(seen) | {k for item in items for k in _item_keys(item)}
    ids = {item.get("id") for item in items}
    kind = str(source.get("publisher_kind") or "")
    needs_date = "TT.MM.JJJJ" in str(source.get("attribution") or "")
    by_date = source.get("dedupe") == "link+date"
    added: list[dict[str, Any]] = []
    for entry in entries:
        verdict, matched = judge(entry["title"], entry["link"], source.get("filter", "cannabis"),
                                 source.get("drop_link_contains") or (), kind)
        if verdict != "keep":
            counter = "dropped" if verdict == "promo" else verdict  # dropped, off_topic, link
            st[counter] = int(st.get(counter) or 0) + 1
            if verdict == "promo":
                st["dropped_titles"] = ([*(st.get("dropped_titles") or []), entry["title"][:TITLE_MAX]]
                                        [-DROPPED_KEEP:])
            continue
        published: datetime | None = entry.get("published")
        if published and published > now + FUTURE_SLACK:
            published = None  # a date in the future is a feed error, first seen is the honest date
        if needs_date and not published:
            continue  # the required source line needs the feed's date; not remembered, it may come dated
        keys = _keys(entry, source)
        item_id = hashlib.sha1(keys[0].encode("utf-8")).hexdigest()[:16]
        if item_id in ids or any(k in known for k in keys):
            continue
        known.update(keys)
        seen.extend(keys)
        ids.add(item_id)
        if first_fetch and published and published < now - FIRST_FETCH_WINDOW:
            continue
        st["kept"] = int(st.get("kept") or 0) + 1
        if by_date:  # a changed page replaces its older version, the list does not grow per change
            page = normalize_link(entry["link"])
            items[:] = [i for i in items if not (i.get("source_id") == source["id"]
                                                 and normalize_link(str(i.get("link") or "")) == page)]
        title, cut = stored_title(entry["title"])
        added.append({
            "id": item_id,
            "source_id": source["id"],
            "source_name": source.get("name", source["id"]),
            "publisher_kind": source.get("publisher_kind"),
            "rights": source.get("rights"),
            "lang": source.get("lang"),
            "title": title,
            "title_cut": cut,
            "link": entry["link"],
            "published": _iso(published or now),
            "dated": "feed" if published else "first_seen",
            "first_seen": _iso(now),
            "matched": matched,
            "keys": keys,
        })
    items.extend(added)
    items.sort(key=_sort_key, reverse=True)
    del items[MAX_ITEMS:]
    if len(seen) > MAX_SEEN:
        keep = {k for item in items for k in _item_keys(item)}
        state["seen"] = [k for k in seen[:-MAX_SEEN] if k in keep] + seen[-MAX_SEEN:]
    return added


# ---------- sources ----------

def _source_problem(raw: Any, ids: set[str]) -> str | None:
    if not isinstance(raw, dict):
        return "not an object"
    if not isinstance(raw.get("id"), str) or not raw["id"] or raw["id"] in ids:
        return "missing or duplicate id"
    url = raw.get("feed_url")
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        return "feed_url must be https"
    if raw.get("rights") not in RIGHTS:
        return f"rights must be one of {RIGHTS}"
    if raw.get("filter") not in FILTERS:
        return f"filter must be one of {FILTERS}"
    if raw.get("publisher_kind") not in KINDS:
        return f"publisher_kind must be one of {KINDS}"
    for field in ("crawl_delay_s", "window_hours"):
        value = raw.get(field, 0)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return f"{field} must be a number >= 0"
    if raw.get("dedupe", "link+guid") not in DEDUPE:
        return f"dedupe must be one of {DEDUPE}"
    return None


def is_private(private: bool | None = None) -> bool:
    """The install's privacy switch. None reads config.NEWS_PRIVATE and, without it, says no: "private"
    sources are only ever fetched or shown after someone said yes on purpose."""
    if private is None:
        return getattr(config, "NEWS_PRIVATE", False) is True
    return bool(private)


def load_sources(private: bool | None = None, path: Path | None = None) -> list[dict[str, Any]]:
    """Active sources from news_sources.json; "private" ones only while the install is private.

    A source with a broken entry is skipped and logged; the rest still load.
    """
    private = is_private(private)
    data = json.loads((path or SOURCES_FILE).read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in data.get("sources", []):
        problem = _source_problem(raw, ids)
        if problem:
            _LOGGER.error("News source %s skipped: %s", raw.get("id") if isinstance(raw, dict) else raw, problem)
            continue
        ids.add(raw["id"])
        if not raw.get("active", True) or (raw["rights"] == "private" and not private):
            continue
        out.append(raw)
    return out


# ---------- fetching ----------

def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


async def _polite_wait(host: str, delay: float, host_last: dict[str, float], sleep: Sleep) -> None:
    last = host_last.get(host)
    if last is not None and delay > 0:
        wait = delay - (time.monotonic() - last)
        if wait > 0:
            await sleep(wait)
    host_last[host] = time.monotonic()


async def _read(resp: aiohttp.ClientResponse) -> bytes:
    """The body, but never more than MAX_BYTES: reading stops there and the feed is skipped."""
    if resp.content_length is not None and resp.content_length > MAX_BYTES:
        raise FeedError(f"larger than {MAX_BYTES} bytes ({resp.content_length})")
    data = bytearray()
    async for chunk in resp.content.iter_chunked(64 * 1024):
        data += chunk
        if len(data) > MAX_BYTES:
            raise FeedError(f"larger than {MAX_BYTES} bytes")
    return bytes(data)


async def _get(session: aiohttp.ClientSession, url: str, headers: dict[str, str], delay: float,
               host_last: dict[str, float], sleep: Sleep) -> tuple[int, str, dict[str, str], bytes]:
    """One feed: status, final URL, the headers we use, body (only for 200).

    Redirects are followed by hand, at most MAX_REDIRECTS, only on the same host (terms and robots.txt are
    per host) and never from https down to http.
    """
    budget = TIMEOUT
    origin = _host(url)
    for _hop in range(MAX_REDIRECTS + 1):
        await _polite_wait(_host(url), delay, host_last, sleep)
        started = time.monotonic()
        async with asyncio.timeout(budget):
            async with session.get(url, headers=headers, allow_redirects=False) as resp:
                kept = {k: resp.headers[k] for k in ("ETag", "Last-Modified", "Retry-After", "Location")
                        if k in resp.headers}
                if resp.status not in REDIRECTS:
                    body = await _read(resp) if resp.status == 200 else b""
                    return resp.status, url, kept, body
        target = _web_link(kept.get("Location", ""), url)
        if not target:
            raise FeedError(f"HTTP {resp.status} without a usable Location")
        if _host(target) != origin:
            raise FeedError(f"redirect to another host refused: {_host(target)}")
        if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
            raise FeedError("redirect from https to http refused")
        url = target
        budget -= time.monotonic() - started
        if budget <= 0:
            raise TimeoutError
    raise FeedError(f"more than {MAX_REDIRECTS} redirects")


def _retry_after(value: str | None, now: datetime) -> timedelta:
    if not value:
        return timedelta(0)
    value = value.strip()
    try:
        if value.isdigit():  # capped before converting: a 30-digit header must not overflow timedelta
            return timedelta(seconds=min(int(value[:12]), int(BACKOFF_MAX.total_seconds())))
        when = parse_date(value)
        return max(when - now, timedelta(0)) if when else timedelta(0)
    except (OverflowError, ValueError, TypeError):
        return timedelta(0)


def _short(err: BaseException) -> str:
    text = str(err).strip()
    return (f"{type(err).__name__}: {text}" if text else type(err).__name__)[:200]


async def _fetch_one(session: aiohttp.ClientSession, source: dict[str, Any], st: dict[str, Any],
                     items: list[dict[str, Any]], state: dict[str, Any], now: datetime, delay: float,
                     host_last: dict[str, float], sleep: Sleep) -> list[dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT}
    if st.get("etag"):
        headers["If-None-Match"] = st["etag"]
    if st.get("last_modified"):
        headers["If-Modified-Since"] = st["last_modified"]
    status, final_url, got, body = await _get(session, source["feed_url"], headers, delay, host_last, sleep)
    # A pause longer than the feed's window can hide items that came and went in between: say so.
    previous = _time(st.get("last_ok"))
    window = float(source.get("window_hours") or 0)
    gap = bool(previous and window and now - previous > timedelta(hours=window))
    if status == 304:
        st.update(last_ok=_iso(now), last_error=None, backoff_until=None, gap=gap)
        return []
    if status in (429, 503):
        pause = min(max(BACKOFF_THROTTLED, _retry_after(got.get("Retry-After"), now)), BACKOFF_MAX)
        st["backoff_until"] = _iso(now + pause)
        raise FeedError(f"HTTP {status}, paused until {st['backoff_until']}")
    if status in (403, 404, 410):
        st["backoff_until"] = _iso(now + BACKOFF_GONE)
        raise FeedError(f"HTTP {status}, paused until {st['backoff_until']}")
    if status != 200:
        raise FeedError(f"HTTP {status}")
    entries = await asyncio.to_thread(parse, body, final_url)  # up to 2 MB of XML: off the event loop
    added = merge(items, state, source, entries, now, first_fetch=not st.get("last_ok"))
    # Validators only after a feed that parsed: a broken answer must not be cached as "unchanged".
    st.update(etag=got.get("ETag"), last_modified=got.get("Last-Modified"), last_ok=_iso(now), last_error=None,
              backoff_until=None, feed_items=len(entries), gap=gap)
    return added


_running = False


def _forget_private(items: list[dict[str, Any]], state: dict[str, Any],
                    sources: Iterable[dict[str, Any]]) -> None:
    """Not private any more: titles of "private" sources leave the store, not only the view."""
    private_ids = {s.get("id") for s in sources if s.get("rights") == "private"}
    before = len(items)
    items[:] = [i for i in items if i.get("rights") != "private" and i.get("source_id") not in private_ids]
    for sid, st in state.get("sources", {}).items():
        if sid in private_ids or st.get("rights") == "private":
            st.pop("dropped_titles", None)
    if len(items) != before:
        _LOGGER.info("News: %d items of private sources removed", before - len(items))


async def fetch_all(session: aiohttp.ClientSession, sources: list[dict[str, Any]],
                    state: dict[str, Any] | None = None, *, private: bool | None = None,
                    now: datetime | None = None, sleep: Sleep = asyncio.sleep) -> list[dict[str, Any]]:
    """Fetch every source, one after the other, store what is new and return the new items.

    Never raises: each failure is logged, written to the source's state and skipped. A source in back-off
    is not asked at all; a "private" source is skipped when the install is not private, and its stored
    titles are removed. private=None reads the install's switch (is_private).
    """
    global _running
    if _running:
        _LOGGER.info("News fetch already running")
        return []
    _running = True
    try:
        private = is_private(private)
        now = _aware(now or _now())
        try:
            state = state if state is not None else load_state()
            state.setdefault("sources", {})
            items = load_items()
        except OSError as err:
            _LOGGER.error("News store unreadable, nothing fetched: %s", err)
            return []
        if not private:
            _forget_private(items, state, sources)
        host_delay: dict[str, float] = {}
        for source in sources:
            host = _host(source.get("feed_url", ""))
            host_delay[host] = max(host_delay.get(host, SAME_HOST_GAP_S), float(source.get("crawl_delay_s") or 0))
        host_last: dict[str, float] = {}
        new: list[dict[str, Any]] = []
        any_ok = False
        for source in sources:
            sid = source.get("id")
            if not sid or not source.get("active", True):
                continue
            if source.get("rights") != "free" and not (private and source.get("rights") == "private"):
                continue
            st = state["sources"].setdefault(sid, {})
            st["rights"] = source.get("rights")
            paused = _time(st.get("backoff_until"))
            if paused and paused > now:
                _LOGGER.info("News source %s paused until %s", sid, st["backoff_until"])
                continue
            try:
                added = await _fetch_one(session, source, st, items, state, now,
                                         host_delay.get(_host(source["feed_url"]), SAME_HOST_GAP_S), host_last, sleep)
            except Exception as err:  # one bad feed never stops the others, nor reaches the caller
                st["last_error"] = _short(err)
                _LOGGER.warning("News source %s skipped: %s", sid, st["last_error"])
                continue
            any_ok = True
            new.extend(added)
            if added:
                _LOGGER.info("News source %s: %d new", sid, len(added))
        for sid, st in state["sources"].items():
            st["count"] = sum(1 for i in items if i.get("source_id") == sid)
        state["last_run"] = _iso(now)
        state.setdefault("first_run", _iso(now))
        if any_ok:
            state["last_success"] = _iso(now)
        try:
            save_items(items)
            save_state(state)
        except OSError as err:
            _LOGGER.error("News store not written: %s", err)
        return new
    except Exception as err:  # the contract is "never raises"; a bug here is logged, not thrown
        _LOGGER.exception("News fetch failed: %s", err)
        return []
    finally:
        _running = False


async def refresh(private: bool | None = None,
                  session: aiohttp.ClientSession | None = None) -> list[dict[str, Any]]:
    """Load the sources and fetch them; for the server's daily task. Never raises."""
    private = is_private(private)
    try:
        sources = load_sources(private=private)
    except (OSError, ValueError) as err:
        _LOGGER.error("News sources unreadable: %s", err)
        return []
    if session is not None:
        return await fetch_all(session, sources, private=private)
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as own:
        return await fetch_all(own, sources, private=private)


# ---------- reading ----------

def due(now: datetime, state: dict[str, Any]) -> bool:
    """Once a day from 06:00 local time; at any hour when the last good run is older than 24 h.
    Never twice within RETRY_GUARD, so a dead network does not mean a request storm."""
    now = _aware(now)
    last_run = _time(state.get("last_run"))
    if last_run is None or last_run > now + timedelta(minutes=5):  # never run, or a clock that went back
        return True
    if now - last_run < RETRY_GUARD:
        return False
    last_success = _time(state.get("last_success"))
    if last_success is None or now - last_success >= STALE_AFTER:
        return True
    threshold = now.replace(hour=DAILY_HOUR, minute=0, second=0, microsecond=0)
    return now >= threshold and last_run < threshold


def attribution(source: dict[str, Any], item: dict[str, Any]) -> str:
    """The source line a publisher requires; the BGH form needs the date of the press release."""
    text = source.get("attribution") or source.get("name") or item.get("source_name") or ""
    if "TT.MM.JJJJ" in text:
        published = _time(item.get("published")) if item.get("dated") == "feed" else None
        text = (text.replace("TT.MM.JJJJ", f"{published:%d.%m.%Y}") if published
                else text.replace(" vom TT.MM.JJJJ", "").replace("TT.MM.JJJJ", "").strip())
    return text


_INTERNAL = ("keys",)  # dedupe data, not for the page


def view(private: bool | None = None, limit: int = 30, days: int | None = None,
         now: datetime | None = None) -> dict[str, Any]:
    """What a future /api/news answers: newest items first, the sources' health and how long it has been quiet.

    Items of "private" sources only while the install is private (private=None reads the switch); a source
    switched off disappears with its items (the gardening chamber can withdraw its consent). days limits the
    list to items first seen within that many days (the page: days=7, limit=10).
    """
    private = is_private(private)
    now = _aware(now or _now())
    try:
        sources = load_sources(private=private)
    except (OSError, ValueError) as err:
        _LOGGER.error("News sources unreadable: %s", err)
        sources = []
    by_id = {s["id"]: s for s in sources}
    try:
        state = load_state()
        items = load_items()
    except OSError as err:
        _LOGGER.error("News store unreadable: %s", err)
        state, items = {"sources": {}}, []
    allowed = {"free", "private"} if private else {"free"}
    visible = [i for i in items if i.get("source_id") in by_id and by_id[i["source_id"]]["rights"] in allowed
               and i.get("rights") in allowed]
    visible.sort(key=_sort_key, reverse=True)
    quiet_since = visible[0].get("first_seen") if visible else state.get("first_run")
    if days is not None:
        since = now - timedelta(days=max(days, 0))
        visible = [i for i in visible if (_time(i.get("first_seen")) or _EPOCH) >= since]
    out_items = [{**{k: v for k, v in i.items() if k not in _INTERNAL},
                  "attribution": attribution(by_id[i["source_id"]], i),
                  "link_target": by_id[i["source_id"]].get("link_target", "_blank")}
                 for i in visible[:max(limit, 0)]]
    health = []
    for s in sources:
        st = state.get("sources", {}).get(s["id"], {})
        checked = _time(f"{s['checked']}T00:00:00") if isinstance(s.get("checked"), str) else None
        health.append({"id": s["id"], "name": s.get("name"), "kind": s.get("publisher_kind"),
                       "rights": s["rights"], "ok": bool(st.get("last_ok")) and not st.get("last_error"),
                       "last_ok": st.get("last_ok"), "last_error": st.get("last_error"),
                       "backoff_until": st.get("backoff_until"), "gap": bool(st.get("gap")),
                       "kept": int(st.get("kept") or 0), "dropped": int(st.get("dropped") or 0),
                       "recheck_due": checked is None or now - checked > RECHECK_AFTER})
    return {"items": out_items, "last_fetch": state.get("last_run"), "sources": health, "quiet_since": quiet_since}
