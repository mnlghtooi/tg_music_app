"""One bounded search over the three catalogs already available in Music Lab."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
import threading
import time
from urllib.parse import urljoin
import httpx


class CatalogError(RuntimeError):
    pass


class AudiusSearch:
    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=httpx.Timeout(12, connect=4))

    def search(self, query, page=1, kind='track'):
        if kind not in ('all', 'track'):
            return {'data': [], 'has_more': False}
        response = self.client.get('https://api.audius.co/v1/tracks/search',
            params={'query': query, 'limit': 11, 'offset': (page - 1) * 10})
        response.raise_for_status()
        raw = response.json().get('data')
        if not isinstance(raw, list): raise CatalogError('Unexpected Audius response')
        items = []
        for t in raw[:10]:
            if not t or not t.get('id'): continue
            items.append(self.normalize(t))
        return {'data': items, 'has_more': len(raw) > 10}

    @staticmethod
    def normalize(t):
        user = t.get('user') or {}
        # Only explicit restrictions mean blocked; missing stream metadata is unknown.
        blocked = bool(t.get('is_stream_gated')) or t.get('is_streamable') is False
        availability = ('blocked' if blocked else
                        'playable' if t.get('is_streamable') is True else 'unknown')
        return {'id': str(t['id']), 'kind': 'track', 'source': 'audius',
            'title': t.get('title') or 'Без названия', 'artist': user.get('name') or '',
            'duration': t.get('duration') or 0, 'artwork': t.get('artwork'), 'preview': False,
            'availability': availability, 'unavailable': blocked,
            'permalink': urljoin('https://audius.co', t.get('permalink') or ''), 'isrc': t.get('isrc') or '',
            'artist_ref': {'id': str(user['id']), 'kind': 'artist', 'source': 'audius', 'title': user.get('name') or ''} if user.get('id') else None}


from music_providers import catalog_sources


class CatalogService:
    SOURCES = catalog_sources()

    def __init__(self, soundcloud, bandcamp, audius=None):
        self.services = {'soundcloud': soundcloud, 'bandcamp': bandcamp,
                         'audius': audius or AudiusSearch()}
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.failures = {}
        self.slots = threading.BoundedSemaphore(6)

    def query(self, source, query, page, kind):
        key = (source, ' '.join(query.casefold().split()), page, kind)
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < 120: return hit[1]
            if self.failures.get(source, 0) > time.monotonic(): raise CatalogError('Source temporarily unavailable')
        if not self.slots.acquire(blocking=False): raise CatalogError('Catalog busy')
        try:
            result = self.services[source].search(query, page, kind)
            with self.lock:
                self.cache[key] = (time.monotonic(), result)
                self.cache.move_to_end(key)
                while len(self.cache) > 300: self.cache.popitem(last=False)
            return result
        except Exception:
            with self.lock: self.failures[source] = time.monotonic() + 30
            raise
        finally: self.slots.release()

    @staticmethod
    def unique(items):
        seen = set()
        result = []
        for item in items:
            key = (item.get('source'), str(item.get('id')))
            if key not in seen:
                seen.add(key); result.append(item)
        return result

    def parallel(self, worker, sources=None):
        sources = tuple(sources or self.SOURCES)
        results = {}
        failed = []
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            jobs = {source: pool.submit(worker, source) for source in sources}
            for source, job in jobs.items():
                try: results[source] = job.result()
                except Exception: failed.append(source)
        return results, failed

    def lookup(self, query, sources=None):
        results, failed = self.parallel(lambda source: self.query(source, query, 1, 'track'), sources)
        return {'data': self.unique([t for source in self.SOURCES for t in results.get(source, {}).get('data', [])]),
                'checked_sources': list(results), 'failed_sources': failed}

    def hydrate(self, item):
        if item.get('source') != 'bandcamp' or item.get('duration'):
            return item
        tracks = self.services['bandcamp'].release_tracks(item['permalink'])
        return next((t for t in tracks if t.get('id') == item.get('id')), item)

    def search(self, query, page=1, kind='all'):
        if not isinstance(page, int) or not 1 <= page <= 100: raise ValueError('Invalid page')
        if kind not in ('all', 'artist', 'album', 'playlist', 'track'): raise ValueError('Invalid type')
        sources = self.SOURCES if kind in ('all', 'track') else ('soundcloud', 'bandcamp')
        needed = page * 10 + 1
        def collect(source):
            items = []
            more = True
            provider_page = 1
            while more and len(items) < needed and provider_page <= min(page + 1, 100):
                batch = self.query(source, query, provider_page, kind)
                items.extend(batch['data'])
                more = bool(batch.get('has_more')) and bool(batch['data'])
                provider_page += 1
            return self.unique(items), more
        results, failed = self.parallel(collect, sources)
        if not results: raise CatalogError('Все источники временно недоступны. Повтори поиск позднее.')
        # Each visible page slices the same interleaved sequence. No results are
        # discarded by fetching ten from each service and slicing that batch.
        combined = self.unique([item for group in zip_longest(
            *(results.get(source, ([], False))[0] for source in sources)) for item in group if item is not None])
        offset = (page - 1) * 10
        return {'data': combined[offset:offset + 10], 'page': page, 'page_size': 10,
                'has_more': page < 100 and (len(combined) > offset + 10 or any(v[1] for v in results.values())),
                'checked_sources': list(results), 'failed_sources': failed}
