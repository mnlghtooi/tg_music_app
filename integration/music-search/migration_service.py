"""Public playlist metadata import. No account login, tokens or audio downloads."""
from collections import OrderedDict
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin
from difflib import SequenceMatcher
import json
import re
import threading
import time
import unicodedata
import httpx

class MigrationError(RuntimeError):
    pass

def words(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value).casefold()))


def match_score(original, candidate):
    title = SequenceMatcher(None, words(original['title']), words(candidate['title'])).ratio()
    artist = SequenceMatcher(None, words(original['artist']), words(candidate['artist'])).ratio()
    score = .65 * title + .35 * artist
    versions = ('live', 'remix', 'cover', 'instrumental', 'karaoke', 'acoustic', 'sped', 'slowed')
    if any((v in words(original['title']).split()) != (v in words(candidate['title']).split()) for v in versions):
        score = min(score, .6)
    if candidate.get('preview'): score = min(score, .65)
    if original.get('duration') and candidate.get('duration'):
        if abs(original['duration'] - candidate['duration']) > 15: score = min(score, .7)
    return round(score, 3)


def playlist_id(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise MigrationError('Вставь ссылку на публичный плейлист Spotify.')
    parsed = urlparse(value.strip())
    if parsed.scheme != 'https' or parsed.netloc != 'open.spotify.com' or parsed.username or parsed.password:
        raise MigrationError('Пока поддерживаются ссылки https://open.spotify.com/playlist/…')
    match = re.fullmatch(r'/(?:intl-[a-z]{2}/)?playlist/([A-Za-z0-9]{22})/?', parsed.path)
    if not match:
        raise MigrationError('Нужна ссылка на обычный публичный плейлист Spotify. Любимые треки сначала скопируй в обычный плейлист.')
    return match[1]

class PublicPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.count = None
        self.in_state = False
        self.state = ''
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and (attrs.get('name') or attrs.get('property')) == 'music:song_count':
            try:
                count = int(attrs.get('content', ''))
                if 0 <= count <= 100000: self.count = count
            except ValueError: pass
        if tag == 'script' and attrs.get('id') == '__NEXT_DATA__': self.in_state = True
    def handle_data(self, data):
        if self.in_state: self.state += data
    def handle_endtag(self, tag):
        if tag == 'script': self.in_state = False

class MigrationService:
    def __init__(self, search, bandcamp, client=None):
        self.search = search
        self.bandcamp = bandcamp
        self.client = client or httpx.Client(timeout=httpx.Timeout(20, connect=5),
            headers={'User-Agent': 'MusicLab/0.7.3'}, follow_redirects=False)
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.slots = threading.BoundedSemaphore(2)

    def page(self, url, item_id):
        for attempt in range(4):
            with self.client.stream('GET', url, follow_redirects=False) as response:
                if response.is_redirect:
                    target = urljoin(url, response.headers.get('Location', ''))
                    parsed = urlparse(target)
                    if (parsed.scheme != 'https' or parsed.netloc != 'open.spotify.com' or
                            not re.fullmatch(r'/(?:intl-[a-z]{2}/|embed/)?playlist/' + item_id + r'/?', parsed.path)):
                        raise MigrationError('Spotify перенаправил на неподдерживаемую страницу. Проверь публичность плейлиста.')
                    url = target
                    continue
                if response.status_code >= 400:
                    raise MigrationError('Spotify не открыл плейлист. Проверь ссылку и публичность или повтори позднее.')
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 5 * 1024 * 1024: raise MigrationError('Страница Spotify слишком большая.')
                    chunks.append(chunk)
                page = PublicPage()
                page.feed(b''.join(chunks).decode('utf-8', errors='replace'))
                return page
        raise MigrationError('Слишком много перенаправлений Spotify.')

    def public_playlist(self, url):
        item_id = playlist_id(url)
        with self.lock:
            hit = self.cache.get(item_id)
            if hit and time.monotonic() - hit[0] < 300: return hit[1]
        if not self.slots.acquire(blocking=False): raise MigrationError('Импорт занят. Повтори через несколько секунд.')
        try:
            embed = self.page('https://open.spotify.com/embed/playlist/' + item_id, item_id)
            try:
                data = json.loads(embed.state)['props']['pageProps']['state']['data']['entity']
            except (ValueError, KeyError, TypeError):
                raise MigrationError('Spotify не предоставил список треков. Возможно, плейлист приватный или формат страницы изменился.') from None
            if not isinstance(data, dict) or data.get('id') != item_id or data.get('type') != 'playlist' or not isinstance(data.get('trackList'), list):
                raise MigrationError('Spotify не предоставил публичный плейлист.')
            total = None
            try: total = self.page('https://open.spotify.com/playlist/' + item_id, item_id).count
            except (MigrationError, httpx.HTTPError): pass
            tracks = []
            skipped = 0
            for raw in data['trackList']:
                if not isinstance(raw, dict):
                    skipped += 1
                    continue
                uri = raw.get('uri', '')
                if (raw.get('entityType') != 'track' or not re.fullmatch(r'spotify:track:[A-Za-z0-9]{22}', uri)
                        or not isinstance(raw.get('title'), str) or not raw['title']
                        or not isinstance(raw.get('subtitle'), str) or not raw['subtitle']):
                    skipped += 1
                    continue
                tracks.append({'title': raw['title'], 'artist': raw['subtitle'],
                    'duration': (raw.get('duration') or 0) / 1000,
                    'source_url': 'https://open.spotify.com/track/' + uri.rsplit(':', 1)[-1]})
            if len(tracks) > 10000: raise MigrationError('Допускается до 10 000 треков за один импорт.')
            result = {'name': data.get('name') or data.get('title') or 'Плейлист Spotify',
                'source': 'spotify', 'source_url': 'https://open.spotify.com/playlist/' + item_id,
                'data': tracks, 'total': total, 'received': len(tracks), 'skipped': skipped,
                'complete': total is not None and total == len(tracks)}
            with self.lock:
                self.cache[item_id] = (time.monotonic(), result)
                self.cache.move_to_end(item_id)
                while len(self.cache) > 20: self.cache.popitem(last=False)
            return result
        finally:
            self.slots.release()

    def resolve(self, original, source):
        if (not isinstance(original, dict) or any(not isinstance(original.get(k), str) or
                not 1 <= len(original[k]) <= 300 for k in ('title', 'artist'))):
            raise MigrationError('Некорректные сведения о треке.')
        duration = original.get('duration', 0)
        if not isinstance(duration, (float, int)) or not 0 <= duration <= 86400:
            raise MigrationError('Некорректная длительность трека.')
        if source not in ('soundcloud', 'bandcamp'): raise MigrationError('Выбери источник для поиска совпадений.')
        service = self.search if source == 'soundcloud' else self.bandcamp
        data = service.search((original['artist'] + ' ' + original['title'])[:150], 1, 'track')['data']
        candidates = [{**t, 'score': match_score(original, t)} for t in data
                      if t.get('kind', 'track') == 'track' and not t.get('unavailable')]
        candidates.sort(key=lambda t: t['score'], reverse=True)
        return {'data': candidates[:5]}


def handle_get(handler, parsed, service):
    if not parsed.path.startswith('/api/migration/'): return False
    if parsed.path == '/api/migration/status':
        handler.respond(200, {'sources': ['spotify'], 'accounts_required': False})
    else: handler.respond(404, {'error': 'Неизвестный запрос переноса.'})
    return True


def handle_post(handler, service):
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 < length <= 8192: raise ValueError('Invalid length')
        raw = handler.rfile.read(length)
    except ValueError:
        handler.close_connection = True
        handler.respond(400, {'error': 'Некорректный размер запроса (до 8 КБ).'}); return
    origin = handler.headers.get('Origin')
    if (handler.headers.get('Content-Type', '').split(';')[0] != 'application/json'
            or origin and urlparse(origin).netloc != handler.headers.get('Host')
            or handler.headers.get('Sec-Fetch-Site') in ('cross-site', 'none')):
        handler.respond(403, {'error': 'Открой перенос из приложения Music Lab.'}); return
    try:
        body = json.loads(raw)
        if not isinstance(body, dict): raise MigrationError('Некорректный запрос.')
        path = urlparse(handler.path).path
        if path == '/api/migration/public-playlist':
            handler.respond(200, service.public_playlist(body.get('url')))
        elif path == '/api/migration/resolve':
            handler.respond(200, service.resolve(body.get('track'), body.get('source')))
        else: handler.respond(404, {'error': 'Неизвестный запрос переноса.'})
    except (MigrationError, ValueError) as error:
        handler.respond(400, {'error': str(error)})
    except Exception:
        handler.respond(502, {'error': 'Источник временно не отвечает. Повтори попытку.'})
