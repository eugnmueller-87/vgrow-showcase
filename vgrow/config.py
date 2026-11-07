"""Where secrets and local state live. Nothing secret is ever inside the repo."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import tempfile
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

HOME = Path(os.environ.get("USERPROFILE") or Path.home())
ENV_FILE = HOME / ".vgrow.env"  # VIVOSUN_EMAIL, VIVOSUN_PASSWORD, optional VGROW_NTFY_TOPIC, VGROW_NEWS_PRIVATE
BRAIN_NTFY_FILE = HOME / ".brain-ntfy.env"  # NTFY_SERVER, NTFY_TOPIC (optional, shared with other tools)
# Windows: %LOCALAPPDATA%\vgrow-control. Elsewhere a dot folder, because the repo itself is
# usually cloned to ~/vgrow-control and the access key must never land inside it.
STATE_DIR = (
    Path(os.environ["LOCALAPPDATA"]) / "vgrow-control" if os.environ.get("LOCALAPPDATA") else HOME / ".vgrow-control"
)
REPO = Path(__file__).resolve().parent.parent
ALERTS_FILE = REPO / "alerts.json"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def credentials() -> tuple[str, str]:
    env = {**read_env(ENV_FILE), **{k: v for k, v in os.environ.items() if k.startswith("VIVOSUN_")}}
    email, password = env.get("VIVOSUN_EMAIL", ""), env.get("VIVOSUN_PASSWORD", "")
    if not email or not password or "@" not in email:
        raise SystemExit(
            f"Vivosun login missing. Put VIVOSUN_EMAIL and VIVOSUN_PASSWORD into {ENV_FILE}"
        )
    return email, password


def ntfy() -> tuple[str, str] | None:
    """Grow alerts use VGROW_NTFY_TOPIC if set, otherwise the shared ntfy topic."""
    brain = read_env(BRAIN_NTFY_FILE)
    own = read_env(ENV_FILE)
    server = own.get("NTFY_SERVER") or brain.get("NTFY_SERVER")
    topic = own.get("VGROW_NTFY_TOPIC") or brain.get("NTFY_TOPIC")
    return (server.rstrip("/"), topic) if server and topic else None


ACCESS_KEY_MIN = 16  # shorter, empty or missing: a new key. An empty key would let every request in.
KEY_IN_TEXT = re.compile(r"(key=)[^\s&\"'<>]+")


def atomic_write(path: Path, text: str) -> None:
    """Write a file in one step: a temp file next to it, then os.replace, so a crash never leaves half a file.
    On POSIX the file is owner-only (mkstemp creates it 0600); Windows keeps the folder's permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            if os.name == "posix":
                os.fchmod(f.fileno(), 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def access_key_path() -> Path:
    return STATE_DIR / "access-key"


def access_key() -> str:
    """Random key the phone presents once; kept outside the repo, owner-only on the Mac.

    Never empty or short: a missing file, an empty one or one under ACCESS_KEY_MIN characters gets a fresh key,
    written atomically. A key that is long enough stays, so the phone's link keeps working.
    """
    path = access_key_path()
    try:
        key = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        key = ""
    except (OSError, UnicodeDecodeError) as err:
        _LOGGER.warning("%s unreadable (%s): writing a new key", path, err)
        key = ""
    if len(key) >= ACCESS_KEY_MIN:
        return key
    if path.exists():
        _LOGGER.warning("%s is empty or shorter than %d characters: writing a new key", path, ACCESS_KEY_MIN)
    key = secrets.token_urlsafe(18)  # 24 characters
    atomic_write(path, key)
    return key


def redact_key(text: str) -> str:
    """Every key=<value> becomes key=<redacted>, like the step in scripts/install_mac.sh does for old log lines."""
    return KEY_IN_TEXT.sub(r"\1<redacted>", text)


class RedactKey(logging.Filter):
    """For the log handlers: aiohttp's access log writes the request line, and the phone's first request is
    /?key=..., so without this every new device would leave the key in server.log."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a record that does not format is not ours to fix here
            return True
        clean = redact_key(message)
        if clean != message:
            record.msg, record.args = clean, ()
        return True


class RedactingFormatter(logging.Formatter):
    """The last step before a line is written: key=<value> is blacked out in the whole formatted text, the traceback
    and the stack included. RedactKey alone sees only the message; aiohttp's "Error handling request" for a malformed
    request line (GET /?key=... HTTP/9.x) carries the raw line in its traceback, which is formatted after the filter
    (review of 22.10.2026). The cached traceback text is cleaned too, for any handler that reuses it."""

    def format(self, record: logging.LogRecord) -> str:
        text = redact_key(super().format(record))
        if record.exc_text:
            record.exc_text = redact_key(record.exc_text)
        return text


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def protect_handler(handler: logging.Handler, fmt: str = LOG_FORMAT) -> logging.Handler:
    """What every handler of the server's root logger gets: the filter for the message, the formatter for the rest."""
    handler.addFilter(RedactKey())
    handler.setFormatter(RedactingFormatter(fmt))
    return handler


# The news watcher's privacy switch (vgrow/news.py reads it through news.is_private()). Off until serve() reads
# ~/.vgrow.env: "private" sources are fetched and shown only on an install whose owner said so on purpose.
NEWS_PRIVATE = False


def news_private() -> bool:
    """VGROW_NEWS_PRIVATE in ~/.vgrow.env, and only "1" counts: "true", "yes", "on" or anything else is no.
    A public copy has no such line and never fetches or shows a "private" source (docs/news-quellen.md, 6.1).
    An unreadable file is no, too: the switch fails closed."""
    try:
        return read_env(ENV_FILE).get("VGROW_NEWS_PRIVATE", "") == "1"
    except (OSError, UnicodeDecodeError) as err:
        _LOGGER.warning("%s unreadable (%s): news stay public", ENV_FILE, err)
        return False


def growcam_ip() -> str | None:
    """Optional fixed camera address; without it the server scans the local network. The camera needs its own power
    supply since 23.10.2026 (no USB port of the box), so this address is the only link to it."""
    return read_env(ENV_FILE).get("GROWCAM_IP") or None


def ecowitt_ip() -> str | None:
    """The Ecowitt gateway's address on the home network (vgrow/soil.py); without it there is no soil tile."""
    return read_env(ENV_FILE).get("ECOWITT_IP") or None


RECORDINGS_DIR = STATE_DIR / "recordings"
PORTS_FILE = REPO / "ports.json"


def use_state_dir(path: Path) -> None:
    """Point the key, logs, plant log, automation and settings at another folder (the demo uses its own)."""
    global STATE_DIR, RECORDINGS_DIR
    STATE_DIR = path
    RECORDINGS_DIR = path / "recordings"


_ports_good: dict[str, dict[str, object]] | None = None  # the last ports.json that parsed
_ports_problem: str | None = None  # the last reported parse problem, so the log says it once


def port_rules() -> dict[str, dict[str, object]] | None:
    """Labels and locks per USB port. No file: no labels, nothing locked.

    Fails closed: a file that exists but does not parse (one trailing comma is enough) gives the last
    version that did, or None if there was none. None means every port-off is refused, so a broken
    file can never unlock a port that ports.json locks always on (a pump excepted: box.check_port_lock).
    A port that is always_on and a pump at once loses its always_on, logged as an error, and the rest of the file
    stays in force: a pump on a port that never switches off waters without end (23.10.2026).
    """
    global _ports_good, _ports_problem
    try:
        data = json.loads(PORTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("the top level is not an object")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        problem = f"{type(err).__name__}: {err}"
        if problem != _ports_problem:
            _ports_problem = problem
            _LOGGER.error("%s unreadable (%s); %s", PORTS_FILE.name, problem,
                          "using the last good version" if _ports_good is not None else "no port is switched off")
        return _ports_good
    good = {k: dict(v) for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
    both = sorted(k for k, v in good.items() if v.get("always_on") and v.get("device") == "pump")
    for name in both:
        del good[name]["always_on"]
    problem = f"always_on together with device pump on {', '.join(both)}: always_on dropped there" if both else None
    if problem and problem != _ports_problem:
        _LOGGER.error("%s: %s", PORTS_FILE.name, problem)
    _ports_problem = problem
    _ports_good = good
    return _ports_good


def pump_ports_raw() -> set[str]:
    """The ports ports.json names a pump on, read from the raw text without any other check: for the moment
    port_rules() is None (the file does not parse and no good version is in memory), so a pump's OFF still goes out
    (box.check_port_lock). Plain JSON first; if that fails, a tolerant scan of each port's own object, so a trailing
    comma elsewhere cannot hide the pump. Unreadable: no names (the owner's naming on the page still counts)."""
    try:
        text = PORTS_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {k for k, v in data.items() if isinstance(v, dict) and v.get("device") == "pump"}
    except ValueError:
        pass
    return set(re.findall(r'"([A-Za-z0-9_]+)"\s*:\s*\{[^{}]*?"device"\s*:\s*"pump"', text))


def alert_rules() -> dict[str, object]:
    return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
