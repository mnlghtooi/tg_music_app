"""Public SoundCloud search: pooled transport, bounded cache, request coalescing."""
from collections import OrderedDict
from concurrent.futures import Future
import threading
import time
import httpx


class SearchService:
    def __init__(self, credentials, client=None):
        self.credentials = credentials
        self.client = client or httpx.Client(
            http2=True, timeout=httpx.Timeout(12, connect=5),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://soundcloud.com/"})
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.pending = {}
        self.track_metadata = OrderedDict()
        self.slots = threading.BoundedSemaphore(4)

    def search(self, query, page=1, kind='all'):
        if kind not in ('all', 'artist', 'album', 'playlist', 'track'):
            raise ValueError('Invalid search type')
        return self._cached(('search', ' '.join(query.casefold().split()), page, kind),
                            lambda: self._search(query, page, kind))

    @staticmethod
    def validate_page(page):
        if not isinstance(page, int) or not 1 <= page <= 100:
            raise ValueError("Invalid page")

    def _get(self, path, params=None):
        credential = self.credentials()
        for attempt in range(2):
            response = self.client.get('https://api-v2.soundcloud.com/' + path,
                                       params={**(params or {}), 'client_id': credential})
            if response.status_code in (401, 403) and attempt == 0:
                credential = self.credentials(credential)
                continue
            response.raise_for_status()
            data = response.json()
            self.remember_tracks(data)
            return data

    def remember_tracks(self, data):
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = data.get('collection', data.get('tracks', [data]))
        else:
            return
        with self.lock:
            for track in entries:
                if not isinstance(track, dict) or not track.get('id') or not track.get('media'):
                    continue
                key = str(track['id'])
                self.track_metadata[key] = (time.monotonic(), track)
                self.track_metadata.move_to_end(key)
            while len(self.track_metadata) > 1000:
                self.track_metadata.popitem(last=False)

    def get_track(self, track_id):
        with self.lock:
            hit = self.track_metadata.get(track_id)
            if hit and time.monotonic() - hit[0] < 300:
                return hit[1]
        return self._get('tracks/' + track_id)

    def _search(self, query, page, kind='all'):
        self.validate_page(page)
        path = {'artist': 'search/users', 'album': 'search/albums', 'playlist': 'search/playlists', 'track': 'search/tracks'}.get(kind, 'search')
        body = self._get(path, {'q': query, 'limit': 11, 'offset': (page - 1) * 10,
                                    'linked_partitioning': 1})
        raw = [t for t in body.get('collection', []) if t and t.get('id')]
        return {'data': [self.normalize(t) for t in raw[:10]],
                'page': page, 'page_size': 10, 'has_more': len(raw) > 10}

    def browse(self, kind, item_id, page=1):
        self.validate_page(page)
        if kind not in ('artist', 'playlist', 'album') or not str(item_id).isdigit() or len(str(item_id)) > 20:
            raise ValueError('Invalid collection')
        return self._cached((kind, str(item_id), page), lambda: self._browse(kind, str(item_id), page))

    def _browse(self, kind, item_id, page):
        offset = (page - 1) * 10
        if kind == 'artist':
            body = self._get('users/' + item_id + '/tracks',
                             {'limit': 11, 'offset': offset, 'linked_partitioning': 1})
            raw = body.get('collection', [])
            has_more = len(raw) > 10
            title = ''
        else:
            # SoundCloud may return only IDs for later playlist tracks. Hydrate
            # the visible page in one batch; never request each track separately.
            body = self._get('playlists/' + item_id)
            all_tracks = body.get('tracks', [])
            raw = all_tracks[offset:offset + 10]
            missing = [str(t['id']) for t in raw if t.get('id') and not t.get('title')]
            if missing:
                hydrated = self._get('tracks', {'ids': ','.join(missing)})
                by_id = {str(t['id']): t for t in hydrated if t and t.get('id')}
                raw = [by_id.get(str(t.get('id')), t) for t in raw]
            has_more = len(all_tracks) > offset + 10
            title = body.get('title', '')
        return {'data': [self.normalize(t) for t in raw[:10] if t and t.get('title')],
                'page': page, 'page_size': 10, 'has_more': has_more, 'title': title}

    def _cached(self, key, loader):
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < 300:
                self.cache.move_to_end(key)
                return hit[1]
            future = self.pending.get(key)
            owner = future is None
            if owner:
                future = self.pending[key] = Future()
        if not owner:
            return future.result(timeout=45)
        try:
            if not self.slots.acquire(blocking=False):
                raise RuntimeError("Сервер занят. Повтори поиск через несколько секунд.")
            try:
                result = loader()
            finally:
                self.slots.release()
            with self.lock:
                self.cache[key] = (time.monotonic(), result)
                self.cache.move_to_end(key)
                while len(self.cache) > 100:
                    self.cache.popitem(last=False)
            future.set_result(result)
            return result
        except Exception as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)

    @staticmethod
    def normalize(track):
        kind = track.get('kind', 'track')
        if kind in ('user', 'playlist'):
            art = track.get('artwork_url') or track.get('avatar_url') or (track.get('user') or {}).get('avatar_url')
            return {'id': str(track['id']), 'kind': 'artist' if kind == 'user' else 'album' if track.get('is_album') or track.get('set_type') == 'album' else 'playlist',
                    'title': (track.get('username') or 'SoundCloud') if kind == 'user' else track.get('title') or 'Без названия',
                    'artist': '' if kind == 'user' else (track.get('user') or {}).get('username', ''),
                    'source': 'soundcloud', 'permalink': track.get('permalink_url') or '',
                    'artwork': {'480x480': art.replace('-large.', '-t500x500.')} if art else None}
        duration = (track.get("duration") or 0) / 1000
        preview = track.get("policy") == "SNIP" or 0 < duration <= 30
        if track.get("policy") == "SNIP":
            duration = min(duration, 30)
        art = track.get("artwork_url") or (track.get("user") or {}).get("avatar_url")
        from stream_service import StreamService
        protected_only = bool((track.get('media') or {}).get('transcodings')) and not StreamService.candidates(track)
        return {"id": str(track["id"]), "kind": "track", "title": track.get("title") or "Без названия",
                "artist": (track.get("publisher_metadata") or {}).get("artist")
                          or (track.get("user") or {}).get("username") or "SoundCloud",
                "source": "soundcloud", "duration": duration, "preview": preview,
                "artist_ref": {"id": str(track["user"]["id"]), "kind": "artist", "source": "soundcloud", "title": track["user"].get("username") or "SoundCloud"} if (track.get("user") or {}).get("id") else None,
                "unavailable": track.get('policy') == 'BLOCK' or protected_only,
                "permalink": track.get("permalink_url") or "",
                "artwork": {"480x480": art.replace("-large.", "-t500x500.")} if art else None}
