"""python -m vgrow [--demo] [--port 8742] [--check]"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from . import config
from .box import Box
from .demo import DemoBox
from .server import serve


async def check() -> None:
    """Read-only smoke test: log in, find the box, print one snapshot. Sends no command. It runs next to the server,
    so it keeps its own login and MQTT identifiers: the server's stay the server's (shared ones would make AWS IoT
    drop one of the two connections)."""
    email, password = config.credentials()
    box = Box(email, password, remember=False)
    await box.start()
    try:
        await asyncio.sleep(4)  # give the shadow a moment to arrive
        snap = box.snapshot()
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        print("\nreported keys:", sorted(box.reported))
        print("sensor keys with values:", sorted(k for k, v in box.sensors.items() if v is not None))
        if box.other_devices:
            print("other devices in the account:", box.other_devices)
    finally:
        await box.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="vgrow")
    parser.add_argument("--demo", action="store_true",
                        help="made-up data, placeholder camera and demo assessment, own state folder, no Vivosun login")
    parser.add_argument("--check", action="store_true", help="read-only connection test, then exit")
    parser.add_argument("--port", type=int, default=8742)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    if sys.stdout is None:  # started windowless (pythonw, autostart): write a log file instead
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(config.STATE_DIR / "server.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    logging.basicConfig(level=logging.INFO, format=config.LOG_FORMAT)
    for handler in logging.getLogger().handlers:  # requests carry /?key=...: never into server.log, tracebacks too
        config.protect_handler(handler)
    if args.check:
        asyncio.run(check())
        return
    box = DemoBox() if args.demo else Box(*config.credentials())
    try:
        asyncio.run(serve(box, port=args.port, host=args.host))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
