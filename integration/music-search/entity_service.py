"""Source-owned profiles and explicit track relationships; never match by name."""
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, parse_qs
from bandcamp_service import decode_id, identifier, public_url


def text(value):
    return unescape(re.sub(r'<[^>]*>', '', str(value or '')))[:16000].strip()


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__(); self.meta = {}; self.bio = []; self.depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta': self.meta[a.get('property') or a.get('name')] = a.get('content', '')
        if self.depth: self.depth += tag not in ('br', 'img', 'meta', 'input', 'hr', 'link')
        elif a.get('id') == 'bio-text': self.depth = 1

    def handle_endtag(self, tag):
        if self.depth: self.depth -= 1

    def handle_data(self, value):
        if self.depth: self.bio.append(value)


class EntityService:
    def __init__(self, soundcloud, bandcamp, audius):
        self.sc, self.bc, self.au = soundcloud, bandcamp, audius

    @staticmethod
    def ref(source, kind, item_id, title, **extra):
        return dict(source=source, kind=kind, id=str(item_id), title=title or 'Без названия', **extra)

    def get(self, source, kind, item_id, page=1, section=''):
        if source not in ('soundcloud', 'bandcamp', 'audius') or kind not in ('artist', 'album', 'playlist', 'track'):
            raise ValueError('Invalid entity')
        if not isinstance(page, int) or not 1 <= page <= 100 or section not in ('', 'tracks', 'albums', 'playlists', 'popular'):
            raise ValueError('Invalid page or section')
        if source == 'soundcloud':
            if not re.fullmatch(r'[0-9]{1,20}', item_id): raise ValueError('Invalid SoundCloud ID')
            return self.sc._cached(('entity', kind, item_id, page, section), lambda: self.soundcloud(kind, item_id, page, section))
        if source == 'bandcamp': return self.bandcamp(kind, item_id, page, section)
        if not re.fullmatch(r'[A-Za-z0-9]{1,100}', item_id): raise ValueError('Invalid Audius ID')
        return self.sc._cached(('entity-audius', kind, item_id, page, section), lambda: self.audius(kind, item_id, page, section))

    def sc_artist(self, raw):
        user = raw.get('user') or {}
        return self.ref('soundcloud', 'artist', user['id'], user.get('username')) if user.get('id') else None

    def soundcloud(self, kind, item_id, page, section):
        if kind == 'track':
            raw = self.sc.get_track(item_id)
            albums = self.sc._get('tracks/' + item_id + '/albums', {'limit': 10}).get('collection', [])
            album = next((self.sc.normalize(a) for a in albums if a.get('is_album') or a.get('set_type') == 'album'), None)
            return {'artist_ref': self.sc_artist(raw), 'album_ref': album}
        raw = self.sc._get(('users/' if kind == 'artist' else 'playlists/') + item_id)
        profile = self.sc.normalize(dict(raw, kind='user' if kind == 'artist' else 'playlist'))
        profile.update(description=text(raw.get('description')), release_date=raw.get('release_date') or raw.get('created_at'),
                       count=raw.get('track_count'), artist_ref=self.sc_artist(raw))
        if kind == 'artist':
            profile['release_date'] = None
            profile['location'] = text(raw.get('city'))
            profile['followers'] = raw.get('followers_count')
            visuals = (raw.get('visuals') or {}).get('visuals') or []
            profile['banner'] = next((v.get('visual_url') for v in visuals if v.get('visual_url', '').startswith('https://')), None)
            section = section or 'albums'
            items, more = self.sc_rows(item_id, section, page)
            sections = ['albums', 'tracks', 'playlists']
        else:
            body = self.sc._browse(kind, item_id, page)
            items, more, sections = body['data'], body['has_more'], ['tracks']
            for t in items:
                t['artist_ref'] = t.get('artist_ref') or profile['artist_ref']
                if profile['kind'] == 'album': t['album_ref'] = {k: profile[k] for k in ('id', 'kind', 'source', 'title')}
        return dict(profile=profile, data=items, page=page, has_more=more, sections=sections, section=section or 'tracks')

    def sc_rows(self, item_id, section, page):
        """The artist feed uses opaque cursors, including across empty batches."""
        path = 'users/' + item_id + '/' + ('toptracks' if section == 'popular' else section)
        params = {'limit': 50, 'offset': 0, 'linked_partitioning': 1}
        entries, seen, cursors = [], set(), set()
        more = False
        for _ in range(40):
            key = tuple(sorted((k, str(v)) for k, v in params.items()))
            if key in cursors: raise RuntimeError('Источник повторил страницу. Попробуй позднее.')
            cursors.add(key)
            body = self.sc._cached(('artist-cursor', path, key), lambda: self.sc._get(path, params))
            for t in body.get('collection', []):
                if not t or not t.get('id') or str(t['id']) in seen: continue
                seen.add(str(t['id']))
                item = self.sc.normalize(t)
                if section == 'playlists' and item['kind'] == 'album': continue
                entries.append(item)
            nxt = body.get('next_href')
            more = bool(nxt)
            if len(entries) > page * 10 or not nxt: break
            parsed = urlparse(nxt)
            if parsed.scheme != 'https' or parsed.netloc != 'api-v2.soundcloud.com' or parsed.path != '/' + path:
                raise ValueError('Invalid source cursor')
            query = parse_qs(parsed.query)
            params = {k: v[0] for k, v in query.items() if k in ('offset', 'limit', 'linked_partitioning')}
        else:
            raise RuntimeError('Источник отдаёт слишком много пустых страниц. Повтори загрузку позднее.')
        start = (page - 1) * 10
        return entries[start:start + 10], len(entries) > page * 10 or more

    def overview(self, source, item_id):
        sections = ['popular', 'albums', 'playlists'] if source == 'soundcloud' else ['tracks', 'albums', 'playlists'] if source == 'audius' else ['albums']
        blocks, profile = [], None
        for section in sections:
            try:
                body = self.get(source, 'artist', item_id, section=section)
                profile = body['profile']
                blocks.append(dict(section=section, data=body['data'], has_more=body['has_more'], page=1))
            except Exception:
                blocks.append(dict(section=section, data=[], error='Не удалось загрузить раздел. Повтори запрос.'))
        if profile is None: raise RuntimeError('Не удалось загрузить исполнителя.')
        # Only explicit source-published links are presented as related profiles.
        links = []
        for url in re.findall(r'https?://[^\s<>"\)]+', profile.get('description', '')):
            try:
                candidate = public_url(url.rstrip('.,;'), 'artist' if urlparse(url).path.rstrip('/') in ('', '/music') else None)
            except ValueError: continue
            if candidate not in [x['url'] for x in links]:
                links.append(dict(url=candidate, title='Bandcamp', entity=self.ref('bandcamp', 'artist' if urlparse(candidate).path == '' else 'album' if '/album/' in candidate else 'track', identifier(candidate), urlparse(candidate).netloc.split('.')[0])))
        return dict(profile=profile, blocks=blocks, links=links)

    def bandcamp(self, kind, item_id, page, section):
        if kind == 'playlist': raise ValueError('Bandcamp has no public playlist profiles')
        url = decode_id(item_id, kind)
        root = 'https://' + urlparse(url).netloc
        data = self.bc.page_data(url + '/music' if kind == 'artist' else url)
        meta = data.metadata
        artist = self.ref('bandcamp', 'artist', identifier(root), data.embed.get('artist') or (data.tralbum or {}).get('artist') or meta.meta.get('og:site_name') or urlparse(url).netloc.split('.')[0])
        if kind == 'track':
            link = (data.embed.get('album_embed_data') or data.embed).get('linkback', '')
            album = None
            try:
                album_url = public_url(link, 'album')
                if urlparse(album_url).netloc == urlparse(url).netloc:
                    album = self.ref('bandcamp', 'album', identifier(album_url), data.embed.get('album_title'))
            except ValueError: pass
            return dict(artist_ref=artist, album_ref=album)
        raw = (data.tralbum or {}).get('current', {})
        art = meta.meta.get('og:image')
        profile = self.ref('bandcamp', kind, item_id, artist['title'] if kind == 'artist' else raw.get('title') or data.embed.get('album_title'),
            artwork={'480x480': art} if art else None, permalink=url, artist_ref=None if kind == 'artist' else artist,
            description=text(''.join(meta.bio) if kind == 'artist' else raw.get('about')),
            release_date=raw.get('release_date'), count=len((data.tralbum or {}).get('trackinfo', [])) if kind == 'album' else None)
        body = self.bc.browse(kind, item_id, page)
        for t in body['data']:
            t['artist_ref'] = artist
            if not t.get('artist'): t['artist'] = artist['title']
            if kind == 'album': t['album_ref'] = self.ref('bandcamp', 'album', item_id, profile['title'])
        return dict(body, profile=profile, sections=['albums'] if kind == 'artist' else ['tracks'], section='albums' if kind == 'artist' else 'tracks')

    def au_get(self, path, **params):
        r = self.au.client.get('https://api.audius.co/v1/' + path, params=params)
        r.raise_for_status(); return r.json()['data']

    def au_item(self, raw, kind='track'):
        user = raw if kind == 'artist' else raw.get('user') or {}
        artist = self.ref('audius', 'artist', user['id'], user.get('name')) if user.get('id') else None
        if kind == 'track':
            return dict(self.au.normalize(raw), artist_ref=artist)
        return self.ref('audius', 'artist' if kind == 'artist' else 'album' if raw.get('is_album') else 'playlist', raw['id'],
            raw.get('name') if kind == 'artist' else raw.get('playlist_name'), artist=user.get('name', ''),
            artist_ref=None if kind == 'artist' else artist, artwork=raw.get('profile_picture') if kind == 'artist' else raw.get('artwork'),
            description=text(raw.get('bio') if kind == 'artist' else raw.get('description')),
            count=raw.get('track_count'), release_date=raw.get('release_date'),
            permalink=urljoin('https://audius.co', '/' + user.get('handle', '')))

    def audius(self, kind, item_id, page, section):
        path = ('users/' if kind == 'artist' else 'tracks/' if kind == 'track' else 'playlists/') + item_id
        result = self.au_get(path)
        raw = result[0] if isinstance(result, list) and result else result
        if not isinstance(raw, dict): raise ValueError('Missing Audius entity')
        if kind == 'track': return dict(artist_ref=self.au_item(raw)['artist_ref'], album_ref=None)
        profile = self.au_item(raw, kind)
        section = (section or 'albums') if kind == 'artist' else 'tracks'
        entries = self.au_get(path + '/' + section, limit=11, offset=(page - 1) * 10)
        items = [self.au_item(t, 'track' if section == 'tracks' else 'playlist') for t in entries[:10]]
        if kind == 'album':
            for t in items: t['album_ref'] = self.ref('audius', 'album', item_id, profile['title'])
        return dict(profile=profile, data=items, page=page, has_more=len(entries) > 10,
                    sections=['albums', 'tracks', 'playlists'] if kind == 'artist' else ['tracks'], section=section)
