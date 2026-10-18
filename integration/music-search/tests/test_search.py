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
if __name__=='__main__':unittest.main()
