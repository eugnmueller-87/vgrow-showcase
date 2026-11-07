"""Read-only reference tools and local reading/editorial state, independent of devices.

No actuator imports, credentials, external account integration or automatic publication.
Corrections are proposals until explicitly reviewed. Accepted facts remain attributed to
their reviewer-supplied source alongside the original reference, rather than erasing it.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from . import config, wiki
from .i18n import current_lang

FACT_FIELDS = frozenset({'classification', 'thc_percent', 'parents', 'aliases', 'terpenes', 'descendants'})
LIST_FIELDS = frozenset({'parents', 'aliases', 'terpenes', 'descendants'})
SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks(article TEXT PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS recent(article TEXT PRIMARY KEY, visited REAL NOT NULL);
CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE NOT NULL);
CREATE TABLE IF NOT EXISTS members(collection_id TEXT REFERENCES collections(id) ON DELETE CASCADE,
 article TEXT NOT NULL, PRIMARY KEY(collection_id,article));
CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY AUTOINCREMENT, article TEXT NOT NULL,
 digest TEXT NOT NULL, observed REAL NOT NULL, snapshot TEXT NOT NULL, reason TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS revisions_article ON revisions(article,id);
CREATE TABLE IF NOT EXISTS corrections(id TEXT PRIMARY KEY, article TEXT NOT NULL, field TEXT NOT NULL,
 value TEXT NOT NULL, comment TEXT NOT NULL, source TEXT NOT NULL, base TEXT NOT NULL,
 created REAL NOT NULL, status TEXT NOT NULL, reviewed REAL, review_note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS corrections_article ON corrections(article,created);
CREATE TABLE IF NOT EXISTS link_checks(url TEXT PRIMARY KEY, checked REAL NOT NULL,
 status TEXT NOT NULL, http_status INTEGER);
"""


class Invalid(ValueError):
    pass


@contextmanager
def database(*, write: bool = False) -> Iterator[sqlite3.Connection]:
    path = config.STATE_DIR / 'wiki' / 'reading.sqlite3'
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


def packed(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def article_id(entry: Mapping[str, Any]) -> str:
    return f"{entry['folder']}/{entry['key']}"


def lookup(cid: Any, library: Any = None) -> dict[str, Any]:
    if not isinstance(cid, str) or cid.count('/') != 1:
        raise Invalid('invalid_article')
    folder, key = cid.split('/')
    entry = wiki.find(folder, key, library)
    if entry is None or not wiki.visible(entry, wiki.packages()):
        raise Invalid('invalid_article')
    return entry


def source_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 600 or any(c.isspace() for c in value):
        raise Invalid('invalid_source')
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.port not in (None, 443):
            raise Invalid('invalid_source')
    except ValueError as err:
        raise Invalid('invalid_source') from err
    return value


def clean_text(value: Any, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise Invalid('invalid_value')
    return value.strip()


def fact_value(field: str, value: Any) -> Any:
    if field == 'classification' and value in ('indica', 'sativa', 'hybrid', 'unknown'):
        return value
    if field == 'thc_percent' and type(value) in (int, float) and 0 <= value <= 100:
        return value
    if field in LIST_FIELDS and isinstance(value, list) and len(value) <= 12:
        return [clean_text(v, 100) for v in value]
    if field == 'note':
        return clean_text(value, 2000)
    raise Invalid('invalid_value')


def accepted(entry: Mapping[str, Any], db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute("SELECT * FROM corrections WHERE article=? AND status='accepted' ORDER BY reviewed,id", (article_id(entry),))
    return [dict(row) for row in rows]


def snapshot(entry: Mapping[str, Any], edits: list[dict[str, Any]]) -> dict[str, Any]:
    # Preserve package marks internally so an old snapshot can be filtered using
    # today's package settings. Only history() exposes a sanitized display copy.
    content = {k: v for k, v in entry['raw'].items() if k != 'sources'}
    return {'content': content, 'sources': wiki._sources(entry, 'en'),
            'reviewed': [{k: e[k] for k in ('id', 'field', 'value', 'source', 'reviewed', 'review_note')} for e in edits]}


def digest(value: Any) -> str:
    return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()


def observe(entry: Mapping[str, Any], db: sqlite3.Connection, reason: str = 'observed') -> dict[str, Any]:
    snap = snapshot(entry, accepted(entry, db))
    checksum = digest(snap)
    last = db.execute('SELECT id,digest,observed FROM revisions WHERE article=? ORDER BY id DESC LIMIT 1', (article_id(entry),)).fetchone()
    if last is None or last['digest'] != checksum:
        now = time.time()
        cursor = db.execute('INSERT INTO revisions(article,digest,observed,snapshot,reason) VALUES(?,?,?,?,?)',
                            (article_id(entry), checksum, now, packed(snap), 'first_observed' if last is None else reason))
        return {'id': cursor.lastrowid, 'digest': checksum, 'observed': now}
    return dict(last)


def evidence(entry: Mapping[str, Any], ref: Mapping[str, Any], edits: list[dict[str, Any]]) -> dict[str, Any]:
    raw = entry['raw'].get('reference', {}).get('evidence', {})
    known = entry.get('sources') or {}
    result = {}
    for field in FACT_FIELDS:
        claims = []
        candidates = raw.get(field, []) if isinstance(raw, Mapping) else []
        for item in candidates if isinstance(candidates, list) else []:
            if not isinstance(item, Mapping) or item.get('source_id') not in known:
                continue
            try:
                value = fact_value(field, item.get('value'))
            except (Invalid, TypeError):
                continue
            source = wiki.parse_source(item['source_id'], known[item['source_id']], 'en')
            try:
                source_url(source.get('url'))
            except Invalid:
                continue
            claims.append({'value': value, 'source': source, 'retrieved': str(item.get('retrieved') or ''),
                           'basis': 'median' if field == 'thc_percent' else 'reported'})
        for edit in edits:
            if edit['field'] == field:
                claims.append({'value': json.loads(edit['value']), 'source': {'id': 'C' + edit['id'],
                               'title': 'Reviewed correction', 'url': edit['source']},
                               'retrieved': datetime.fromtimestamp(edit['reviewed'], timezone.utc).date().isoformat(),
                               'basis': 'reviewed', 'review_note': edit['review_note']})
        distinct = {packed(sorted(c['value']) if isinstance(c['value'], list) else c['value']) for c in claims}
        result[field] = {'claims': claims, 'disputed': len(distinct) > 1,
                         'status': 'reviewed' if any(c['basis'] == 'reviewed' for c in claims) else 'reported' if claims else 'unknown',
                         'selected': ref.get(field)}
    return result


def gallery(entry: Mapping[str, Any], lang: str, on: list[str]) -> list[dict[str, Any]]:
    raw = entry['raw'].get('image') or {}
    items = raw.get('gallery', []) if isinstance(raw, Mapping) else []
    out = []
    seen = set()
    for item in [raw, *(items if isinstance(items, list) else [])]:
        view = wiki.image_view(item, lang, on, kind=entry['kind']) or {}
        photo = view.get('external') or view.get('photo')
        if photo and photo['src'] not in seen:
            seen.add(photo['src'])
            out.append({'id': hashlib.sha256(photo['src'].encode()).hexdigest()[:12],
                        'kind': 'external' if 'external' in view else 'local', **photo})
    return out[:8]


def enrich(entry: Mapping[str, Any], data: dict[str, Any], lang: str, on: list[str]) -> dict[str, Any]:
    with database(write=True) as db:
        edits = accepted(entry, db)
        ref = data['card'].get('reference')
        if entry['kind'] == 'variety' and isinstance(ref, dict):
            for edit in edits:
                if edit['field'] in FACT_FIELDS:
                    ref[edit['field']] = json.loads(edit['value'])
            data['evidence'] = evidence(entry, ref, edits)
        elif entry['kind'] == 'plant_condition':
            data['fact_sources'] = [s for s in data['sources'] if s.get('url')]
        data['reviewed_notes'] = [{'id': e['id'], 'text': json.loads(e['value']), 'source': e['source'],
                                  'reviewed': e['reviewed'], 'review_note': e['review_note']} for e in edits if e['field'] == 'note']
        data['revision'] = observe(entry, db)
    data['gallery'] = gallery(entry, lang, on)
    return data


def enrich_rows(data: dict[str, Any], library: Any = None) -> dict[str, Any]:
    with database() as db:
        for row in data['varieties']:
            entry = wiki.find(row['folder'], row['key'], library)
            if entry:
                edits = accepted(entry, db)
                for edit in edits:
                    if edit['field'] in FACT_FIELDS:
                        row['reference'][edit['field']] = json.loads(edit['value'])
                row['evidence'] = evidence(entry, row['reference'], edits)
    return data


def catalogue(library: Any = None) -> dict[str, dict[str, Any]]:
    return {article_id(e): e for e in wiki._entries(library or wiki.LIBRARY) if wiki.visible(e, wiki.packages())}


def reading_state(lang: str, library: Any = None) -> dict[str, Any]:
    visible = catalogue(library)
    labels = {cid: wiki.summary(e, lang, wiki.packages()) for cid, e in visible.items()}
    with database() as db:
        bookmarks = [r['article'] for r in db.execute('SELECT article FROM bookmarks ORDER BY created DESC') if r['article'] in visible]
        collections = [{'id': r['id'], 'name': r['name'], 'articles': [m['article'] for m in db.execute(
            'SELECT article FROM members WHERE collection_id=? ORDER BY article', (r['id'],)) if m['article'] in visible]}
            for r in db.execute('SELECT id,name FROM collections ORDER BY name COLLATE NOCASE')]
        recent = [r['article'] for r in db.execute('SELECT article FROM recent ORDER BY visited DESC LIMIT 100') if r['article'] in visible][:30]
        row = db.execute("SELECT value FROM preferences WHERE key='compare'").fetchone()
        compare = [cid for cid in json.loads(row['value']) if cid in visible] if row else []
    ids = set(bookmarks + recent + compare + [cid for c in collections for cid in c['articles']])
    return {'bookmarks': bookmarks, 'collections': collections, 'recent': recent, 'compare': compare,
            'articles': {cid: labels[cid] for cid in ids}}


def mutate_reading(body: Mapping[str, Any], library: Any = None) -> None:
    action = body.get('action')
    entry = lookup(body.get('article'), library) if action in ('bookmark', 'compare', 'visit', 'member') else None
    cid = article_id(entry) if entry else ''
    with database(write=True) as db:
        if action in ('bookmark', 'compare', 'member') and type(body.get('selected')) is not bool:
            raise Invalid('invalid_value')
        if action == 'bookmark':
            if body['selected']:
                if db.execute('SELECT COUNT(*) FROM bookmarks').fetchone()[0] >= 1000 and not db.execute('SELECT 1 FROM bookmarks WHERE article=?', (cid,)).fetchone():
                    raise Invalid('limit_reached')
                db.execute('INSERT OR IGNORE INTO bookmarks VALUES(?,?)', (cid, time.time()))
            else:
                db.execute('DELETE FROM bookmarks WHERE article=?', (cid,))
        elif action == 'visit':
            db.execute('INSERT INTO recent VALUES(?,?) ON CONFLICT(article) DO UPDATE SET visited=excluded.visited', (cid, time.time()))
            db.execute('DELETE FROM recent WHERE article NOT IN (SELECT article FROM recent ORDER BY visited DESC LIMIT 100)')
        elif action == 'compare':
            if entry['kind'] != 'variety':
                raise Invalid('invalid_article')
            row = db.execute("SELECT value FROM preferences WHERE key='compare'").fetchone()
            values = json.loads(row['value']) if row else []
            values = [v for v in values if v != cid]
            if body['selected']:
                if len(values) >= 4:
                    raise Invalid('compare_limit')
                values.append(cid)
            db.execute("INSERT INTO preferences VALUES('compare',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (packed(values),))
        elif action == 'collection_create':
            name = clean_text(body.get('name'), 80)
            if db.execute('SELECT COUNT(*) FROM collections').fetchone()[0] >= 50:
                raise Invalid('limit_reached')
            try:
                db.execute('INSERT INTO collections VALUES(?,?)', (uuid.uuid4().hex[:16], name))
            except sqlite3.IntegrityError as err:
                raise Invalid('duplicate_collection') from err
        elif action in ('collection_rename', 'collection_delete', 'member'):
            collection = body.get('collection')
            if not isinstance(collection, str) or not db.execute('SELECT 1 FROM collections WHERE id=?', (collection,)).fetchone():
                raise Invalid('invalid_collection')
            if action == 'collection_rename':
                try:
                    db.execute('UPDATE collections SET name=? WHERE id=?', (clean_text(body.get('name'), 80), collection))
                except sqlite3.IntegrityError as err:
                    raise Invalid('duplicate_collection') from err
            elif action == 'collection_delete':
                db.execute('DELETE FROM collections WHERE id=?', (collection,))
            elif body['selected']:
                db.execute('INSERT OR IGNORE INTO members VALUES(?,?)', (collection, cid))
            else:
                db.execute('DELETE FROM members WHERE collection_id=? AND article=?', (collection, cid))
        else:
            raise Invalid('invalid_action')


def propose(body: Mapping[str, Any], library: Any = None) -> str:
    entry = lookup(body.get('article'), library)
    field = body.get('field')
    if field != 'note' and (entry['kind'] != 'variety' or field not in FACT_FIELDS):
        raise Invalid('invalid_field')
    value = fact_value(field, body.get('value'))
    source = source_url(body.get('source')) if body.get('source') or field != 'note' else ''
    comment = clean_text(body.get('comment'), 2000)
    with database(write=True) as db:
        revision = observe(entry, db)
        if body.get('base') != revision['digest']:
            raise Invalid('stale_correction')
        if db.execute("SELECT COUNT(*) FROM corrections WHERE status='pending'").fetchone()[0] >= 500:
            raise Invalid('limit_reached')
        ident = uuid.uuid4().hex[:16]
        db.execute('INSERT INTO corrections(id,article,field,value,comment,source,base,created,status) VALUES(?,?,?,?,?,?,?,?,?)',
                   (ident, article_id(entry), field, packed(value), comment, source, revision['digest'], time.time(), 'pending'))
        return ident


def review(body: Mapping[str, Any], library: Any = None) -> None:
    decision = body.get('decision')
    if decision not in ('accepted', 'rejected'):
        raise Invalid('invalid_action')
    note = clean_text(body.get('note'), 2000)
    with database(write=True) as db:
        row = db.execute('SELECT * FROM corrections WHERE id=?', (str(body.get('id') or ''),)).fetchone()
        if not row or row['status'] != 'pending':
            raise Invalid('invalid_correction')
        entry = lookup(row['article'], library)
        if decision == 'accepted' and observe(entry, db)['digest'] != row['base']:
            raise Invalid('stale_correction')
        db.execute('UPDATE corrections SET status=?,reviewed=?,review_note=? WHERE id=?', (decision, time.time(), note, row['id']))
        if decision == 'accepted':
            observe(entry, db, 'reviewed_correction')


def history(cid: str, library: Any = None) -> dict[str, Any]:
    entry = lookup(cid, library)
    with database(write=True) as db:
        observe(entry, db)
        rows = [dict(r) for r in db.execute('SELECT * FROM revisions WHERE article=? ORDER BY id DESC LIMIT 50', (cid,))]
        total = db.execute('SELECT COUNT(*) FROM revisions WHERE article=?', (cid,)).fetchone()[0]
    for row in rows:
        row['snapshot'] = json.loads(row['snapshot'])
        # A general article can contain optional package sections. Hiding a package also
        # hides those sections in older snapshots; history is not an alternate access path.
        content, changed = wiki._pkg_filter(row['snapshot']['content'], wiki.packages())
        row['snapshot']['content'] = wiki.no_consumption(wiki._strip(content), entry['kind'])
        if entry['kind'] == 'variety':
            row['snapshot']['content']['reference'] = wiki.variety_reference(content.get('reference'))
        sources = row['snapshot']['sources']
        if isinstance(sources, dict):
            sources = [wiki.parse_source(sid, value, 'en') for sid, value in sources.items()]
        allowed = wiki._cited(content, {s['id'] for s in sources}) if changed else {s['id'] for s in sources}
        row['snapshot']['sources'] = [s for s in sources if s['id'] in allowed]
    return {'article': cid, 'revisions': rows, 'total': total, 'coverage': 'observed_from_first_access'}


def editorial(lang: str, library: Any = None) -> dict[str, Any]:
    entries = catalogue(library)
    today = datetime.now(timezone.utc).date()
    with database() as db:
        checks = {r['url']: dict(r) for r in db.execute('SELECT * FROM link_checks')}
        corrections = [dict(r) for r in db.execute('SELECT * FROM corrections ORDER BY created DESC') if r['article'] in entries]
        rows = []
        for cid, entry in entries.items():
            summary = wiki.summary(entry, lang, wiki.packages())
            photos = gallery(entry, lang, wiki.packages())
            flags = []
            if not photos:
                flags.append('missing_image')
            elif all(p.get('representative') for p in photos):
                flags.append('representative_image')
            sources = wiki._sources(entry, lang)
            if not sources:
                flags.append('missing_source')
            table = entry.get('sources', {})
            dates = [raw.get('retrieved') for s in sources if isinstance(raw := table.get(s['id']), Mapping)]
            dated = []
            for value in dates:
                try:
                    dated.append(date.fromisoformat(str(value)))
                except ValueError:
                    pass
            if dated and (today - min(dated)).days > 180:
                flags.append('stale_source')
            if entry['kind'] == 'variety':
                ref = wiki.variety_reference(entry['raw'].get('reference'))
                edits = accepted(entry, db)
                for edit in edits:
                    if edit['field'] in FACT_FIELDS:
                        ref[edit['field']] = json.loads(edit['value'])
                claims = evidence(entry, ref, edits)
                if any(f['disputed'] for f in claims.values()):
                    flags.append('source_disagreement')
                if not ref['parents']:
                    flags.append('missing_ancestry')
                flags.extend(['unknown_ratios', 'unverified_availability'])
            links = [{**check} for s in sources if (check := checks.get(s.get('url')))]
            if any(c['status'] == 'broken' for c in links):
                flags.append('broken_link')
            rows.append({'article': cid, 'label': summary['label'], 'flags': flags, 'source_count': len(sources), 'checks': links})
    for correction in corrections:
        correction['value'] = json.loads(correction['value'])
        correction['label'] = wiki.summary(entries[correction['article']], lang, wiki.packages())['label']
    return {'articles': rows, 'corrections': corrections,
            'counts': {flag: sum(flag in r['flags'] for r in rows) for flag in {f for r in rows for f in r['flags']}}}


def atlas(lang: str, library: Any = None) -> dict[str, Any]:
    """Manual visual observations on credited photographs, never automatic diagnoses."""
    path = wiki.LIBRARY.parent / 'web' / 'img' / 'wiki' / 'atlas.json'
    items = json.loads(path.read_text(encoding='utf-8'))
    out = []
    for item in items:
        try:
            entry = lookup(item['article'], library)
        except Invalid:
            continue
        view = wiki.image_view(entry['raw'].get('image'), lang, wiki.packages(), kind=entry['kind']) or {}
        if not view.get('photo'):
            continue
        def local(value: Mapping[str, Any]) -> str:
            return str(value.get(lang) or value.get('en') or '')
        markers = [{'x': m['x'], 'y': m['y'], 'label': local(m['label']), 'text': local(m['text'])}
                   for m in item['markers'] if 0 <= m['x'] <= 100 and 0 <= m['y'] <= 100]
        out.append({'id': item['id'], 'part': item['part'], 'label': local(item['label']),
                    'description': local(item['description']), 'photo': view['photo'], 'markers': markers,
                    'lookalikes': item.get('lookalikes', [])})
    return {'items': out}


class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[dict[str, Any]]:
        rows = await aiohttp.resolver.ThreadedResolver().resolve(host, port, family)
        if not rows or any(not ipaddress.ip_address(row['host']).is_global for row in rows):
            raise OSError('non-public address')
        return rows

    async def close(self) -> None:
        return None


async def check_links(cid: str, library: Any = None) -> list[dict[str, Any]]:
    entry = lookup(cid, library)
    # Only committed reference URLs, never reviewer-entered URLs or arbitrary submitted targets.
    urls = sorted({s['url'] for s in wiki._sources(entry, 'en') if s.get('url') and s['url'].startswith('https://')})[:12]
    # aiohttp bypasses its resolver for IP literals. Reject those here as well.
    urls = [url for url in urls if _public_literal(url)]
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=3)
    results = []
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=6), trust_env=False) as session:
        async def check(url: str) -> dict[str, Any]:
            status = None
            try:
                async with session.head(url, allow_redirects=False) as response:
                    status = response.status
                outcome = 'ok' if 200 <= status < 300 else 'broken' if status in (404, 410) else 'redirect' if 300 <= status < 400 else 'unconfirmed'
            except (OSError, asyncio.TimeoutError, aiohttp.ClientError):
                outcome = 'unconfirmed'
            return {'url': url, 'status': outcome, 'http_status': status, 'checked': time.time()}
        results = await asyncio.gather(*(check(url) for url in urls))
    await asyncio.to_thread(save_checks, results)
    return results


def save_checks(results: list[dict[str, Any]]) -> None:
    with database(write=True) as db:
        for result in results:
            db.execute('INSERT INTO link_checks VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET checked=excluded.checked,status=excluded.status,http_status=excluded.http_status',
                       (result['url'], result['checked'], result['status'], result['http_status']))


def _public_literal(url: str) -> bool:
    try:
        source_url(url)
        host = urlsplit(url).hostname
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True  # Hostnames are checked and pinned by PublicResolver.
    except Invalid:
        return False


def register(app: web.Application, library: Any = None) -> None:
    link_lock = asyncio.Lock()
    async def state(_: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(reading_state, current_lang(), library), headers={'Cache-Control': 'no-store'})

    async def write_state(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise Invalid('invalid_value')
            await asyncio.to_thread(mutate_reading, body, library)
            return await state(request)
        except (Invalid, ValueError, TypeError) as err:
            return web.json_response({'error': str(err)}, status=400)

    async def edits(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise Invalid('invalid_value')
            if request.match_info['action'] == 'propose':
                ident = await asyncio.to_thread(propose, body, library)
                return web.json_response({'id': ident, 'status': 'pending'})
            await asyncio.to_thread(review, body, library)
            return web.json_response({'ok': True})
        except (Invalid, ValueError, TypeError) as err:
            return web.json_response({'error': str(err)}, status=400)

    async def revisions(request: web.Request) -> web.Response:
        try:
            return web.json_response(await asyncio.to_thread(history, request.query.get('article', ''), library), headers={'Cache-Control': 'no-store'})
        except Invalid as err:
            return web.json_response({'error': str(err)}, status=404)

    async def dashboard(_: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(editorial, current_lang(), library), headers={'Cache-Control': 'no-store'})

    async def links(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            if link_lock.locked():
                return web.json_response({'error': 'check_in_progress'}, status=409)
            async with link_lock:
                return web.json_response({'checks': await check_links(body.get('article'), library)})
        except (Invalid, ValueError, AttributeError) as err:
            return web.json_response({'error': str(err)}, status=400)

    async def pictures(_: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(atlas, current_lang(), library))

    app.router.add_get('/api/wiki/tools/state', state)
    app.router.add_post('/api/wiki/tools/state', write_state)
    app.router.add_post('/api/wiki/tools/corrections/{action:propose|review}', edits)
    app.router.add_get('/api/wiki/tools/history', revisions)
    app.router.add_get('/api/wiki/tools/editorial', dashboard)
    app.router.add_post('/api/wiki/tools/check-links', links)
    app.router.add_get('/api/wiki/tools/atlas', pictures)
