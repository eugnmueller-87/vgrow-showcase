"""Disposable localhost sensor fixture for protocol_screens.py. No hardware or model connection.

Run in a second terminal, then set VGROW_URL=http://127.0.0.1:18748/?key=protocol-demo-only-not-real.
The fixed key is only for this temporary test server; all state is removed when it exits.
"""
import asyncio
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from vgrow import config
from vgrow.alerts import Alerts
from vgrow.autopilot import Autopilot, AutopilotHub, text
from vgrow.budget import CommandBudget
from vgrow.demo import DemoBox
from vgrow.plant import PlantLog
from vgrow.server import make_app


class Box(DemoBox):
    def __init__(self):
        super().__init__()
        self.rh, self.pvpd = 67, .84
        self.sensor_at, self.shadow_at = time.time() - 30, time.time()
        self.dfan_step, self.cfan_step = 8, 10
        self.light["level"] = 100
        self.ports["usb2"] = False

    def snapshot(self):
        snap = super().snapshot()
        snap.update(sensors_age=time.time() - self.sensor_at, shadow_age=time.time() - self.shadow_at)
        snap["climate"].update(probe_humidity=self.rh, probe_vpd=self.pvpd, probe_temp=25)
        return snap

    async def set_dfan(self, step):
        await super().set_dfan(step)
        self.shadow_at = time.time()


async def main():
    with tempfile.TemporaryDirectory(prefix="vgrow-protocol-demo-") as folder:
        config.use_state_dir(Path(folder))
        box, log = Box(), PlantLog()
        now = datetime.now(ZoneInfo("Europe/Berlin"))
        log.set_stage("early_flower", (now - timedelta(days=4)).date().isoformat())
        pilot = Autopilot(box, plant_log=log, budget=CommandBudget())
        pilot.state["mode"] = "auto"
        for device in pilot.state["devices"].values():
            device["level"] = "auto"

        def control(snap, device, at):
            until = pilot._paused(device, at)
            return ("paused", text("ap.hold.paused", until={"at": until})) if until else ("ready", text("ap.hold.on_target"))
        pilot.control_info = control
        # Only this server observes the demo. No real regulation loop is running.
        hank = config.STATE_DIR / "hank"
        hank.mkdir(parents=True)
        row = {"time": (now - timedelta(hours=1)).isoformat(), "origin": "hank_accepted", "action": "dfan",
               "body": {"action": "dfan", "step": 9}, "proposal": "p-history", "ok": True}
        older = [{"time": (now - timedelta(days=1, minutes=minute)).isoformat(), "origin": "human", "action": "dfan",
                  "body": {"action": "dfan", "step": 4}, "proposal": None, "ok": True} for minute in range(55)]
        (hank / "actions.jsonl").write_text("\n".join(json.dumps(r) for r in [*older, row]) + "\n", encoding="utf-8")
        (hank / "ledger.json").write_text(json.dumps({"items": [{"metric": "probe_humidity",
            "baseline": {"value": 66.91, "dfan": 8}, "recommendation": {"proposal_id": "p-history"}}]}), encoding="utf-8")
        app = make_app(box, Alerts(), "protocol-demo-only-not-real", plant_log=log, autopilot=AutopilotHub([pilot]))

        async def reading(request):
            body = await request.json()
            box.rh, box.pvpd, box.sensor_at = body["rh"], body["vpd"], time.time()
            return web.json_response({"ok": True})
        app.router.add_post("/test/reading", reading)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 18748).start()
        print("Isolated protocol demo ready on 18748", flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
