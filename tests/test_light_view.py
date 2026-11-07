"""A light level and a plan flag never establish switch times. Recorded reports cannot fill long gaps."""
import asyncio
import time

from aiohttp.test_utils import TestClient, TestServer

from vgrow import config, server, workspace
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox
from vgrow.light_view import observations, schedule
from vgrow.measurements import MeasurementStore


def test_no_timetable_is_inferred_from_a_brightness_or_plan_flag():
    for light, expected in [({'level': 100, 'mode': 0, 'in_plan': False}, 'inactive'),
                            ({'level': 0, 'in_plan': True}, 'active'),
                            ({'level': 100, 'in_plan': False, 'in_plan_known': False}, 'unknown'),
                            ({'level': 100}, 'unknown'), ({'in_plan': 1}, 'unknown')]:
        view = schedule({'light': light})
        assert view['state'] == expected
        assert view['timings_available'] is False
        assert all(view[key] is None for key in ('on_time', 'off_time', 'hours_on', 'next_switch'))


def test_reports_are_bounded_and_missing_values_or_long_gaps_remain_unknown():
    view = observations([{'time': 10, 'light': 100}, {'time': 50, 'light': None},
                         {'time': 500, 'light': 0}, {'time': 550, 'light': 0},
                         {'time': 800, 'light': 50}, {'time': 1000, 'light': 100}], 0, 1000)
    assert view['readings'] == 4
    assert view['segments'] == [
        {'start': 0, 'end': 10, 'level': None}, {'start': 10, 'end': 50, 'level': 100},
        {'start': 50, 'end': 500, 'level': None}, {'start': 500, 'end': 640, 'level': 0},
        {'start': 640, 'end': 800, 'level': None}, {'start': 800, 'end': 890, 'level': 50},
        {'start': 890, 'end': 1000, 'level': None}]
    assert view['coverage_percent'] == 27


def test_invalid_reports_never_look_like_light_or_darkness():
    view = observations([{'time': 0, 'light': True}, {'time': 20, 'light': -1}, {'time': 30, 'light': 101},
                         {'time': 40, 'light': float('nan')}, {'time': float('inf'), 'light': 100}], 0, 100)
    assert view['readings'] == 0 and view['coverage_percent'] == 0
    assert view['segments'] == [{'start': 0, 'end': 100, 'level': None}]


def test_authenticated_light_view_reads_local_data_and_refuses_another_boxes_context():
    now = time.time()
    store = MeasurementStore(config.STATE_DIR / 'measurements.sqlite3')
    store.add([{'ts': int(now - 180), 'bTemp': 2000, 'light': 80},
               {'ts': int(now - 90), 'bTemp': 2000, 'light': 0}], source='poll')
    box = DemoBox()
    commands = []
    async def reject(*args):
        commands.append(args)
        raise AssertionError('No command is allowed')
    box.set_light = reject
    box.refresh_shadow = reject

    async def run():
        async with TestClient(TestServer(server.make_app(box, Alerts(), 'k', measurements=store))) as client:
            response = await client.get('/api/light')
            assert response.status == 403
            client.session.cookie_jar.update_cookies({server.COOKIE: 'k'})
            inventory = await (await client.get('/api/workspace')).json()
            response = await client.get('/api/light', headers={'X-Workspace-Box': workspace.NATIVE_BOX})
            assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
            data = await response.json()
            assert data['history']['readings'] == 2 and data['schedule']['hours_on'] is None
            assert any(row['level'] == 0 for row in data['history']['segments'])
            assert set(data['balance']) == {'lit_hours', 'dark_hours', 'measured_hours'}
            other = await (await client.post('/api/workspace', json={'action': 'box_create', 'name': 'Other shelf', 'revision': inventory['revision']})).json()
            response = await client.get('/api/light', headers={'X-Workspace-Box': other['created']})
            assert response.status == 409 and (await response.json())['error'] == 'workspace_unbound'
    asyncio.run(run())
    assert not commands


def test_empty_store_and_broken_store_do_not_invent_a_timetable():
    class Broken:
        def rows(self, *_):
            raise OSError('Unavailable')
    async def run(store, status):
        async with TestClient(TestServer(server.make_app(DemoBox(), Alerts(), 'k', measurements=store))) as client:
            client.session.cookie_jar.update_cookies({server.COOKIE: 'k'})
            response = await client.get('/api/light')
            assert response.status == status
            data = await response.json()
            if status == 200:
                assert data['history']['readings'] == 0
                assert data['schedule']['on_time'] is None
    asyncio.run(run(None, 200))
    asyncio.run(run(Broken(), 503))
