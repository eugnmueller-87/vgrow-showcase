"""After the harvest: the plant leaves its box, moves through drying, curing and ready, and the archive keeps all of it."""
import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import server, workspace as ws
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.plant import PlantLog


@pytest.fixture
def records(monkeypatch):
    return set_up(monkeypatch)


def set_up(monkeypatch):
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 11, 1))
    profile = {'plant': {'strain': 'Ice Cream Cake', 'start_date': '2026-09-10'},
               'stages': [{'key': 'flower', 'label': 'Blüte', 'label_en': 'Flower'}]}
    log = PlantLog()
    log.set_stage('flower', '2026-10-20')
    ws.initialize('VGrow Y18', profile, log, device='device-a')
    return log


def change(**body):
    return ws.mutate({'revision': ws.view()['revision'], **body}, device='device-a')


def row(ident):
    return next(p for p in ws.view()['plants'] if p['id'] == ident)


def test_harvest_frees_the_box_and_stops_the_age(records, monkeypatch):
    change(action='height', plant=ws.NATIVE_PLANT, day='2026-10-28', cm=80)
    change(action='plant_harvest', id=ws.NATIVE_PLANT, day='2026-10-30')
    plant = row(ws.NATIVE_PLANT)
    assert plant['status'] == 'drying' and plant['harvested'] == '2026-10-30'
    assert plant['post_since'] == '2026-10-30' and plant['post_days'] == 2
    assert plant['age_days'] == 50  # 10.09. to the harvest, not to today
    assert ws.view()['boxes'][0]['plant_count'] == 0
    place = ws.record(ws.NATIVE_PLANT)['box_intervals'][0]
    assert place['until'] is not None
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 11, 9))
    assert row(ws.NATIVE_PLANT)['age_days'] == 50 and row(ws.NATIVE_PLANT)['post_days'] == 10


def test_stages_move_on_and_back_with_their_own_days(records):
    change(action='plant_harvest', id=ws.NATIVE_PLANT, day='2026-10-25')
    change(action='plant_stage', id=ws.NATIVE_PLANT, stage='curing', day='2026-10-31')
    assert row(ws.NATIVE_PLANT)['status'] == 'curing' and row(ws.NATIVE_PLANT)['post_days'] == 1
    change(action='plant_stage', id=ws.NATIVE_PLANT, stage='drying', day='2026-10-31')  # buds still too wet
    change(action='plant_stage', id=ws.NATIVE_PLANT, stage='ready', day='2026-11-01')
    assert row(ws.NATIVE_PLANT)['status'] == 'ready' and row(ws.NATIVE_PLANT)['harvested'] == '2026-10-25'
    kinds = [(e['kind'], e['data'].get('to')) for e in ws.record(ws.NATIVE_PLANT)['events'][1:]]
    assert kinds == [('harvested', None), ('stage', 'curing'), ('stage', 'drying'), ('stage', 'ready')]


@pytest.mark.parametrize('fields,error', [
    ({'action': 'plant_stage', 'stage': 'curing', 'day': '2026-10-31'}, 'workspace_not_harvested'),
    ({'action': 'plant_harvest', 'day': '2026-11-02'}, 'workspace_date'),
    ({'action': 'plant_harvest', 'day': '2026-09-01'}, 'workspace_date_conflict'),
    ({'action': 'plant_harvest'}, 'workspace_date'),
])
def test_harvest_and_stage_dates_are_checked(records, fields, error):
    before = ws.view()
    with pytest.raises(ws.Invalid, match=error):
        change(id=ws.NATIVE_PLANT, **fields)
    assert ws.view() == before


def test_a_harvest_cannot_cut_off_a_height_measured_later(records):
    change(action='height', plant=ws.NATIVE_PLANT, day='2026-10-30', cm=80)
    with pytest.raises(ws.Invalid, match='workspace_date_conflict'):
        change(action='plant_harvest', id=ws.NATIVE_PLANT, day='2026-10-29')


@pytest.mark.parametrize('fields,error', [
    ({'action': 'plant_harvest', 'id': ws.NATIVE_PLANT, 'day': '2026-10-31'}, 'workspace_harvested'),
    ({'action': 'plant_stage', 'id': ws.NATIVE_PLANT, 'stage': 'drying', 'day': '2026-10-31'}, 'workspace_invalid'),
    ({'action': 'plant_stage', 'id': ws.NATIVE_PLANT, 'stage': 'eaten', 'day': '2026-10-31'}, 'workspace_invalid'),
    ({'action': 'plant_stage', 'id': ws.NATIVE_PLANT, 'stage': 'curing', 'day': '2026-10-29'}, 'workspace_date_conflict'),
    ({'action': 'height', 'plant': ws.NATIVE_PLANT, 'day': '2026-10-31', 'cm': 81}, 'workspace_harvested'),
])
def test_a_harvested_plant_has_no_second_harvest_and_no_heights(records, fields, error):
    change(action='plant_harvest', id=ws.NATIVE_PLANT, day='2026-10-30')
    with pytest.raises(ws.Invalid, match=error):
        change(**fields)


def test_notes_and_edits_go_on_but_the_plant_never_moves_back_into_a_box(records):
    plant = change(action='plant_create', box=ws.NATIVE_BOX, name='Purple Punch', started='2026-09-20', phase='Flower')
    change(action='plant_harvest', id=plant, day='2026-10-30')
    change(action='note', plant=plant, day='2026-10-31', text='Stems bend, not snap yet')
    change(action='plant_update', id=plant, box=ws.NATIVE_BOX, name='Purple Punch #1', phase='Flower')
    assert row(plant)['journal'][0]['text'] == 'Stems bend, not snap yet' and row(plant)['name'] == 'Purple Punch #1'
    other = change(action='box_create', name='Tent 2')
    with pytest.raises(ws.Invalid, match='workspace_harvested'):
        change(action='plant_update', id=plant, box=other, name='Purple Punch #1', phase='Flower')
    assert len(ws.record(plant)['box_intervals']) == 1


def test_the_archive_keeps_the_stages_and_cannot_predate_the_last_one(records):
    change(action='plant_harvest', id=ws.NATIVE_PLANT, day='2026-10-20')
    change(action='plant_stage', id=ws.NATIVE_PLANT, stage='curing', day='2026-10-28')
    with pytest.raises(ws.Invalid, match='workspace_date_conflict'):
        change(action='plant_finish', id=ws.NATIVE_PLANT, ended='2026-10-27', confirmed=True)
    change(action='plant_finish', id=ws.NATIVE_PLANT, ended='2026-10-31', confirmed=True, summary='Into the archive')
    plant = row(ws.NATIVE_PLANT)
    assert plant['status'] == 'completed' and plant['post_stage'] == 'curing' and plant['post_days'] == 3
    saved = ws.record(ws.NATIVE_PLANT)['record']
    assert (saved['harvested'], saved['post_stage'], saved['post_since']) == ('2026-10-20', 'curing', '2026-10-28')
    with pytest.raises(ws.Invalid, match='workspace_completed'):
        change(action='plant_stage', id=ws.NATIVE_PLANT, stage='ready', day='2026-11-01')


def test_harvest_ends_the_box_interval_on_the_harvest_day_not_today(records, monkeypatch):
    from vgrow import harvest
    clock = {'now': datetime(2026, 10, 28, 9).timestamp()}
    monkeypatch.setattr(ws.time, 'time', lambda: clock['now'])
    monkeypatch.setattr(harvest.time, 'time', lambda: clock['now'])
    plant = change(action='plant_create', box=ws.NATIVE_BOX, name='Purple Punch', started='2026-09-20')
    clock['now'] = datetime(2026, 11, 1, 12).timestamp()
    change(action='plant_harvest', id=plant, day='2026-10-30')
    place = ws.record(plant)['box_intervals'][0]
    assert place['until'] == datetime(2026, 10, 31, tzinfo=ZoneInfo('Europe/Berlin')).timestamp()  # midnight after the harvest day, Berlin time


def test_the_api_moves_stages_and_never_sends_a_device_command(records):
    async def run():
        box = DemoBox()
        sent = []
        for name in ('set_light', 'set_usb', 'set_fan'):
            if hasattr(box, name):
                original = getattr(box, name)
                async def spy(*args, _name=name, _original=original, **kwargs):
                    sent.append(_name)
                    return await _original(*args, **kwargs)
                setattr(box, name, spy)
        async with TestClient(TestServer(server.make_app(box, Alerts(), 'k', plant_log=records))) as client:
            client.session.cookie_jar.update_cookies({server.COOKIE: 'k'})
            revision = (await (await client.get('/api/workspace')).json())['revision']
            response = await client.post('/api/workspace', json={'action': 'plant_harvest', 'id': ws.NATIVE_PLANT,
                                                                  'day': '2026-10-30', 'revision': revision})
            data = await response.json()
            assert response.status == 200 and data['plants'][0]['status'] == 'drying'
            response = await client.post('/api/workspace', json={'action': 'plant_stage', 'id': ws.NATIVE_PLANT, 'stage': 'nope',
                                                                  'day': '2026-10-31', 'revision': data['revision']})
            assert response.status == 400 and (await response.json())['error'] == 'workspace_invalid'
        assert sent == []
    asyncio.run(run())


def box(ident=ws.NATIVE_BOX):
    return next(b for b in ws.view()['boxes'] if b['id'] == ident)


def test_an_empty_box_gets_a_fresh_list_each_time_it_empties(monkeypatch):
    from vgrow import harvest
    # the clock before the box is set up: set up at the real time, the box's interval began after 9:00 on any later run
    clock = {'now': datetime(2026, 11, 1, 8).timestamp()}
    monkeypatch.setattr(harvest.time, 'time', lambda: clock['now'])
    monkeypatch.setattr(ws.time, 'time', lambda: clock['now'])
    set_up(monkeypatch)
    clock['now'] = datetime(2026, 11, 1, 9).timestamp()
    change(action='plant_harvest', id=ws.NATIVE_PLANT, day='2026-11-01')
    assert box()['prep'] == [] and box()['emptied'] == clock['now']
    clock['now'] += 60
    change(action='box_prep', box=ws.NATIVE_BOX, item='clean', done=True)
    change(action='box_prep', box=ws.NATIVE_BOX, item='debris', done=True)
    change(action='box_prep', box=ws.NATIVE_BOX, item='clean', done=False)
    assert box()['prep'] == ['debris']
    # the next plant moves in and is harvested a day later: the old tick belongs to the last turnover
    clock['now'] += 60
    plant = change(action='plant_create', box=ws.NATIVE_BOX, name='Purple Punch', started='2026-11-01')
    clock['now'] = datetime(2026, 11, 2, 9).timestamp()
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 11, 2))
    change(action='plant_harvest', id=plant, day='2026-11-02')
    assert box()['prep'] == []


@pytest.mark.parametrize('fields', [{'item': 'polish', 'done': True}, {'item': 'clean', 'done': 'yes'}, {'item': 'clean'}])
def test_the_list_takes_only_its_own_steps(records, fields):
    before = ws.view()
    with pytest.raises(ws.Invalid, match='workspace_invalid'):
        change(action='box_prep', box=ws.NATIVE_BOX, **fields)
    with pytest.raises(ws.Invalid, match='workspace_missing'):
        change(action='box_prep', box='box-nope', item='clean', done=True)
    assert ws.view() == before
