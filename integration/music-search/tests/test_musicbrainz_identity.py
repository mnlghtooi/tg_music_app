import http.client
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog_models import Artist, Release, Recording, ProviderRecording, RecordingAvailability, mbid, isrc
from musicbrainz_identity import MusicBrainzIdentity, profile_url


MBID='a9e8df35-2e30-4a44-a658-018680bb13a9'
OTHER='3f016e1e-64a0-42d7-a437-41ab2ccf5ba1'


def fixture(relation='soundcloud', resource='https://soundcloud.com/portishead',
            second=False):
    artist={'id':MBID,'name':'Portishead','score':100}
    other={'id':OTHER,'name':'Portishead','score':100,'disambiguation':'Another act'}
    seen=[]
    def handler(req):
        seen.append(req)
        assert req.url.host=='musicbrainz.org'
        assert req.headers.get('user-agent','').startswith('MusicLab/')
        assert req.url.params.get('fmt')=='json'
        if req.url.path=='/ws/2/artist/':
            return httpx.Response(200,json={'artists':[artist,other] if second else [artist]})
        if req.url.path=='/ws/2/artist/'+MBID:
            return httpx.Response(200,json={'relations':[{'type':relation,'url':{'resource':resource}}]})
        if req.url.path=='/ws/2/artist/'+OTHER:
            return httpx.Response(200,json={'relations':[]})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler)),seen


class CatalogModelTests(unittest.TestCase):
    def test_identity_requires_mbid_not_shared_names_or_isrc(self):
        a=Artist('Portishead')
        b=Artist('Portishead')
        self.assertFalse(a.same_identity(b))
        verified=Artist('Portishead',MBID)
        self.assertTrue(verified.same_identity(Artist('Portishead',MBID)))
        self.assertFalse(verified.same_identity(Artist('Portishead',OTHER)))
        one=Recording('Roads',a,isrc='GB-AAA-95-00001')
        two=Recording('Roads live',a,isrc='GBAAA9500001',version='live')
        self.assertEqual(one.isrc,'GBAAA9500001')
        self.assertFalse(one.same_identity(two),'matching ISRC is insufficient proof')
        self.assertFalse(Recording('Roads',verified,MBID).same_identity(one))
        self.assertEqual(Release('Dummy',verified).schema_version,1)

    def test_provider_recording_preserves_original_playback_id_and_status(self):
        item=ProviderRecording('soundcloud','raw-123',
            availability=RecordingAvailability.UNKNOWN)
        self.assertEqual(item.provider_key,'soundcloud:raw-123')
        self.assertEqual(item.availability.value,'unknown')
        with self.assertRaises(ValueError):ProviderRecording('vk','123')
        with self.assertRaises(ValueError):ProviderRecording('soundcloud','123',availability='playable')
        with self.assertRaises(ValueError):mbid('fake')
        with self.assertRaises(ValueError):isrc('short')

    def test_mbid_does_not_change_source_owned_ids(self):
        self.assertEqual(mbid(str(UUID(MBID)).upper()),MBID)
        self.assertEqual(Artist('A',MBID).schema_version,1)


class MusicBrainzResolverTests(unittest.TestCase):
    def test_exact_musicbrainz_relationship_verifies_only_current_profile(self):
        client,seen=fixture()
        svc=MusicBrainzIdentity(client=client,interval=0)
        result=svc.resolve('Portishead','soundcloud','https://soundcloud.com/portishead/')
        self.assertEqual(result['status'],'verified_profile_link')
        self.assertTrue(result['identity_verified'])
        self.assertEqual(result['artist']['mbid'],MBID)
        self.assertEqual(result['matched_source'],'soundcloud')
        self.assertEqual(len(seen),2)
        self.assertEqual(svc.resolve('Portishead','soundcloud','https://soundcloud.com/portishead/'),result)
        self.assertEqual(len(seen),2,'cached artist identity queries do not hammer MusicBrainz')

    def test_name_only_even_when_musicbrainz_search_score_is_100(self):
        client,seen=fixture(resource='https://soundcloud.com/cover-account')
        result=MusicBrainzIdentity(client=client,interval=0).resolve(
            'Portishead','soundcloud','https://soundcloud.com/portishead')
        self.assertEqual(result['status'],'name_only')
        self.assertFalse(result['identity_verified'])
        self.assertEqual(result['candidates'][0]['mbid'],MBID)

    def test_cross_source_or_ended_relationship_never_verifies(self):
        client,_=fixture(relation='bandcamp',
                         resource='https://soundcloud.com/portishead')
        r=MusicBrainzIdentity(client=client,interval=0).resolve(
            'Portishead','soundcloud','https://soundcloud.com/portishead')
        self.assertFalse(r['identity_verified'])
        client,_=fixture(relation='bandcamp',
                         resource='http://portishead.bandcamp.com/music')
        verified=MusicBrainzIdentity(client=client,interval=0).resolve(
            'Portishead','bandcamp','https://portishead.bandcamp.com/')
        self.assertTrue(verified['identity_verified'])

    def test_generic_and_unsafe_urls_cannot_be_identity_proof(self):
        urls=[
            'https://soundcloud.com.evil.test/portishead',
            'http://127.0.0.1/internal',
            'https://soundcloud.com/portishead/sets/other',
            'https://user:pass@soundcloud.com/portishead',
            'javascript:alert(1)',
            'https://soundcloud.com:444/portishead',
        ]
        for url in urls:self.assertIsNone(profile_url('soundcloud',url),url)
        self.assertEqual(profile_url('bandcamp','https://portishead.bandcamp.com/music'),
                         'portishead.bandcamp.com')
        self.assertIsNone(profile_url('bandcamp','https://evilbandcamp.com'))
        client,seen=fixture()
        r=MusicBrainzIdentity(client=client,interval=0).resolve(
            'Portishead','soundcloud','https://soundcloud.com.evil.test/foo')
        self.assertEqual(r['status'],'missing_profile')
        self.assertEqual(seen,[])

    def test_one_failed_request_degrades_without_music_player_change(self):
        def handler(_):return httpx.Response(503)
        client=httpx.Client(transport=httpx.MockTransport(handler))
        result=MusicBrainzIdentity(client=client,interval=0).resolve(
            'Portishead','soundcloud','https://soundcloud.com/portishead')
        self.assertEqual(result['status'],'unavailable')
        self.assertFalse(result['identity_verified'])

    def test_search_limit_two_exact_name_candidates_with_different_people(self):
        client,seen=fixture(second=True)
        result=MusicBrainzIdentity(client=client,interval=0).resolve(
            'Portishead','soundcloud','https://soundcloud.com/portishead')
        self.assertTrue(result['identity_verified'])
        self.assertEqual(result['artist']['mbid'],MBID)
        self.assertEqual(len(seen),3)

    def test_invalid_and_injected_names_rejected_before_upstream(self):
        client,seen=fixture()
        resolver=MusicBrainzIdentity(client=client,interval=0)
        for name in ['DJ','x'*101,'Portishead" OR artist:*','']:
            with self.assertRaises(ValueError):
                resolver.resolve(name,'soundcloud','https://soundcloud.com/portishead')
        self.assertEqual(seen,[])

    def test_identity_api_protected_and_legacy_endpoints_not_changed(self):
        import server
        from http.server import ThreadingHTTPServer
        instance=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        worker=threading.Thread(target=instance.serve_forever,daemon=True)
        worker.start()
        try:
            path='/api/artists/identity?name=Portishead&source=soundcloud&url=https%3A%2F%2Fsoundcloud.com%2Fportishead'
            with patch.object(server.MUSICBRAINZ,'resolve',return_value={
                'status':'name_only','identity_verified':False,'candidates':[]}) as resolved:
                for cookie,expected in [('',403),('musiclab_access='+server.KEY,200)]:
                    connection=http.client.HTTPConnection('127.0.0.1',instance.server_port,timeout=3)
                    connection.request('GET',path,headers={'Cookie':cookie} if cookie else {})
                    result=connection.getresponse()
                    result.read()
                    self.assertEqual(result.status,expected)
                    connection.close()
                self.assertEqual(resolved.call_count,1)
            connection=http.client.HTTPConnection('127.0.0.1',instance.server_port,timeout=3)
            connection.request('GET','/api/artists/identity?name=DJ&source=vk&url=garbage',
                               headers={'Cookie':'musiclab_access='+server.KEY})
            response=connection.getresponse()
            self.assertEqual(response.status,400)
            response.read()
            connection.close()
        finally:
            instance.shutdown();instance.server_close();worker.join()


if __name__=='__main__':
    unittest.main()
