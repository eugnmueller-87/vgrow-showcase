"""Public community metadata, isolated from account sessions and device commands.

Fetch only fixed public endpoints. Retain the last good snapshot across outages/restarts;
never proxy login pages, cookies, private messages, or arbitrary client-supplied URLs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import aiohttp
from aiohttp import web

from . import config
from .i18n import current_lang

TTL = 600
MAX_BYTES = 2_000_000
DISCORD_INVITE = 'DHp7a6jxNk'
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
IMAGE_HOST = 'image.next.vivosun.com'
REDDIT_IMAGE_HOSTS = frozenset(('preview.redd.it', 'i.redd.it', 'external-preview.redd.it',
                                'a.thumbs.redditmedia.com', 'b.thumbs.redditmedia.com'))


class NextData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = dict(attrs).get('id') == '__NEXT_DATA__'

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def plain(value, limit=240):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def image_url(value, *, reddit=False):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(char) < 33 for char in value):
        return None
    if not reddit and value.startswith('asset/'):
        value = f'https://{IMAGE_HOST}/{value}'
    try:
        parsed = urlsplit(value)
        hosts = REDDIT_IMAGE_HOSTS if reddit else (IMAGE_HOST,)
        if parsed.scheme == 'https' and parsed.hostname in hosts and not parsed.username and not parsed.password and parsed.port in (None, 443):
            return value
    except ValueError:
        pass
    return None


class PostImage(HTMLParser):
    """Read the first source thumbnail; discard all post HTML, links and author details."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.image = None

    def handle_starttag(self, tag, attrs):
        if tag == 'img' and self.image is None:
            self.image = image_url(dict(attrs).get('src'), reddit=True)


def post_image(entry, atom):
    media = '{http://search.yahoo.com/mrss/}'
    for thumbnail in entry.iter(media + 'thumbnail'):
        image = image_url(thumbnail.get('url'), reddit=True)
        if image:
            return image
    parser = PostImage()
    parser.feed(entry.findtext(atom + 'content') or '')
    return parser.image


def parse_page(body, lang):
    parser = NextData()
    parser.feed(body)
    features = json.loads(''.join(parser.parts))['props']['pageProps']['features']
    if not isinstance(features, list):
        raise ValueError('Invalid public community metadata')
    def objects(value):
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    items, seen = [], set()
    for feature in objects(features):
        if feature.get('type') == 'articleCards':
            for row in objects(feature.get('list')):
                slug, title = row.get('articleCode'), plain(row.get('title'))
                if not isinstance(slug, str) or not slug or len(slug) > 500 or not title:
                    continue
                ident = 'story-' + hashlib.sha256(str(row.get('articleId') or slug).encode()).hexdigest()[:20]
                if ident in seen:
                    continue
                seen.add(ident)
                items.append({'id': ident, 'kind': 'story', 'title': title, 'creator': 'VIVOSUN',
                              'url': f'https://vivosun.com/{lang}-DE/community/article/{quote(slug, safe="")}',
                              'image': image_url(row.get('imgSrc')), 'source': 'VIVOSUN Community'})
        elif feature.get('type') == 'tabsCelebrities':
            for creator in objects(feature.get('list')):
                name = plain(creator.get('name'), 120)
                for row in objects(creator.get('videos')):
                    frame = row.get('iframe')
                    value = frame.get('src', '') if isinstance(frame, dict) else ''
                    if not isinstance(value, str):
                        continue
                    try:
                        parsed = urlsplit(value)
                    except ValueError:
                        continue
                    ident = parsed.path.removeprefix('/embed/')
                    if parsed.scheme != 'https' or parsed.netloc != 'www.youtube.com' or not VIDEO_ID.fullmatch(ident) or ident in seen:
                        continue
                    seen.add(ident)
                    items.append({'id': ident, 'kind': 'video', 'video': ident, 'title': '', 'creator': name,
                                  'url': f'https://www.youtube.com/watch?v={ident}', 'image': image_url(row.get('cover')),
                                  'source': 'VIVOSUN Community / YouTube'})
    if not items:
        raise ValueError('No public community metadata')
    return sorted(items, key=lambda item: item['kind'] == 'video')[:60]


class SourceError(Exception):
    def __init__(self, status=0):
        self.status = status


async def fetch(session, url):
    # Redirects are deliberately refused: fixed public sources must not turn into a credential/private proxy.
    async with session.get(url, allow_redirects=False) as response:
        if response.status != 200:
            raise SourceError(response.status)
        data = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError('Public source too large')
        return data.decode('utf-8')


def discord_counts(body):
    data = json.loads(body)
    guild = data['guild']
    if not isinstance(guild.get('id'), str) or not guild['id'].isdigit():
        raise ValueError('Invalid public server')
    counts = {}
    for key, target in (('approximate_member_count', 'members'), ('approximate_presence_count', 'online')):
        value = data.get(key)
        counts[target] = value if type(value) is int and 0 <= value <= 100_000_000 else None
    return {'name': plain(guild.get('name'), 120), **counts}


def parse_posts(body):
    if '<!DOCTYPE' in body.upper() or '<!ENTITY' in body.upper():
        raise ValueError('RSS declarations are not supported')
    root = ET.fromstring(body)
    atom = '{http://www.w3.org/2005/Atom}'
    if root.tag != atom + 'feed':
        raise ValueError('Invalid public RSS feed')
    items = []
    for entry in root.findall(atom + 'entry')[:25]:
        link = entry.find(atom + 'link')
        url = link.get('href', '') if link is not None else ''
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        title = plain(entry.findtext(atom + 'title'))
        if (not title or parsed.scheme != 'https' or parsed.netloc != 'www.reddit.com'
                or not re.fullmatch(r'/r/VIVOSUN/comments/[a-z0-9]+(?:/[^?#]*)?', parsed.path, re.IGNORECASE)):
            continue
        ident = 'reddit-' + hashlib.sha256(parsed.path.split('/')[4].encode()).hexdigest()[:20]
        items.append({'id': ident, 'kind': 'post', 'title': title, 'url': url, 'source': 'r/VIVOSUN',
                      'creator': 'r/VIVOSUN', 'image': post_image(entry, atom),
                      'published': plain(entry.findtext(atom + 'published') or entry.findtext(atom + 'updated'), 40)})
    return items


class Community:
    def __init__(self, root: Path, *, demo=False, clock=time.time, read=fetch):
        self.root, self.demo, self.clock, self.read = root, demo, clock, read
        self.lock = asyncio.Lock()
        self.cache = {}

    def load(self, source):
        if source not in self.cache:
            try:
                raw = json.loads((self.root / f'{source}.json').read_text(encoding='utf-8'))
                if raw.get('schema') != 1 or not isinstance(raw.get('at'), (int, float)) or not isinstance(raw.get('data'), (list, dict)):
                    raise ValueError
                self.cache[source] = raw
            except (OSError, ValueError, TypeError):
                self.cache[source] = {'at': 0, 'data': None}
        return self.cache[source]

    def save(self, source, value):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f'{source}.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    async def vivosun(self, session, lang):
        result = parse_page(await self.read(session, f'https://vivosun.com/{lang}-DE/community'), lang)
        old = {item['id']: item for item in (self.load(f'vivosun-{lang}').get('data') or [])}
        semaphore = asyncio.Semaphore(4)

        async def title(item):
            if item['kind'] != 'video':
                return
            if old.get(item['id'], {}).get('title'):
                item['title'] = old[item['id']]['title']
                return
            async with semaphore:
                try:
                    url = 'https://www.youtube.com/oembed?' + urlencode({'url': item['url'], 'format': 'json'})
                    data = json.loads(await self.read(session, url))
                    item['title'] = plain(data.get('title'))
                except (SourceError, ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError):
                    pass  # an unavailable video title must not remove the public stories
        await asyncio.gather(*(title(item) for item in result))
        return result

    async def update(self, session, source, lang, force):
        previous = self.load(source)
        now = self.clock()
        if previous.get('retry', 0) > now or (previous.get('checked', previous['at']) > now - (60 if force else TTL)):
            return
        try:
            if source == 'discord':
                data = discord_counts(await self.read(session, f'https://discord.com/api/v10/invites/{DISCORD_INVITE}?with_counts=true'))
            elif source == 'reddit':
                data = parse_posts(await self.read(session, 'https://www.reddit.com/r/VIVOSUN/new/.rss'))
            else:
                data = await self.vivosun(session, lang)
            value = {'schema': 1, 'at': now, 'checked': now, 'data': data, 'error': None}
            try:
                self.save(source, value)
            except OSError:
                value['storage_error'] = True  # live public data still works; no claim of durable offline cache
            self.cache[source] = value
        except (SourceError, ValueError, KeyError, TypeError, ET.ParseError, OSError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            delay = 3600 if isinstance(error, SourceError) and error.status in (403, 404, 410) else TTL
            self.cache[source] = {**previous, 'checked': now, 'retry': now + delay, 'error': 'unavailable'}

    async def view(self, lang, *, force=False):
        lang = 'de' if lang == 'de' else 'en'
        if self.demo:
            return demo_view(lang, self.clock())
        async with self.lock:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), cookie_jar=aiohttp.DummyCookieJar(),
                                             headers={'User-Agent': 'VGrowCockpit/1.0 (public community reader)'}) as session:
                await asyncio.gather(*(self.update(session, source, lang, force) for source in (f'vivosun-{lang}', 'discord', 'reddit')))
            feed, discord, posts = self.load(f'vivosun-{lang}'), self.load('discord'), self.load('reddit')
            return {'items': [*(posts.get('data') or []), *(feed.get('data') or [])], 'discord': discord.get('data'), 'demo': False,
                    'sources': {name: {'at': value['at'] or None, 'checked': value.get('checked'),
                                       'stale': bool(value.get('error')) or value['at'] < self.clock() - TTL,
                                       'error': value.get('error'), 'storage_error': bool(value.get('storage_error'))}
                                for name, value in (('vivosun', feed), ('discord', discord), ('reddit', posts))}, 'refresh_seconds': TTL}


def demo_view(lang, now):
    items = [{'id': f'demo-story-{n}', 'kind': 'story', 'title': title, 'creator': 'DEMO', 'image': None,
              'url': f'https://vivosun.com/{lang}-DE/community', 'source': 'DEMO'}
             for n, title in enumerate(['A community project', 'Meet the community', 'Community event'])]
    # Demo links are real public video IDs from VIVOSUN's page, with no player loaded until explicitly requested.
    items.append({'id': 'demo-video', 'kind': 'video', 'video': 'HZOcNR8eczc', 'title': 'DEMO video card', 'creator': 'DEMO',
                  'url': 'https://www.youtube.com/watch?v=HZOcNR8eczc', 'image': None, 'source': 'DEMO'})
    items[:0] = [{'id': f'demo-post-{number}', 'kind': 'post', 'title': title, 'creator': 'DEMO r/VIVOSUN', 'source': 'DEMO r/VIVOSUN',
                  'url': f'https://www.reddit.com/r/VIVOSUN/comments/demo{number}/community_post/', 'image': None,
                  'published': '2026-10-25T12:00:00Z'} for number, title in enumerate(('Community check-in', 'A shared project update'))]
    return {'items': items, 'discord': {'name': 'DEMO server', 'members': 1200, 'online': 80}, 'demo': True,
            'sources': {key: {'at': now, 'checked': now, 'stale': False, 'error': None, 'storage_error': False}
                        for key in ('vivosun', 'discord', 'reddit')}, 'refresh_seconds': TTL}


def register(app, *, demo=False):
    service = Community(config.STATE_DIR / 'community', demo=demo)

    async def read(request):
        return web.json_response(await service.view(current_lang(), force=request.query.get('refresh') == '1'),
                                 headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/community', read)
