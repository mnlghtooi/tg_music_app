import sys
from pathlib import Path
import threading
import unittest
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog_service import CatalogService, CatalogError, AudiusSearch


class Source:
    def __init__(self,name,count=15,barrier=None,fail=False):
        self.name=name;self.count=count;self.barrier=barrier;self.fail=fail;self.calls=[]
    def search(self,q,page=1,kind='all'):
        self.calls.append((q,page,kind))
        if self.barrier:self.barrier.wait(timeout=2)
        if self.fail:raise RuntimeError('private upstream error')
        offset=(page-1)*10
        return {'data':[{'id':str(i),'source':self.name,'kind':'track','title':'Song','artist':'Artist','duration':180}
            for i in range(offset,min(offset+10,self.count))], 'has_more':offset+10<self.count}


class CatalogTests(unittest.TestCase):
    def test_all_three_sources_are_searched_concurrently(self):
        barrier=threading.Barrier(3)
        services=[Source(name,barrier=barrier) for name in CatalogService.SOURCES]
        catalog=CatalogService(*services)
        result=catalog.lookup('Song')
        self.assertEqual(result['checked_sources'],list(CatalogService.SOURCES))
        self.assertEqual(len(result['data']),30)
        self.assertEqual(result['failed_sources'],[])
        catalog.lookup('Song')
        self.assertEqual([len(s.calls) for s in services],[1,1,1])

    def test_failure_keeps_other_sources_and_short_cooldown_prevents_retry_storm(self):
        services=[Source('soundcloud'),Source('bandcamp',fail=True),Source('audius')]
        catalog=CatalogService(*services)
        result=catalog.lookup('Song')
        self.assertEqual(result['failed_sources'],['bandcamp'])
        self.assertEqual({t['source'] for t in result['data']},{'soundcloud','audius'})
        catalog.lookup('Different song')
        self.assertEqual(len(services[1].calls),1)
        self.assertEqual(len(services[0].calls),2)

    def test_merged_pagination_does_not_discard_or_repeat_tracks(self):
        catalog=CatalogService(Source('soundcloud',13),Source('bandcamp',4),Source('audius',21))
        items=[]
        for page in range(1,5):
            result=catalog.search('Song',page,'track');items.extend(result['data'])
        keys=[(t['source'],t['id']) for t in items]
        self.assertEqual(len(keys),38)
        self.assertEqual(len(set(keys)),38)
        self.assertFalse(result['has_more'])
        self.assertEqual([t['source'] for t in items[:3]],list(CatalogService.SOURCES))

    def test_all_sources_unavailable_are_not_reported_as_empty_search(self):
        catalog=CatalogService(*(Source(name,fail=True) for name in CatalogService.SOURCES))
        with self.assertRaises(CatalogError):catalog.search('Song')

    def test_audius_normalization_preserves_availability_and_duration(self):
        def handler(request):
            self.assertEqual(request.url.path,'/v1/tracks/search')
            self.assertEqual(request.url.params['offset'],'10')
            return httpx.Response(200,json={'data':[
                {'id':'a','title':'Song','user':{'name':'Artist'},'duration':180,'permalink':'/artist/song'},
                {'id':'b','title':'Gated','duration':180,'is_stream_gated':True}]})
        service=AudiusSearch(httpx.Client(transport=httpx.MockTransport(handler)))
        result=service.search('Song',2)
        self.assertEqual(result['data'][0]['duration'],180)
        self.assertEqual(result['data'][0]['permalink'],'https://audius.co/artist/song')
        self.assertTrue(result['data'][1]['unavailable'])
