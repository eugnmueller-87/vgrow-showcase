"""Durable reading state, attributed corrections, observed history and bounded source checks."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp.test_utils import TestClient, TestServer

import test_wiki as tw
from vgrow import wiki, wiki_tools as tools

ARTICLE = 'varieties/blue-dream'


def revision() -> str:
    entry = tools.lookup(ARTICLE)
    with tools.database(write=True) as db:
        return tools.observe(entry, db)['digest']


def proposal(base: str, **kw):
    return {'article': ARTICLE, 'field': 'thc_percent', 'value': 22, 'source': 'https://example.org/reference',
            'comment': 'A new cited reference value', 'base': base, **kw}


def test_bookmarks_collections_and_compare_survive_connections_and_concurrent_writes():
    tools.mutate_reading({'action': 'collection_create', 'name': 'Reference collection'})
    collection = tools.reading_state('en')['collections'][0]['id']
    ids = ['varieties/' + key for key in ('blue-dream', 'sour-diesel', 'ice-cream-cake', 'og-kush')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda cid: tools.mutate_reading({'action': 'compare', 'article': cid, 'selected': True}), ids))
    assert set(tools.reading_state('de')['compare']) == set(ids)
    with pytest.raises(tools.Invalid, match='compare_limit'):
        tools.mutate_reading({'action': 'compare', 'article': 'varieties/gelato', 'selected': True})
    for action in ('bookmark', 'member', 'visit'):
        tools.mutate_reading({'action': action, 'article': ARTICLE, 'collection': collection, 'selected': True})
    state = tools.reading_state('en')
    assert state['bookmarks'] == state['recent'] == state['collections'][0]['articles'] == [ARTICLE]
    assert state['articles'][ARTICLE]['label'] == 'Blue Dream'
    with pytest.raises(tools.Invalid, match='duplicate_collection'):
        tools.mutate_reading({'action': 'collection_create', 'name': 'reference COLLECTION'})
    tools.mutate_reading({'action': 'collection_delete', 'collection': collection})
    with tools.database() as db:
        assert db.execute('SELECT COUNT(*) FROM members').fetchone()[0] == 0
    assert tools.reading_state('en')['bookmarks'] == [ARTICLE]


def test_hidden_package_never_leaks_reading_state_history_or_atlas():
    tools.mutate_reading({'action': 'bookmark', 'article': ARTICLE, 'selected': True})
    wiki.set_package('cannabis', False)
    state = tools.reading_state('en')
    assert not state['bookmarks'] and ARTICLE not in state['articles']
    for action in (lambda: tools.history(ARTICLE), lambda: tools.lookup(ARTICLE)):
        with pytest.raises(tools.Invalid, match='invalid_article'):
            action()
    assert not any(i['id'] in ('stippling', 'interveinal', 'larva', 'mold') for i in tools.atlas('en')['items'])
    wiki.set_package('cannabis', True)
    assert tools.reading_state('en')['bookmarks'] == [ARTICLE]


def test_corrections_require_review_and_keep_old_claims_with_sources():
    base = revision()
    pending = tools.propose(proposal(base))
    stale = tools.propose(proposal(base, value=23))
    assert len(tools.history(ARTICLE)['revisions']) == 1
    assert tw.get('/api/wiki/card/' + ARTICLE, lang='en')[1]['card']['reference']['thc_percent'] == 21
    tools.review({'id': pending, 'decision': 'accepted', 'note': 'Checked against reference'})
    card = tw.get('/api/wiki/card/' + ARTICLE, lang='en')[1]
    assert card['card']['reference']['thc_percent'] == 22
    claims = card['evidence']['thc_percent']
    assert claims['selected'] == 22 and claims['disputed']
    assert {c['value'] for c in claims['claims']} == {21, 22}
    assert claims['claims'][-1]['source']['url'] == 'https://example.org/reference'
    rows = tw.get('/api/wiki/varieties', lang='en')[1]['varieties']
    assert next(r for r in rows if r['key'] == 'blue-dream')['reference']['thc_percent'] == 22
    with pytest.raises(tools.Invalid, match='stale_correction'):
        tools.review({'id': stale, 'decision': 'accepted', 'note': 'Stale proposal'})
    tools.review({'id': stale, 'decision': 'rejected', 'note': 'Superseded'})
    with pytest.raises(tools.Invalid, match='stale_correction'):
        tools.propose(proposal(base))
    history = tools.history(ARTICLE)
    assert [r['reason'] for r in history['revisions']] == ['reviewed_correction', 'first_observed']
    assert len(tools.history(ARTICLE)['revisions']) == 2
    edit = history['revisions'][0]['snapshot']['reviewed'][0]
    assert edit['source'] == 'https://example.org/reference'


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), -1, 101, '22'])
def test_invalid_facts_cannot_be_reviewed(value):
    with pytest.raises(tools.Invalid, match='invalid_value'):
        tools.propose(proposal(revision(), value=value))


@pytest.mark.parametrize('url', ['http://example.org', 'javascript:alert(1)', 'https://user:pass@example.org', 'https://example.org:8443', 'https://example.org/a b'])
def test_invalid_sources_cannot_enter_correction_queue(url):
    with pytest.raises(tools.Invalid, match='invalid_source'):
        tools.propose(proposal(revision(), source=url))


def test_real_source_disagreement_gallery_and_atlas_are_attributed():
    d = tw.get('/api/wiki/card/varieties/ice-cream-cake', lang='en')[1]
    assert d['evidence']['classification']['disputed']
    assert {c['value'] for c in d['evidence']['classification']['claims']} == {'hybrid', 'indica'}
    assert len(d['gallery']) >= 2
    assert all(p['source'].startswith('https://www.leafly.com/') for p in d['gallery'])
    atlas = tools.atlas('en')['items']
    assert len(atlas) == 6
    assert all(i['photo']['credit']['license_url'].startswith('https://') for i in atlas)
    assert all(0 <= p['x'] <= 100 and 0 <= p['y'] <= 100 for i in atlas for p in i['markers'])


def test_all_tool_routes_need_cockpit_auth_and_mutations_have_validation():
    async def run():
        async with TestClient(TestServer(tw.cockpit())) as client:
            for path in ('state', 'editorial', 'atlas', 'history?article=' + ARTICLE):
                response = await client.get('/api/wiki/tools/' + path, allow_redirects=False)
                assert response.status in (302, 401, 403)
            client.session.cookie_jar.update_cookies({tw.COOKIE: 'k'})
            response = await client.post('/api/wiki/tools/state', json={'action': 'member', 'article': ARTICLE, 'selected': True, 'collection': 'missing'})
            assert response.status == 400
            response = await client.post('/api/wiki/tools/corrections/propose', json=proposal(revision()))
            assert response.status == 200
            assert (await response.json())['status'] == 'pending'
            response = await client.get('/api/wiki/tools/editorial')
            assert len((await response.json())['corrections']) == 1
    asyncio.run(run())


def test_public_address_validation_covers_ip_literals_and_resolved_hosts(monkeypatch):
    for url in ('https://127.0.0.1/a', 'https://[::1]/', 'https://192.168.1.82/', 'https://169.254.169.254/'):
        assert not tools._public_literal(url)
    assert tools._public_literal('https://example.org/')

    async def private(*args):
        return [{'host': '127.0.0.1'}]
    monkeypatch.setattr(tools.aiohttp.resolver.ThreadedResolver, 'resolve', private)
    async def run():
        with pytest.raises(OSError, match='non-public'):
            await tools.PublicResolver().resolve('example.org')
    asyncio.run(run())


def test_revision_detects_reverts_without_inventing_earlier_history():
    entry = tools.lookup(ARTICLE)
    with tools.database(write=True) as db:
        first = tools.observe(entry, db)
        assert first == tools.observe(entry, db)
        changed = {**entry, 'raw': {**entry['raw'], 'label': 'Updated name'}}
        second = tools.observe(changed, db)
        third = tools.observe(entry, db)
    assert first['digest'] == third['digest'] != second['digest']
    assert first['id'] < second['id'] < third['id']


def test_manual_link_check_uses_committed_urls_and_never_follows_redirects(monkeypatch):
    calls = []
    class Response:
        status = 302

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        def __init__(self, **kw):
            self.connector = kw['connector']
            assert kw['trust_env'] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.connector.close()

        def head(self, url, **kw):
            calls.append((url, kw))
            return Response()

    tools.propose(proposal(revision(), source='https://reviewer.example/should-never-be-fetched'))
    monkeypatch.setattr(tools.aiohttp, 'ClientSession', Session)
    result = asyncio.run(tools.check_links(ARTICLE))
    assert len(calls) == 2
    assert all(url.startswith('https://www.leafly.com/') and options == {'allow_redirects': False} for url, options in calls)
    assert all(row['status'] == 'redirect' for row in result)
    assert tools.editorial('en')['articles']
    with tools.database() as db:
        assert db.execute('SELECT COUNT(*) FROM link_checks').fetchone()[0] == 2


def test_history_filters_optional_package_sections_in_old_general_article_snapshots(tmp_path):
    lib = tmp_path / 'library'
    tw.write(lib / 'crops' / 'mint.json', {'key': 'mint', 'kind': 'crop', 'label': 'Mint',
             'facts': ['General reference [S1]', 'Hidden package section [S2]'], 'facts_packages': [None, 'cannabis'],
             'sources': {'S1': 'General: https://example.org/general', 'S2': 'Package: https://example.org/package'}})
    entry = tools.lookup('crops/mint', lib)
    with tools.database(write=True) as db:
        tools.observe(entry, db)
    wiki.set_package('cannabis', False)
    history = tools.history('crops/mint', lib)
    assert 'Hidden package section' not in str(history)
    assert '/package' not in str(history)
