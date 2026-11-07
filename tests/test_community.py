"""Public metadata, durable fallback, rate limiting and account/device isolation."""
from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vgrow import community as c, server
from vgrow.alerts import Alerts
from vgrow.demo import DemoBox


def page(*features):
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({'props': {'pageProps': {'features': list(features)}}}) + '</script>'


def fixture_page():
    return page({'type': 'articleCards', 'list': [
        {'articleId': '1', 'articleCode': 'a-story', 'title': 'A community story', 'imgSrc': 'asset/picture/sample.jpg'},
        {'articleId': '1', 'articleCode': 'duplicate', 'title': 'Duplicate'},
        {'link': {'href': 'https://example.com'}, 'title': 'Not an official story'},
    ]}, {'type': 'tabsCelebrities', 'list': [{'name': 'A creator', 'videos': [
        {'iframe': {'src': 'https://www.youtube.com/embed/HZOcNR8eczc'}, 'cover': 'asset/picture/video.jpg'},
        {'iframe': {'src': 'https://evil.example/embed/12345678901'}},
        {'iframe': {'src': 'javascript:alert(1)'}},
        {'iframe': 'unexpected changed markup'},
        {'iframe': {'src': 123}},
    ]}]})


COUNTS = json.dumps({'guild': {'id': '123', 'name': 'Public server'}, 'approximate_member_count': 1200,
                     'approximate_presence_count': 85, 'private_messages': 'DO NOT STORE', 'user': {'token': 'DO NOT STORE'}})
EMPTY_RSS = '<feed xmlns="http://www.w3.org/2005/Atom"/>'


def test_only_official_metadata_and_supported_video_links_are_retained():
    items = c.parse_page(fixture_page(), 'de')
    assert len(items) == 2
    story, video = items
    assert story['url'] == 'https://vivosun.com/de-DE/community/article/a-story'
    assert story['image'] == 'https://image.next.vivosun.com/asset/picture/sample.jpg'
    assert video['video'] == 'HZOcNR8eczc' and video['url'] == 'https://www.youtube.com/watch?v=HZOcNR8eczc'
    assert not any(key in story for key in ('description', 'body', 'content', 'cookies'))


@pytest.mark.parametrize('value', ['http://image.next.vivosun.com/p.jpg', 'https://image.next.vivosun.com.evil/p.jpg',
                                 'https://user:password@image.next.vivosun.com/p.jpg', 'https://image.next.vivosun.com:444/p.jpg',
                                 'javascript:alert(1)', '//evil.example/asset/p.jpg', None])
def test_images_cannot_become_an_arbitrary_url(value):
    assert c.image_url(value) is None


def test_changed_or_empty_source_is_not_reported_as_a_successful_empty_feed():
    for value in ('<html>Login required</html>', page(), page({'type': 'articleCards', 'list': ['unexpected']})):
        with pytest.raises((ValueError, KeyError)):
            c.parse_page(value, 'en')


def test_discord_counts_exclude_users_and_private_fields():
    assert c.discord_counts(COUNTS) == {'name': 'Public server', 'members': 1200, 'online': 85}
    value = json.loads(COUNTS)
    value.update(approximate_member_count=-1, approximate_presence_count=True)
    assert c.discord_counts(json.dumps(value)) == {'name': 'Public server', 'members': None, 'online': None}


def test_cache_coalesces_clients_and_force_refresh_has_a_minimum_interval(tmp_path):
    calls, clock = [], [2000]
    async def fetch(_, url):
        calls.append(url)
        if '/invites/' in url:
            return COUNTS
        if '/oembed?' in url:
            return json.dumps({'title': 'A public video title'})
        if '.rss' in url:
            return EMPTY_RSS
        return fixture_page()
    service = c.Community(tmp_path, clock=lambda: clock[0], read=fetch)
    async def run():
        first, second = await asyncio.gather(service.view('en'), service.view('en'))
        assert first == second and len(calls) == 4
        await service.view('en', force=True)
        assert len(calls) == 4
        clock[0] += 61
        result = await service.view('en', force=True)
        assert len(calls) == 7  # video title reused, not fetched every time
        assert result['items'][1]['title'] == 'A public video title'
    asyncio.run(run())
    stored = ''.join(path.read_text(encoding='utf-8') for path in tmp_path.glob('*.json'))
    assert 'DO NOT STORE' not in stored and 'private_messages' not in stored


def test_outage_keeps_last_good_feed_and_counts_across_restart(tmp_path):
    clock, fail = [2000], [False]
    async def fetch(_, url):
        if fail[0]:
            raise c.SourceError(429)
        if '/invites/' in url:
            return COUNTS
        if '/oembed?' in url:
            return '{}'
        if '.rss' in url:
            return EMPTY_RSS
        return fixture_page()
    service = c.Community(tmp_path, clock=lambda: clock[0], read=fetch)
    first = asyncio.run(service.view('en'))
    clock[0] += c.TTL + 1
    fail[0] = True
    restarted = c.Community(tmp_path, clock=lambda: clock[0], read=fetch)
    result = asyncio.run(restarted.view('en'))
    assert result['items'] == first['items'] and result['discord'] == first['discord']
    assert all(source['stale'] and source['error'] == 'unavailable' for source in result['sources'].values())
    assert result['sources']['vivosun']['at'] == 2000


def test_unavailable_source_does_not_hide_the_other_service(tmp_path):
    async def fetch(_, url):
        if '/invites/' in url:
            return COUNTS
        raise c.SourceError(403)
    result = asyncio.run(c.Community(tmp_path, read=fetch).view('en'))
    assert not result['items'] and result['discord']['online'] == 85
    assert result['sources']['vivosun']['stale'] and not result['sources']['discord']['stale']


def test_oembed_failure_does_not_drop_a_featured_video(tmp_path):
    async def fetch(_, url):
        if '/invites/' in url:
            return COUNTS
        if '/oembed?' in url:
            raise c.SourceError(403)
        if '.rss' in url:
            return EMPTY_RSS
        return fixture_page()
    result = asyncio.run(c.Community(tmp_path, read=fetch).view('de'))
    assert len(result['items']) == 2 and result['items'][1]['title'] == ''


def test_demo_never_calls_any_external_service(tmp_path):
    async def reject(*_):
        raise AssertionError('Demo must be offline')
    result = asyncio.run(c.Community(tmp_path, demo=True, read=reject).view('de', force=True))
    assert result['demo'] and len(result['items']) == 6 and not list(tmp_path.glob('*.json'))


def test_api_is_authenticated_read_only_and_device_independent():
    async def run():
        async with TestClient(TestServer(server.make_app(DemoBox(), Alerts(), 'test-key'))) as client:
            response = await client.get('/api/community')
            assert response.status == 403
            client.session.cookie_jar.update_cookies({server.COOKIE: 'test-key'})
            response = await client.get('/api/community?refresh=1', headers={'X-Lang': 'en'})
            assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
            data = await response.json()
            assert data['demo'] and next(item for item in data['items'] if item['kind'] == 'story')['url'] == 'https://vivosun.com/en-DE/community'
            response = await client.post('/api/community', json={'url': 'http://127.0.0.1/private'})
            assert response.status == 405
    asyncio.run(run())


def test_public_rss_keeps_titles_dates_and_canonical_links_without_post_bodies():
    rss = '''<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>t3_abc</id><title>A public discussion</title><link href="https://www.reddit.com/r/VIVOSUN/comments/abc/a_post/"/>
        <published>2026-10-25T10:00:00Z</published><content>DO NOT STORE BODY</content></entry>
      <entry><title>Malicious link</title><link href="https://evil.example/r/VIVOSUN/comments/abc/a_post/"/></entry>
    </feed>'''
    result = c.parse_posts(rss)
    assert len(result) == 1 and result[0]['kind'] == 'post' and result[0]['published'] == '2026-10-25T10:00:00Z'
    assert 'DO NOT STORE BODY' not in json.dumps(result)
    assert c.parse_posts(EMPTY_RSS) == []
    with pytest.raises(ValueError):
        c.parse_posts('<html/>')
    with pytest.raises(ValueError, match='declarations'):
        c.parse_posts('<!DOCTYPE feed><feed/>')


def image_post(content='', media=''):
    from html import escape
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
      <entry><title>Discussion with a photo</title>
      <link href="https://www.reddit.com/r/VIVOSUN/comments/abc/photo/"/>
      <content type="html">{escape(content)}</content>{media}</entry></feed>'''


def test_rss_photo_is_extracted_without_retaining_post_html_or_author():
    url = 'https://preview.redd.it/photo.jpeg?width=640&auto=webp&s=signature'
    body = f'<p>DO NOT STORE</p><a href="https://example.com">link</a><img src="{url.replace("&", "&amp;")}" alt="DO NOT STORE">'
    items = c.parse_posts(image_post(body))
    assert items[0]['image'] == url
    assert 'DO NOT STORE' not in json.dumps(items)
    assert 'example.com' not in json.dumps(items)


def test_rss_media_thumbnail_and_html_fallback_are_supported():
    url = 'https://i.redd.it/original.jpg'
    assert c.parse_posts(image_post(media=f'<media:thumbnail url="{url}"/>'))[0]['image'] == url
    assert c.parse_posts(image_post(f'<img src="{url}">', '<media:thumbnail url="https://evil.example/track"/>'))[0]['image'] == url
    assert c.parse_posts(image_post())[0]['image'] is None


@pytest.mark.parametrize('value', ['https://preview.redd.it.evil.example/p.jpg', 'http://preview.redd.it/p.jpg',
                                 'https://user:password@i.redd.it/p.jpg', 'https://i.redd.it:444/p.jpg',
                                 'https://image.next.vivosun.com/p.jpg', 'https://127.0.0.1/p.jpg',
                                 'javascript:alert(1)', 'data:image/svg+xml,test', '//i.redd.it/p.jpg',
                                 'https://preview.redd.it/p.jpg\n', 'https://preview.redd.it/' + 'x' * 2000])
def test_rss_images_reject_foreign_hosts_and_unsafe_urls(value):
    assert c.parse_posts(image_post(f'<img src="{value}">'))[0]['image'] is None


def test_rss_skips_unsupported_image_but_preserves_the_first_valid_source_photo():
    assert c.parse_posts(image_post('<img src="https://evil.example/p.jpg"><img src="https://preview.redd.it/first.jpg"><img src="https://i.redd.it/second.jpg">'))[0]['image'] == 'https://preview.redd.it/first.jpg'


def test_fetch_refuses_redirects_and_oversized_responses():
    class Content:
        async def iter_chunked(self, _):
            yield b'x' * (c.MAX_BYTES + 1)
    class Response:
        status = 302
        content = Content()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
    class Session:
        def get(self, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            return Response()
    with pytest.raises(c.SourceError):
        asyncio.run(c.fetch(Session(), 'https://example.com'))
    Response.status = 200
    with pytest.raises(ValueError, match='too large'):
        asyncio.run(c.fetch(Session(), 'https://example.com'))
