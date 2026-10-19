import html
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search_service import SearchService
from catalog_service import AudiusSearch
from bandcamp_service import BandcampService, identifier


class AvailabilityTests(unittest.TestCase):
    @staticmethod
    def soundcloud_track(**kwargs):
        return SearchService.normalize(dict(id=123, title='Short song', duration=20000, **kwargs))

    def test_short_full_song_is_not_preview(self):
        track = self.soundcloud_track(policy='ALLOW')
        self.assertEqual(track['duration'], 20)
        self.assertFalse(track['preview'])
        self.assertFalse(track['unavailable'])
        self.assertEqual(track['availability'], 'playable')

    def test_short_song_with_usable_transcoding_is_playable_even_without_policy(self):
        track = self.soundcloud_track(media={'transcodings': [
            {'url': 'https://api-v2.soundcloud.com/media/123',
             'preset': 'aac_160', 'format': {'protocol': 'hls'}, 'snipped': False}
        ]})
        self.assertEqual(track['availability'], 'playable')
        self.assertFalse(track['preview'])

    def test_snip_policy_is_preview_even_for_long_metadata(self):
        track = SearchService.normalize(dict(id=7, title='Excerpt',
                                             duration=210000, policy='SNIP'))
        self.assertEqual(track['availability'], 'preview_only')
        self.assertTrue(track['preview'])
        self.assertEqual(track['duration'], 30)
        self.assertFalse(track['unavailable'])

    def test_all_snipped_formats_are_preview_only(self):
        track = self.soundcloud_track(media={'transcodings': [
            {'url': 'https://api-v2.soundcloud.com/media/123',
             'preset': 'aac_96', 'format': {'protocol': 'hls'}, 'snipped': True}
        ]})
        self.assertEqual(track['availability'], 'preview_only')

    def test_explicit_block_and_drm_only_are_unavailable(self):
        blocked = self.soundcloud_track(policy='BLOCK')
        self.assertEqual(blocked['availability'], 'blocked')
        self.assertTrue(blocked['unavailable'])
        protected = self.soundcloud_track(media={'transcodings': [
            {'url': 'https://api-v2.soundcloud.com/media/protected',
             'preset': 'aac_160', 'format': {'protocol': 'hls'}, 'drm': True}
        ]})
        self.assertEqual(protected['availability'], 'blocked')
        self.assertTrue(protected['unavailable'])

    def test_missing_signals_not_conflated_with_block(self):
        track = SearchService.normalize(dict(id=11, title='Unknown', duration=0))
        self.assertEqual(track['availability'], 'unknown')
        self.assertFalse(track['preview'])
        self.assertFalse(track['unavailable'])

    def test_audius_gated_false_streamable_and_unknown(self):
        normal = AudiusSearch.normalize({'id': 'a', 'is_streamable': True, 'duration': 19})
        gated = AudiusSearch.normalize({'id': 'b', 'is_stream_gated': True, 'is_streamable': True})
        blocked = AudiusSearch.normalize({'id': 'c', 'is_streamable': False})
        unknown = AudiusSearch.normalize({'id': 'd'})
        self.assertEqual(normal['availability'], 'playable')
        self.assertEqual(normal['duration'], 19)
        self.assertFalse(normal['preview'])
        self.assertEqual(gated['availability'], 'blocked')
        self.assertEqual(blocked['availability'], 'blocked')
        self.assertEqual(unknown['availability'], 'unknown')

    def test_bandcamp_public_audio_and_private_tracks_have_distinct_states(self):
        album_url = 'https://artist.bandcamp.com/album/example'
        album = {'artist': 'Artist', 'trackinfo': [
            {'title': 'Short complete', 'title_link': '/track/short', 'duration': 21,
             'file': {'mp3-128': 'https://t4.bcbits.com/stream/short'}},
            {'title': 'Private', 'title_link': '/track/private', 'private': True,
             'file': {'mp3-128': 'https://t4.bcbits.com/stream/private'}},
            {'title': 'No public stream', 'title_link': '/track/missing', 'file': None}
        ]}
        def handler(request):
            return httpx.Response(200, text='<script data-tralbum="' +
                html.escape(json.dumps(album), quote=True) + '"></script>')
        service = BandcampService(lambda _: '/token', httpx.Client(
            transport=httpx.MockTransport(handler)))
        items = service.browse('album', identifier(album_url))['data']
        self.assertEqual([t['availability'] for t in items],
                         ['playable', 'blocked', 'blocked'])
        self.assertEqual(items[0]['duration'], 21)

    def test_bandcamp_search_only_has_unknown_availability(self):
        def handler(request):
            return httpx.Response(200, json={'auto': {'results': [
                {'type': 't', 'name': 'Unknown public audio', 'band_name': 'Artist',
                 'item_url_root': 'https://artist.bandcamp.com',
                 'item_url_path': '/track/example'}
            ]}})
        service = BandcampService(lambda _: '/token', httpx.Client(
            transport=httpx.MockTransport(handler)))
        item = service.search('example')['data'][0]
        self.assertEqual(item['availability'], 'unknown')
        self.assertNotIn('unavailable', item)


if __name__ == '__main__':
    unittest.main()
