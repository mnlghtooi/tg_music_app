"""Guest Bandcamp search and public player streams. No account or purchase access."""
import base64
from collections import OrderedDict
from concurrent.futures import Future
from html.parser import HTMLParser
import json
import re
import threading
import time
from urllib.parse import urljoin, urlparse
import httpx


class BandcampError(RuntimeError):
    pass


def public_url(url, kind=None):
    p = urlparse(url)
    if (p.scheme != 'https' or not re.fullmatch(r'[a-z0-9-]+\.bandcamp\.com', p.netloc)
            or p.username or p.password):
        raise ValueError('Unsupported Bandcamp URL')
    path = p.path.rstrip('/')
    if kind == 'artist':
        if path not in ('', '/music'): raise ValueError('Invalid artist URL')
        path = ''
    elif not re.fullmatch(r'/(track|album)/[a-zA-Z0-9%._~-]+', path):
        raise ValueError('Invalid release URL')
    if kind in ('track', 'album') and not path.startswith('/' + kind + '/'):
        raise ValueError('Unexpected release type')
    return 'https://' + p.netloc + path


def identifier(url):
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip('=')


def decode_id(value, kind):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,2048}', value): raise ValueError('Invalid Bandcamp ID')
    return public_url(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)).decode(), kind)


class PageData(HTMLParser):
    def __init__(self):
        super().__init__(); self.tralbum = None; self.embed = {}; self.releases = []
        self.item = None; self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ('data-tralbum', 'data-embed'):
            if key in attrs:
                data = json.loads(attrs[key])
                if key == 'data-tralbum' and 'trackinfo' in data: self.tralbum = data
                if key == 'data-embed': self.embed = data
        if tag == 'li' and 'music-grid-item' in attrs.get('class', '').split():
            self.item = {'title': '', 'href': '', 'art': ''}
        if self.item is not None:
            if tag == 'a' and not self.item['href']: self.item['href'] = attrs.get('href', '')
            if tag == 'img': self.item['art'] = attrs.get('src') or attrs.get('data-original', '')
            if tag == 'p' and 'title' in attrs.get('class', '').split(): self.in_title = True

    def handle_data(self, data):
        if self.in_title and self.item is not None: self.item['title'] += data

    def handle_endtag(self, tag):
        if tag == 'p': self.in_title = False
        if tag == 'li' and self.item is not None:
            self.releases.append(self.item); self.item = None; self.in_title = False


class BandcampService:
    def __init__(self, tokenize, client=None):
        self.tokenize = tokenize
        self.client = client or httpx.Client(http2=True, timeout=httpx.Timeout(15, connect=5),
            headers={'User-Agent': 'MusicLab/0.7.3'},
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=4))
        self.lock = threading.Lock(); self.cache = OrderedDict(); self.pending = {}
        self.slots = threading.BoundedSemaphore(3)

    def cached(self, key, loader):
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < 300: return hit[1]
            future = self.pending.get(key); owner = future is None
            if owner: future = self.pending[key] = Future()
        if not owner: return future.result(timeout=35)
        acquired = False
        try:
            acquired = self.slots.acquire(blocking=False)
            if not acquired: raise BandcampError('Bandcamp занят. Повтори запрос через несколько секунд.')
            result = loader()
            with self.lock:
                self.cache[key] = (time.monotonic(), result); self.cache.move_to_end(key)
                while len(self.cache) > 150: self.cache.popitem(last=False)
            future.set_result(result); return result
        except Exception as error:
            future.set_exception(error); raise
        finally:
            if acquired: self.slots.release()
            with self.lock: self.pending.pop(key, None)

    @staticmethod
    def page(items, page, **extra):
        if not isinstance(page, int) or not 1 <= page <= 100: raise ValueError('Invalid page')
        offset = (page - 1) * 10
        return {'data': items[offset:offset + 10], 'page': page, 'page_size': 10,
                'has_more': len(items) > offset + 10, **extra}

    def search(self, query, page=1, kind='all'):
        if kind not in ('all', 'artist', 'album', 'playlist', 'track'):
            raise ValueError('Invalid search type')
        if kind == 'playlist': return self.page([], page)
        def fetch():
            response = self.client.post('https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic',
                json={'search_text': query, 'search_filter': {'artist': 'b', 'album': 'a', 'track': 't'}.get(kind, ''), 'full_page': True, 'fan_id': None})
            response.raise_for_status()
            if 'application/json' not in response.headers.get('content-type', ''):
                raise BandcampError('Bandcamp не предоставил поиск. Попробуй позднее.')
            items = []
            for raw in response.json().get('auto', {}).get('results', []):
                item_kind = {'t': 'track', 'a': 'album', 'b': 'artist'}.get(raw.get('type'))
                if not item_kind or kind != 'all' and item_kind != kind: continue
                try: url = public_url(urljoin(raw.get('item_url_root', ''), raw.get('item_url_path') or ''), item_kind)
                except ValueError: continue
                items.append({'id': identifier(url), 'kind': item_kind, 'title': raw.get('name') or 'Без названия',
                    'artist': raw.get('band_name') or '', 'source': 'bandcamp', 'permalink': url,
                    'duration': 0, 'preview': False, 'artwork': {'480x480': raw['img']} if raw.get('img') else None})
            return items
        items = self.cached(('search', ' '.join(query.casefold().split()), kind), fetch)
        return self.page(items, page, result_limit=50)

    def page_data(self, url):
        def fetch():
            response = self.client.get(url); response.raise_for_status()
            if len(response.content) > 4 * 1024 * 1024: raise BandcampError('Слишком большая страница Bandcamp.')
            if 'Client Challenge' in response.text: raise BandcampError('Bandcamp временно ограничил доступ к странице.')
            parser = PageData(); parser.feed(response.text)
            from entity_service import Metadata
            parser.metadata = Metadata(); parser.metadata.feed(response.text)
            return parser
        return self.cached(('page', url), fetch)

    def release_tracks(self, url):
        data = self.page_data(url)
        if not data.tralbum: raise BandcampError('На этой странице нет доступных данных плеера Bandcamp.')
        album = data.tralbum
        artist = data.embed.get('artist') or album.get('artist') or ''
        art = album.get('art_id')
        result = []
        for raw in album.get('trackinfo', []):
            if not raw.get('title_link'): continue
            track_url = public_url(urljoin(url, raw['title_link']), 'track')
            playable = bool((raw.get('file') or {}).get('mp3-128')) and not raw.get('is_capped') and not raw.get('private') and not album.get('is_private_stream')
            result.append({'id': identifier(track_url), 'kind': 'track', 'source': 'bandcamp',
                'title': raw.get('title') or 'Без названия', 'artist': raw.get('artist') or artist,
                'duration': raw.get('duration') or 0, 'permalink': track_url, 'preview': False,
                'unavailable': not playable,
                'artwork': {'480x480': 'https://f4.bcbits.com/img/a' + str(art) + '_5.jpg'} if art else None})
            # Keep public player metadata private, so selecting a track from an
            # album does not require reloading each track page.
            with self.lock:
                key = ('audio', identifier(track_url))
                self.cache[key] = (time.monotonic(), raw['file']['mp3-128'] if playable else None)
                self.cache.move_to_end(key)
                while len(self.cache) > 150: self.cache.popitem(last=False)
        return result

    def browse(self, kind, item_id, page=1):
        if kind not in ('artist', 'album'): raise ValueError('Invalid Bandcamp collection')
        url = decode_id(item_id, kind)
        if kind == 'album': return self.page(self.release_tracks(url), page)
        data = self.page_data(url + '/music'); items = []
        for raw in data.releases:
            try:
                release = public_url(urljoin(url, raw['href']))
                release_kind = 'album' if '/album/' in release else 'track'
            except ValueError: continue
            items.append({'id': identifier(release), 'kind': release_kind, 'source': 'bandcamp',
                'title': raw['title'].strip() or 'Без названия', 'artist': '', 'permalink': release,
                'artwork': {'480x480': raw['art']} if raw['art'] else None})
        return self.page(items, page)

    def stream(self, item_id):
        url = decode_id(item_id, 'track')
        with self.lock:
            hit = self.cache.get(('audio', item_id))
        if not hit or time.monotonic() - hit[0] >= 300:
            self.release_tracks(url)
            with self.lock: hit = self.cache.get(('audio', item_id))
        if not hit or not hit[1]: raise BandcampError('Для этой записи нет доступного публичного аудио Bandcamp.')
        media = urljoin('https://bandcamp.com', hit[1])
        host = urlparse(media).hostname or ''
        if urlparse(media).scheme != 'https' or not host.endswith('.bcbits.com'):
            raise ValueError('Unsupported Bandcamp audio host')
        return self.tokenize(media)
