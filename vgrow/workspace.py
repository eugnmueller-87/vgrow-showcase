"""General box and plant inventory. Records never issue device commands or select growing targets; only the owner's
own choice of a plant does, and that lives in vgrow/plant_run.py."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time as day_time, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web

from . import config, harvest, plant_run
from .i18n import current_lang

NATIVE_BOX = 'box-1'
NATIVE_PLANT = 'existing-plant'
ORIGINS = ('', 'seed', 'cutting', 'existing', 'other')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boxes(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
 capacity INTEGER, notes TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS plants(id TEXT PRIMARY KEY, box TEXT NOT NULL REFERENCES boxes(id),
 name TEXT NOT NULL, variety TEXT NOT NULL, phase TEXT NOT NULL, started TEXT, approximate INTEGER NOT NULL,
 notes TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS heights(plant TEXT REFERENCES plants(id), day TEXT NOT NULL, cm REAL NOT NULL,
 PRIMARY KEY(plant,day));
CREATE TABLE IF NOT EXISTS height_metadata(plant TEXT NOT NULL, day TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(plant,day), FOREIGN KEY(plant,day) REFERENCES heights(plant,day) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS journal(id TEXT PRIMARY KEY, plant TEXT NOT NULL REFERENCES plants(id),
 day TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS phase_dates(plant TEXT PRIMARY KEY REFERENCES plants(id), observed_since TEXT, recorded TEXT);
CREATE TABLE IF NOT EXISTS record_lifecycle(plant TEXT PRIMARY KEY REFERENCES plants(id), origin TEXT NOT NULL,
 ended TEXT, summary TEXT NOT NULL DEFAULT '', snapshot TEXT);
CREATE TABLE IF NOT EXISTS record_events(id INTEGER PRIMARY KEY, plant TEXT NOT NULL REFERENCES plants(id),
 at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS record_places(id INTEGER PRIMARY KEY, plant TEXT NOT NULL REFERENCES plants(id),
 box TEXT NOT NULL REFERENCES boxes(id), since REAL NOT NULL, until REAL, device TEXT);
'''
SCHEMA += harvest.SCHEMA  # drying, curing, ready (vgrow/harvest.py)
SCHEMA += '''
CREATE TABLE IF NOT EXISTS plant_variety(plant TEXT PRIMARY KEY REFERENCES plants(id), source TEXT, key TEXT, flowering TEXT);
'''  # the pick from the wiki's varieties (vgrow/plant_run.py)


class Invalid(ValueError):
    pass


def today() -> date:
    from datetime import datetime
    return datetime.now(ZoneInfo('Europe/Berlin')).date()


@contextmanager
def database(write=False):
    path = config.STATE_DIR / 'workspace' / 'inventory.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        if write:
            db.execute('BEGIN IMMEDIATE')
        with db:
            yield db
    finally:
        db.close()


def text(value, maximum=120, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise Invalid('workspace_invalid')
    return value.strip()


def day(value, *, required=False):
    if value in ('', None) and not required:
        return None
    if not isinstance(value, str):
        raise Invalid('workspace_date')
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed > today() or parsed.year < 1900:
            raise ValueError
        return value
    except ValueError as err:
        raise Invalid('workspace_date') from err


def capacity(value):
    if value in ('', None):
        return None
    if type(value) is not int or not 1 <= value <= 1000:
        raise Invalid('workspace_capacity')
    return value


def load(db, table, ident, *, active=True):
    if table not in ('boxes', 'plants') or not isinstance(ident, str):
        raise Invalid('workspace_missing')
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (ident,)).fetchone()
    if row is None or (active and row['archived']):
        raise Invalid('workspace_missing')
    if active and table == 'plants':
        lifecycle = db.execute('SELECT ended FROM record_lifecycle WHERE plant=?', (ident,)).fetchone()
        if lifecycle and lifecycle['ended']:
            raise Invalid('workspace_completed')
    return row


def event(db, ident, kind, data):
    db.execute('INSERT INTO record_events(plant,at,kind,data) VALUES(?,?,?,?)',
               (ident, time.time(), kind, json.dumps(data, ensure_ascii=False)))


def initialize_records(db, device=None):
    if db.execute("SELECT 1 FROM meta WHERE key='records_initialized'").fetchone():
        if device:
            changed = db.execute('UPDATE record_places SET device=?,since=? WHERE box=? AND device IS NULL AND until IS NULL',
                                 (device, time.time(), NATIVE_BOX)).rowcount
            if changed:
                db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        return
    for row in db.execute('SELECT * FROM plants').fetchall():
        db.execute('INSERT OR IGNORE INTO record_lifecycle(plant,origin) VALUES(?,?)', (row['id'], ''))
        db.execute('INSERT INTO record_places(plant,box,since,device) VALUES(?,?,?,?)',
                   (row['id'], row['box'], time.time(), device if row['box'] == NATIVE_BOX else None))
        event(db, row['id'], 'imported', {'note': 'History before record tracking was not reconstructed.',
                                        'baseline': record_data(db, row['id'])['record']})
    db.execute("INSERT INTO meta VALUES('records_initialized','1')")
    db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def initialize(name, profile, log, device=None):
    """Copy existing observations once. Original profile/log remain intact and keep their existing consumers."""
    from .plant import current_stage, effective_start, with_owner_start
    with database(write=True) as db:
        if db.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone():
            if not db.execute("SELECT 1 FROM meta WHERE key='phase_dates_initialized'").fetchone():
                # Old entries did not record an observed start. A dated manual entry is not evidence of onset.
                existing = db.execute('SELECT phase FROM plants WHERE id=?', (NATIVE_PLANT,)).fetchone()
                stage = current_stage(with_owner_start(profile, log), log) if profile else None
                record_day = stage_entry_day(stage)
                labels = json.loads(existing['phase']).values() if existing else []
                if record_day and any(label in labels for label in (stage.get('label'), stage.get('label_en')) if label):
                    db.execute('INSERT OR IGNORE INTO phase_dates VALUES(?,?,?)', (NATIVE_PLANT, None, record_day))
                    db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
                db.execute("INSERT INTO meta VALUES('phase_dates_initialized','1')")
            initialize_records(db, device)
            return
        db.execute('INSERT INTO boxes VALUES(?,?,?,?,?,?,?)', (NATIVE_BOX, str(name or 'Box 1')[:120], 'VGrow', None, '', 0, time.time()))
        if profile:
            profile = with_owner_start(profile, log)
            plant = profile.get('plant') or {}
            stage = current_stage(profile, log) or {}
            started, origin = effective_start(profile, log)
            approximate = bool(started and ((origin == 'plan' and plant.get('start_approx'))
                                           or plant.get('start_from') == 'age_weeks'))
            phase = json.dumps({'de': stage.get('label') or stage.get('key') or '',
                                'en': stage.get('label_en') or stage.get('label') or stage.get('key') or ''}, ensure_ascii=False)
            label = str(plant.get('strain') or 'Plant 1')[:120]
            db.execute('INSERT INTO plants VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (NATIVE_PLANT, NATIVE_BOX, label, label, phase, started.isoformat() if started else None,
                        int(approximate), '', 0, time.time()))
            db.execute('INSERT INTO phase_dates VALUES(?,?,?)', (NATIVE_PLANT, None, stage_entry_day(stage)))
            for measure in log.data.get('heights') or []:
                cm = measure.get('cm')
                try:
                    stamp = day(measure.get('date'), required=True)
                except Invalid:
                    continue
                if started and stamp < started.isoformat():
                    continue
                if type(cm) in (float, int) and math.isfinite(cm) and 0.01 <= cm <= 10000:
                    db.execute('INSERT OR REPLACE INTO heights VALUES(?,?,?)', (NATIVE_PLANT, stamp, cm))
                    from .plant import height_metadata
                    try:
                        metadata = height_metadata(measure.get('estimated', False), measure.get('camera'))
                    except ValueError:
                        metadata = {}
                    if metadata:
                        db.execute('INSERT OR REPLACE INTO height_metadata VALUES(?,?,?)', (NATIVE_PLANT, stamp, json.dumps(metadata)))
        db.execute("INSERT INTO meta VALUES('initialized','1')")
        db.execute("INSERT INTO meta VALUES('revision','1')")
        db.execute("INSERT INTO meta VALUES('phase_dates_initialized','1')")
        initialize_records(db, device)


def stage_entry_day(stage):
    if not stage or stage.get('set_by') != 'manual':
        return None
    try:
        return day(stage.get('since'))
    except Invalid:
        return None


def height_rows(db, plant, *, descending=False, limit=100):
    order = 'DESC' if descending else 'ASC'
    rows = []
    for row in db.execute(f'SELECT h.day,h.cm,m.data FROM heights h LEFT JOIN height_metadata m '
                         f'ON h.plant=m.plant AND h.day=m.day WHERE h.plant=? ORDER BY h.day {order} LIMIT ?', (plant, limit)):
        metadata = json.loads(row['data']) if row['data'] else {}
        rows.append({'day': row['day'], 'cm': row['cm'], **metadata})
    return rows


def linked_id():
    """The plant record the cockpit runs on: the one the owner chose (vgrow/plant_run.py), else the imported one."""
    return plant_run.active_record() or NATIVE_PLANT


def variety_pick(body):
    """The owner's pick from the wiki's varieties and the flowering type; both optional, a pick must exist."""
    flowering = body.get('flowering') or None
    if flowering is not None and flowering not in plant_run.FLOWERING:
        raise Invalid('workspace_invalid')
    ref = body.get('variety_ref')
    if ref is None:
        return None, flowering
    if not isinstance(ref, dict) or plant_run.find(ref) is None:
        raise Invalid('workspace_invalid')
    return {'source': ref['source'], 'key': ref['key']}, flowering


def store_pick(db, ident, ref, flowering):
    db.execute('INSERT INTO plant_variety VALUES(?,?,?,?) ON CONFLICT(plant) DO UPDATE SET source=excluded.source,'
               'key=excluded.key,flowering=excluded.flowering',
               (ident, ref['source'] if ref else None, ref['key'] if ref else None, flowering))


def plant_record(ident):
    """What the cockpit needs to run on this plant (plant_run.build_run)."""
    with database() as db:
        plant = dict(load(db, 'plants', ident))
        picked = db.execute('SELECT source,key,flowering FROM plant_variety WHERE plant=?', (plant['id'],)).fetchone()
    plant['variety_ref'] = {'source': picked['source'], 'key': picked['key']} if picked and picked['key'] else None
    plant['flowering'] = picked['flowering'] if picked else None
    return plant


def view(lang='en'):
    linked = linked_id()
    with database() as db:
        boxes = [dict(r) for r in db.execute('SELECT * FROM boxes ORDER BY created,rowid')]
        plants = [dict(r) for r in db.execute('SELECT * FROM plants ORDER BY created,rowid')]
        for plant in plants:
            phase = json.loads(plant['phase'])
            plant['phase'] = phase.get(lang) or phase.get('en') or phase.get('de') or ''
            lifecycle = db.execute('SELECT origin,ended,summary FROM record_lifecycle WHERE plant=?', (plant['id'],)).fetchone()
            plant.update(dict(lifecycle) if lifecycle else {'origin': '', 'ended': None, 'summary': ''})
            plant.update(harvest.fields(db, plant['id'], plant['ended']))
            plant['status'] = 'completed' if plant['ended'] else plant['post_stage'] or 'active'
            plant['linked_profile'] = plant['id'] == linked and not plant['ended']
            plant['imported'] = plant['id'] == NATIVE_PLANT
            picked = db.execute('SELECT source,key,flowering FROM plant_variety WHERE plant=?', (plant['id'],)).fetchone()
            plant['variety_ref'] = {'source': picked['source'], 'key': picked['key']} if picked and picked['key'] else None
            plant['flowering'] = picked['flowering'] if picked else None
            plant['variety_facts'] = plant_run.find(plant['variety_ref'], lang) if plant['variety_ref'] else None
            plant['heights'] = height_rows(db, plant['id'], descending=True)
            plant['journal'] = [dict(r) for r in db.execute('SELECT id,day,text,created FROM journal WHERE plant=? ORDER BY day DESC,created DESC LIMIT 100', (plant['id'],))]
            stop = plant['harvested'] or plant['ended']  # a harvested plant no longer ages
            end_day = date.fromisoformat(stop) if stop else today()
            plant['age_days'] = (end_day - date.fromisoformat(plant['started'])).days if plant['started'] else None
            dates = db.execute('SELECT observed_since,recorded FROM phase_dates WHERE plant=?', (plant['id'],)).fetchone()
            plant['phase_since'] = dates['observed_since'] if dates else None
            plant['phase_recorded'] = dates['recorded'] if dates else None
            plant['phase_age_days'] = (end_day - date.fromisoformat(plant['phase_since'])).days if plant['phase_since'] else None
        for box in boxes:
            box['controller'] = NATIVE_BOX if box['id'] == NATIVE_BOX else None
            box['plant_count'] = sum(p['box'] == box['id'] and not p['archived'] and not p['ended'] and not p['harvested'] for p in plants)
            box.update(harvest.box_fields(db, box['id']))  # between two grows
        row = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
    return {'revision': int(row['value']) if row else 0, 'boxes': boxes, 'plants': plants,
            'native_box': NATIVE_BOX, 'linked_plant': linked}


def mutate(body, device=None):
    if not isinstance(body, dict):
        raise Invalid('workspace_invalid')
    action = body.get('action')
    with database(write=True) as db:
        row = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
        version = int(row['value']) if row else 0
        if type(body.get('revision')) is not int or body['revision'] != version:
            raise Invalid('workspace_stale')
        ident = None
        if action in ('box_create', 'box_update'):
            name = text(body.get('name'), required=True)
            kind = text(body.get('kind', ''))
            size = capacity(body.get('capacity'))
            notes = text(body.get('notes', ''), 4000)
            if action == 'box_create':
                if db.execute('SELECT COUNT(*) FROM boxes').fetchone()[0] >= 100:
                    raise Invalid('workspace_limit')
                ident = 'box-' + uuid.uuid4().hex[:16]
                db.execute('INSERT INTO boxes VALUES(?,?,?,?,?,?,?)', (ident, name, kind, size, notes, 0, time.time()))
            else:
                ident = load(db, 'boxes', body.get('id'))['id']
                db.execute('UPDATE boxes SET name=?,kind=?,capacity=?,notes=? WHERE id=?', (name, kind, size, notes, ident))
        elif action in ('plant_create', 'plant_update'):
            box = load(db, 'boxes', body.get('box'))['id']
            name = text(body.get('name'), required=True)
            variety = text(body.get('variety', ''))
            ref, flowering = variety_pick(body)
            phase_label = text(body.get('phase', ''))
            phase = json.dumps({'de': phase_label, 'en': phase_label}, ensure_ascii=False)
            started = day(body.get('started'))
            observed = day(body.get('phase_since'))
            if observed and (not phase_label or (started and observed < started)):
                raise Invalid('workspace_before_start' if phase_label else 'workspace_invalid')
            notes = text(body.get('notes', ''), 4000)
            origin = body.get('origin', '')
            if origin not in ORIGINS:
                raise Invalid('workspace_invalid')
            if type(body.get('approximate', False)) is not bool:
                raise Invalid('workspace_invalid')
            approximate = int(body.get('approximate', False) and started is not None)
            if action == 'plant_create':
                count = body.get('count', 1)
                if type(count) is not int or not 1 <= count <= 50:
                    raise Invalid('workspace_invalid')
                if db.execute('SELECT COUNT(*) FROM plants').fetchone()[0] + count > 2000:
                    raise Invalid('workspace_limit')
                for index in range(count):
                    ident = 'plant-' + uuid.uuid4().hex[:16]
                    label = name if count == 1 else f'{name[:110]} {index + 1}'
                    db.execute('INSERT INTO plants VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (ident, box, label, variety, phase, started, approximate, notes, 0, time.time()))
                    db.execute('INSERT INTO phase_dates VALUES(?,?,?)', (ident, observed, today().isoformat() if phase_label else None))
                    db.execute('INSERT INTO record_lifecycle(plant,origin) VALUES(?,?)', (ident, origin))
                    store_pick(db, ident, ref, flowering)
                    db.execute('INSERT INTO record_places(plant,box,since,device) VALUES(?,?,?,?)', (ident, box, time.time(), device if box == NATIVE_BOX else None))
                    event(db, ident, 'created', {'box': box, 'phase': phase_label, 'phase_since': observed, 'origin': origin})
            else:
                plant = load(db, 'plants', body.get('id'))
                ident = plant['id']
                if box != plant['box'] and harvest.since(db, ident):
                    raise Invalid('workspace_harvested')  # it left its box at the harvest
                unchanged = phase_label in json.loads(plant['phase']).values()
                previous = db.execute('SELECT * FROM phase_dates WHERE plant=?', (ident,)).fetchone()
                if unchanged:
                    phase = plant['phase']  # editing a name must not erase the imported translations
                    if 'phase_since' not in body:
                        observed = previous['observed_since'] if previous else None
                recorded = (previous['recorded'] if previous else None) if unchanged else today().isoformat() if phase_label else None
                if observed and (not phase_label or (started and observed < started)):
                    raise Invalid('workspace_before_start' if phase_label else 'workspace_invalid')
                if ident in (NATIVE_PLANT, linked_id()) and box != NATIVE_BOX:
                    raise Invalid('workspace_linked')
                if 'variety_ref' in body or 'flowering' in body:
                    store_pick(db, ident, ref, flowering)
                dates = db.execute('SELECT MIN(day) FROM (SELECT day FROM heights WHERE plant=? UNION ALL SELECT day FROM journal WHERE plant=?)', (ident, ident)).fetchone()[0]
                if started and dates and dates < started:
                    raise Invalid('workspace_date_conflict')
                db.execute('UPDATE plants SET box=?,name=?,variety=?,phase=?,started=?,approximate=?,notes=? WHERE id=?',
                           (box, name, variety, phase, started, approximate, notes, ident))
                db.execute('INSERT INTO phase_dates VALUES(?,?,?) ON CONFLICT(plant) DO UPDATE SET observed_since=excluded.observed_since,recorded=excluded.recorded',
                           (ident, observed, recorded))
                if 'origin' in body:
                    db.execute('UPDATE record_lifecycle SET origin=? WHERE plant=?', (origin, ident))
                if plant['box'] != box:
                    now = time.time()
                    db.execute('UPDATE record_places SET until=? WHERE plant=? AND until IS NULL', (now, ident))
                    db.execute('INSERT INTO record_places(plant,box,since,device) VALUES(?,?,?,?)', (ident, box, now, device if box == NATIVE_BOX else None))
                event(db, ident, 'updated', {'before': dict(plant), 'phase_since_before': previous['observed_since'] if previous else None,
                                           'after': {'box': box, 'name': name, 'variety': variety, 'phase': json.loads(phase),
                                                     'phase_since': observed, 'started': started, 'approximate': approximate, 'notes': notes,
                                                     'origin': origin if 'origin' in body else None}})
        elif action == 'plant_finish':
            plant = load(db, 'plants', body.get('id'))
            ident = plant['id']
            ended = day(body.get('ended'), required=True)
            summary = text(body.get('summary', ''), 4000)
            if body.get('confirmed') is not True:
                raise Invalid('workspace_invalid')
            phase_day = db.execute('SELECT observed_since FROM phase_dates WHERE plant=?', (ident,)).fetchone()
            latest = db.execute('SELECT MAX(day) FROM (SELECT day FROM heights WHERE plant=? UNION ALL SELECT day FROM journal WHERE plant=?)', (ident, ident)).fetchone()[0]
            if any(value and value > ended for value in (plant['started'], latest, phase_day['observed_since'] if phase_day else None,
                                                         harvest.since(db, ident))):
                raise Invalid('workspace_date_conflict')
            cutoff = min(time.time(), datetime.combine(date.fromisoformat(ended) + timedelta(days=1), day_time(), ZoneInfo('Europe/Berlin')).timestamp())
            db.execute('UPDATE record_places SET until=MAX(since,?) WHERE plant=? AND until IS NULL', (cutoff, ident))
            db.execute('UPDATE record_lifecycle SET ended=?,summary=? WHERE plant=?', (ended, summary, ident))
            event(db, ident, 'completed', {'ended': ended, 'summary': summary})
            snapshot = record_data(db, ident)
            db.execute('UPDATE record_lifecycle SET snapshot=? WHERE plant=?', (json.dumps(snapshot, ensure_ascii=False), ident))
        elif action in ('box_archive', 'plant_archive'):
            table = 'boxes' if action == 'box_archive' else 'plants'
            entity = load(db, table, body.get('id'), active=False)
            ident = entity['id']
            if table == 'plants':
                finished = db.execute('SELECT ended FROM record_lifecycle WHERE plant=?', (ident,)).fetchone()
                if finished and finished['ended']:
                    raise Invalid('workspace_completed')
            value = body.get('archived')
            if type(value) is not bool:
                raise Invalid('workspace_invalid')
            if ident in (NATIVE_BOX, NATIVE_PLANT, linked_id()):
                raise Invalid('workspace_linked')
            if table == 'plants' and not value:
                load(db, 'boxes', entity['box'])
            db.execute(f'UPDATE {table} SET archived=? WHERE id=?', (int(value), ident))
            if table == 'plants':
                event(db, ident, 'archived' if value else 'restored', {})
        elif action in ('height', 'note'):
            plant = load(db, 'plants', body.get('plant'))
            load(db, 'boxes', plant['box'])
            stamp = day(body.get('day'), required=True)
            if plant['started'] and stamp < plant['started']:
                raise Invalid('workspace_before_start')
            if action == 'height' and harvest.since(db, plant['id']):
                raise Invalid('workspace_harvested')  # notes go on through drying and curing, heights end at the harvest
            if action == 'height':
                from .plant import height_metadata
                try:
                    metadata = height_metadata(body.get('estimated', False), body.get('camera'), body.get('method'))
                except ValueError as err:
                    raise Invalid('workspace_height') from err
                cm = body.get('cm')
                if type(cm) not in (int, float) or not math.isfinite(cm) or not 0.01 <= cm <= 10000:
                    raise Invalid('workspace_height')
                db.execute('INSERT INTO heights VALUES(?,?,?) ON CONFLICT(plant,day) DO UPDATE SET cm=excluded.cm', (plant['id'], stamp, round(cm, 2)))
                db.execute('DELETE FROM height_metadata WHERE plant=? AND day=?', (plant['id'], stamp))
                if metadata:
                    db.execute('INSERT INTO height_metadata VALUES(?,?,?)', (plant['id'], stamp, json.dumps(metadata)))
                event(db, plant['id'], 'height', {'day': stamp, 'cm': round(cm, 2), **metadata})
            else:
                if db.execute('SELECT COUNT(*) FROM journal WHERE plant=?', (plant['id'],)).fetchone()[0] >= 1000:
                    raise Invalid('workspace_limit')
                db.execute('INSERT INTO journal VALUES(?,?,?,?,?)', (uuid.uuid4().hex, plant['id'], stamp, text(body.get('text'), 4000, required=True), time.time()))
                event(db, plant['id'], 'note', {'day': stamp, 'text': text(body.get('text'), 4000, required=True)})
        elif action in harvest.ACTIONS:
            ident = harvest.mutate(db, action, body)
        else:
            raise Invalid('workspace_invalid')
        db.execute("INSERT INTO meta VALUES('revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version + 1),))
    return ident


def record_data(db, ident):
    plant = dict(load(db, 'plants', ident, active=False))
    plant['phase'] = json.loads(plant['phase'])
    life = db.execute('SELECT origin,ended,summary FROM record_lifecycle WHERE plant=?', (ident,)).fetchone()
    plant.update(dict(life) if life else {'origin': '', 'ended': None, 'summary': ''})
    plant.update(harvest.fields(db, ident, plant['ended']))
    dates = db.execute('SELECT observed_since,recorded FROM phase_dates WHERE plant=?', (ident,)).fetchone()
    plant['phase_since'] = dates['observed_since'] if dates else None
    plant['phase_recorded'] = dates['recorded'] if dates else None
    plant['heights'] = height_rows(db, ident, limit=-1)
    plant['journal'] = [dict(r) for r in db.execute('SELECT * FROM journal WHERE plant=? ORDER BY day,created', (ident,))]
    events = [dict(r) for r in db.execute('SELECT at,kind,data FROM record_events WHERE plant=? ORDER BY id', (ident,))]
    for row in events:
        row['data'] = json.loads(row['data'])
    places = [dict(r) for r in db.execute('SELECT p.box,b.name,p.since,p.until,p.device FROM record_places p JOIN boxes b ON b.id=p.box WHERE plant=? ORDER BY p.id', (ident,))]
    return {'version': 1, 'captured_at': time.time(), 'record': plant, 'events': events, 'box_intervals': places,
            'coverage': {'sensors': 'Shared box readings only during recorded box assignments. Earlier assignments are unknown.',
                         'camera_ai': 'Photos and model results remain box-level; no individual plant attribution exists.'}}


def record(ident):
    with database() as db:
        row = db.execute('SELECT snapshot FROM record_lifecycle WHERE plant=?', (ident,)).fetchone()
        return json.loads(row['snapshot']) if row and row['snapshot'] else record_data(db, ident)


def validate_context(box, plant=None):
    """Never redirect a command for an unconnected box to the original controller."""
    with database() as db:
        b = load(db, 'boxes', box)
        if b['id'] != NATIVE_BOX:
            raise Invalid('workspace_unbound')
        if plant:
            p = load(db, 'plants', plant)
            if p['box'] != box:
                raise Invalid('workspace_context')
    return plant == linked_id() or not plant


def camera_height(day_text, cm):
    """The strip's reading (vgrow/grow_plan.py) on the record of the run: the page's height list shows it. False without an
    active record. A height the owner typed for that day stays."""
    ident = linked_id()
    with database() as db:
        row = db.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone()
        typed = db.execute('SELECT m.data FROM heights h LEFT JOIN height_metadata m ON h.plant=m.plant AND h.day=m.day '
                           'WHERE h.plant=? AND h.day=?', (ident, day_text)).fetchone()
    if typed is not None and 'camera_strip' not in (typed['data'] or ''):
        return False
    try:
        mutate({'revision': int(row['value']) if row else 0, 'action': 'height', 'plant': ident, 'day': day_text, 'cm': cm,
                'estimated': True, 'camera': 'growcam', 'method': 'camera_strip'})
    except Invalid:
        return False
    return True


def owner_height(day_text, cm):
    """A height the owner entered under the reading's picture (vgrow/grow_plan.py), on the record of the run. It replaces the
    camera's value of that day, and camera_height then leaves it alone. False without an active record."""
    with database() as db:
        row = db.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone()
    try:
        mutate({'revision': int(row['value']) if row else 0, 'action': 'height', 'plant': linked_id(), 'day': day_text, 'cm': cm})
    except Invalid:
        return False
    return True


def sync_stage(label_de, label_en, since):
    """The run's stage on its record, when the owner set it on the controller. False without an active record."""
    ident = linked_id()
    with database(write=True) as db:
        try:
            load(db, 'plants', ident)
        except Invalid:
            return False
        db.execute('UPDATE plants SET phase=? WHERE id=?', (json.dumps({'de': label_de, 'en': label_en}, ensure_ascii=False), ident))
        db.execute('INSERT INTO phase_dates VALUES(?,?,?) ON CONFLICT(plant) DO UPDATE SET observed_since=excluded.observed_since,recorded=excluded.recorded',
                   (ident, since, today().isoformat()))
        event(db, ident, 'updated', {'after': {'phase': {'de': label_de, 'en': label_en}, 'phase_since': since}})
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
    return True


def register(app, *, name, profile, log, measurements=None):
    async def ensure():
        await asyncio.to_thread(initialize, name(), profile(), log, getattr(measurements, 'device_id', None))

    async def read(_):
        try:
            await ensure()
            return web.json_response(await asyncio.to_thread(view, current_lang()), headers={'Cache-Control': 'no-store'})
        except (OSError, sqlite3.Error):
            return web.json_response({'error': 'workspace_storage'}, status=503)

    async def write(request):
        try:
            await ensure()
            ident = await asyncio.to_thread(mutate, await request.json(), getattr(measurements, 'device_id', None))
            data = await asyncio.to_thread(view, current_lang())
            return web.json_response({**data, 'created': ident}, headers={'Cache-Control': 'no-store'})
        except Invalid as err:
            return web.json_response({'error': str(err)}, status=409 if str(err) == 'workspace_stale' else 400)
        except (ValueError, TypeError, OverflowError):
            return web.json_response({'error': 'workspace_invalid'}, status=400)
        except (OSError, sqlite3.Error):
            return web.json_response({'error': 'workspace_storage'}, status=503)

    async def varieties(_):
        """The wiki's varieties for the plant form; a broken list costs the suggestions, the form still takes free text."""
        try:
            rows = await asyncio.to_thread(plant_run.options, current_lang())
        except Exception:
            logging.getLogger(__name__).exception('Variety list failed')
            rows = []
        return web.json_response({'rows': rows}, headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/workspace', read)
    app.router.add_get('/api/workspace/varieties', varieties)
    app.router.add_post('/api/workspace', write)
    from .record_export import register as register_exports
    register_exports(app, ensure=ensure, measurements=measurements)
