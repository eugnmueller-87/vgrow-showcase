"""Read-only humidifier maintenance estimates. USB power is not proof of mist or tank level.

The selected device profile records manufacturer specifications separately from measured consumption. A known fill volume
followed by a refill from empty can calibrate ml per reported hour, without long recording gaps.
The 20 minute / 1 percentage point response check is a maintenance heuristic, not an
empty-tank detector. It never sends commands or changes any automation guard.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import time
from pathlib import Path
from typing import Any

from . import config
from .i18n import t

MAX_GAP = 180
HEARTBEAT_SECONDS = 600
RESPONSE_SECONDS = 20 * 60
RESPONSE_RISE = 1.0
RESPONSE_FALL_KPA = 0.1  # vapour lost in RESPONSE_SECONDS with nothing else changed: it has stopped. One exhaust step is about 0.03 (06.11.2026)
_LOGGER = logging.getLogger(__name__)
U075 = {
    "id": "vivosun_aerostream_u075", "name": "VIVOSUN AeroStream U075", "model_number": "VSE-ASU075",
    "capacity_ml": 750, "output_min_ml_h": 30, "output_max_ml_h": 50,
    "continuous_hours": 18, "intermittent_hours": 28, "low_water_shutoff": True,
    "source": "https://www.vivosun.com/support/guide/aerostream-usb-humidifier",
}
MIST_MODES = ("unknown", "continuous", "intermittent")


def number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


class HumidifierTank:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.STATE_DIR / "humidifier-tank.json"
        self.error = False
        self.state: dict[str, Any] = {
            "capacity_ml": 800.0, "rate_ml_h": None, "rate_source": None,
            "runtime_s": 0.0, "cycle_runtime_s": 0.0, "refilled_at": None, "cycle_start_ml": None,
            "cycle_complete": False, "refill_count": 0, "refills": [],
            "last": None, "response": None, "no_response": False,
            "button_start": False, "mist_confirmed_at": None,
            "model": None, "mist_mode": "unknown",
        }
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or not number(data.get("capacity_ml"))
                    or not 50 <= data["capacity_ml"] <= 10000
                    or not all(number(data.get(k)) and data[k] >= 0 for k in
                               ("runtime_s", "cycle_runtime_s", "refill_count"))
                    or data.get("rate_ml_h") is not None and (not number(data["rate_ml_h"])
                                                              or not 0.1 <= data["rate_ml_h"] <= 5000)
                    or data.get("refilled_at") is not None and not number(data["refilled_at"])
                    or data.get("cycle_start_ml") is not None and (not number(data["cycle_start_ml"])
                        or not 0 < data["cycle_start_ml"] <= data["capacity_ml"])
                    or data.get("last") is not None and (not isinstance(data["last"], dict)
                                                        or not number(data["last"].get("at")))
                    or data.get("response") is not None and (not isinstance(data["response"], dict)
                        or not all(number(data["response"].get(k)) for k in ("at", "rh", "temp", "vapour"))
                        or not isinstance(data["response"].get("context"), list))
                    or data.get("model") not in (None, U075["id"])
                    or data.get("model") == U075["id"] and data["capacity_ml"] != U075["capacity_ml"]
                    or data.get("mist_mode", "unknown") not in MIST_MODES
                    or not isinstance(data.get("refills"), list)
                    or any(not isinstance(r, dict) or not number(r.get("at"))
                           or type(r.get("was_empty")) is not bool for r in data["refills"])):
                raise ValueError("invalid tank record")
            self.state.update(data)
            if self.state["refilled_at"] is not None and self.state["cycle_start_ml"] is None:
                self.state["cycle_start_ml"] = self.state["capacity_ml"]  # older records confirmed a full tank
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.error = True
            _LOGGER.warning("Humidifier tank record unreadable; level and consumption are unknown")

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(self.path)
        self.state = data
        self.error = False

    def configure(self, capacity: Any, rate: Any, *, button_start: Any = None,
                  model: Any = None, mist_mode: Any = None) -> None:
        if not number(capacity) or not 50 <= capacity <= 10000:
            raise ValueError(t("tank.capacity_range"))
        if rate is not None and (not number(rate) or not 0.1 <= rate <= 5000):
            raise ValueError(t("tank.rate_range"))
        if button_start is not None and type(button_start) is not bool:
            raise ValueError(t("tank.button_kind"))
        if model not in (None, "custom", U075["id"]) or mist_mode is not None and mist_mode not in MIST_MODES:
            raise ValueError(t("tank.model_kind"))
        if model == U075["id"] and capacity != U075["capacity_ml"]:
            raise ValueError(t("tank.model_capacity"))
        data = copy.deepcopy(self.state)
        if button_start is not None and button_start != data["button_start"]:
            data.update(button_start=button_start, mist_confirmed_at=None, cycle_complete=False)
        mode_changed = mist_mode is not None and mist_mode != data["mist_mode"]
        keep_fill = number(data["cycle_start_ml"]) and 0 < data["cycle_start_ml"] <= capacity
        keep_learned = (not mode_changed and (capacity == data["capacity_ml"] or keep_fill)
                        and rate is None and data["rate_source"] == "refills")
        if capacity != data["capacity_ml"]:
            if not keep_fill:
                data.update(refilled_at=None, cycle_start_ml=None, cycle_runtime_s=0.0, cycle_complete=False)
            if data["model"] == U075["id"] and capacity != U075["capacity_ml"]:
                data["model"] = None
        if model is not None:
            data["model"] = None if model == "custom" else model
        if mode_changed:
            data.update(mist_mode=mist_mode, cycle_complete=False, response=None, no_response=False)
        data.update(capacity_ml=float(capacity), rate_ml_h=data["rate_ml_h"] if keep_learned else rate,
                    rate_source="refills" if keep_learned else "owner" if rate is not None else None)
        self._save(data)

    def refill(self, was_empty: Any, *, amount_ml: Any = None, now: float | None = None) -> None:
        if type(was_empty) is not bool:
            raise ValueError(t("tank.refill_kind"))
        now = time.time() if now is None else now
        data = copy.deepcopy(self.state)
        amount_ml = data["capacity_ml"] if amount_ml is None else amount_ml
        if not number(amount_ml) or not 0 < amount_ml <= data["capacity_ml"]:
            raise ValueError(t("tank.refill_range"))
        runtime = data["cycle_runtime_s"]
        calibrated = False
        if (was_empty and data["refilled_at"] is not None and data["cycle_complete"]
                and runtime >= 10 * 60 and data["rate_source"] != "owner"):
            rate = data["cycle_start_ml"] / (runtime / 3600)
            if 0.1 <= rate <= 5000:
                data.update(rate_ml_h=round(rate, 2), rate_source="refills")
                calibrated = True
        data["refills"] = (data["refills"] + [{"at": now, "was_empty": was_empty,
                                               "amount_ml": amount_ml, "runtime_s": runtime,
                                               "calibrated": calibrated}])[-50:]
        data.update(refill_count=data["refill_count"] + 1, refilled_at=now,
                    cycle_runtime_s=0.0, cycle_start_ml=amount_ml, cycle_complete=True,
                    response=None, no_response=False, last=None, mist_confirmed_at=None)
        self._save(data)

    def confirm_mist(self, *, now: float | None = None) -> None:
        data = copy.deepcopy(self.state)
        data.update(mist_confirmed_at=time.time() if now is None else now, response=None, no_response=False)
        self._save(data)

    def observe(self, snap: dict[str, Any], *, assigned: bool, off_at: float | None,
                now: float | None = None) -> None:
        now = time.time() if now is None else now
        try:
            self._observe(snap, assigned, off_at, now)
        except (OSError, ValueError, TypeError, KeyError):
            self.error = True
            self.state["cycle_complete"] = False
            self.state.update(response=None, no_response=False, last=None)
            _LOGGER.exception("Humidifier maintenance observation failed; automation continues")

    def _observe(self, snap: dict[str, Any], assigned: bool, off_at: float | None, now: float) -> None:
        data = copy.deepcopy(self.state)
        age = snap.get("shadow_age")
        sensor_age = snap.get("sensors_age")
        on = ((snap.get("ports") or {}).get("usb2") or {}).get("on")
        known = (assigned and bool(snap.get("online")) and bool(snap.get("mqtt"))
                 and number(age) and age >= 0 and type(on) is bool
                 and number(sensor_age) and 0 <= sensor_age <= HEARTBEAT_SECONDS)
        # An unchanged shadow need not be re-published. Carry its state only with a live MQTT
        # connection, device-online report and recent device telemetry, as USB history does.
        last = data["last"]
        elapsed = now - last["at"] if isinstance(last, dict) else None
        continuous = elapsed is not None and 0 < elapsed <= MAX_GAP and known and last.get("known")
        confirmed = data["mist_confirmed_at"]
        if (not known or not on or last and number(confirmed) and last["at"] >= confirmed
                and (not last.get("on") or elapsed is not None and elapsed > MAX_GAP)):
            data["mist_confirmed_at"] = None
        mist_ready = not data["button_start"] or data["mist_confirmed_at"] is not None
        if continuous and on and last.get("on"):
            data["runtime_s"] += elapsed
            if data["refilled_at"] is not None and mist_ready:
                start = max(last["at"], data["mist_confirmed_at"]) if data["button_start"] else last["at"]
                data["cycle_runtime_s"] += max(0, now - start)
        if (not known or elapsed is not None and (elapsed < 0 or elapsed > MAX_GAP)
                or last is None and number(data["refilled_at"]) and now - data["refilled_at"] > MAX_GAP
                or last is not None and not last.get("known") or known and on and not mist_ready):
            data["cycle_complete"] = False
        climate = snap.get("climate") or {}
        rh, temp = climate.get("probe_humidity"), climate.get("probe_temp")
        ready = (known and on and mist_ready and number(sensor_age) and 0 <= sensor_age <= MAX_GAP
                 and number(rh) and 0 <= rh <= 100 and number(temp) and -20 <= temp <= 60
                 and number(off_at) and rh < off_at)
        if not ready:
            data.update(response=None, no_response=False)
        else:
            # Temperature and ventilation changes confound relative-humidity response.
            context = [(snap.get("dfan") or {}).get("step"), (snap.get("light") or {}).get("level")]
            window = data["response"]
            vapour = 0.6108 * math.exp(17.27 * temp / (temp + 237.3)) * rh / 100
            start = {"at": now, "rh": rh, "temp": temp, "vapour": vapour, "context": context}
            rose = isinstance(window, dict) and bool(window.get("rose")) and continuous
            if (not isinstance(window, dict) or not continuous or window["context"] != context
                    or abs(window["temp"] - temp) > 2):
                data.update(response={**start, "rose": rose}, no_response=False)
            elif rh - window["rh"] >= RESPONSE_RISE or vapour - window["vapour"] >= 0.05:
                data.update(response={**start, "rose": True}, no_response=False)
            elif now - window["at"] >= RESPONSE_SECONDS:
                # It answered once since it came on: flat is the air taking what the mist gives, all day with a small
                # humidifier and an exhaust (06.11.2026: 54 % for an hour). Only vapour going away says it has stopped.
                if not rose or window["vapour"] - vapour >= RESPONSE_FALL_KPA:
                    data["no_response"] = True
                else:
                    data["response"] = {**start, "rose": True}
        data["last"] = {"at": now, "known": bool(known), "on": on if known else None}
        self._save(data)

    def view(self, *, implausible: bool = False, now: float | None = None) -> dict[str, Any]:
        data = self.state
        now = time.time() if now is None else now
        last = data["last"] or {}
        fresh = number(last.get("at")) and 0 <= now - last["at"] <= MAX_GAP
        needs_button = bool(data["button_start"] and fresh and last.get("known") and last.get("on")
                            and data["mist_confirmed_at"] is None)
        complete = bool(data["cycle_complete"] and fresh and last.get("known") and not self.error)
        rate = data["rate_ml_h"]
        usable = number(rate) and rate > 0 and data["refilled_at"] is not None and complete
        used = data["cycle_runtime_s"] / 3600 * rate if usable else None
        remaining = max(0, data["cycle_start_ml"] - used) if used is not None else None
        warning = ("start_button" if needs_button else "no_response" if implausible or fresh and data["no_response"] else
                   "estimated_low" if remaining is not None and remaining <= data["capacity_ml"] * 0.2 else None)
        return {"model": dict(U075) if data["model"] == U075["id"] else None, "mist_mode": data["mist_mode"],
                "capacity_ml": data["capacity_ml"], "rate_ml_h": rate, "rate_source": data["rate_source"],
                "runtime_s": round(data["runtime_s"]), "cycle_runtime_s": round(data["cycle_runtime_s"]),
                "refilled_at": data["refilled_at"], "starting_ml": data["cycle_start_ml"],
                "refill_count": data["refill_count"],
                "refills": list(reversed(data["refills"][-5:])), "complete": complete,
                "used_ml_estimate": round(used) if used is not None else None,
                "remaining_ml_estimate": round(remaining) if remaining is not None else None,
                "warning": warning, "response_minutes": RESPONSE_SECONDS // 60,
                "recording_error": self.error, "level_measured": False,
                "button_start": data["button_start"], "mist_confirmed_at": data["mist_confirmed_at"],
                "mist_confirmation_needed": needs_button}
