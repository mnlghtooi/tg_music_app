"""Bounded public-audio preparation and Telegram's user-confirmed share dialog."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.parse import parse_qsl, urlparse
import httpx
import yt_dlp
from bandcamp_service import decode_id

MAX_BYTES = 19 * 1024 * 1024


def telegram_user(init_data, token, now=None):
    if not isinstance(init_data, str) or len(init_data) > 16384: raise ValueError('Открой приложение через Telegram-бота.')
    pairs = parse_qsl(init_data, keep_blank_values=True)
    values = dict(pairs)
    if len(values) != len(pairs): raise ValueError('Некорректная авторизация Telegram.')
    supplied = values.pop('hash', '')
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, '\n'.join(k + '=' + v for k, v in sorted(values.items())).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied): raise ValueError('Открой приложение заново через Telegram-бота.')
    age = (now or time.time()) - int(values.get('auth_date', '0'))
    if not -30 <= age <= 7200: raise ValueError('Сессия Telegram устарела. Открой приложение заново.')
    user = json.loads(values.get('user', '{}'))
    if not isinstance(user.get('id'), int) or user['id'] <= 0: raise ValueError('Не удалось определить пользователя Telegram.')
    return user['id']


class Quiet:
    def debug(self, *args): pass
    def warning(self, *args): pass
    def error(self, *args): pass


class AudioShare:
    def __init__(self, root, entities):
        self.root = Path(root); self.entities = entities
        self.lock = threading.RLock(); self.jobs = {}; self.public = {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='audio-share')
        self.slots = threading.BoundedSemaphore(4)
        self.client = httpx.Client(timeout=30)

    def token(self):
        value = os.environ.get('MUSICLAB_BOT_TOKEN', '')
        if value: return value
        try: return json.loads((self.root / '.private/telegram.json').read_text('utf-8')).get('token', '')
        except (OSError, ValueError): return ''

    def bot_call(self, method, token, **payload):
        try:
            r = self.client.post('https://api.telegram.org/bot' + token + '/' + method, json=payload)
            body = r.json()
            if r.status_code != 200 or not body.get('ok'): raise RuntimeError()
            return body['result']
        except Exception:
            raise RuntimeError('Telegram не принял запрос. Проверь подключение бота и повтори.') from None

    def configure(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}', token): raise ValueError('Проверь токен бота.')
        bot = self.bot_call('getMe', token)
        if bot.get('username', '').lower() != 'mnlgth_music_app_bot': raise ValueError('Это токен другого бота. Нужен @mnlgth_music_app_bot.')
        target = self.root / '.private/telegram.json'; target.parent.mkdir(exist_ok=True)
        temp = target.with_suffix('.tmp'); temp.write_text(json.dumps({'token': token}), 'utf-8'); temp.replace(target)
        return {'configured': True, 'username': bot['username']}

    def descriptor(self, source, item_id):
        if source == 'soundcloud':
            if not re.fullmatch(r'[0-9]{1,20}', item_id): raise ValueError('Некорректный трек.')
            raw = self.entities.sc.get_track(item_id)
            if raw.get('policy') in ('BLOCK', 'SNIP'): raise ValueError('Источник не предоставляет полный трек для отправки.')
            track = self.entities.sc.normalize(raw)
        elif source == 'bandcamp':
            url = decode_id(item_id, 'track')
            track = next((t for t in self.entities.bc.release_tracks(url) if t['id'] == item_id), None)
            if not track or track.get('unavailable'): raise ValueError('Источник не предоставляет полный трек для отправки.')
        elif source == 'audius':
            if not re.fullmatch(r'[A-Za-z0-9]{1,100}', item_id): raise ValueError('Некорректный трек.')
            data = self.entities.au_get('tracks/' + item_id)
            raw = data[0] if isinstance(data, list) and data else data
            track = self.entities.au_item(raw)
            if track.get('unavailable'): raise ValueError('Источник не предоставляет полный трек для отправки.')
        else: raise ValueError('Неизвестный источник.')
        url = urlparse(track.get('permalink', ''))
        host = {'soundcloud': 'soundcloud.com', 'bandcamp': 'bandcamp.com', 'audius': 'audius.co'}[source]
        if url.scheme != 'https' or url.username or url.password or url.port or not (url.hostname == host or (url.hostname or '').endswith('.' + host)):
            raise ValueError('Нет публичной ссылки на трек.')
        return track

    def start(self, source, item_id):
        if source not in ('soundcloud', 'bandcamp', 'audius') or not isinstance(item_id, str) or len(item_id) > 2048: raise ValueError('Некорректный трек.')
        with self.lock:
            now = time.time()
            self.jobs = {k: v for k, v in self.jobs.items() if now - v['created'] < 1800 or v['state'] == 'working'}
            for key, job in self.jobs.items():
                if job['source'] == source and job['item_id'] == item_id and job['state'] != 'error': return {'job': key}
            if len(self.jobs) >= 50 or not self.slots.acquire(blocking=False): raise ValueError('Подготовка занята. Попробуй через минуту.')
            key = secrets.token_urlsafe(24)
            self.jobs[key] = dict(source=source, item_id=item_id, created=now, state='working')
            self.pool.submit(self.prepare, key)
            return {'job': key}

    def prepare(self, key):
        directory = self.root / '.cache/audio-share'
        try:
            directory.mkdir(parents=True, exist_ok=True)
            # Bound cache lifetime and disk usage; only this dedicated directory.
            for old in directory.glob('*'):
                if old.is_file() and time.time() - old.stat().st_mtime > 1800: old.unlink(missing_ok=True)
            if sum(p.stat().st_size for p in directory.glob('*') if p.is_file()) > 300 * 1024 * 1024: raise ValueError('Кэш аудио заполнен. Повтори позднее.')
            job = self.jobs[key]; track = self.descriptor(job['source'], job['item_id'])
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            opts = dict(quiet=True, no_warnings=True, logger=Quiet(), noplaylist=True, socket_timeout=15, retries=0,
                extractor_retries=0, cachedir=str(self.root / '.cache'), ffmpeg_location=ffmpeg,
                outtmpl=str(directory / (key + '.%(ext)s')), format='bestaudio/best', max_filesize=MAX_BYTES,
                match_filter=lambda info, **kw: 'Preview unavailable' if info.get('is_drm') or info.get('is_live') else None,
                postprocessors=[dict(key='FFmpegExtractAudio', preferredcodec='mp3', preferredquality='192'),
                                dict(key='FFmpegMetadata')],
                progress_hooks=[lambda info: self.limit_download(info, job['created'])])
            target = directory / (key + '.mp3')
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            if job['source'] == 'bandcamp':
                original = directory / (key + '.input.mp3')
                self.download_bandcamp(job['item_id'], original, job['created'])
                conversion = subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(original),
                    '-map', '0:a:0', '-c:a', 'copy', '-metadata', 'title=' + track['title'], '-metadata', 'artist=' + track['artist'], str(target)],
                    capture_output=True, timeout=60, creationflags=flags)
                original.unlink(missing_ok=True)
                if conversion.returncode: raise ValueError('Не удалось подготовить MP3.')
            else:
                with yt_dlp.YoutubeDL(opts) as downloader:
                    downloader.extract_info(track['permalink'], download=True)
            if not target.exists() or target.stat().st_size < 1000 or target.stat().st_size > MAX_BYTES:
                raise ValueError('Аудио слишком большое для отправки: сейчас поддерживается до 19 МБ.')
            # Probe the actual output, rather than trusting the extractor's duration.
            probe = subprocess.run([ffmpeg, '-hide_banner', '-i', str(target)], capture_output=True, timeout=15, creationflags=flags)
            match = re.search(rb'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', probe.stderr)
            duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) if match else 0
            expected = track.get('duration') or 0
            if duration <= 0 or expected and abs(duration - expected) > max(4, expected * .03):
                raise ValueError('Не удалось подтвердить полный трек. Отправка отменена.')
            filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', track['artist'] + ' — ' + track['title'])[:160] + '.mp3'
            with self.lock: job.update(state='ready', path=target, filename=filename, title=track['title'], artist=track['artist'])
        except Exception as error:
            with self.lock: self.jobs[key].update(state='error', error=str(error) if isinstance(error, ValueError) else 'Источник ограничивает скачивание этого трека.' if 'DRM' in str(error) else 'Не удалось подготовить аудио из этого источника. Попробуй другой трек.')
            for file in directory.glob(key + '.*'): file.unlink(missing_ok=True)
        finally: self.slots.release()

    def download_bandcamp(self, item_id, target, started):
        url = self.entities.bc.audio_url(item_id)
        for _ in range(4):
            parsed = urlparse(url)
            if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.bcbits.com') or parsed.username or parsed.password or parsed.port:
                raise ValueError('Источник вернул неподдерживаемый аудиопоток.')
            with self.client.stream('GET', url) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers.get('location', '')); continue
                response.raise_for_status()
                if int(response.headers.get('content-length', '0')) > MAX_BYTES: raise ValueError('Аудио слишком большое: поддерживается до 19 МБ.')
                size = 0
                with target.open('wb') as output:
                    for chunk in response.iter_bytes(65536):
                        size += len(chunk); self.limit_download({'downloaded_bytes': size}, started); output.write(chunk)
                return
        raise ValueError('Не удалось получить аудиофайл.')

    @staticmethod
    def limit_download(info, started):
        if info.get('downloaded_bytes', 0) > MAX_BYTES or time.time() - started > 180:
            raise ValueError('Аудио слишком большое или источник отвечает слишком долго.')

    def status(self, key):
        with self.lock:
            job = self.jobs.get(key)
            if not job or time.time() - job['created'] > 1800: raise ValueError('Подготовка устарела. Выбери трек заново.')
            return {k: job[k] for k in ('state', 'filename', 'title', 'artist', 'error') if k in job}

    def file(self, key):
        if self.status(key)['state'] != 'ready': raise ValueError('Аудио ещё не готово.')
        return self.jobs[key]

    def prepared_message(self, key, init_data, origin):
        token = self.token()
        if not token: raise ValueError('Сначала подключи Telegram-бота в настройках Music Lab на компьютере.')
        user_id = telegram_user(init_data, token)
        url = urlparse(origin)
        allowed = os.environ.get('MUSICLAB_PUBLIC_URL', '').rstrip('/')
        if origin != allowed and not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com', origin):
            raise ValueError('Открой Mini App по публичной HTTPS-ссылке приложения.')
        if url.scheme != 'https': raise ValueError('Для отправки требуется HTTPS.')
        job = self.file(key)
        capability = secrets.token_urlsafe(32)
        with self.lock:
            self.public = {k: v for k, v in self.public.items() if v[1] > time.time()}
            if len(self.public) >= 100: raise ValueError('Слишком много отправок. Повтори позднее.')
            self.public[capability] = (key, time.time() + 600)
        result = dict(type='audio', id=key, audio_url=origin + '/api/shared-audio/' + capability + '.mp3', title=job['title'][:64], performer=job['artist'][:64])
        prepared = self.bot_call('savePreparedInlineMessage', token, user_id=user_id, result=result,
            allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True, allow_bot_chats=False)
        return {'id': prepared['id']}

    def public_file(self, capability):
        with self.lock: entry = self.public.get(capability)
        if not entry or entry[1] < time.time(): raise ValueError('Ссылка устарела.')
        return self.file(entry[0])
