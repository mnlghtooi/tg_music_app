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
        self.slots = threading.BoundedSemaphore(4)

    def search(self, query, page=1):
        if not isinstance(page, int) or not 1 <= page <= 100:
            raise ValueError("Invalid page")
        key = (" ".join(query.casefold().split()), page)
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
                credential = self.credentials()
                for attempt in range(2):
                    response = self.client.get("https://api-v2.soundcloud.com/search/tracks", params={
                        "q": query, "limit": 11, "offset": (page - 1) * 10,
                        "linked_partitioning": 1, "client_id": credential})
                    if response.status_code in (401, 403) and attempt == 0:
                        credential = self.credentials(credential)
                        continue
                    response.raise_for_status()
                    break
                raw = [t for t in response.json().get("collection", []) if t and t.get("id")]
                result = {"data": [self.normalize(t) for t in raw[:10]],
                          "page": page, "page_size": 10, "has_more": len(raw) > 10}
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
        duration = (track.get("duration") or 0) / 1000
        preview = track.get("policy") == "SNIP" or 0 < duration <= 30
        if track.get("policy") == "SNIP":
            duration = min(duration, 30)
        art = track.get("artwork_url") or (track.get("user") or {}).get("avatar_url")
        return {"id": str(track["id"]), "title": track.get("title") or "Без названия",
                "artist": (track.get("publisher_metadata") or {}).get("artist")
                          or (track.get("user") or {}).get("username") or "SoundCloud",
                "source": "soundcloud", "duration": duration, "preview": preview,
                "permalink": track.get("permalink_url") or "",
                "artwork": {"480x480": art.replace("-large.", "-t500x500.")} if art else None}
