"""Read-only light reports, saved timer settings and local observations."""
from __future__ import annotations

import asyncio
import math
import time

from aiohttp import web

from . import workspace

WINDOW_SECONDS = 86400
OBSERVATION_SECONDS = 90  # at most one normal poll interval; gaps are unknown, never extended overnight
READING_HOLD = 180  # seconds a light reading stands for the rows after it: one stored row in three carries none
DAY_REFRESH = 600  # the alarms read the store this seldom: the day moves slowly and a poll comes every minute


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def observations(rows, start, end):
    points = {}
    for row in rows:
        at, level = row.get('time'), row.get('light')
        if number(at) and start <= at < end:
            points[at] = level if number(level) and 0 <= level <= 100 else None
    points = sorted(points.items())
    segments = []

    def append(a, b, level):
        if b <= a:
            return
        if segments and segments[-1]['end'] == a and segments[-1]['level'] == level:
            segments[-1]['end'] = b
        else:
            segments.append({'start': a, 'end': b, 'level': level})

    cursor = start
    valid = 0
    for index, (at, level) in enumerate(points):
        append(cursor, at, None)
        stop = min(end, at + OBSERVATION_SECONDS, points[index + 1][0] if index + 1 < len(points) else end)
        append(at, stop, level)
        cursor = stop
        valid += level is not None
    append(cursor, end, None)
    covered = sum(row['end'] - row['start'] for row in segments if row['level'] is not None)
    return {'start': start, 'end': end, 'segments': segments, 'readings': valid,
            'coverage_percent': round(covered / (end - start) * 100, 1) if end > start else 0,
            'max_observation_seconds': OBSERVATION_SECONDS}


def _stands(rows, start, end):
    """Each valid light reading in [start, end) and the seconds it stands for: it covers the rows without one
    (READING_HOLD), never a longer gap, so what nobody measured counts for nothing."""
    points = {}
    for row in rows:
        at, level = row.get('time'), row.get('light')
        if number(at) and start <= at < end and number(level) and 0 <= level <= 100:
            points[at] = level
    points = sorted(points.items())
    for index, (at, level) in enumerate(points):
        yield level, min(end, at + READING_HOLD, points[index + 1][0] if index + 1 < len(points) else end) - at


def balance(rows, start, end):
    """Hours of light and of dark in [start, end), from the stored readings."""
    lit = dark = 0.0
    for level, seconds in _stands(rows, start, end):
        if level > 0:
            lit += seconds
        else:
            dark += seconds
    return {'lit_hours': round(lit / 3600, 1), 'dark_hours': round(dark / 3600, 1),
            'measured_hours': round((lit + dark) / 3600, 1)}


def lamp_hours(rows, start, end):
    """Hours at full brightness in [start, end): each reading's brightness over the time it stands for."""
    return sum(level / 100 * seconds for level, seconds in _stands(rows, start, end)) / 3600


class LightDay:
    """The last 24 measured hours for the alarms (alerts.py: dark_period, short_day). Off the event loop, at most
    every DAY_REFRESH seconds. A failed read raises to the caller and keeps nothing: no alarm stands on a guess."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.value, self.read_at = None, None

    async def read(self):
        now = self.clock()
        if self.read_at is None or now - self.read_at >= DAY_REFRESH:
            start = now - WINDOW_SECONDS
            rows = await asyncio.to_thread(self.store.rows, start, now)
            self.value, self.read_at = balance(rows, start, now), now
        return self.value


def schedule(snapshot):
    light = snapshot.get('light') or {}
    known = light.get('in_plan_known', isinstance(light.get('in_plan'), bool))
    flag = light.get('in_plan')
    state = 'unknown' if not known or type(flag) is not bool else 'active' if flag else 'inactive'
    # The snapshot exposes a plan flag, not the plan's on/off timetable. Never infer clock times or 24/0 from it.
    return {'state': state, 'timings_available': False, 'on_time': None, 'off_time': None,
            'hours_on': None, 'next_switch': None, 'source': 'controller_plan_flag'}


def register(app, *, box, measurements=None, controller=None):
    async def read(request):
        if request.headers.get('X-Workspace-Box'):
            try:
                await asyncio.to_thread(workspace.validate_context, request.headers['X-Workspace-Box'],
                                        request.headers.get('X-Workspace-Plant'))
            except workspace.Invalid as err:
                return web.json_response({'error': str(err)}, status=409)
        snapshot = box.snapshot()
        end = time.time()
        start = end - WINDOW_SECONDS
        try:
            rows = await asyncio.to_thread(measurements.rows, start, end) if measurements else []
        except Exception:
            return web.json_response({'error': 'light_history_unavailable'}, status=503,
                                     headers={'Cache-Control': 'no-store'})
        return web.json_response({'device': snapshot.get('device'), 'demo': bool(snapshot.get('demo')),
                                  'light': snapshot.get('light'), 'online': snapshot.get('online'),
                                  'mqtt': snapshot.get('mqtt'), 'shadow_age': snapshot.get('shadow_age'),
                                  'schedule': schedule(snapshot), 'light_schedule': controller.view(snapshot) if controller else None,
                                  'store': measurements is not None,
                                  'history': observations(rows, start, end), 'balance': balance(rows, start, end)},
                                 headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/light', read)
