import html
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from bandcamp_service import BandcampService, BandcampError, identifier, decode_id, public_url


class BandcampTests(unittest.TestCase):
    def service(self, handler):
        return BandcampService(lambda url: '/token', httpx.Client(transport=httpx.MockTransport(handler)))

    def test_mixed_results_and_cached_pagination(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.method, 'POST')
            self.assertEqual(json.loads(request.content)['search_text'], 'test')
            return httpx.Response(200, json={'auto': {'results': [
                {'type': kind, 'name': str(i), 'band_name': 'Artist', 'item_url_root': 'https://artist.bandcamp.com',
                 'item_url_path': '' if kind == 'b' else '/' + ('album' if kind == 'a' else 'track') + '/song-' + str(i)}
                for i, kind in enumerate(['b', 'a', 't'] * 8)]}})
        service = self.service(handler)
        first = service.search('test', 1); second = service.search('test', 2); last = service.search('test', 3)
        self.assertEqual([t['kind'] for t in first['data'][:3]], ['artist', 'album', 'track'])
        self.assertEqual(len(first['data']), 10); self.assertEqual(len(second['data']), 10)
        self.assertEqual(len(last['data']), 4); self.assertFalse(last['has_more'])
        self.assertEqual(len(calls), 1)

    def test_album_metadata_reused_for_stream(self):
        calls = []
        data = {'art_id': 123, 'artist': 'Artist', 'trackinfo': [
            {'title': 'Playable', 'title_link': '/track/playable', 'duration': 120,
             'file': {'mp3-128': 'https://t4.bcbits.com/stream/audio'}},
            {'title': 'Private', 'title_link': '/track/private', 'private': True,
             'file': {'mp3-128': 'https://t4.bcbits.com/stream/private'}},
            {'title': 'Unavailable', 'title_link': '/track/unavailable', 'file': None}]}
        def handler(request):
            calls.append(request)
            return httpx.Response(200, text='<script data-tralbum="' + html.escape(json.dumps(data), quote=True) + '"></script>')
        service = self.service(handler)
        result = service.browse('album', identifier('https://artist.bandcamp.com/album/release'))
        self.assertEqual(len(result['data']), 3)
        self.assertFalse(result['data'][0]['unavailable'])
        self.assertEqual(service.stream(result['data'][0]['id']), '/token')
        for item in result['data'][1:]:
            self.assertTrue(item['unavailable'])
            with self.assertRaises(BandcampError): service.stream(item['id'])
        self.assertEqual(len(calls), 1)

    def test_artist_releases(self):
        def handler(request):
            self.assertEqual(request.url.path, '/music')
            return httpx.Response(200, text='<ol><li class="music-grid-item"><a href="/album/one"><img src="https://f4.bcbits.com/one.jpg"><p class="title">One &amp; Two</p></a></li></ol>')
        service = self.service(handler)
        result = service.browse('artist', identifier('https://artist.bandcamp.com'))
        self.assertEqual(result['data'][0]['title'], 'One & Two')
        self.assertEqual(result['data'][0]['kind'], 'album')

    def test_url_validation_and_media_allowlist(self):
        for url in ['http://artist.bandcamp.com/track/x', 'https://evil.test/track/x',
                    'https://artist.bandcamp.com.evil.test/track/x', 'https://artist.bandcamp.com:444/track/x',
                    'https://user:secret@artist.bandcamp.com/track/x']:
            with self.assertRaises(ValueError): public_url(url, 'track')
        with self.assertRaises(ValueError): decode_id(identifier('https://evil.test/track/x'), 'track')
        self.assertEqual(decode_id(identifier('https://artist.bandcamp.com/track/x'), 'track'),
                         'https://artist.bandcamp.com/track/x')

    def test_challenge_is_reported_not_silently_empty(self):
        service = self.service(lambda request: httpx.Response(200, text='<title>Client Challenge</title>'))
        with self.assertRaises(BandcampError): service.search('test')
        with self.assertRaises(BandcampError): service.browse('album', identifier('https://artist.bandcamp.com/album/x'))
        self.assertFalse(service.pending)
