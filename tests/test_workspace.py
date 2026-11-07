"""Box/plant identity, migration, durable observations and command context isolation."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, server, workspace as ws
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.plant import PlantLog


@pytest.fixture
def inventory(monkeypatch):
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 10, 26))
    profile = {'plant': {'strain': 'Basil', 'start_date': '2026-09-01', 'start_approx': True},
               'stages': [{'key': 'growing', 'label': 'Wachstum', 'label_en': 'Growing'}],
               'stage_estimate': {'key': 'growing', 'since': '2026-10-01'}}
    log = PlantLog()
    log.add_height(10, '2026-10-23')
    monkeypatch.setattr(server, 'load_profile', lambda: profile)
    ws.initialize('Existing box', profile, log)
    return profile, log


def change(**body):
    return ws.mutate({'revision': ws.view()['revision'], **body})


def add(box=ws.NATIVE_BOX, **kw):
    return change(action='plant_create', box=box, name='Mint', variety='Mentha', phase='Growing', started='2026-10-01', **kw)


def test_import_keeps_original_files_and_runs_once(inventory):
    profile, log = inventory
    original = log.path.read_bytes()
    state = ws.view('en')
    assert len(state['boxes']) == len(state['plants']) == 1
    p = state['plants'][0]
    assert p['name'] == 'Basil' and p['phase'] == 'Growing' and p['approximate']
    assert p['heights'] == [{'day': '2026-10-23', 'cm': 10}]
    assert 'preset' not in p and 'targets' not in p
    change(action='plant_update', id=p['id'], box=p['box'], name='Basil record', phase='Observed', started=p['started'])
    change(action='height', plant=p['id'], day='2026-10-26', cm=12)
    ws.initialize('Other name', profile, log)
    assert ws.view()['plants'][0]['name'] == 'Basil record'
    assert log.path.read_bytes() == original and log.data['heights'][0]['cm'] == 10
    assert ws.view()['boxes'][0]['name'] == 'Existing box'


def test_six_independent_plants_can_move_without_losing_their_records(inventory):
    box = change(action='box_create', name='Large enclosure', kind='Cabinet', capacity=6)
    last = add(box, count=6)
    state = ws.view()
    plants = [p for p in state['plants'] if p['box'] == box]
    assert len(plants) == 6 and len({p['id'] for p in plants}) == 6
    assert [p['name'] for p in plants] == [f'Mint {i}' for i in range(1, 7)]
    assert next(b for b in state['boxes'] if b['id'] == box)['plant_count'] == 6
    change(action='height', plant=last, day='2026-10-26', cm=17)
    change(action='note', plant=last, day='2026-10-26', text='Moved the label to the front')
    change(action='plant_update', id=last, box=ws.NATIVE_BOX, name='Moved mint', phase='Resting', started='2026-10-01')
    state = ws.view()
    moved = next(p for p in state['plants'] if p['id'] == last)
    assert moved['box'] == ws.NATIVE_BOX and moved['phase'] == 'Resting'
    assert len(moved['heights']) == len(moved['journal']) == 1
    assert all(not p['heights'] and not p['journal'] for p in state['plants'] if p['box'] == box)


@pytest.mark.parametrize('source,approximate', [('date', False), ('age_weeks', True)])
def test_import_respects_owner_date_precision_and_excludes_previous_run(monkeypatch, source, approximate):
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 10, 26))
    profile = {'plant': {'strain': 'Basil', 'start_date': '2026-09-01', 'start_approx': True}}
    log = PlantLog()
    log.data['start_date'] = {'date': '2026-10-01', 'run': '2026-09-01', 'from': source}
    log.add_height(5, '2026-09-15')
    log.add_height(10, '2026-10-23')
    ws.initialize('Shelf', profile, log)
    plant = ws.view()['plants'][0]
    assert plant['started'] == '2026-10-01' and bool(plant['approximate']) is approximate
    assert plant['heights'] == [{'day': '2026-10-23', 'cm': 10}]


def test_archive_restore_is_non_destructive_and_connected_profile_cannot_move(inventory):
    box = change(action='box_create', name='Shelf')
    plant = add(box)
    change(action='note', plant=plant, day='2026-10-26', text='Inventory note')
    change(action='box_archive', id=box, archived=True)
    with pytest.raises(ws.Invalid, match='workspace_missing'):
        change(action='height', plant=plant, day='2026-10-26', cm=4)
    change(action='box_archive', id=box, archived=False)
    change(action='plant_archive', id=plant, archived=True)
    change(action='plant_archive', id=plant, archived=False)
    assert next(p for p in ws.view()['plants'] if p['id'] == plant)['journal'][0]['text'] == 'Inventory note'
    for body in ({'action': 'box_archive', 'id': ws.NATIVE_BOX, 'archived': True},
                 {'action': 'plant_archive', 'id': ws.NATIVE_PLANT, 'archived': True},
                 {'action': 'plant_update', 'id': ws.NATIVE_PLANT, 'box': box, 'name': 'Moved'}):
        with pytest.raises(ws.Invalid, match='workspace_linked'):
            change(**body)


def test_stale_concurrent_edits_cannot_overwrite_each_other(inventory):
    revision = ws.view()['revision']
    def edit(name):
        try:
            ws.mutate({'revision': revision, 'action': 'box_update', 'id': ws.NATIVE_BOX, 'name': name})
            return 'saved'
        except ws.Invalid as err:
            return str(err)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(edit, ('First', 'Second')))
    assert sorted(outcomes) == ['saved', 'workspace_stale']
    assert ws.view()['revision'] == revision + 1


@pytest.mark.parametrize('cm', [None, True, 0, 0.001, -2, float('nan'), float('inf'), 10001, '20'])
def test_invalid_heights_do_not_change_any_record(inventory, cm):
    version = ws.view()['revision']
    with pytest.raises(ws.Invalid, match='workspace_height'):
        change(action='height', plant=ws.NATIVE_PLANT, day='2026-10-26', cm=cm)
    assert ws.view()['revision'] == version


def test_dates_corrections_and_journals_stay_with_the_right_plant(inventory):
    p = add()
    change(action='height', plant=p, day='2026-10-26', cm=10)
    change(action='height', plant=p, day='2026-10-26', cm=11)
    assert next(x for x in ws.view()['plants'] if x['id'] == p)['heights'] == [{'day': '2026-10-26', 'cm': 11}]
    with pytest.raises(ws.Invalid, match='workspace_before_start'):
        change(action='note', plant=p, day='2026-09-30', text='Before start')
    with pytest.raises(ws.Invalid, match='workspace_date'):
        change(action='note', plant=p, day='2026-10-27', text='Future')
    with pytest.raises(ws.Invalid, match='workspace_date_conflict'):
        change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil', started='2026-10-25')


def test_authenticated_api_never_sends_a_command_for_an_unconnected_box(inventory):
    _, log = inventory
    box = DemoBox()
    calls = []
    async def reject(*args):
        calls.append(args)
        raise AssertionError('No actuator should be reached')
    box.set_light = reject
    async def run():
        async with TestClient(TestServer(server.make_app(box, Alerts(), 'k', plant_log=log))) as client:
            response = await client.get('/api/workspace')
            assert response.status == 403
            client.session.cookie_jar.update_cookies({server.COOKIE: 'k'})
            response = await client.post('/api/workspace', json={'revision': ws.view()['revision'], 'action': 'box_create', 'name': 'Second box'})
            assert response.status == 200
            second = (await response.json())['created']
            response = await client.post('/api/cmd', json={'action': 'light', 'level': 0}, headers={'X-Workspace-Box': second})
            assert response.status == 409 and (await response.json())['error'] == 'workspace_unbound'
            response = await client.post('/api/cmd', json={'action': 'set_lang', 'lang': 'en'},
                                         headers={'X-Workspace-Box': second})
            assert response.status == 200 and (await response.json())['server_lang'] == 'en'
            plant = add()
            response = await client.post('/api/cmd', json={'action': 'plant_height', 'cm': 20},
                                         headers={'X-Workspace-Box': ws.NATIVE_BOX, 'X-Workspace-Plant': plant})
            assert response.status == 409 and (await response.json())['error'] == 'workspace_profile'
            response = await client.post('/api/hank/ask', json={'question': 'This plant?'},
                                         headers={'X-Workspace-Box': ws.NATIVE_BOX, 'X-Workspace-Plant': plant})
            assert response.status == 409 and (await response.json())['error'] == 'workspace_profile'
            response = await client.post('/api/workspace', json={'revision': 0, 'action': 'box_create', 'name': 'Stale'})
            assert response.status == 409
    asyncio.run(run())
    assert not calls and log.data['heights'][-1]['cm'] == 10


def test_unreadable_storage_does_not_reset_existing_records(inventory):
    path = config.STATE_DIR / 'workspace' / 'inventory.sqlite3'
    path.write_bytes(b'not a sqlite database')
    async def run():
        async with TestClient(TestServer(server.make_app(DemoBox(), Alerts(), 'k'))) as client:
            client.session.cookie_jar.update_cookies({server.COOKIE: 'k'})
            response = await client.get('/api/workspace')
            assert response.status == 503 and (await response.json())['error'] == 'workspace_storage'
    asyncio.run(run())
    assert path.read_bytes() == b'not a sqlite database'
