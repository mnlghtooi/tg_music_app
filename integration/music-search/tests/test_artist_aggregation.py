import copy
import http.client
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artist_aggregation import ArtistAggregateService
from music_providers import PROVIDERS, artist_sources, catalog_sources, public_capabilities


def song(source, id_, name='Portishead', title='Roads', availability='playable'):
    return dict(source=source, id=str(id_), kind='track', artist=name,
                title=title, duration=180, availability=availability)


class FakeEntities:
    def __init__(self, title='Portishead'):
        self.calls = 0
        self.title = title

    def overview(self, source, item_id):
        self.calls += 1
        return dict(
            profile=dict(title=self.title, kind='artist', source=source, id=item_id),
            blocks=[dict(section='popular', data=[song(source, '1')], has_more=False),
                    dict(section='albums', data=[], has_more=False)],
            links=[],
        )


class FakeCatalog:
    def __init__(self, responses=None, failure=None):
        self.services = {source: object() for source in catalog_sources()}
        self.responses = responses or {}
        self.failure = failure or set()
        self.calls = []

    def query(self, source, query, page, kind):
        self.calls.append((source, query, page, kind))
        if source in self.failure:
            raise RuntimeError('provider unavailable')
        items = self.responses.get((source, page), [])
        return {'data': copy.deepcopy(items), 'has_more': bool(self.responses.get((source, page + 1)))}


class ArtistArchitectureTests(unittest.TestCase):
    def test_registry_keeps_three_real_sources_separate_from_playback(self):
        self.assertEqual(catalog_sources(), ('soundcloud', 'bandcamp', 'audius'))
        self.assertEqual(artist_sources(), catalog_sources())
        self.assertEqual([p.source for p in PROVIDERS], list(catalog_sources()))
        self.assertTrue(all(p.playback == 'in_app' for p in PROVIDERS))
        self.assertNotIn('vk', catalog_sources())
        self.assertNotIn('credentials', str(public_capabilities()))

    def test_cross_provider_candidates_keep_original_source_and_id(self):
        primary = FakeEntities()
        catalog = FakeCatalog({
            ('bandcamp', 1): [
                song('bandcamp', '42'),
                song('bandcamp', '43', name='Portishead tribute'),
                song('bandcamp', '44', availability='blocked'),
                song('bandcamp', '45', availability='preview_only'),
                song('bandcamp', '46', name='Different Artist', title='Portishead - Glory Box')],
            ('audius', 1): [
                song('audius', '11', title='Portishead - Roads', name='someone else', availability='unknown'),
                song('audius', '12', name='Portishead', title='Sour Times'),
                song('audius', '12', name='Portishead', title='Sour Times')],
        })
        svc = ArtistAggregateService(primary, catalog)
        result = svc.overview('soundcloud', '123')
        self.assertEqual(primary.calls, 1)
        self.assertEqual(result['blocks'][0]['section'], 'popular')
        self.assertEqual([(t['source'], t['id']) for t in result['blocks'][-1]['data']],
                         [('bandcamp', '42'), ('audius', '12')])
        self.assertTrue(all(t['verified_artist'] is False for t in result['blocks'][-1]['data']))
        self.assertTrue(all(t['discovery'] == 'artist_name_only' for t in result['blocks'][-1]['data']))
        self.assertEqual(result['aggregation']['status'], 'complete')
        self.assertFalse(result['aggregation']['identity_verified'])
        self.assertNotIn('vk', result['aggregation']['searched_sources'])

    def test_unknown_candidates_are_not_mislabeled_as_verified_playable(self):
        c = FakeCatalog({('audius', 1): [song('audius', 'one', availability='unknown')]})
        a = ArtistAggregateService(FakeEntities(), c).overview('soundcloud', '123')
        candidate = a['blocks'][-1]['data'][0]
        self.assertEqual(candidate['availability'], 'unknown')
        self.assertFalse(candidate['verified_artist'])

    def test_one_unavailable_provider_keeps_primary_and_other_sources(self):
        c = FakeCatalog({('bandcamp', 1): [song('bandcamp', 2)]}, failure={'audius'})
        result = ArtistAggregateService(FakeEntities(), c).overview('soundcloud', '123')
        self.assertEqual(result['aggregation']['failed_sources'], ['audius'])
        self.assertEqual(result['aggregation']['status'], 'partial')
        self.assertEqual(len(result['blocks'][-1]['data']), 1)
        self.assertEqual(result['blocks'][0]['data'][0]['id'], '1')

    def test_caches_aggregation_and_preserves_provider_documents(self):
        e = FakeEntities()
        data = {('audius', 1): [song('audius', 2)]}
        source_snapshot = copy.deepcopy(data)
        c = FakeCatalog(data)
        svc = ArtistAggregateService(e, c, ttl=100)
        first = svc.overview('soundcloud', '123')
        second = svc.overview('soundcloud', '123')
        self.assertEqual(first, second)
        self.assertEqual(e.calls, 1)
        self.assertEqual(data, source_snapshot)
        self.assertEqual(len(c.calls), 2)

    def test_weak_artist_name_does_not_trigger_foreign_search(self):
        e = FakeEntities(title='DJ')
        c = FakeCatalog()
        data = ArtistAggregateService(e, c).overview('soundcloud', '1')
        self.assertEqual(data['aggregation']['status'], 'skipped')
        self.assertEqual(c.calls, [])

    def test_search_is_bounded_two_pages_per_provider(self):
        c = FakeCatalog({('bandcamp', 1): [song('bandcamp', '1')],
                         ('bandcamp', 2): [song('bandcamp', '2')],
                         ('bandcamp', 3): [song('bandcamp', '3')]})
        data = ArtistAggregateService(FakeEntities(), c).overview('soundcloud', '1')
        self.assertEqual([t['id'] for t in data['blocks'][-1]['data']], ['1', '2'])
        self.assertEqual(sorted(c.calls), sorted([
            ('bandcamp', 'Portishead', 1, 'track'),
            ('bandcamp', 'Portishead', 2, 'track'),
            ('audius', 'Portishead', 1, 'track')]))

    def test_rejects_invalid_provider_and_missing_id(self):
        svc = ArtistAggregateService(FakeEntities(), FakeCatalog())
        for source, identifier in [('vk', '1'), ('soundcloud', ''), ('soundcloud', 'z'*2049)]:
            with self.assertRaises(ValueError):
                svc.overview(source, identifier)

    def test_merged_api_requires_cookie_and_old_api_is_unchanged(self):
        import server
        from http.server import ThreadingHTTPServer
        instance = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        worker = threading.Thread(target=instance.serve_forever, daemon=True)
        worker.start()
        try:
            for cookie, expected_code in [('', 403), ('musiclab_access='+server.KEY, 200)]:
                connection = http.client.HTTPConnection('127.0.0.1', instance.server_port, timeout=3)
                with patch.object(server.ARTISTS, 'overview', return_value={'blocks': [], 'aggregation': {}}) as merged:
                    headers = {'Cookie': cookie} if cookie else {}
                    connection.request('GET', '/api/artists/merged?source=soundcloud&id=123', headers=headers)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, expected_code)
                    self.assertEqual(merged.call_count, int(expected_code == 200))
                connection.close()
            connection = http.client.HTTPConnection('127.0.0.1', instance.server_port, timeout=3)
            connection.request('GET', '/api/artists/merged?source=vk&id=123',
                               headers={'Cookie': 'musiclab_access='+server.KEY})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 400)
            connection.close()
        finally:
            instance.shutdown()
            instance.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
