"""What Jev said beside Hank's router during the watch (vgrow/hank/jev.py).

Usage (on the Mac, in ~/vgrow-control): .venv/bin/python scripts/jev_watch_report.py [path/to/jev-watch.jsonl]
Without a path it reads STATE_DIR/hank/jev-watch.jsonl. Prints the summary as JSON; changes nothing.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vgrow.hank import jev  # noqa: E402


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else jev.watch_file()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if path.exists() else []
    print(json.dumps({"file": str(path), **jev.summarize(rows)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
