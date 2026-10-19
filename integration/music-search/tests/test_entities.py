import html
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from entity_service import EntityService
from search_service import SearchService
from bandcamp_service import BandcampService, identifier
from catalog_service import AudiusSearch


class EntityTests(unittest.TestCase):
    def service(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return EntityService(SearchService(lambda: 'test', client), BandcampService(lambda u: u, client), AudiusSearch(client))

    def test_soundcloud_artist_discography_and_album_tracks_keep_relationships(self):
        calls = []
        user = dict(id=1, username='Artist', description='Artist bio')
        album = dict(id=2, kind='playlist', title='Album', is_album=True, description='Release notes', user=user,
                     tracks=[dict(id=3, title='Song', user=user, duration=180000)])
        def handler(r):
            calls.append(r.url.path)
            return httpx.Response(200, json={'/users/1': user, '/users/1/albums': {'collection': [album]}, '/playlists/2': album}[r.url.path])
        service = self.service(handler)
        a = service.get('soundcloud', 'artist', '1')
        self.assertEqual(a['profile']['description'], 'Artist bio')
        self.assertEqual(a['data'][0]['kind'], 'album')
        b = service.get('soundcloud', 'album', '2')
        self.assertEqual(b['data'][0]['album_ref']['id'], '2')
        self.assertEqual(b['data'][0]['artist_ref']['id'], '1')
        service.get('soundcloud', 'artist', '1')
        self.assertEqual(calls.count('/users/1/albums'), 1)

    def test_soundcloud_track_album_is_provider_relationship_not_name_search(self):
        def handler(r):
            if r.url.path == '/tracks/3': return httpx.Response(200, json=dict(id=3, user=dict(id=1, username='Artist')))
            self.assertEqual(r.url.path, '/tracks/3/albums')
            return httpx.Response(200, json={'collection': [dict(id=2, kind='playlist', title='Album', is_album=True)]})
        result = self.service(handler).get('soundcloud', 'track', '3')
        self.assertEqual(result['album_ref']['id'], '2')

    def test_bandcamp_track_uses_parent_embed_and_rejects_foreign_album(self):
        for parent, expected in [('https://artist.bandcamp.com/album/release', True), ('https://other.bandcamp.com/album/release', False)]:
            embed = dict(artist='Artist', album_title='Release', linkback='https://artist.bandcamp.com/track/song', album_embed_data={'linkback': parent})
            markup = '<div data-embed="' + html.escape(json.dumps(embed), quote=True) + '"></div>'
            service = self.service(lambda r: httpx.Response(200, text=markup))
            result = service.get('bandcamp', 'track', identifier('https://artist.bandcamp.com/track/song'))
            self.assertEqual(bool(result['album_ref']), expected)
            self.assertEqual(result['artist_ref']['title'], 'Artist')

    def test_bandcamp_profile_description_is_text_and_unavailable_tracks_remain_visible(self):
        data = dict(artist='Artist', current=dict(title='Release', about='<p>Release notes</p>'), trackinfo=[dict(title='Song', title_link='/track/song', file=None)])
        markup = '<meta property="og:image" content="https://f4.bcbits.com/img/art.jpg"><div data-tralbum="' + html.escape(json.dumps(data), quote=True) + '"></div>'
        service = self.service(lambda r: httpx.Response(200, text=markup))
        result = service.get('bandcamp', 'album', identifier('https://artist.bandcamp.com/album/release'))
        self.assertEqual(result['profile']['description'], 'Release notes')
        self.assertTrue(result['data'][0]['unavailable'])
        self.assertEqual(result['data'][0]['album_ref']['title'], 'Release')

    def test_audius_artist_and_album_have_tracks_and_owner(self):
        user = dict(id='abc', name='Artist', bio='Biography')
        album = dict(id='xyz', playlist_name='Release', is_album=True, user=user)
        track = dict(id='trk', title='Song', user=user)
        def handler(r):
            values = {'/v1/users/abc': [user], '/v1/users/abc/albums': [album], '/v1/playlists/xyz': [album], '/v1/playlists/xyz/tracks': [track]}
            return httpx.Response(200, json={'data': values[r.url.path]})
        service = self.service(handler)
        artist = service.get('audius', 'artist', 'abc')
        self.assertEqual(artist['profile']['description'], 'Biography')
        self.assertEqual(artist['data'][0]['kind'], 'album')
        release = service.get('audius', 'album', 'xyz')
        self.assertEqual(release['data'][0]['album_ref']['id'], 'xyz')

    def test_invalid_source_ids_pages_and_sections_never_make_network_requests(self):
        handler = Mock(side_effect=AssertionError('Network must not be used'))
        service = self.service(handler)
        for args in [('soundcloud', 'artist', '../1'), ('audius', 'artist', 'x/../y'), ('bandcamp', 'artist', identifier('https://localhost/music')), ('audius', 'artist', 'abc', 0), ('soundcloud', 'artist', '1', 1, '../private')]:
            with self.assertRaises(ValueError): service.get(*args)
        handler.assert_not_called()


if __name__ == '__main__': unittest.main()
