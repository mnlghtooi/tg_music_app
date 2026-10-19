"""Ephemeral, read-only Music Lab 2 feasibility probe in GitHub Actions.

No credentials, no copyrighted audio downloads, no local library mutations.
Runs once with Python CI and prints one machine-readable JSON line.
"""
import asyncio
import json
import time
import unittest
from urllib.parse import urlsplit

import httpx


def call(client, url, params=None, stream=False):
    try:
        if stream:
            # Streaming context only observes status and Content-Type; never
            # iterates bytes or saves audio.
            with client.stream('GET', url, params=params, follow_redirects=True) as resp:
                return {'status':resp.status_code,
                    'media_type':resp.headers.get('content-type','').split(';')[0],
                    'host':urlsplit(str(resp.url)).hostname}, None
        resp=client.get(url, params=params, follow_redirects=True)
        return {'status':resp.status_code,
            'media_type':resp.headers.get('content-type','').split(';')[0],
            'host':urlsplit(str(resp.url)).hostname}, resp
    except (httpx.HTTPError,ValueError) as e:
        return {'error':type(e).__name__},None


async def public_bandcamp_search():
    try:
        from bandcamp_async_api import BandcampAPIClient
        async def do():
            async with BandcampAPIClient() as api:
                results=await api.search('Portishead')
                return {'status':'ok','count':len(results),
                    'types':[str(getattr(x,'type','')) for x in results[:4]]}
        return await asyncio.wait_for(do(),timeout=15)
    except Exception as ex:
        return {'status':'failed','error':type(ex).__name__}


class LiveProviderPoC(unittest.TestCase):
    def test_real_public_provider_responses_no_secrets(self):
        started=time.monotonic()
        output={'scenario':'Music Lab 2.0 · read-only live provider feasibility',
            'search':'Portishead', 'audio_downloaded':False,
            'iphone_playback_verified':False, 'auth_provided':False}
        with httpx.Client(timeout=httpx.Timeout(12.0,connect=6.0),
                headers={'User-Agent':'MusicLab2-Spike/0.1 (read-only, no music download)'}) as client:
            audius_resp,audius_raw=call(client,'https://api.audius.co/v1/tracks/search',
                {'query':'Portishead','limit':10})
            output['audius_search_http']=audius_resp
            available=[]
            if audius_raw is not None and audius_raw.status_code==200:
                try:
                    items=audius_raw.json()['data']
                    output['audius_results']=len(items)
                    output['audius_top_names']=[
                        {'title':str(t.get('title',''))[:60],
                        'artist':str((t.get('user') or {}).get('name',''))[:60]}
                        for t in items[:3] if isinstance(t,dict)]
                    available=[t for t in items if isinstance(t,dict) and t.get('id')
                        and t.get('is_stream_gated') is not True and t.get('is_streamable') is not False]
                except (ValueError,KeyError,TypeError):output['audius_parse']='invalid'
            if not available:
                trending,raw=call(client,'https://api.audius.co/v1/tracks/trending',{'limit':5})
                output['audius_trending_http']=trending
                if raw is not None and raw.status_code==200:
                    try:
                        items=raw.json()['data']
                        available=[t for t in items if isinstance(t,dict)
                            and t.get('id') and t.get('is_stream_gated') is not True
                            and t.get('is_streamable') is not False]
                    except (ValueError,KeyError,TypeError):pass
            if available:
                url='https://api.audius.co/v1/tracks/'+str(available[0]['id'])+'/stream'
                stream,_=call(client,url,stream=True)
                output['audius_audio_headers']=stream
                output['audius_audio_title']=str(available[0].get('title',''))[:60]
            bc,raw=call(client,'https://bandcamp.com/search',{'q':'Portishead'})
            output['bandcamp_web_search_http']=bc
            if raw is not None and raw.status_code==200:
                content=raw.text[:120000].lower()
                output['bandcamp_result_markup_present']=('searchresult' in content
                    or 'search_results' in content)
            sc,_=call(client,'https://api.soundcloud.com/tracks',
                {'q':'Portishead','access':'playable','limit':2})
            output['soundcloud_no_oauth_http']=sc
            sp,_=call(client,'https://api.spotify.com/v1/search',
                {'q':'Portishead','type':'track','limit':2})
            output['spotify_no_oauth_http']=sp
        output['bandcamp_async_client']=asyncio.run(public_bandcamp_search())
        output['bandcamp_subsonic_collection']='not_tested_requires_fan_credentials'
        output['spotify_streaming']='not_tested_requires_premium_oauth_browser'
        output['soundcloud_streaming']='not_tested_requires_oauth'
        output['elapsed_seconds']=round(time.monotonic()-started,2)
        print('MUSICLAB2_SPIKE_RESULT='+json.dumps(output,ensure_ascii=False),flush=True)
        # Never fail a repository's normal CI for external provider outages.
        self.assertFalse(output['audio_downloaded'])
        self.assertFalse(output['iphone_playback_verified'])
