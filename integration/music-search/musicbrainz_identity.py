"""Opt-in, read-only MusicBrainz identity checks for source-owned artist pages.

An artist search hit (even with high score) is NOT proof. We only call a
provider account 'linked' when a MusicBrainz URL relation exactly matches its
validated profile URL. No track playback, localStorage, or merges here.
"""
from collections import OrderedDict
import re
import threading
import time
import unicodedata
from urllib.parse import urlsplit

import httpx
from catalog_models import mbid

_MUSICBRAINZ = 'https://musicbrainz.org/ws/2/'
_AGENT = 'MusicLab/0.7.11 (https://github.com/mnlghtooi/tg_music_app)'
_SOURCES = frozenset(('soundcloud', 'bandcamp', 'audius'))
_RELATION = {'soundcloud': 'soundcloud', 'bandcamp': 'bandcamp', 'audius': 'audius'}


def name_key(name):
    return unicodedata.normalize('NFKC',' '.join(name.casefold().split()))


def profile_url(source, value):
    """Canonical account locator for comparison, not a URL we will fetch."""
    if source not in _SOURCES or not isinstance(value,str) or len(value)>2048:
        return None
    try:
        p=urlsplit(value)
        host=(p.hostname or '').lower()
        if p.scheme not in ('http','https') or p.username or p.password or p.port:
            return None
        if not host or not re.fullmatch(r'[a-z0-9.-]+',host):
            return None
        path=p.path.rstrip('/').lower()
        if source=='soundcloud':
            if host not in ('soundcloud.com','www.soundcloud.com'):return None
            if not re.fullmatch(r'/[a-z0-9_-]+',path):return None
            return 'soundcloud.com'+path
        if source=='bandcamp':
            if host == 'bandcamp.com':
                if not re.fullmatch(r'/[a-z0-9_-]+',path):return None
                return 'bandcamp.com'+path
            if not host.endswith('.bandcamp.com') or host=='www.bandcamp.com':
                return None
            sub=host.removesuffix('.bandcamp.com')
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',sub) or path not in ('','/music'):
                return None
            return host
        if host not in ('audius.co','www.audius.co') or not re.fullmatch(r'/[a-z0-9_.-]+',path):
            return None
        return 'audius.co'+path
    except ValueError:
        return None


class MusicBrainzIdentity:
    """Thread-safe quota, bounded lookups and short negative/positive cache."""
    def __init__(self,client=None,interval=1.1,ttl=1800):
        self.client=client or httpx.Client(timeout=httpx.Timeout(6,connect=3),
            headers={'User-Agent':_AGENT,'Accept':'application/json'},
            follow_redirects=False)
        self.interval=interval
        self.ttl=ttl
        self.lock=threading.Lock()
        self.last=None
        self.cache=OrderedDict()

    def _get(self,path,params):
        # One request/second across all callers sharing this client (official MB limit).
        if self.last is not None:
            delay=self.interval-(time.monotonic()-self.last)
            if delay>0:time.sleep(delay)
        self.last=time.monotonic()
        response=self.client.get(_MUSICBRAINZ+path,params=params)
        response.raise_for_status()
        data=response.json()
        if not isinstance(data,dict):raise ValueError('Unexpected MusicBrainz response')
        return data

    def resolve(self,name,source,url):
        if not isinstance(name,str) or not 3<=len(name.strip())<=100 or source not in _SOURCES:
            raise ValueError('Invalid artist identity query')
        if not re.fullmatch(r"[\w\s.'’&()\-]+",name,re.UNICODE):
            raise ValueError('Invalid artist identity query')
        target=profile_url(source,url)
        if target is None:
            return {'status':'missing_profile','identity_verified':False,'candidates':[]}
        key=(name_key(name),source,target)
        with self.lock:
            hit=self.cache.get(key)
            if hit and time.monotonic()-hit[0]<self.ttl:
                self.cache.move_to_end(key)
                return hit[1]
            try:
                result=self._lookup(name,source,target)
            except (httpx.HTTPError,ValueError,KeyError,TypeError):
                result={'status':'unavailable','identity_verified':False,'candidates':[]}
            self.cache[key]=(time.monotonic(),result)
            self.cache.move_to_end(key)
            while len(self.cache)>128:self.cache.popitem(last=False)
            return result

    def _lookup(self,name,source,target):
        # Search score and artist name remain discovery hints only.
        search=self._get('artist/',{'query':'artist:"'+name+'"','fmt':'json','limit':5})
        artists=search.get('artists') or []
        if not isinstance(artists,list):raise ValueError('Invalid MusicBrainz artists')
        candidates=[]
        found=[]
        for item in artists[:5]:
            if not isinstance(item,dict) or name_key(str(item.get('name','')))!=name_key(name):
                continue
            try:identifier=mbid(item.get('id'))
            except ValueError:continue
            if identifier is None:continue
            candidates.append({'mbid':identifier,'name':item.get('name'),
                'disambiguation':str(item.get('disambiguation') or '')[:160]})
        # Bounded relationship reads only for up to 3 exact-name candidates.
        for item in candidates[:3]:
            details=self._get('artist/'+item['mbid'],{'inc':'url-rels','fmt':'json'})
            for relation in details.get('relations') or []:
                if not isinstance(relation,dict) or relation.get('type')!=_RELATION[source] or relation.get('ended') is True:
                    continue
                linked=(relation.get('url') or {}).get('resource')
                if profile_url(source,linked)==target:
                    found.append(item)
                    break
        if len(found)==1:
            return {'status':'verified_profile_link','identity_verified':True,
                'artist':found[0],'matched_source':source,
                'evidence':'musicbrainz_url_relationship','candidates':[]}
        # A conflicting relation (or an ordinary name match) cannot join accounts.
        return {'status':'ambiguous' if len(found)>1 else 'name_only',
                'identity_verified':False,'candidates':candidates[:5]}
