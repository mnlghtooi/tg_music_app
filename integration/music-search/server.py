import argparse, concurrent.futures, hashlib, hmac, json, mimetypes, os, re, secrets, socket, threading, time
from collections import OrderedDict
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
import httpx
import yt_dlp

ROOT=Path(__file__).resolve().parent
KEY=os.environ.get("MUSICLAB_ACCESS_KEY") or secrets.token_urlsafe(24)
LOCK=threading.RLock()
MEDIA=OrderedDict()
MEDIA_CLIENT=httpx.Client(http2=True,timeout=httpx.Timeout(25,connect=5),
    limits=httpx.Limits(max_connections=12,max_keepalive_connections=8),
    headers={"User-Agent":"Mozilla/5.0","Accept-Encoding":"identity"})
SC_LOCK=threading.Lock()
SC_CLIENT=None
class Quiet:
    def debug(self,*a): pass
    def warning(self,*a): pass
    def error(self,*a): pass

def options(flat=False):
    return dict(quiet=True,no_warnings=True,logger=Quiet(),skip_download=True,
                extract_flat=flat,socket_timeout=15,retries=0,extractor_retries=0,
                cachedir=str(ROOT/".cache"),noplaylist=not flat)

def media_token(url):
    p=urlparse(url);host=p.hostname or ""
    if p.scheme!="https" or not (host.endswith(".sndcdn.com") or host.endswith(".soundcloud.com") or host.endswith(".soundcloud.cloud") or host.endswith('.bcbits.com') or host=="soundcloud.com"):
        raise ValueError("Unsupported media host")
    token=hashlib.sha256(url.encode()).hexdigest()
    with LOCK:
        MEDIA[token]=(url,time.time())
        while len(MEDIA)>20000: MEDIA.popitem(last=False)
    return "/api/media/"+token

def initialize_search():
    global SC_CLIENT
    if SC_CLIENT is None:
        from yt_dlp.extractor.soundcloud import SoundcloudSearchIE
        client=yt_dlp.YoutubeDL(options(True))
        extractor=SoundcloudSearchIE(client)
        extractor.initialize()
        SC_CLIENT=(client,extractor)
    return SC_CLIENT[1]

def search_credentials(expired=None):
    # Serialize credential bootstrap/refresh only, never ordinary searches.
    with SC_LOCK:
        extractor=initialize_search()
        if expired is not None and extractor._CLIENT_ID==expired:
            extractor._update_client_id()
        return extractor._CLIENT_ID

from search_service import SearchService
from stream_service import StreamService
from bandcamp_service import BandcampService, BandcampError
SEARCH_SERVICE=SearchService(search_credentials)
STREAM_SERVICE=StreamService(SEARCH_SERVICE,media_token)
BANDCAMP=BandcampService(media_token)
from catalog_service import CatalogService, CatalogError
CATALOG=CatalogService(SEARCH_SERVICE,BANDCAMP)
from entity_service import EntityService
ENTITIES=EntityService(SEARCH_SERVICE,BANDCAMP,CATALOG.services["audius"])
from artist_aggregation import ArtistAggregateService
ARTISTS=ArtistAggregateService(ENTITIES,CATALOG)
from musicbrainz_identity import MusicBrainzIdentity
MUSICBRAINZ=MusicBrainzIdentity()
from audio_share import AudioShare
AUDIO_SHARE=AudioShare(ROOT,ENTITIES)
from migration_service import MigrationService, handle_get as migration_get, handle_post as migration_post
MIGRATION=MigrationService(SEARCH_SERVICE,BANDCAMP,catalog=CATALOG)

def warm_search():
    try: search_credentials()
    except Exception: pass

def search(q,page=1):
    return SEARCH_SERVICE.search(q,page)

def stream(track_id):
    return STREAM_SERVICE.resolve(track_id)

class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def handle(self):
        try: super().handle()
        except ConnectionError: pass  # Browser cancelled a segment or closed keep-alive.
    def log_message(self,*args): pass
    def respond(self,status,body=b"",content_type="application/json; charset=utf-8",headers=None):
        if isinstance(body,(dict,list)): body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff")
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers()
        if self.command!="HEAD": self.wfile.write(body)
    def local_setup(self):
        host=self.headers.get('Host','')
        return (self.client_address[0] in ('127.0.0.1','::1') and host.split(':')[0] in ('127.0.0.1','localhost')
            and not self.headers.get('CF-Connecting-IP') and not self.headers.get('X-Forwarded-For')
            and self.headers.get('Origin','http://'+host) in ('http://'+host,'https://'+host))

    def audio_file(self,job):
        from urllib.parse import quote
        path=job['path'];size=path.stat().st_size
        self.send_response(200)
        self.send_header('Content-Type','audio/mpeg');self.send_header('Content-Length',str(size))
        self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(job['filename']))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
        if self.command!='HEAD':
            with path.open('rb') as stream:
                while chunk:=stream.read(65536):self.wfile.write(chunk)

    def authenticated(self):
        try:
            c=SimpleCookie();c.load(self.headers.get("Cookie",""))
            return "musiclab_access" in c and hmac.compare_digest(c["musiclab_access"].value,KEY)
        except Exception: return False
    def do_HEAD(self): self.do_GET()
    def do_POST(self):
        if not self.authenticated():
            self.close_connection=True
            self.respond(403,{'error':'Открой личную ссылку Music Lab.'});return
        if self.path.startswith('/api/audio-share/'):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 20000: raise ValueError('Некорректный запрос.')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict): raise ValueError('Некорректный запрос.')
                if self.path=='/api/audio-share/start':result=AUDIO_SHARE.start(data.get('source'),data.get('id'))
                elif self.path=='/api/audio-share/telegram':result=AUDIO_SHARE.prepared_message(data.get('job',''),data.get('init_data',''),data.get('origin',''))
                elif self.path=='/api/audio-share/config':
                    if not self.local_setup():
                        self.respond(403,{'error':'Подключи бота в локальном браузере на компьютере.'});return
                    result=AUDIO_SHARE.configure(data.get('token'))
                else:self.respond(404,{'error':'Not found'});return
                self.respond(200,result)
            except (ValueError,TypeError,RuntimeError) as error:
                self.close_connection=True
                self.respond(400,{'error':str(error) if isinstance(error,(ValueError,RuntimeError)) else 'Некорректный запрос.'})
            return
        if not self.path.startswith('/api/migration/'):

            self.respond(404,{'error':'Not found'});return
        migration_post(self,MIGRATION)
    def do_GET(self):
        try:
            p=urlparse(self.path)
            if p.path=='/healthz':
                self.respond(200,{'ok':True,'version':'0.7.10'});return
            if p.path.startswith("/open/") and hmac.compare_digest(p.path.removeprefix("/open/").strip("/"),KEY):
                self.respond(302,headers={"Location":"/","Set-Cookie":"musiclab_access="+KEY+"; HttpOnly; SameSite=Lax; Path=/"})
                return
            if p.path.startswith('/api/shared-audio/'):
                try:job=AUDIO_SHARE.public_file(p.path.rsplit('/',1)[-1].removesuffix('.mp3'))
                except ValueError:self.respond(410,{'error':'Ссылка устарела.'});return
                self.audio_file(job);return
            if not self.authenticated():
                self.respond(403,{"error":"Открой личную ссылку из окна запуска Music Lab."});return
            if migration_get(self,p,MIGRATION):return
            if p.path in ('/library_transfer.js','/migration_ui.js','/audio_share_ui.js','/mini_seek.js','/track_availability.js'):
                self.respond(200,(ROOT/p.path.lstrip('/')).read_bytes(),'text/javascript; charset=utf-8');return
            if p.path=="/":
                self.respond(200,(ROOT/"index.html").read_bytes(),"text/html; charset=utf-8");return
            if p.path=="/hls.min.js":
                self.respond(200,(ROOT/"hls.min.js").read_bytes(),"text/javascript");return
            if p.path=="/api/health":
                self.respond(200,{"ok":True,"provider":"soundcloud","version":"0.7.10"});return
            if p.path=="/api/search":
                q=parse_qs(p.query).get("q",[""])[0].strip()
                if not 1<=len(q)<=150: self.respond(400,{"error":"Введи название или исполнителя (до 150 символов)."});return
                try:page=int(parse_qs(p.query).get("page",["1"])[0])
                except ValueError:self.respond(400,{"error":"Некорректная страница"});return
                if not 1<=page<=100:self.respond(400,{"error":"Некорректная страница"});return
                provider=parse_qs(p.query).get('source',['all'])[0]
                if provider not in ('all','soundcloud','bandcamp','audius'):
                    self.respond(400,{'error':'Неизвестный источник поиска'});return
                kind=parse_qs(p.query).get('type',['all'])[0]
                if kind not in ('all','artist','album','playlist','track'):
                    self.respond(400,{'error':'Неизвестный тип поиска'});return
                result=CATALOG.query('audius',q,page,kind) if provider=='audius' else (CATALOG if provider=='all' else BANDCAMP if provider=='bandcamp' else SEARCH_SERVICE).search(q,page,kind)
                self.respond(200,result);return
            if p.path=='/api/artists/identity':
                params=parse_qs(p.query)
                try:
                    result=MUSICBRAINZ.resolve(
                        params.get('name',[''])[0],params.get('source',[''])[0],
                        params.get('url',[''])[0])
                except (ValueError,TypeError):
                    self.respond(400,{'error':'Некорректная проверка исполнителя.'});return
                self.respond(200,result);return
            if p.path in ('/api/artist','/api/artists/merged'):
                params=parse_qs(p.query)
                source=params.get('source',[''])[0];item_id=params.get('id',[''])[0]
                if source not in ('soundcloud','bandcamp','audius') or not item_id or len(item_id)>2048:
                    self.respond(400,{'error':'Некорректный исполнитель.'});return
                if p.path=='/api/artists/merged':
                    self.respond(200,ARTISTS.overview(source,item_id));return
                self.respond(200,ENTITIES.overview(source,item_id));return
            if p.path=='/api/audio-share/config':
                local=self.local_setup()
                self.respond(200,{'configured':bool(AUDIO_SHARE.token()),'username':'mnlgth_music_app_bot','local_setup':local});return
            if p.path=='/api/audio-share/status':
                self.respond(200,AUDIO_SHARE.status(parse_qs(p.query).get('job',[''])[0]));return
            if p.path=='/api/audio-share/file':
                self.audio_file(AUDIO_SHARE.file(parse_qs(p.query).get('job',[''])[0]));return
            if p.path=="/api/entity":
                params=parse_qs(p.query)
                try:
                    result=ENTITIES.get(params.get('source',[''])[0],params.get('kind',[''])[0],params.get('id',[''])[0],int(params.get('page',['1'])[0]),params.get('section',[''])[0])
                except ValueError:
                    self.respond(400,{'error':'Некорректная карточка или страница'});return
                self.respond(200,result);return
            if p.path=="/api/collection":
                params=parse_qs(p.query)
                kind=params.get('kind',[''])[0];item_id=params.get('id',[''])[0]
                try:
                    page=int(params.get('page',['1'])[0])
                    source=params.get('source',['soundcloud'])[0]
                    if source not in ('soundcloud','bandcamp'):raise ValueError('Invalid source')
                    result=(BANDCAMP if source=='bandcamp' else SEARCH_SERVICE).browse(kind,item_id,page)
                except ValueError:
                    self.respond(400,{'error':'Некорректная коллекция или страница'});return
                self.respond(200,result);return
            if p.path=="/api/stream":
                track_id=parse_qs(p.query).get("id",[""])[0]
                source=parse_qs(p.query).get('source',['soundcloud'])[0]
                if source not in ('soundcloud','bandcamp'):
                    self.respond(400,{'error':'Неизвестный источник аудио'});return
                self.respond(302,headers={"Location":BANDCAMP.stream(track_id) if source=='bandcamp' else stream(track_id)});return
            if p.path.startswith("/api/media/"):
                token=p.path.rsplit("/",1)[-1]
                with LOCK: entry=MEDIA.get(token)
                if not entry or time.time()-entry[1]>3600: self.respond(410,{"error":"Поток устарел. Выбери трек заново."});return
                headers={}
                if self.headers.get("Range"): headers["Range"]=self.headers["Range"]
                with MEDIA_CLIENT.stream('GET',entry[0],headers=headers) as r:
                    r.raise_for_status()
                    kind=r.headers.get("Content-Type","application/octet-stream")
                    is_playlist="mpegurl" in kind.lower() or ".m3u8" in urlparse(entry[0]).path
                    if is_playlist:
                        chunks=[];size=0
                        for chunk in r.iter_bytes(16*1024):
                            size+=len(chunk)
                            if size>2*1024*1024:raise ValueError('Manifest too large')
                            chunks.append(chunk)
                        text=b''.join(chunks).decode("utf-8")
                        lines=[]
                        for line in text.splitlines():
                            if line and not line.startswith("#"): line=media_token(urljoin(str(r.url),line))
                            elif 'URI="' in line:
                                line=re.sub(r'URI="([^"]+)"',lambda m:'URI="'+media_token(urljoin(str(r.url),m.group(1)))+'"',line)
                            lines.append(line)
                        self.respond(200,("\n".join(lines)+"\n").encode(),"application/vnd.apple.mpegurl");return
                    self.send_response(r.status_code);self.send_header("Content-Type",kind);self.send_header("Cache-Control","private, max-age=120")
                    length=r.headers.get("Content-Length")
                    if length:self.send_header("Content-Length",length)
                    else:self.send_header("Connection","close");self.close_connection=True
                    for name in ("Content-Range","Accept-Ranges"):
                        if r.headers.get(name):self.send_header(name,r.headers[name])
                    self.end_headers()
                    if self.command!="HEAD":
                        for chunk in r.iter_raw(16*1024):
                            self.wfile.write(chunk)
                    return
            self.respond(404,{"error":"Not found"})
        except ConnectionError: pass
        except CatalogError as error:
            try:self.respond(502,{'error':str(error),'code':'CatalogUnavailable'})
            except ConnectionError:pass
        except BandcampError as error:
            try:self.respond(502,{'error':str(error),'code':'BandcampUnavailable'})
            except ConnectionError:pass
        except Exception as e:
            # URLs and upstream access data are never sent to the browser.
            try:self.respond(502,{"error":"Не удалось получить музыку. Источник может ограничивать этот трек; попробуй другой результат.","code":type(e).__name__})
            except ConnectionError:pass

if __name__=="__main__":
    a=argparse.ArgumentParser();a.add_argument("--port",type=int,default=int(os.environ.get('PORT','8765')))
    a.add_argument('--host',default='127.0.0.1');args=a.parse_args()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    threading.Thread(target=warm_search,daemon=True).start()
    print("Music Lab v0.7.10 — SoundCloud",flush=True)
    print("LOCAL_URL=http://127.0.0.1:"+str(args.port)+"/open/"+KEY,flush=True)
    print("Держи это окно открытым. Ctrl+C — остановка.",flush=True)
    server.serve_forever()

