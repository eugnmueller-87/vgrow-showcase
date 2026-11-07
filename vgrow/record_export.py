"""Download complete observation records and shared sensor rows without calling a model or any actuator."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from aiohttp import web

from . import workspace
from .measurements import MeasurementStore


def build_export(data, measurements):
    """Bound memory to one day of sensor rows. Record JSON includes all observations, beyond UI limits."""
    descriptor, name = tempfile.mkstemp(prefix='vgrow-record-', suffix='.zip')
    os.close(descriptor)
    path = Path(name)
    manifest = {'version': 1, 'exported_at': time.time(), 'record': data['record']['id'],
                'coverage': data['coverage'], 'intervals': [],
                'media': 'Camera files and AI results remain in box storage; they are not attributed to individual records.'}
    try:
        with ZipFile(path, 'w', compression=ZIP_DEFLATED) as archive:
            archive.writestr('record.json', json.dumps(data, ensure_ascii=False, indent=2))
            for index, place in enumerate(data['box_intervals']):
                start = place['since']
                end = min(place['until'] if place['until'] is not None else data['captured_at'], data['captured_at'])
                info = {**place, 'until': end, 'rows': 0, 'first_reading': None, 'last_reading': None}
                manifest['intervals'].append(info)
                if not measurements or not place.get('device') or place['box'] != workspace.NATIVE_BOX:
                    info['available'] = False
                    continue
                source = MeasurementStore(path=measurements.file_for(place['device']), tz=measurements.tz)
                info['available'] = True
                while start < end:
                    stop = min(start + 86400, end)
                    rows = [row for row in source.rows(start, stop) if start <= row['time'] < stop]
                    if rows:
                        archive.writestr(f'shared-box/{index + 1}/{int(start)}.json', json.dumps(rows, ensure_ascii=False))
                        info['rows'] += len(rows)
                        info['first_reading'] = info['first_reading'] if info['first_reading'] is not None else rows[0]['time']
                        info['last_reading'] = rows[-1]['time']
                    start = stop
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def register(app, *, ensure, measurements=None):
    async def handle(request):
        path = None
        try:
            await ensure()
            data = await asyncio.to_thread(workspace.record, request.match_info['id'])
            if not request.path.endswith('/export'):
                return web.json_response(data, headers={'Cache-Control': 'no-store'})
            path = await asyncio.to_thread(build_export, data, measurements)
            ident = data['record']['id']
            # IDs are generated internally; do not put user-provided plant names in response headers.
            response = web.StreamResponse(headers={'Content-Type': 'application/zip', 'Cache-Control': 'no-store',
                                                   'Content-Disposition': f'attachment; filename="record-{ident}.zip"'})
            await response.prepare(request)
            with path.open('rb') as file:
                while chunk := await asyncio.to_thread(file.read, 65536):
                    await response.write(chunk)
            await response.write_eof()
            return response
        except workspace.Invalid as err:
            return web.json_response({'error': str(err)}, status=404)
        except (OSError, sqlite3.Error, ValueError):
            return web.json_response({'error': 'workspace_storage'}, status=503)
        finally:
            if path:
                path.unlink(missing_ok=True)

    app.router.add_get('/api/workspace/records/{id}', handle)
    app.router.add_get('/api/workspace/records/{id}/export', handle)
