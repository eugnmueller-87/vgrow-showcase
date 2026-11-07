"""After the harvest: drying, curing and ready, until the record goes to the archive. Records only, never a device command.

Harvesting takes the plant out of its box (owner, 01.11.2026: "so it frees up the space"): the box interval ends that day,
the box no longer counts the plant, and no later box reading belongs to this record. Each later stage keeps the day it
began; going back (curing to drying, when buds were still too wet) is allowed. The archive is the workspace's completion
("plant_finish"), which freezes the record together with these fields.

The stage after curing is called "ready", not "consume": the cockpit shows and animates no consumption (KCanG section 6,
owner's rule for Hank's scenes since 22.10.2026).

Between two grows (owner, 01.11.2026: "let's clean up the box, prepare for the next grow"): an empty box gets a short
list, PREP. A tick counts from the moment the box was last emptied, so every turnover starts with a fresh list and
nothing needs resetting. The list is housekeeping, it never sends a device command either.

Hooks in vgrow/workspace.py: SCHEMA (the table), view() and record_data() (fields), mutate() (ACTIONS, and the guards
for heights, box moves and the completion date).
"""
from __future__ import annotations

import time
from datetime import date, datetime, time as day_time, timedelta
from zoneinfo import ZoneInfo

STAGES = ('drying', 'curing', 'ready')
PREP = ('debris', 'clean', 'water', 'cameras', 'inspect')
ACTIONS = ('plant_harvest', 'plant_stage', 'box_prep')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS post_harvest(plant TEXT PRIMARY KEY REFERENCES plants(id), harvested TEXT NOT NULL,
 stage TEXT NOT NULL, since TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS box_prep(box TEXT NOT NULL REFERENCES boxes(id), item TEXT NOT NULL, at REAL NOT NULL,
 PRIMARY KEY(box,item));
'''


def fields(db, ident, ended=None):
    """The record's harvest fields; post_days counts to its completion, if it has one."""
    from .workspace import today
    row = db.execute('SELECT harvested,stage,since FROM post_harvest WHERE plant=?', (ident,)).fetchone()
    if row is None:
        return {'harvested': None, 'post_stage': None, 'post_since': None, 'post_days': None}
    end = date.fromisoformat(ended) if ended else today()
    return {'harvested': row['harvested'], 'post_stage': row['stage'], 'post_since': row['since'],
            'post_days': (end - date.fromisoformat(row['since'])).days}


def box_fields(db, ident):
    """When the box was last emptied (None if never) and the prep steps ticked since then."""
    emptied = db.execute('SELECT MAX(until) FROM record_places WHERE box=?', (ident,)).fetchone()[0]
    ticked = {row['item'] for row in db.execute('SELECT item FROM box_prep WHERE box=? AND at>=?', (ident, emptied or 0))}
    return {'emptied': emptied, 'prep': [item for item in PREP if item in ticked]}


def since(db, ident):
    """The day the current post-harvest stage began, or None while the plant still grows."""
    row = db.execute('SELECT since FROM post_harvest WHERE plant=?', (ident,)).fetchone()
    return row['since'] if row else None


def mutate(db, action, body):
    from .workspace import Invalid, day, event, load
    if action == 'box_prep':
        box = load(db, 'boxes', body.get('box'))['id']
        item, done = body.get('item'), body.get('done')
        if item not in PREP or type(done) is not bool:
            raise Invalid('workspace_invalid')
        if done:
            db.execute('INSERT INTO box_prep VALUES(?,?,?) ON CONFLICT(box,item) DO UPDATE SET at=excluded.at',
                       (box, item, time.time()))
        else:
            db.execute('DELETE FROM box_prep WHERE box=? AND item=?', (box, item))
        return box
    plant = load(db, 'plants', body.get('id'))
    ident = plant['id']
    when = day(body.get('day'), required=True)
    current = db.execute('SELECT stage,since FROM post_harvest WHERE plant=?', (ident,)).fetchone()
    if action == 'plant_harvest':
        if current:
            raise Invalid('workspace_harvested')
        phase = db.execute('SELECT observed_since FROM phase_dates WHERE plant=?', (ident,)).fetchone()
        latest = db.execute('SELECT MAX(day) FROM heights WHERE plant=?', (ident,)).fetchone()[0]
        if any(value and value > when for value in (plant['started'], latest, phase['observed_since'] if phase else None)):
            raise Invalid('workspace_date_conflict')
        cutoff = min(time.time(), datetime.combine(date.fromisoformat(when) + timedelta(days=1), day_time(),
                                                   ZoneInfo('Europe/Berlin')).timestamp())
        db.execute('UPDATE record_places SET until=MAX(since,?) WHERE plant=? AND until IS NULL', (cutoff, ident))
        db.execute('INSERT INTO post_harvest VALUES(?,?,?,?)', (ident, when, 'drying', when))
        event(db, ident, 'harvested', {'day': when, 'box': plant['box']})
    else:
        stage = body.get('stage')
        if current is None:
            raise Invalid('workspace_not_harvested')
        if stage not in STAGES or stage == current['stage']:
            raise Invalid('workspace_invalid')
        if when < current['since']:
            raise Invalid('workspace_date_conflict')
        db.execute('UPDATE post_harvest SET stage=?,since=? WHERE plant=?', (stage, when, ident))
        event(db, ident, 'stage', {'from': current['stage'], 'to': stage, 'day': when})
    return ident
