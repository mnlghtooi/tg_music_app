"""Conservative metadata matching: exact identity + version + duration for auto-import."""
from difflib import SequenceMatcher
import math
import re
import unicodedata


def words(value):
    text = unicodedata.normalize('NFKD', value or '').casefold()
    return ' '.join(re.findall(r'\w+', ''.join(c for c in text if not unicodedata.combining(c))))


VERSION_PATTERNS = {
    'live': r'\b(live|concert|концерт\w*)\b',
    'remix': r'\b(remix\w*|ремикс\w*)\b',
    'cover': r'\b(cover\w*|кавер\w*)\b',
    'instrumental': r'\b(instrumental|инструментал\w*|минус\w*)\b',
    'karaoke': r'\b(karaoke|караоке)\b',
    'acoustic': r'\b(acoustic|акуст\w*)\b',
    'speed': r'\b(sped|slowed|nightcore|ускор\w*|замедл\w*)\b',
    'edit': r'\b(edit|extended|radio|rework|demo)\b',
    'remaster': r'\b(remaster\w*)\b',
    'mono': r'\bmono\b', 'stereo': r'\bstereo\b',
}


def versions(title):
    return {label for label, pattern in VERSION_PATTERNS.items() if re.search(pattern, words(title))}


def identity(track, expected_artist=''):
    title = track.get('title') or ''
    artist = track.get('artist') or ''
    # Crediting the original artist in "Artist - Title" is common in catalog
    # uploads whose uploader name is not the recording artist.
    prefix = re.match(r'^(.+?)\s+[-–—|:]\s+(.+)$', title)
    if prefix and expected_artist and words(prefix[1]) == words(expected_artist):
        artist, title = prefix[1], prefix[2]
    feature = re.search(r'\s*[\(\[]?\b(?:feat\.?|ft\.?|featuring)\s+(.+?)[\)\]]?\s*$', title, re.I)
    if feature:
        credit = feature[1].rstrip(')]').strip()
        if words(credit) not in words(artist): artist += ' ' + credit
        title = title[:feature.start()]
    return words(title), words(artist)


def seconds(value):
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else 0


def assess(original, candidate):
    original_title, original_artist = identity(original)
    title, artist = identity(candidate, original.get('artist', ''))
    title_score = SequenceMatcher(None, original_title, title).ratio()
    artist_score = SequenceMatcher(None, original_artist, artist).ratio()
    original_duration = seconds(original.get('duration'))
    duration = seconds(candidate.get('duration'))
    delta = abs(original_duration - duration) if original_duration and duration else None
    tolerance = max(2, min(5, original_duration * .015))
    duration_score = max(0, 1 - delta / max(15, original_duration * .1)) if delta is not None else 0
    score = .55 * title_score + .30 * artist_score + .15 * duration_score
    reasons = []
    if title != original_title: reasons.append('Название отличается')
    if artist != original_artist or not artist: reasons.append('Исполнитель не подтверждён')
    if versions(original.get('title', '')) != versions(candidate.get('title', '')):
        reasons.append('Другая версия записи'); score = min(score, .6)
    if candidate.get('preview'):
        reasons.append('Доступно только превью'); score = min(score, .6)
    if candidate.get('unavailable'):
        reasons.append('Запись недоступна'); score = 0
    if delta is None: reasons.append('Не подтверждена длительность')
    elif delta > tolerance:
        reasons.append('Длительность отличается'); score = min(score, .7)
    original_isrc = words(original.get('isrc', '')).replace(' ', '')
    candidate_isrc = words(candidate.get('isrc', '')).replace(' ', '')
    if original_isrc and original_isrc != candidate_isrc:
        reasons.append('Код записи не совпал')
    return {'score': round(score, 4), 'automatic': not reasons,
            'reasons': reasons, 'duration_delta': delta,
            'identity': (title, artist, tuple(sorted(versions(candidate.get('title', '')))))}


def rank_matches(original, candidates):
    unique = {}
    for candidate in candidates:
        if candidate.get('kind', 'track') != 'track' or candidate.get('unavailable'): continue
        assessment = assess(original, candidate)
        item = {**candidate, 'score': assessment['score'], 'assessment': assessment}
        key = (item.get('source'), str(item.get('id')))
        if key not in unique or item['score'] > unique[key]['score']: unique[key] = item
    ranked = sorted(unique.values(), key=lambda t: (
        not t['assessment']['automatic'], -t['score'],
        t['assessment']['duration_delta'] if t['assessment']['duration_delta'] is not None else float('inf'),
        {'bandcamp': 0, 'audius': 1, 'soundcloud': 2}.get(t.get('source'), 3), str(t.get('id'))))
    automatic = [t for t in ranked if t['assessment']['automatic']]
    selected = automatic[0] if automatic else None
    if len(automatic) > 1:
        first, second = automatic[:2]
        if (abs(first['assessment']['duration_delta'] - second['assessment']['duration_delta']) < .5
                and abs(seconds(first.get('duration')) - seconds(second.get('duration'))) > 2):
            selected = None
            first['assessment']['reasons'].append('Есть равноценные варианты с разной длительностью')
            first['assessment']['automatic'] = False
    best = ranked[0] if ranked and ranked[0]['score'] >= .65 else None
    return {'selected': selected, 'best': best, 'state': 'matched' if selected else 'review' if best else 'not_found',
            'data': ranked[:5]}


def match_score(original, candidate):
    return assess(original, candidate)['score']
