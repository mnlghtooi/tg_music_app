import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migration_service import MigrationService, MigrationError, playlist_id, match_score

ID = '01Ysxbk3Zpf0KcwnPmIy5h'
URL = 'https://open.spotify.com/playlist/' + ID

class SearchStub:
    def search(self, query, page, kind):
        return {'data': [
            {'id':'1','kind':'track','source':'soundcloud','title':'Song (Live)','artist':'Artist','duration':180},
            {'id':'2','kind':'track','source':'soundcloud','title':'Song','artist':'Artist','duration':180},
            {'id':'3','kind':'track','source':'soundcloud','title':'Song','artist':'Artist','duration':180,'unavailable':True}]}

class StaticCatalog:
    def lookup(self, query, sources=None):
        return {'data': SearchStub().search(query,1,'track')['data'],
                'checked_sources':['soundcloud','bandcamp','audius'],'failed_sources':[]}
    def hydrate(self, item): return item

def embed(tracks=2, item_id=ID):
    raw = [{'uri':'spotify:track:'+str(i).zfill(22),'title':'Song','subtitle':'Artist','duration':180000,
            'entityType':'track','audioPreview':{'url':'https://private-audio.test'}} for i in range(tracks)]
    data={'props':{'pageProps':{'state':{'data':{'entity':{
        'id':item_id,'type':'playlist','name':'Public mix','trackList':raw}}}}}}
    return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'

class MigrationTests(unittest.TestCase):
    def service(self, handler):
        return MigrationService(SearchStub(), SearchStub(), catalog=StaticCatalog(), client=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_public_url_validation(self):
        self.assertEqual(playlist_id(URL+'?si=tracking'),ID)
        self.assertEqual(playlist_id('https://open.spotify.com/intl-ru/playlist/'+ID),ID)
        for url in ['http://127.0.0.1/playlist/'+ID,'https://open.spotify.com.evil.test/playlist/'+ID,
                    'https://user:secret@open.spotify.com/playlist/'+ID,'https://open.spotify.com:443/playlist/'+ID,
                    'https://open.spotify.com/collection/tracks','https://open.spotify.com/playlist/../me','spotify:playlist:'+ID]:
            with self.assertRaises(MigrationError): playlist_id(url)

    def test_full_public_list_cache_and_metadata_only(self):
        calls=[]
        def handler(r):
            calls.append(r)
            self.assertNotIn('authorization',r.headers)
            return httpx.Response(200,text=embed() if '/embed/' in r.url.path else '<meta name="music:song_count" content="2">')
        service=self.service(handler)
        result=service.public_playlist(URL+'?si=tracking')
        self.assertTrue(result['complete']);self.assertEqual(result['received'],2)
        self.assertEqual(result['data'][0]['duration'],180)
        self.assertEqual(result['source_url'],URL)
        self.assertNotIn('private-audio',json.dumps(result))
        self.assertEqual(service.public_playlist(URL),result)
        self.assertEqual(len(calls),2)

    def test_truncated_and_unknown_totals_are_never_reported_as_full(self):
        for page,total in [('<meta name="music:song_count" content="250">',250),('<html>no count</html>',None)]:
            service=self.service(lambda r:httpx.Response(200,text=embed() if '/embed/' in r.url.path else page))
            result=service.public_playlist(URL)
            self.assertFalse(result['complete']);self.assertEqual(result['total'],total)
            self.assertEqual(result['received'],2)

    def test_private_changed_or_wrong_playlist_fails(self):
        for content in ['<html>Log in</html>',embed(item_id='A'*22)]:
            service=self.service(lambda r:httpx.Response(200,text=content))
            with self.assertRaises(MigrationError):service.public_playlist(URL)
        service=self.service(lambda r:httpx.Response(403))
        with self.assertRaises(MigrationError):service.public_playlist(URL)

    def test_redirects_cannot_leave_provider_or_playlist(self):
        for location in ['http://127.0.0.1/secret','https://evil.test/','https://open.spotify.com/playlist/'+'A'*22]:
            calls=[]
            def handler(r):
                calls.append(r)
                return httpx.Response(302,headers={'Location':location})
            service=self.service(handler)
            with self.assertRaises(MigrationError):service.public_playlist(URL)
            self.assertEqual(len(calls),1)

    def test_match_versions_blocked_and_invalid_input(self):
        service=self.service(lambda r:httpx.Response(500))
        result=service.resolve({'title':'Song','artist':'Artist','duration':180})['data']
        self.assertEqual(result[0]['id'],'2');self.assertEqual(result[0]['score'],1)
        self.assertLess(result[1]['score'],.9)
        self.assertNotIn('3',[t['id'] for t in result])
        with self.assertRaises(MigrationError):service.resolve({'title':'Song'})
        with self.assertRaises(MigrationError):service.resolve({'title':'Song','artist':'Artist','duration':float('nan')})
        self.assertLess(match_score({'title':'Song','artist':'Artist','duration':180},
            {'title':'Song','artist':'Artist','preview':True}),.9)

    def test_bandcamp_duration_is_hydrated_before_automatic_selection(self):
        catalog=Mock()
        candidate={'id':'bc','source':'bandcamp','kind':'track','title':'Song','artist':'Artist','duration':0}
        catalog.lookup.return_value={'data':[candidate],'checked_sources':['soundcloud','bandcamp','audius'],'failed_sources':[]}
        catalog.hydrate.return_value={**candidate,'duration':180,'unavailable':False}
        service=MigrationService(None,None,catalog=catalog)
        result=service.resolve({'title':'Song','artist':'Artist','duration':180})
        self.assertEqual(result['state'],'matched')
        self.assertEqual(result['selected']['source'],'bandcamp')
        catalog.hydrate.assert_called_once_with(candidate)

    def test_missing_duration_and_failed_catalog_are_not_silently_auto_imported(self):
        catalog=Mock()
        catalog.lookup.return_value={'data':[],'checked_sources':['soundcloud','audius'],'failed_sources':['bandcamp']}
        service=MigrationService(None,None,catalog=catalog)
        result=service.resolve({'title':'Song','artist':'Artist','duration':180})
        self.assertEqual(result['state'],'incomplete')
        self.assertEqual(result['failed_sources'],['bandcamp'])
        catalog.lookup.return_value={'data':[],'checked_sources':[],'failed_sources':['soundcloud','bandcamp','audius']}
        with self.assertRaises(MigrationError) as error:service.resolve({'title':'Song','artist':'Artist'})
        self.assertEqual(error.exception.code,'sources_unavailable')

class MigrationHTTPTests(unittest.TestCase):
    def setUp(self):
        import server
        self.module=server;self.old=server.MIGRATION
        server.MIGRATION=MigrationService(SearchStub(),SearchStub(),catalog=StaticCatalog(),client=httpx.Client(transport=httpx.MockTransport(
            lambda r:httpx.Response(200,text=embed() if '/embed/' in r.url.path else '<meta name="music:song_count" content="2">'))))
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.origin='http://127.0.0.1:'+str(self.http.server_port)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.client=httpx.Client(base_url=self.origin,headers={'Cookie':'musiclab_access='+server.KEY,'Origin':self.origin})
    def tearDown(self):
        self.client.close();self.http.shutdown();self.http.server_close();self.module.MIGRATION=self.old
    def test_auth_origin_and_public_import(self):
        self.assertEqual(httpx.get(self.origin+'/api/migration/status').status_code,403)
        self.assertEqual(self.client.post('/api/migration/public-playlist',json={'url':URL},headers={'Origin':'https://evil.test'}).status_code,403)
        response=self.client.post('/api/migration/public-playlist',json={'url':URL})
        self.assertEqual(response.status_code,200);self.assertTrue(response.json()['complete'])
        self.assertEqual(self.client.post('/api/migration/spotify/start',json={}).status_code,404)
    def test_resolve_and_invalid_form(self):
        response=self.client.post('/api/migration/resolve',content='track=bad',headers={'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.post('/api/migration/resolve',json={'track':{},'source':'soundcloud'}).status_code,400)
        response=self.client.post('/api/migration/resolve',json={'track':{'title':'Song','artist':'Artist','duration':180},'source':'soundcloud'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['data'][0]['id'],'2')

    def test_default_all_catalog_search_and_explicit_selection(self):
        with patch.object(self.module.CATALOG,'search',return_value={'data':[],'has_more':False}) as unified:
            response=self.client.get('/api/search?q=Song')
            self.assertEqual(response.status_code,200)
            unified.assert_called_once_with('Song',1,'all')
        with patch.object(self.module.SEARCH_SERVICE,'search',return_value={'data':[],'has_more':False}) as single:
            response=self.client.get('/api/search?q=Song&source=soundcloud&type=track')
            self.assertEqual(response.status_code,200)
            single.assert_called_once_with('Song',1,'track')
