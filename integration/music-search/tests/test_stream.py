import http.client
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from search_service import SearchService
from stream_service import StreamService


def track(formats=None):
    return {'id': 12, 'kind': 'track', 'title': 'Song', 'track_authorization': 'public-auth',
            'media': {'transcodings': formats if formats is not None else [
                {'url': 'https://api-v2.soundcloud.com/media/160', 'preset': 'aac_160k',
                 'quality': 'sq', 'format': {'protocol': 'hls'}},
                {'url': 'https://api-v2.soundcloud.com/media/96', 'preset': 'aac_96k',
                 'quality': 'lq', 'format': {'protocol': 'hls'}}]}}


class StreamTests(unittest.TestCase):
    def make(self, handler):
        search = SearchService(lambda *a: 'public-client', httpx.Client(transport=httpx.MockTransport(handler)))
        search.remember_tracks({'collection': [track()]})
        return search, StreamService(search, lambda url: url)

    def test_reuses_metadata_and_only_one_format(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            self.assertEqual(request.url.params['track_authorization'], 'public-auth')
            return httpx.Response(200, json={'url': 'https://cdn.sndcdn.com/song.m3u8'})
        search, stream = self.make(handler)
        result = stream.resolve('12')
        self.assertEqual(calls, ['/media/160'])
        self.assertEqual(stream.resolve('12'), result)
        self.assertEqual(len(calls), 1)

    def test_fetches_metadata_for_saved_track(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, json=track() if request.url.path == '/tracks/12'
                                  else {'url': 'https://cdn.sndcdn.com/song.m3u8'})
        search, stream = self.make(handler)
        search.track_metadata.clear()
        stream.resolve('12')
        self.assertEqual(calls, ['/tracks/12', '/media/160'])

    def test_rate_limit_is_not_retried(self):
        calls = []
        def handler(request):
            calls.append(1)
            return httpx.Response(429)
        _, stream = self.make(handler)
        with self.assertRaises(httpx.HTTPStatusError): stream.resolve('12')
        self.assertEqual(len(calls), 1)
        self.assertFalse(stream.pending)

    def test_missing_format_has_single_fallback(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(404 if len(calls) == 1 else 200,
                                  json={'url': 'https://cdn.sndcdn.com/song.m3u8'})
        _, stream = self.make(handler)
        stream.resolve('12')
        self.assertEqual(calls, ['/media/160', '/media/96'])

    def test_invalid_drm_and_blocked_tracks(self):
        search, stream = self.make(lambda r: self.fail('No upstream request expected'))
        with self.assertRaises(ValueError): stream.resolve('../12')
        metadata = track()
        metadata['policy'] = 'BLOCK'
        search.remember_tracks([metadata])
        with self.assertRaises(RuntimeError): stream.resolve('12')
        metadata = track([{'url': 'https://api-v2.soundcloud.com/drm', 'preset': 'aac_160k',
                           'format': {'protocol': 'ctr-hls'}}])
        search.remember_tracks([metadata])
        with self.assertRaises(RuntimeError): stream.resolve('12')

    def test_same_stream_requests_coalesce(self):
        from concurrent.futures import ThreadPoolExecutor
        calls = []; entered = threading.Event(); release = threading.Event()
        def handler(request):
            calls.append(1); entered.set(); release.wait(2)
            return httpx.Response(200, json={'url': 'https://cdn.sndcdn.com/song.m3u8'})
        _, stream = self.make(handler)
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(stream.resolve, '12'); entered.wait(2)
            second = pool.submit(stream.resolve, '12'); release.set()
            self.assertEqual(first.result(), second.result())
        self.assertEqual(len(calls), 1)


class MediaProxyTests(unittest.TestCase):
    def test_forwards_first_chunk_before_upstream_finishes(self):
        import server
        release = threading.Event(); waiting = threading.Event()
        class Body(httpx.SyncByteStream):
            def __iter__(self):
                yield b'a' * 16384
                waiting.set()
                if not release.wait(3): raise TimeoutError('Proxy waited for full segment')
                yield b'b' * 16384
        def handler(request):
            self.assertEqual(request.headers['Range'], 'bytes=0-32767')
            return httpx.Response(206, headers={'Content-Type': 'audio/mp4', 'Content-Length': '32768',
                                                'Content-Range': 'bytes 0-32767/99999'}, stream=Body())
        client = httpx.Client(transport=httpx.MockTransport(handler))
        local_server = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=local_server.serve_forever, daemon=True).start()
        try:
            with patch.object(server, 'MEDIA_CLIENT', client):
                token = server.media_token('https://cdn.sndcdn.com/segment.m4a')
                connection = http.client.HTTPConnection('127.0.0.1', local_server.server_port, timeout=2)
                connection.request('GET', token, headers={'Cookie': 'musiclab_access=' + server.KEY,
                                                         'Range': 'bytes=0-32767'})
                response = connection.getresponse()
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(16384), b'a' * 16384)
                self.assertTrue(waiting.wait(1))
                self.assertFalse(release.is_set())
                release.set()
                self.assertEqual(response.read(), b'b' * 16384)
                connection.close()
        finally:
            release.set(); local_server.shutdown(); local_server.server_close(); client.close()
