"""Read only: what does an accessory in the account report? Prints its shadow once, then every change.

On the Mac, next to the running server, with its own login and MQTT identifiers (the same reasoning as --check):
  PYTHONPATH=. .venv/bin/python scripts/capture_accessory.py --match E25 --seconds 600

The one thing it publishes is the empty read request to shadow/get, never an update: nothing can switch from here.
Values go through the capture redaction (vgrow/vivosun/redaction.py). Commands the app sends appear as "desired".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

from vgrow import config
from vgrow.box import Box
from vgrow.vivosun import shadow as sh
from vgrow.vivosun.const import TOPIC_SHADOW_GET
from vgrow.vivosun.models import client_model_token
from vgrow.vivosun.mqtt_client import MQTTClient
from vgrow.vivosun.redaction import sanitize_mapping_for_capture


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: value}


def stamp() -> str:
    return time.strftime("%H:%M:%S")


class Watcher:
    def __init__(self) -> None:
        self.reported: dict[str, Any] = {}
        self.first = True

    def on_message(self, topic: str, payload: bytes, qos: int) -> None:
        try:
            document = json.loads(payload)
        except json.JSONDecodeError:
            print(stamp(), "not json on", topic.rsplit("/", 2)[-1], len(payload), "bytes", flush=True)
            return
        if not isinstance(document, dict):
            return
        reported = sh.extract_shadow_reported(document)
        if reported:
            now = flatten(sanitize_mapping_for_capture(reported))
            if self.first:
                print(stamp(), "first shadow:", json.dumps(now, ensure_ascii=False, sort_keys=True), flush=True)
                self.first = False
            else:
                for key in sorted(now):
                    if key in self.reported and self.reported[key] != now[key]:
                        print(stamp(), f"changed {key}: {self.reported[key]} -> {now[key]}", flush=True)
                    elif key not in self.reported:
                        print(stamp(), f"new {key}: {now[key]}", flush=True)
            self.reported.update(now)
        state = document.get("state")
        if isinstance(state, Mapping) and isinstance(state.get("desired"), Mapping):
            desired = sanitize_mapping_for_capture(state["desired"])
            print(stamp(), "desired:", json.dumps(desired, ensure_ascii=False, sort_keys=True), flush=True)


async def capture(match: str, seconds: float) -> int:
    email, password = config.credentials()
    box = Box(email, password, remember=False)
    await box.start()
    client: MQTTClient | None = None
    try:
        devices = await box._api.get_devices(box._tokens)
        wanted = [d for d in devices if match.lower() in d.name.lower()]
        if not wanted:
            print("no device matches", match, "in", [d.name for d in devices], flush=True)
            return 1
        device = wanted[0]
        print(stamp(), json.dumps({"name": device.name, "model": client_model_token(device.client_id),
                                   "type": device.device_type, "online": device.online}), flush=True)
        url = box._aws.sigv4_sign_mqtt_url(endpoint=box._identity.aws_host, region=box._identity.aws_region,
                                           credentials=box._creds)
        client = MQTTClient(websocket_url=url, thing=device.client_id, topic_prefix=device.topic_prefix,
                            client_id=f"vgrow-capture-{uuid.uuid4().hex[:12]}", label="capture")
        await client.connect()
        watcher = Watcher()
        client.add_message_callback(watcher.on_message)
        await client.publish(TOPIC_SHADOW_GET.format(thing=device.client_id), b"{}")
        await asyncio.sleep(seconds)
        print(stamp(), "end of capture", flush=True)
        return 0
    finally:
        if client is not None:
            await client.disconnect()
        await box.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", default="E25", help="part of the device name in the VIVOSUN account")
    parser.add_argument("--seconds", type=float, default=600)
    args = parser.parse_args()
    return asyncio.run(capture(args.match, args.seconds))


if __name__ == "__main__":
    raise SystemExit(main())
