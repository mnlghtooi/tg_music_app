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
    def test_encrypted_only_soundcloud_audio_is_not_presented_as_playable(self):
        item=SearchService.normalize(dict(id=1,title='Protected',policy='MONETIZE',media={'transcodings':[
            dict(url='https://api-v2.soundcloud.com/media',format={'protocol':'ctr-encrypted-hls'},quality='sq')]}))
        self.assertTrue(item['unavailable'])

    def test_artist_feed_follows_empty_cursor_pages_and_deduplicates(self):
        offsets = []
        def handler(r):
            if r.url.path == '/users/1': return httpx.Response(200, json=dict(id=1, username='Artist'))
            offset = r.url.params.get('offset'); offsets.append(offset)
            if offset == '0': return httpx.Response(200, json={'collection': [], 'next_href': 'https://api-v2.soundcloud.com/users/1/tracks?offset=opaque-first&limit=50&client_id=ignored'})
            entries = [dict(id=i, title='Song '+str(i)) for i in (range(1,7) if offset == 'opaque-first' else range(6,25))]
            body = dict(collection=entries)
            if offset == 'opaque-first': body['next_href'] = 'https://api-v2.soundcloud.com/users/1/tracks?offset=opaque-next&limit=50'
            return httpx.Response(200, json=body)
        service = self.service(handler)
        first = service.get('soundcloud','artist','1',1,'tracks')
        second = service.get('soundcloud','artist','1',2,'tracks')
        self.assertEqual([t['id'] for t in first['data']], [str(i) for i in range(1,11)])
        self.assertEqual([t['id'] for t in second['data']], [str(i) for i in range(11,21)])
        self.assertTrue(second['has_more'])
        self.assertEqual(offsets, ['0', 'opaque-first', 'opaque-next'])

    def test_artist_feed_rejects_foreign_or_repeated_cursors(self):
        for nxt in ['https://localhost/users/1/tracks?offset=bad', 'https://api-v2.soundcloud.com/users/1/tracks?limit=50&offset=0&linked_partitioning=1']:
            service = self.service(lambda r: httpx.Response(200, json={'collection': [], 'next_href': nxt}))
            with self.assertRaises((ValueError,RuntimeError)): service.sc_rows('1','tracks',1)

    def test_overview_keeps_successful_sections_when_one_source_request_fails(self):
        service = self.service(lambda r: httpx.Response(500))
        def get(source, kind, item_id, page=1, section=''):
            if section == 'popular': raise RuntimeError('unavailable')
            return {'profile': {'title':'Artist','description':'https://artist.bandcamp.com/album/release https://localhost/private'}, 'data':[], 'has_more':False}
        service.get = get
        overview = service.overview('soundcloud','1')
        self.assertTrue(overview['blocks'][0]['error'])
        self.assertEqual(len(overview['blocks']),3)
        self.assertEqual(len(overview['links']),1)
        self.assertEqual(overview['links'][0]['entity']['kind'],'album')

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
