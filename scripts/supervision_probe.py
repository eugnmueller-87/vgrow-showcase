"""The Mac side of the PC's daily check on VGrow (Aufsicht, 15.11.2026): one look at the running server, as JSON.

The PC's liveness check (in the brain vault, not in this repo) runs it over ssh, once a day:
  ssh <mac-user>@<mac-host> 'cd ~/vgrow-control && .venv/bin/python scripts/supervision_probe.py'
It asks http://127.0.0.1:8742/api/supervision with the access key as the cookie vgrow_key, reads
`launchctl list de.vgrow.control` and `pmset -g batt`, and prints one line of JSON. Every outcome exits 0: the PC
decides what it means (only a wrong command line exits 2, argparse's own code).

The key stays on the Mac. It is read inside this process from STATE_DIR/access-key and sent in a header, so it is in no
argv (no curl on a command line, so no key in a process list), in no output and in no log. Only a loopback address is
asked; no redirect is followed (urllib would carry the cookie along to wherever it points) and no proxy is used (one
set in the environment would see it). Standard library only and no vgrow import: a deploy that broke the package must
not also break the check that reports it.

The JSON object (keys always present):
  probe        1, the version of this format
  ok           true: HTTP 200 with a JSON object
  error        null, or why not:
               no_key       no key file or an empty one; nothing was asked
               not_local    the address is not this Mac; nothing was asked, the key goes nowhere else
               refused      nothing listens on the port: the service is down, or launchd did not start it again
               timeout      the server took the connection but gave no answer in time: a wedged event loop
               forbidden    403, the key does not match
               unavailable  503, the running server has no supervisor
               http         any other status, a redirect included
               bad_json     200, but no JSON object
               unreachable  another network error, detail names its class
  status       the HTTP status, null without an answer
  reachable    true when the server answered at all, false when not, null when nothing was asked
  detail       the exception's class name for unreachable, else null; never its text
  supervision  the body of /api/supervision when ok, else null
  launchd      {"loaded", "pid", "last_exit"} from launchctl, null when launchctl could not run
  power        {"source", "percent", "line"} from pmset, null when pmset could not run
The launchctl and pmset formats are read as macOS documents them, not yet checked on the owner's Mac: a field whose line
does not parse stays null and nothing else changes.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

FORMAT = 1
BASE = "http://127.0.0.1:8742"  # python -m vgrow --port 8742, as the launchd service runs it (scripts/install_mac.sh)
PATH = "/api/supervision"
COOKIE = "vgrow_key"  # vgrow/server.py COOKIE
LABEL = "de.vgrow.control"  # scripts/install_mac.sh LABEL
LOCAL = ("127.0.0.1", "localhost", "::1")
TIMEOUT_S = 15.0  # the HTTP answer: a healthy server answers within a second, a wedged one never
RUN_TIMEOUT_S = 5.0  # launchctl and pmset, each
STATUS_ERRORS = {403: "forbidden", 503: "unavailable"}

Runner = Callable[[Sequence[str], float], tuple[int, str] | None]


def state_dir() -> Path:
    """vgrow/config.py STATE_DIR, worked out the same way without importing it."""
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "vgrow-control"
    return Path(os.environ.get("USERPROFILE") or Path.home()) / ".vgrow-control"


def read_key(path: Path) -> str | None:
    try:
        key = path.read_text(encoding="utf-8").strip()  # the server strips it the same way (config.access_key)
    except (OSError, UnicodeDecodeError):
        return None
    return key or None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer (error http), never followed: the cookie would travel along to wherever it points."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def run_command(argv: Sequence[str], timeout: float) -> tuple[int, str] | None:
    """(exit code, stdout) of a fixed command, None when it cannot run (not a Mac, too slow). No argv holds the key."""
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.returncode, done.stdout or ""


def _number(pattern: str, text: str) -> int | None:
    found = re.search(pattern, text)
    return int(found.group(1)) if found else None


def launchd(run: Runner) -> dict[str, Any] | None:
    """The service as launchd sees it: loaded, its pid (none while it is not running) and its last exit code."""
    got = run(["launchctl", "list", LABEL], RUN_TIMEOUT_S)
    if got is None:
        return None
    code, out = got
    if code != 0:  # launchctl knows no such label: the service is not loaded
        return {"loaded": False, "pid": None, "last_exit": None}
    return {"loaded": True, "pid": _number(r'"PID"\s*=\s*(\d+)\s*;', out),
            "last_exit": _number(r'"LastExitStatus"\s*=\s*(-?\d+)\s*;', out)}


def power(run: Runner) -> dict[str, Any] | None:
    """Mains or battery: on battery the Mac may sleep and the server with it (only pmset -c is set for mains)."""
    got = run(["pmset", "-g", "batt"], RUN_TIMEOUT_S)
    if got is None or got[0] != 0:
        return None
    out = got[1]
    found = re.search(r"drawing from '([^']+)'", out)
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return {"source": found.group(1) if found else None, "percent": _number(r"(\d+)%", out),
            "line": lines[0] if lines else None}


def _asked(base: str, path: str, key: str, timeout: float, lang: str) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(base.rstrip("/") + path, headers={
        "Cookie": f"{COOKIE}={key}", "Accept": "application/json", "X-Lang": lang})
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as err:
        err.close()
        return {"reachable": True, "status": err.code, "error": STATUS_ERRORS.get(err.code, "http")}
    except urllib.error.URLError as err:  # before an answer: connecting
        if isinstance(err.reason, ConnectionRefusedError):
            return {"reachable": False, "error": "refused"}
        if isinstance(err.reason, TimeoutError):
            return {"reachable": False, "error": "timeout"}
        return {"reachable": False, "error": "unreachable", "detail": type(err.reason).__name__}
    except TimeoutError:  # connected, but the answer did not come
        return {"reachable": False, "error": "timeout"}
    except (OSError, http.client.HTTPException) as err:  # reset, closed or cut off: http.client raises them unwrapped
        return {"reachable": False, "error": "unreachable", "detail": type(err).__name__}
    if status != 200:
        return {"reachable": True, "status": status, "error": "http"}
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        return {"reachable": True, "status": status, "error": "bad_json"}
    return {"reachable": True, "status": status, "ok": True, "supervision": body}


def probe(base: str = BASE, key_file: Path | None = None, timeout: float = TIMEOUT_S, run: Runner | None = run_command,
          *, path: str = PATH, lang: str = "de") -> dict[str, Any]:
    """One look at the server. run: the runner for launchctl and pmset (None: neither is asked)."""
    result: dict[str, Any] = {"probe": FORMAT, "ok": False, "error": None, "status": None, "reachable": None,
                              "detail": None, "supervision": None, "launchd": None, "power": None}
    key = read_key(key_file or state_dir() / "access-key")
    if key is None:
        result["error"] = "no_key"
    elif urllib.parse.urlsplit(base).hostname not in LOCAL:
        result["error"] = "not_local"
    else:
        result.update(_asked(base, path, key, timeout, lang))
    if run is not None:
        result["launchd"], result["power"] = launchd(run), power(run)
    return result


def main(argv: Sequence[str] | None = None, run: Runner | None = run_command) -> int:
    parser = argparse.ArgumentParser(description="One look at the VGrow server on this Mac, as one line of JSON.")
    parser.add_argument("--base", default=BASE, help="the server, a loopback address only (default %(default)s)")
    parser.add_argument("--key-file", type=Path, default=None, help="default: STATE_DIR/access-key")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S, help="seconds for the answer")
    parser.add_argument("--lang", choices=("de", "en"), default="de", help="language of the texts in the answer")
    args = parser.parse_args(argv)
    result = probe(args.base, args.key_file, args.timeout, run, lang=args.lang)
    text = json.dumps(result, separators=(",", ":"))  # ASCII only: the PC reads it through ssh in any code page
    key = read_key(args.key_file or state_dir() / "access-key")
    if key:  # the second lock: the server's own test keeps the key out of the body, this keeps it off the PC
        text = text.replace(key, "<redacted>")
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
