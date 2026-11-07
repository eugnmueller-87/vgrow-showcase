"""Completed generic records keep their own observations, shared sensor provenance and independent successors."""
import asyncio
import json
from datetime import date, datetime
from zipfile import ZipFile

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, server, workspace as ws
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.measurements import MeasurementStore
from vgrow.plant import PlantLog
from vgrow.record_export import build_export


@pytest.fixture
def records(monkeypatch):
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 10, 26))
    profile = {'plant': {'strain': 'Basil', 'start_date': '2026-10-01'},
               'stages': [{'key': 'growing', 'label': 'Wachstum', 'label_en': 'Growing'}]}
    log = PlantLog()
    log.set_stage('growing', '2026-10-24')
    original = log.path.read_bytes()
    ws.initialize('Shelf', profile, log, device='device-a')
    return profile, log, original


def change(**body):
    return ws.mutate({'revision': ws.view()['revision'], **body}, device='device-a')


def create(name='Mint', **body):
    return change(action='plant_create', box=ws.NATIVE_BOX, name=name, started='2026-10-01', phase='Observed', **body)


def finish(ident, **body):
    return change(action='plant_finish', id=ident, ended=body.pop('ended', '2026-10-26'), confirmed=True, **body)


def test_finishing_the_imported_record_keeps_the_original_log_and_new_records_separate(records, monkeypatch):
    _, log, original = records
    change(action='height', plant=ws.NATIVE_PLANT, day='2026-10-25', cm=18)
    change(action='note', plant=ws.NATIVE_PLANT, day='2026-10-26', text='Final observation')
    finish(ws.NATIVE_PLANT, summary='Stored for later comparison')
    saved = ws.record(ws.NATIVE_PLANT)
    replacement = create('Basil 2', origin='existing')
    change(action='height', plant=replacement, day='2026-10-26', cm=12)
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 11, 1))
    rows = {row['id']: row for row in ws.view()['plants']}
    assert rows[ws.NATIVE_PLANT]['status'] == 'completed'
    assert rows[ws.NATIVE_PLANT]['age_days'] == 25
    assert not rows[ws.NATIVE_PLANT]['linked_profile']
    assert rows[replacement]['status'] == 'active' and rows[replacement]['heights'][0]['cm'] == 12
    assert ws.record(ws.NATIVE_PLANT) == saved
    assert log.path.read_bytes() == original
    assert ws.view()['boxes'][0]['plant_count'] == 1


@pytest.mark.parametrize('action,fields', [('height', {'plant': ws.NATIVE_PLANT, 'day': '2026-10-26', 'cm': 20}),
                                         ('note', {'plant': ws.NATIVE_PLANT, 'day': '2026-10-26', 'text': 'Changed'}),
                                         ('plant_update', {'id': ws.NATIVE_PLANT, 'box': ws.NATIVE_BOX, 'name': 'Changed'}),
                                         ('plant_archive', {'id': ws.NATIVE_PLANT, 'archived': False}),
                                         ('plant_finish', {'id': ws.NATIVE_PLANT, 'ended': '2026-10-26', 'confirmed': True})])
def test_completed_records_are_read_only(records, action, fields):
    finish(ws.NATIVE_PLANT)
    before = ws.record(ws.NATIVE_PLANT)
    revision = ws.view()['revision']
    with pytest.raises(ws.Invalid, match='workspace_completed'):
        change(action=action, **fields)
    assert ws.record(ws.NATIVE_PLANT) == before and ws.view()['revision'] == revision
    with pytest.raises(ws.Invalid, match='workspace_completed'):
        ws.validate_context(ws.NATIVE_BOX, ws.NATIVE_PLANT)


def test_finished_dates_cannot_cut_off_existing_observations_and_require_confirmation(records):
    change(action='note', plant=ws.NATIVE_PLANT, day='2026-10-25', text='Keep this')
    before = ws.view()
    for fields, error in [({'ended': '2026-10-24', 'confirmed': True}, 'workspace_date_conflict'),
                          ({'ended': '2026-10-27', 'confirmed': True}, 'workspace_date'),
                          ({'ended': '2026-10-26'}, 'workspace_invalid')]:
        with pytest.raises(ws.Invalid, match=error):
            change(action='plant_finish', id=ws.NATIVE_PLANT, **fields)
        assert ws.view() == before


def test_complete_export_keeps_observations_beyond_the_ui_limit_and_prior_phase_labels(records):
    ident = create(origin='cutting')
    with ws.database(write=True) as db:
        for index in range(110):
            db.execute('INSERT INTO journal VALUES(?,?,?,?,?)', (f'j-{index}', ident, '2026-10-25', f'Observation {index}', index))
    change(action='plant_update', id=ident, box=ws.NATIVE_BOX, name='Mint', started='2026-10-01', phase='Second observation')
    finish(ident)
    report = ws.record(ident)
    assert len(report['record']['journal']) == 110
    assert report['record']['origin'] == 'cutting'
    assert report['events'][0]['data']['phase'] == 'Observed'
    assert report['events'][1]['data']['after']['phase']['en'] == 'Second observation'
    path = build_export(report, None)
    try:
        with ZipFile(path) as archive:
            assert json.loads(archive.read('record.json')) == report
            assert json.loads(archive.read('manifest.json'))['intervals'][0]['available'] is False
    finally:
        path.unlink()


def test_export_respects_box_intervals_and_device_identity(records, monkeypatch):
    base = datetime(2026, 10, 26, 10).timestamp()
    monkeypatch.setattr(ws.time, 'time', lambda: base + .5)
    ident = create()
    store = MeasurementStore(config.STATE_DIR / 'measurements.sqlite3', clock=lambda: base + 3600)
    store.add_rows([{'time': base, 'bTemp': 2000}, {'time': base + 60, 'bTemp': 2100}, {'time': base + 180, 'bTemp': 2200}], device_id='device-a')
    second_box = change(action='box_create', name='Unconnected shelf')
    monkeypatch.setattr(ws.time, 'time', lambda: base + 120)
    change(action='plant_update', id=ident, box=second_box, name='Mint', phase='Observed')
    monkeypatch.setattr(ws.time, 'time', lambda: base + 240)
    finish(ident)
    # The current controller changes; the old record must still read device-a, never device-b.
    store.add_rows([{'time': base + 60, 'bTemp': 9900}], device_id='device-b')
    path = build_export(ws.record(ident), store)
    try:
        with ZipFile(path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['intervals'][0]['rows'] == 1
            assert manifest['intervals'][1]['available'] is False
            rows = [row for name in archive.namelist() if name.startswith('shared-box/') for row in json.loads(archive.read(name))]
            assert len(rows) == 1 and rows[0]['time'] == base + 60 and rows[0]['bTemp'] == 21
    finally:
        path.unlink()


def test_migration_and_restart_never_reset_records_or_make_up_old_box_assignments(records):
    profile, log, original = records
    finish(ws.NATIVE_PLANT)
    before = ws.record(ws.NATIVE_PLANT)
    revision = ws.view()['revision']
    ws.initialize('Shelf renamed', profile, log, device='device-a')
    assert ws.view()['revision'] == revision
    assert ws.record(ws.NATIVE_PLANT) == before and log.path.read_bytes() == original
    assert len(before['box_intervals']) == 1
    assert before['box_intervals'][0]['since'] >= ws.view()['plants'][0]['created']


def test_completion_before_tracking_has_an_empty_sensor_interval_instead_of_a_reversed_one(records):
    finish(ws.NATIVE_PLANT, ended='2026-10-24')
    place = ws.record(ws.NATIVE_PLANT)['box_intervals'][0]
    assert place['until'] == place['since']


def test_import_baseline_keeps_original_values_when_later_corrected(records):
    profile, log, _ = records
    with ws.database(write=True) as db:
        db.execute('INSERT INTO heights VALUES(?,?,?)', (ws.NATIVE_PLANT, '2026-10-25', 10))
        db.execute('DELETE FROM record_events')
        db.execute('DELETE FROM record_places')
        db.execute("DELETE FROM meta WHERE key='records_initialized'")
    ws.initialize('Shelf', profile, log, device='device-a')
    change(action='height', plant=ws.NATIVE_PLANT, day='2026-10-25', cm=12)
    report = ws.record(ws.NATIVE_PLANT)
    assert report['events'][0]['data']['baseline']['heights'] == [{'day': '2026-10-25', 'cm': 10}]
    assert report['record']['heights'] == [{'day': '2026-10-25', 'cm': 12}]


def test_history_api_is_authenticated_and_never_issues_device_commands(records):
    _, log, _ = records
    async def run():
        async with TestClient(TestServer(server.make_app(DemoBox(), Alerts(), 'k', plant_log=log))) as client:
            response = await client.get('/api/workspace/records/existing-plant')
            assert response.status == 403
            client.session.cookie_jar.update_cookies({server.COOKIE: 'k'})
            response = await client.get('/api/workspace/records/existing-plant')
            assert response.status == 200 and (await response.json())['record']['id'] == ws.NATIVE_PLANT
            response = await client.get('/api/workspace/records/existing-plant/export')
            assert response.status == 200 and response.content_type == 'application/zip'
            assert (await response.read()).startswith(b'PK')
            response = await client.get('/api/workspace/records/missing')
            assert response.status == 404
    asyncio.run(run())
