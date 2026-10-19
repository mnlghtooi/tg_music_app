import sys,threading,time,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from search_service import SearchService
class SearchTests(unittest.TestCase):
 def test_parallel(self):
  barrier=threading.Barrier(2)
  def handler(r):
   barrier.wait(timeout=2);return httpx.Response(200,json={'collection':[]})
  service=SearchService(lambda *a:'public',httpx.Client(transport=httpx.MockTransport(handler)))
  with ThreadPoolExecutor(2) as pool:
   jobs=[pool.submit(service.search,q) for q in ['one','two']]
   for j in jobs:self.assertEqual(j.result()['data'],[])
 def test_coalesce_cache_page(self):
  calls=[];entered=threading.Event();release=threading.Event()
  def handler(r):
   calls.append(r);entered.set();release.wait(2)
   return httpx.Response(200,json={'collection':[{'id':i+1,'title':'Song','duration':100000,'policy':'SNIP'} for i in range(11)]})
  service=SearchService(lambda *a:'public',httpx.Client(transport=httpx.MockTransport(handler)))
  with ThreadPoolExecutor(4) as pool:
   first=pool.submit(service.search,'Song',2);entered.wait(2)
   jobs=[pool.submit(service.search,'song',2) for _ in range(3)];time.sleep(.05);release.set()
   result=first.result()
   for j in jobs:self.assertEqual(j.result(),result)
  self.assertEqual(len(calls),1);self.assertEqual(calls[0].url.params['offset'],'10')
  self.assertEqual(len(result['data']),10);self.assertTrue(result['has_more']);self.assertTrue(result['data'][0]['preview'])
  self.assertEqual(service.search(' SONG ',2),result);self.assertEqual(len(calls),1)
 def test_refresh_and_rate_limit(self):
  seen=[]
  def handler(r):
   seen.append(r.url.params['client_id']);return httpx.Response(401 if len(seen)==1 else 200,json={'collection':[]})
  service=SearchService(lambda expired=None:'new' if expired else 'old',httpx.Client(transport=httpx.MockTransport(handler)))
  service.search('test');self.assertEqual(seen,['old','new'])
  count=[]
  def limited(r):count.append(1);return httpx.Response(429)
  service=SearchService(lambda *a:'public',httpx.Client(transport=httpx.MockTransport(limited)))
  with self.assertRaises(httpx.HTTPStatusError):service.search('test')
  self.assertEqual(len(count),1);self.assertFalse(service.pending)
 def test_mixed_search(self):
  def handler(r):
   self.assertEqual(r.url.path,'/search')
   return httpx.Response(200,json={'collection':[
    {'kind':'user','id':1,'username':'Artist'},
    {'kind':'playlist','id':1,'title':'Playlist','user':{'username':'Owner'}},
    {'kind':'track','id':1,'title':'Song','duration':120000}]})
  service=SearchService(lambda *a:'public',httpx.Client(transport=httpx.MockTransport(handler)))
  result=service.search('test')['data']
  self.assertEqual([t['kind'] for t in result],['artist','playlist','track'])
  self.assertEqual(result[0]['title'],'Artist');self.assertEqual(result[1]['artist'],'Owner')
 def test_playlist_batch_hydration(self):
  paths=[]
  def handler(r):
   paths.append(r.url.path)
   if r.url.path=='/playlists/12':return httpx.Response(200,json={'title':'List','tracks':[{'id':i} for i in range(1,24)]})
   self.assertEqual(r.url.params['ids'],','.join(str(i) for i in range(11,21)))
   return httpx.Response(200,json=[{'id':i,'kind':'track','title':str(i)} for i in range(20,10,-1)])
  service=SearchService(lambda *a:'public',httpx.Client(transport=httpx.MockTransport(handler)))
  result=service.browse('playlist','12',2)
  self.assertEqual([t['id'] for t in result['data']],[str(i) for i in range(11,21)])
  self.assertTrue(result['has_more']);self.assertEqual(len(paths),2)
  service.browse('playlist','12',2);self.assertEqual(len(paths),2)
 def test_artist_page_and_validation(self):
  def handler(r):
   self.assertEqual(r.url.path,'/users/2/tracks');self.assertEqual(r.url.params['offset'],'10')
   return httpx.Response(200,json={'collection':[{'id':1,'title':'Public song'}]})
  service=SearchService(lambda *a:'public',httpx.Client(transport=httpx.MockTransport(handler)))
  self.assertFalse(service.browse('artist','2',2)['has_more'])
  for kind,item,page in [('other','2',1),('artist','../x',1),('playlist','2',0)]:
   with self.assertRaises(ValueError):service.browse(kind,item,page)
if __name__=='__main__':unittest.main()


class FilterTests(unittest.TestCase):
 def test_endpoints_cache_and_album_kind(self):
  seen=[]
  def handler(r):
   seen.append(r.url.path)
   return httpx.Response(200,json={'collection':[{'id':1,'kind':'playlist','is_album':True,'title':'Album'}]})
  service=SearchService(lambda *a:'public',httpx.Client(transport=httpx.MockTransport(handler)))
  for kind in ['all','artist','album','playlist','track']:
   service.search('same',kind=kind)
  self.assertEqual(seen,['/search','/search/users','/search/albums','/search/playlists','/search/tracks'])
  self.assertEqual(service.search('same',kind='album')['data'][0]['kind'],'album')
  self.assertEqual(len(seen),5)
  with self.assertRaises(ValueError): service.search('test',kind='invalid')
