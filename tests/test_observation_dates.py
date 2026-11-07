"""Observation dates are inventory facts, independent of entry dates and controller programs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from vgrow import workspace as ws
from vgrow.plant import PlantLog


@pytest.fixture
def observed(monkeypatch):
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 10, 26))
    profile = {'plant': {'strain': 'Basil', 'start_date': '2026-09-01'},
               'stages': [{'key': 'growing', 'label': 'Wachstum', 'label_en': 'Growing'}]}
    log = PlantLog()
    log.set_stage('growing', '2026-10-24')
    original = log.path.read_bytes()
    ws.initialize('Box', profile, log)
    return profile, log, original


def change(**body):
    return ws.mutate({'revision': ws.view()['revision'], **body})


def plant(ident=ws.NATIVE_PLANT, lang='en'):
    return next(row for row in ws.view(lang)['plants'] if row['id'] == ident)


def test_entry_date_is_not_an_inferred_observed_start(observed):
    p = plant()
    assert p['phase_recorded'] == '2026-10-24'
    assert p['phase_since'] is None and p['phase_age_days'] is None


def test_editing_names_and_measuring_height_do_not_restart_observed_duration(observed):
    change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil', phase='Growing',
           started='2026-09-01', phase_since='2026-10-19')
    assert plant()['phase_age_days'] == 7 and plant()['phase_recorded'] == '2026-10-24'
    change(action='height', plant=ws.NATIVE_PLANT, day='2026-10-26', cm=18)
    change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil renamed', phase='Wachstum', started='2026-09-01')
    assert plant()['phase_age_days'] == 7 and plant()['phase_recorded'] == '2026-10-24'
    assert plant(lang='en')['phase'] == 'Growing' and plant(lang='de')['phase'] == 'Wachstum'
    profile, log, original = observed
    ws.initialize('Box', profile, log)
    assert log.path.read_bytes() == original and log.data['stages'][0]['since'] == '2026-10-24'


def test_new_labels_do_not_inherit_old_observed_dates_without_an_explicit_date(observed):
    change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil', phase='Growing', phase_since='2026-10-19')
    change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil', phase='Resting')
    assert plant()['phase_since'] is None and plant()['phase_age_days'] is None
    assert plant()['phase_recorded'] == '2026-10-26'


def test_duration_advances_with_the_calendar_without_a_record_edit(observed, monkeypatch):
    change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil', phase='Growing', phase_since='2026-10-19')
    revision = ws.view()['revision']
    monkeypatch.setattr(ws, 'today', lambda: date(2026, 10, 27))
    assert plant()['phase_age_days'] == 8 and plant()['phase_recorded'] == '2026-10-24'
    assert ws.view()['revision'] == revision


@pytest.mark.parametrize('value,error', [('2026-10-27', 'workspace_date'), ('2026-08-31', 'workspace_before_start'), ('invalid', 'workspace_date')])
def test_invalid_observed_dates_do_not_change_records(observed, value, error):
    before = ws.view()
    with pytest.raises(ws.Invalid, match=error):
        change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil', phase='Growing', started='2026-09-01', phase_since=value)
    assert ws.view() == before


def test_a_stage_date_needs_a_stage(observed):
    before = ws.view()
    form = dict(action='plant_create', name='Mint', box=ws.NATIVE_BOX, origin='cutting', phase='', started='2026-10-26', notes='Auto')
    with pytest.raises(ws.Invalid, match='workspace_invalid'):
        change(**form, phase_since='2026-10-26')
    assert ws.view() == before
    assert plant(change(**form, phase_since=None))['phase_since'] is None


def test_dates_move_archive_and_restore_with_their_plant(observed):
    box = change(action='box_create', name='Shelf')
    ident = change(action='plant_create', name='Mint', box=box, phase='Observed', phase_since='2026-10-19', started='2026-10-01')
    change(action='plant_update', id=ident, name='Mint', box=ws.NATIVE_BOX, phase='Observed', started='2026-10-01')
    change(action='plant_archive', id=ident, archived=True)
    change(action='plant_archive', id=ident, archived=False)
    assert plant(ident)['phase_since'] == '2026-10-19' and plant(ident)['phase_age_days'] == 7


def test_migration_imports_only_the_entry_date_once_and_keeps_existing_measurements(observed):
    profile, log, original = observed
    change(action='height', plant=ws.NATIVE_PLANT, day='2026-10-26', cm=18)
    with ws.database(write=True) as db:
        db.execute('DROP TABLE phase_dates')
        db.execute("DELETE FROM meta WHERE key='phase_dates_initialized'")
    revision = ws.view()['revision']
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: ws.initialize('Box', profile, log), range(2)))
    p = plant()
    assert p['phase_recorded'] == '2026-10-24' and p['phase_since'] is None
    assert p['heights'] == [{'day': '2026-10-26', 'cm': 18}]
    assert ws.view()['revision'] == revision + 1
    assert log.path.read_bytes() == original


def test_unknown_entry_dates_remain_unknown_when_the_legacy_label_has_changed(observed):
    profile, log, _ = observed
    change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil', phase='Different label')
    with ws.database(write=True) as db:
        db.execute('DELETE FROM phase_dates')
        db.execute("DELETE FROM meta WHERE key='phase_dates_initialized'")
    ws.initialize('Box', profile, log)
    assert plant()['phase_recorded'] is None and plant()['phase_since'] is None
    change(action='plant_update', id=ws.NATIVE_PLANT, box=ws.NATIVE_BOX, name='Basil renamed', phase='Different label')
    assert plant()['phase_recorded'] is None
