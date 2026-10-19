"""Augment an authoritative artist profile with clearly labelled cross-source leads.

A matching artist *name* is discovery evidence, never proof of a shared artist
identity or a licensed copy of a particular master recording. Provider-owned
cards, IDs and streaming paths remain untouched.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import re
import threading
import time

from music_providers import artist_sources
from track_matching import words


class ArtistAggregateService:
    def __init__(self, entities, catalog, ttl=90):
        self.entities = entities
        self.catalog = catalog
        self.ttl = ttl
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    @staticmethod
    def artist_credit(item, name):
        """Only strict artist attribution qualifies; no substring matching."""
        expected = words(name)
        if not expected or len(expected) < 3:
            return False
        credited = words(item.get('artist') or '')
        if credited == expected:
            return True
        # User-uploaded tracks sometimes put an artist credit in their title.
        title = item.get('title') or ''
        prefix = re.match(r'^(.+?)\s+[-–—|:]\s+(.+)$', title)
        return bool(prefix and words(prefix.group(1)) == expected)

    @staticmethod
    def source_key(item):
        return (item.get('source'), str(item.get('id')))

    @staticmethod
    def visible_candidate(item):
        if not isinstance(item, dict) or item.get('kind', 'track') != 'track':
            return False
        if not item.get('id') or not item.get('source'):
            return False
        if item.get('unavailable') or item.get('preview'):
            return False
        return item.get('availability') not in ('blocked', 'preview_only', 'temporary_error')

    def lookup_source(self, source, artist):
        """Search a bounded number of existing catalog pages; never resolve audio."""
        found = []
        for page in (1, 2):
            result = self.catalog.query(source, artist, page, 'track')
            if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                raise ValueError('Unexpected provider catalog response')
            found.extend(result['data'])
            if not result.get('has_more'):
                break
        return found

    def discover(self, source, name, existing):
        sources = [s for s in artist_sources() if s != source and s in self.catalog.services]
        if not sources:
            return [], [], []
        matches = {}
        failed = []
        # Provider queries are bounded and already protected by CatalogService
        # timeouts, concurrency limits, cooldown and caching.
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            jobs = {src: pool.submit(self.lookup_source, src, name) for src in sources}
            for src, job in jobs.items():
                try:
                    matches[src] = job.result()
                except Exception:
                    failed.append(src)
        seen = set(existing)
        tracks = []
        for src in sources:
            for item in matches.get(src, []):
                if not self.visible_candidate(item) or not self.artist_credit(item, name):
                    continue
                key = self.source_key(item)
                if key in seen:
                    continue
                seen.add(key)
                # The source's own track identity remains stable. Never relabel
                # an unverified cover/remix as an original release.
                tracks.append({**item, 'discovery': 'artist_name_only',
                               'verified_artist': False})
                if len(tracks) >= 20:
                    break
            if len(tracks) >= 20:
                break
        return tracks, sources, failed

    def overview(self, source, item_id):
        if source not in artist_sources() or not item_id or len(str(item_id)) > 2048:
            raise ValueError('Invalid artist')
        key = (source, str(item_id))
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < self.ttl:
                self.cache.move_to_end(key)
                return hit[1]
        primary = self.entities.overview(source, item_id)
        profile = primary.get('profile') or {}
        name = profile.get('title') or ''
        # Do not accidentally merge popular terms, anonymous or malformed names.
        if not isinstance(name, str) or not words(name) or len(words(name)) < 3:
            return {**primary, 'aggregation': {
                'status': 'skipped', 'reason': 'Недостаточно данных об исполнителе.',
                'searched_sources': [], 'failed_sources': []}}
        current = {
            self.source_key(item)
            for block in primary.get('blocks', [])
            for item in block.get('data', [])
            if isinstance(item, dict) and item.get('kind', 'track') == 'track'
        }
        tracks, searched, failed = self.discover(source, name, current)
        blocks = list(primary.get('blocks', []))
        blocks.append({'section': 'discovered', 'data': tracks, 'has_more': False,
                       'note': 'Найдено по имени исполнителя в других сервисах. '
                               'Подлинность оригинальной записи не подтверждена.'})
        result = {**primary, 'blocks': blocks, 'aggregation': {
            'status': 'partial' if failed else 'complete',
            'searched_sources': searched, 'failed_sources': failed,
            'candidate_count': len(tracks),
            'identity_verified': False}}
        with self.lock:
            self.cache[key] = (time.monotonic(), result)
            self.cache.move_to_end(key)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
        return result
