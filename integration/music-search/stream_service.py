"""Resolve one public audio format, without fetching every available format."""
from collections import OrderedDict
from concurrent.futures import Future
import re
import threading
import time
from urllib.parse import urlparse
import httpx


class StreamService:
    def __init__(self, search, tokenize):
        self.search = search
        self.tokenize = tokenize
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.pending = {}
        self.slots = threading.BoundedSemaphore(4)

    def resolve(self, track_id):
        if not re.fullmatch(r'[0-9]{1,20}', track_id):
            raise ValueError('Invalid track ID')
        with self.lock:
            hit = self.cache.get(track_id)
            if hit and time.monotonic() - hit[0] < 300:
                return hit[1]
            future = self.pending.get(track_id)
            owner = future is None
            if owner:
                future = self.pending[track_id] = Future()
        if not owner:
            return future.result(timeout=45)
        acquired = False
        try:
            acquired = self.slots.acquire(blocking=False)
            if not acquired:
                raise RuntimeError('Сервер занят подготовкой аудио. Повтори через несколько секунд.')
            track = self.search.get_track(track_id)
            if track.get('policy') == 'BLOCK':
                raise RuntimeError('Этот трек недоступен у источника.')
            formats = self.candidates(track)
            if not formats:
                raise RuntimeError('Нет доступного публичного аудиопотока.')
            for index, candidate in enumerate(formats[:2]):
                url = urlparse(candidate['url'])
                if url.scheme != 'https' or url.netloc != 'api-v2.soundcloud.com':
                    raise ValueError('Unsupported resolver host')
                params = {}
                if track.get('track_authorization'):
                    params['track_authorization'] = track['track_authorization']
                try:
                    body = self.search._get(url.path.lstrip('/'), params)
                    result = self.tokenize(body['url'])
                    break
                except httpx.HTTPStatusError as error:
                    # Missing format: try one alternative. Never retry rate limits
                    # or access restrictions by cycling through formats.
                    if error.response.status_code != 404 or index + 1 >= min(2, len(formats)):
                        raise
            with self.lock:
                self.cache[track_id] = (time.monotonic(), result)
                while len(self.cache) > 200:
                    self.cache.popitem(last=False)
            future.set_result(result)
            return result
        except Exception as error:
            future.set_exception(error)
            raise
        finally:
            if acquired:
                self.slots.release()
            with self.lock:
                self.pending.pop(track_id, None)

    @staticmethod
    def candidates(track):
        result = []
        for item in (track.get('media') or {}).get('transcodings', []):
            fmt = item.get('format') or {}
            if (not item.get('url') or fmt.get('protocol') not in ('hls', 'progressive')
                    or item.get('preset', '').startswith('abr')
                    or item.get('quality') == 'hq' or item.get('drm') or item.get('has_drm')):
                continue
            result.append(item)
        # Keep full-length audio ahead of previews, then HLS and the existing
        # 160 kbps AAC quality. Fast startup does not require reducing quality.
        return sorted(result, key=lambda t: (
            bool(t.get('snipped')), (t.get('format') or {}).get('protocol') != 'hls',
            not t.get('preset', '').startswith('aac_160'),
            not t.get('preset', '').startswith('aac_96')))
