import base64
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from warnings import catch_warnings, filterwarnings
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from dateutil import parser as date_parser

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
from sources import SOURCES, SUBGENRES

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / 'data' / 'history.json'
REPORT_PATH = ROOT / 'data' / 'latest-report.md'
NOW = datetime.now(timezone.utc)
LOOKBACK = NOW - timedelta(days=7)
LOOKAHEAD = NOW + timedelta(days=14)
ROME = ZoneInfo('Europe/Rome')
MIN_TRACKS = 8
MAX_TRACKS = 15
MAX_PER_ARTIST = 2
MAX_PER_ALBUM = 3
MAX_PER_SOURCE = 3
CANDIDATE_POOL = 60
MIN_SCORE = 40
PITCHFORK_ONLY_CAP = 3
SINGLE_SPECIALIST_CAP = 3
SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'
SPOTIFY_API = 'https://api.spotify.com/v1'
SEARCH_LIMIT = 5
FEED_HEADERS = {
    'User-Agent': 'MetalRadar/1.0 (+https://github.com/domenicomarra79-art/metal-radar)',
    'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml, */*',
}
PLAYLIST_NAME = f'METAL RADAR — {datetime.now(ROME).year}'
PLAYLIST_DESCRIPTION = (
    'The essential metal radar. New riffs, heavy sounds and future classics — '
    'curated weekly from Pitchfork, Decibel, Revolver, Metal Injection, Loudwire, '
    'Stereogum, Consequence, BrooklynVegan, Kerrang, Louder, The Quietus, '
    'Metalitalia and Metallus. '
    'No algorithm. No filler. Just the stuff worth hearing.'
)
BUCKET_QUOTAS = {'established': 6, 'emerging': 5, 'underground': 3, 'european': 1}

METAL = re.compile(
    r'metal|doom|thrash|deathcore|metalcore|hardcore|sludge|stoner|post-metal|'
    r'black metal|death metal|heavy metal|riff|grind|djent|screamo',
    re.I,
)
PAIR = re.compile(r'(?P<artist>[A-Z][^—–\-:|]{1,80})\s*[—–\-:]\s*["“\']?(?P<title>[^"”\'\n|]{2,100})')
PLAYLIST_IN_URL = re.compile(r'(/playlists/)([^/?#]+)')
REQUIRED_PLAYLIST_SCOPES = (
    'playlist-read-private',
    'playlist-modify-private',
    'playlist-modify-public',
)
NOISE = re.compile(
    r'the best|this week|metal hammer|new music friday|albums you need|tracklist|'
    r'best metal|new metal albums|new metal releases|you need to hear|'
    r'the post\b|appeared first|track premieres?|album review|news roundup|'
    r'der beitrag\b|erschien zuerst|l[\'’]articolo\b|black listed friday|'
    r'premiere des musikvideos|premiere der single|veröffentlichen|erhältlich|'
    r'cambio di location|una data al|live il\b|biglietti|dettagli dell|'
    r'\bwatch\b|\blisten\b|enter now|full(?: album)? stream|video premiere|'
    r'win a trip|share new version',
    re.I,
)
EDITORIAL = re.compile(
    r'album of the week|song of the week|track of the week|premiere|exclusive|'
    r'essential|best new|must hear|album of the month',
    re.I,
)
ALBUMISH = re.compile(r'\balbum\b|\blp\b|\bfull[- ]length\b|\breview\b|\brecensione\b', re.I)
CURRENT = re.compile(r'\b(premiere|out now|released|reissue|new single|announced)\b', re.I)
NOSTALGIA = re.compile(r'\b(throwback|classic album|best of 19|anniversary unless)\b', re.I)
SUBGENRE_RE = re.compile('|'.join(re.escape(name) for name in sorted(SUBGENRES, key=len, reverse=True)), re.I)


def load():
    if not HISTORY_PATH.exists():
        return {'tracks': [], 'pairs': [], 'albums': [], 'articles': [], 'last_run': None}
    payload = json.loads(HISTORY_PATH.read_text())
    payload.setdefault('pairs', [])
    payload.setdefault('albums', [])
    return payload


def save(value):
    HISTORY_PATH.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def published(entry):
    for key in ('published', 'updated', 'created'):
        if entry.get(key):
            try:
                value = date_parser.parse(entry[key])
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            except (ValueError, OverflowError, TypeError):
                pass
    return NOW


def in_window(date, text):
    if LOOKBACK <= date <= LOOKAHEAD:
        return True
    if date < LOOKBACK and date >= NOW - timedelta(days=14) and CURRENT.search(text):
        return True
    return False


def clean_text(entry):
    title = entry.get('title', '') or ''
    with catch_warnings():
        filterwarnings('ignore', category=MarkupResemblesLocatorWarning)
        summary = BeautifulSoup(str(entry.get('summary', '')), 'html.parser').get_text(' ')
    return title, f'{title} {summary}'


def fetch_feed(url):
    try:
        response = requests.get(url, headers=FEED_HEADERS, timeout=30)
    except Exception as exc:
        return None, f'{type(exc).__name__}'
    if response.status_code != 200:
        return None, f'HTTP {response.status_code}'
    content_type = (response.headers.get('content-type') or '').lower()
    body = response.content
    if 'html' in content_type and b'<rss' not in body[:2000] and b'<feed' not in body[:2000]:
        return None, 'html response'
    feed = feedparser.parse(body)
    entries = getattr(feed, 'entries', None) or []
    if not entries:
        reason = 'parse error' if getattr(feed, 'bozo', False) else 'empty feed'
        return None, reason
    return feed, None


def articles():
    output = []
    consulted = []
    for source, config in SOURCES.items():
        fetched = False
        failures = []
        for url in config['feeds']:
            feed, error = fetch_feed(url)
            if error:
                failures.append(error)
                continue
            fetched = True
            for entry in feed.entries:
                date = published(entry)
                title, text = clean_text(entry)
                if not METAL.search(text):
                    continue
                if NOSTALGIA.search(text) and not CURRENT.search(text):
                    continue
                if not in_window(date, text):
                    continue
                output.append({
                    'source': source,
                    'title': title,
                    'link': entry.get('link', ''),
                    'text': text,
                    'published': date.isoformat(),
                    'region': config['region'],
                    'kind': config['kind'],
                    'authority': config['authority'],
                })
        if fetched:
            consulted.append(source)
        elif failures:
            print(f'RSS source skipped: {source} ({failures[0]})')
    unique = {}
    for item in output:
        unique[item['link'] or item['title']] = item
    return list(unique.values()), consulted


def detect_subgenre(text):
    match = SUBGENRE_RE.search(text or '')
    return match.group(0).lower() if match else 'metal'


def is_album(text, title):
    return bool(ALBUMISH.search(text) or ALBUMISH.search(title))


def source_bucket(sources, regions, kinds):
    if regions <= {'IT', 'EU'} and 'editorial' not in kinds:
        return 'european'
    if kinds <= {'specialist', 'wire'} or (len(sources) == 1 and 'specialist' in kinds):
        return 'underground'
    if len(sources) >= 3 or 'Pitchfork' in sources and len(sources) >= 2:
        return 'established'
    if 'Pitchfork' in sources or 'Kerrang' in sources or 'Louder' in sources or 'The Quietus' in sources:
        return 'established' if len(sources) >= 2 else 'emerging'
    return 'emerging'


def consensus_bonus(source_count, kinds):
    if source_count >= 3:
        return 15
    if source_count == 2:
        return 10
    if 'editorial' in kinds:
        return 5
    if 'specialist' in kinds:
        return 3
    return 0


def score_candidate(item):
    sources = item['sources']
    kinds = item['kinds']
    authority = max(item['authorities'] or [5])
    single_specialist = len(sources) == 1 and kinds <= {'specialist', 'wire'}
    quality = min(30, 12 + authority * 2 + (8 if item['editorial'] else 0))
    if single_specialist:
        originality = 8
        discovery = 4
    else:
        originality = 20 if item['bucket'] in {'underground', 'european'} else 12 if item['bucket'] == 'emerging' else 8
        discovery = 10 if item['bucket'] in {'underground', 'european', 'emerging'} else 4
    critical = min(15, 5 * max(0, len(sources) - 1) + (5 if item['editorial'] else 0))
    relevance = 10 if item['recent'] else 6
    metal_cred = 10 if item['subgenre'] != 'metal' else 7
    diversity = 5
    total = quality + originality + critical + relevance + metal_cred + discovery + diversity
    total = min(100, total + consensus_bonus(len(sources), kinds))
    if 'Pitchfork' in sources and len(sources) == 1:
        total = min(total, 72)
    if single_specialist:
        total = min(total, 70)
    return total, {
        'quality': quality,
        'originality': originality,
        'critical_consensus': critical,
        'relevance': relevance,
        'metal_credibility': metal_cred,
        'discovery_value': discovery,
        'diversity': diversity,
    }


def valid_pair(artist, title):
    artist = re.sub(r'\s+', ' ', artist).strip(' .,:"\'')
    title = re.sub(r'\s+', ' ', title).strip(' .,:"\'')
    artist = re.sub(r'^(review|interview|news|album)\s*\]\s*', '', artist, flags=re.I)
    if len(artist) < 2 or len(title) < 2 or len(artist) > 50 or len(title) > 70:
        return None
    if len(artist.split()) > 5:
        return None
    if NOISE.search(artist) or NOISE.search(title):
        return None
    if artist.lower() in {'metal', 'metal scene', 'news', 'review', 'album', 'premiere'}:
        return None
    if re.search(r'\*{2,}|f\*\*+', artist, re.I):
        return None
    if re.search(
        r'^(watch|listen|read|enter|full|video|news|premiere|stream|welcome)\b',
        artist,
        re.I,
    ):
        return None
    if re.search(
        r'\b(announces?|interview|festival|\bfest\b|pre-?order|vinyl variant|dettagli dell|'
        r'special|beitrag|artikel|premiere des|premiere der|tour returns|'
        r'went super|most exciting|thanked for)\b',
        artist,
        re.I,
    ):
        return None
    if re.search(
        r'^(il nuovo|la nuova|with the|conceptual|titled album|back to the|disponibile)\b',
        title,
        re.I,
    ):
        return None
    if re.search(
        r'\b(tour|concerto|biglietti|carroponte|intervista|location|musikvideos?|'
        r'veröffentlichen|erhältlich|physisch|single vom|zweite single|'
        r'win a trip|anybody living|month[- ]long|support act|nuovo singolo|'
        r'rivelati i dettagli|neues video|szene gefeiert)\b|\b202[7-9]\b',
        title,
        re.I,
    ):
        return None
    if len(artist.split()) >= 3 and len(title.split()) <= 2 and title.isupper():
        return None
    if not re.search(r'[A-Za-zÀ-ÿ]', artist) or not re.search(r'[A-Za-zÀ-ÿ]', title):
        return None
    return artist, title


def candidates(items):
    grouped = {}
    for article in items:
        blobs = [article['title'], article['text'][:400]]
        for blob in blobs:
            for match in PAIR.finditer(blob):
                parsed = valid_pair(match.group('artist'), match.group('title'))
                if not parsed:
                    continue
                artist, title = parsed
                key = (artist.lower(), title.lower())
                if key not in grouped:
                    grouped[key] = {
                        'artist': artist,
                        'title': title,
                        'album': title if is_album(article['text'], article['title']) else '',
                        'kind': 'album' if is_album(article['text'], article['title']) else 'track',
                        'sources': set(),
                        'source_urls': [],
                        'kinds': set(),
                        'regions': set(),
                        'authorities': [],
                        'editorial': False,
                        'recent': False,
                        'subgenre': detect_subgenre(article['text']),
                        'summary': article['title'],
                    }
                grouped[key]['sources'].add(article['source'])
                if article['link']:
                    grouped[key]['source_urls'].append(article['link'])
                grouped[key]['kinds'].add(article['kind'])
                grouped[key]['regions'].add(article['region'])
                grouped[key]['authorities'].append(article['authority'])
                grouped[key]['editorial'] = grouped[key]['editorial'] or bool(EDITORIAL.search(article['text']))
                published_at = date_parser.parse(article['published'])
                grouped[key]['recent'] = grouped[key]['recent'] or published_at >= LOOKBACK
                if is_album(article['text'], article['title']):
                    grouped[key]['kind'] = 'album'
                    grouped[key]['album'] = grouped[key]['album'] or title
    ranked = []
    for item in grouped.values():
        item['bucket'] = source_bucket(item['sources'], item['regions'], item['kinds'])
        item['score'], item['source_scores'] = score_candidate(item)
        item['source_publications'] = sorted(item['sources'])
        ranked.append(item)
    ranked.sort(key=lambda item: item['score'], reverse=True)
    return ranked[:CANDIDATE_POOL]


def diagnostic_mode():
    return os.environ.get('METAL_RADAR_DIAGNOSTIC', '').strip().lower() in {'1', 'true', 'yes'}


def _redact_endpoint(url):
    if not url:
        return ''
    return PLAYLIST_IN_URL.sub(r'\1***', url.split('?')[0])


def _mask_id(value):
    text = str(value or '')
    if not text:
        return 'missing'
    if len(text) <= 4:
        return '***'
    return f'{text[:2]}***{text[-2:]}'


def _spotify_status_code_message(response):
    status = response.status_code
    code = 'unknown'
    message = 'unknown'
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return status, 'unreadable', 'unreadable'
    if not isinstance(payload, dict):
        return status, 'unknown', 'unknown'
    error = payload.get('error')
    if isinstance(error, dict):
        code = str(error.get('reason') or error.get('status') or 'unknown')
        message = str(error.get('message') or 'unknown')
    elif error:
        code = str(error)
        message = code
    return status, code, message


def _log_spotify_call(label, method, url, response):
    status, code, message = _spotify_status_code_message(response)
    print(f'Spotify {label}: HTTP {status}')
    print(f'Spotify {label}: {method} {_redact_endpoint(url)}')
    if not response.ok:
        print(f'Spotify {label}: error_code={code} message={message}')
    return status, code, message


def _spotify_error(action, response):
    status, code, message = _spotify_status_code_message(response)
    detail = message if action != 'token' and message not in {'unknown', 'unreadable'} else code
    return RuntimeError(f'Spotify {action} HTTP {status} [{detail}]')


def _auth_headers(access):
    return {'Authorization': f'Bearer {access}'}


def _refresh_access_token():
    client_id = os.environ['SPOTIFY_CLIENT_ID']
    client_secret = os.environ['SPOTIFY_CLIENT_SECRET']
    refresh_token = os.environ['SPOTIFY_REFRESH_TOKEN']
    auth = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        headers={
            'Authorization': f'Basic {auth}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        data={
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        },
        timeout=30,
    )
    if not response.ok:
        raise _spotify_error('token', response)
    payload = response.json()
    access_token = payload.get('access_token')
    if not access_token:
        raise RuntimeError('Spotify token HTTP 200 [missing_access_token]')
    scope = payload.get('scope')
    granted = scope.strip() if isinstance(scope, str) else ''
    return access_token, granted


def token():
    access_token, _granted = _refresh_access_token()
    return access_token


def _log_granted_scopes(granted):
    if not granted:
        print('Spotify granted scope: not_returned_by_token_endpoint')
        print('Spotify required playlist scopes cannot be verified from this refresh token response')
        return
    present = set(granted.split())
    print('Spotify granted scope:', ' '.join(sorted(present)))
    missing = [scope for scope in REQUIRED_PLAYLIST_SCOPES if scope not in present]
    if missing:
        print('Spotify missing required playlist scopes:', ' '.join(missing))
    else:
        print('Spotify required playlist scopes: present')


def diagnose_spotify(access, playlist, granted_scope):
    print(f'SPOTIFY_PLAYLIST_ID set: {"yes" if str(playlist or "").strip() else "no"}')
    _log_granted_scopes(granted_scope)

    me_url = f'{SPOTIFY_API}/me'
    me_response = requests.get(me_url, headers=_auth_headers(access), timeout=30)
    _log_spotify_call('me', 'GET', me_url, me_response)
    if me_response.ok:
        payload = me_response.json()
        payload = payload if isinstance(payload, dict) else {}
        display_name = payload.get('display_name') or 'missing_display_name'
        print(f'Spotify account display_name={display_name}')
        print(f'Spotify account id={_mask_id(payload.get("id"))}')

    if not str(playlist or '').strip():
        raise RuntimeError('SPOTIFY_PLAYLIST_ID missing')

    playlist_url = f'{SPOTIFY_API}/playlists/{playlist}'
    playlist_response = requests.get(playlist_url, headers=_auth_headers(access), timeout=30)
    _log_spotify_call('playlist', 'GET', playlist_url, playlist_response)
    if playlist_response.ok:
        payload = playlist_response.json()
        payload = payload if isinstance(payload, dict) else {}
        owner = payload.get('owner') if isinstance(payload.get('owner'), dict) else {}
        print(f'Spotify playlist public={payload.get("public")}')
        print(f'Spotify playlist collaborative={payload.get("collaborative")}')
        print(f'Spotify playlist owner_id={_mask_id(owner.get("id"))}')

    items_url = f'{SPOTIFY_API}/playlists/{playlist}/items'
    items_response = requests.get(
        items_url,
        headers=_auth_headers(access),
        params={'limit': 1},
        timeout=30,
    )
    _log_spotify_call('playlist items', 'GET', items_url, items_response)
    if not items_response.ok:
        raise _spotify_error('playlist items', items_response)


def _search(access, query, kind):
    response = requests.get(
        f'{SPOTIFY_API}/search',
        headers=_auth_headers(access),
        params={'q': query, 'type': kind, 'market': 'IT', 'limit': SEARCH_LIMIT},
        timeout=30,
    )
    if not response.ok:
        raise _spotify_error('search', response)
    return response.json()


def search(access, artist, title):
    payload = _search(access, f'track:{title} artist:{artist}', 'track')
    tracks = payload.get('tracks', {}).get('items', [])
    for track in tracks:
        names = ' '.join(a['name'] for a in track.get('artists', []))
        if artist.lower() in names.lower() and title.lower() in track['name'].lower():
            return track
    return tracks[0] if tracks else None


def album_tracks(access, artist, title):
    payload = _search(access, f'album:{title} artist:{artist}', 'album')
    albums = payload.get('albums', {}).get('items', []) or []
    album = None
    for item in albums:
        names = ' '.join(a['name'] for a in item.get('artists', []))
        if artist.lower() in names.lower() and title.lower() in (item.get('name') or '').lower():
            album = item
            break
    album = album or (albums[0] if albums else None)
    if not album or not album.get('id'):
        return []
    response = requests.get(
        f'{SPOTIFY_API}/albums/{album["id"]}/tracks',
        headers=_auth_headers(access),
        params={'limit': 10, 'market': 'IT'},
        timeout=30,
    )
    if not response.ok:
        _log_spotify_call('album tracks', 'GET', f'{SPOTIFY_API}/albums/{album["id"]}/tracks', response)
        fallback = search(access, artist, title)
        return [fallback] if fallback else []
    tracks = []
    for item in response.json().get('items') or []:
        if not item or not item.get('id'):
            continue
        item = dict(item)
        item['album'] = {'name': album.get('name', title), 'id': album.get('id')}
        if not item.get('artists'):
            item['artists'] = album.get('artists') or [{'name': artist}]
        tracks.append(item)
        if len(tracks) >= MAX_PER_ALBUM:
            break
    return tracks


def resolve_candidate(access, candidate):
    if candidate.get('kind') == 'album':
        tracks = album_tracks(access, candidate['artist'], candidate.get('album') or candidate['title'])
        if tracks:
            return tracks
    track = search(access, candidate['artist'], candidate['title'])
    return [track] if track else []


def existing(access, playlist):
    items = []
    url = f'{SPOTIFY_API}/playlists/{playlist}/items'
    params = {'limit': 100}
    while url:
        response = requests.get(
            url,
            headers=_auth_headers(access),
            params=params,
            timeout=30,
        )
        params = None
        if not response.ok:
            _log_spotify_call('playlist items', 'GET', url, response)
            raise _spotify_error('playlist items', response)
        payload = response.json()
        items.extend(payload.get('items') or [])
        url = payload.get('next')
    return items


def add(access, playlist, uris):
    if not uris:
        return
    url = f'{SPOTIFY_API}/playlists/{playlist}/items'
    response = requests.post(
        url,
        headers={**_auth_headers(access), 'Content-Type': 'application/json'},
        json={'uris': uris},
        timeout=30,
    )
    if not response.ok:
        _log_spotify_call('playlist add', 'POST', url, response)
        raise _spotify_error('playlist add', response)


def update_playlist_profile(access, playlist):
    url = f'{SPOTIFY_API}/playlists/{playlist}'
    response = requests.put(
        url,
        headers={**_auth_headers(access), 'Content-Type': 'application/json'},
        json={'name': PLAYLIST_NAME, 'description': PLAYLIST_DESCRIPTION},
        timeout=30,
    )
    if not response.ok:
        _log_spotify_call('playlist profile', 'PUT', url, response)


def playlist_entry(item):
    if not isinstance(item, dict):
        return None
    for key in ('item', 'track'):
        entry = item.get(key)
        if isinstance(entry, dict) and entry.get('id'):
            return entry
    return None


def playlist_ids(items):
    ids = set()
    for item in items:
        entry = playlist_entry(item)
        if entry:
            ids.add(entry['id'])
    return ids


def pair_key(artist, title):
    return (str(artist or '').strip().lower(), str(title or '').strip().lower())


def catalog_from_items(items, history):
    ids = set(history.get('tracks') or []) | playlist_ids(items)
    pairs = {tuple(part.split('|', 1)) for part in history.get('pairs') or [] if '|' in part}
    albums = {tuple(part.split('|', 1)) for part in history.get('albums') or [] if '|' in part}
    for item in items:
        entry = playlist_entry(item)
        if not entry:
            continue
        artist = ((entry.get('artists') or [{}])[0].get('name') or '')
        title = entry.get('name') or ''
        album = ((entry.get('album') or {}).get('name') or '')
        if artist and title:
            pairs.add(pair_key(artist, title))
        if artist and album:
            albums.add(pair_key(artist, album))
    return {'ids': ids, 'pairs': pairs, 'albums': albums}


def why_selected(candidate):
    pubs = ', '.join(candidate.get('source_publications') or sorted(candidate.get('sources') or []))
    bucket = candidate.get('bucket', 'emerging')
    return f'Score {candidate.get("score", 0)}; {bucket} pick via {pubs or "press coverage"}.'


def passes_quality_gate(candidate, track, catalog, subgenre_counts):
    if not track or not track.get('id') or track['id'] in catalog['ids']:
        return False
    if candidate.get('source_scores') and candidate.get('score', 0) < MIN_SCORE:
        return False
    artist = ((track.get('artists') or [{}])[0].get('name') or candidate['artist'])
    title = track.get('name') or candidate['title']
    album = ((track.get('album') or {}).get('name') or candidate.get('album') or '')
    if pair_key(artist, title) in catalog['pairs']:
        return False
    if album and pair_key(artist, album) in catalog['albums'] and candidate.get('kind') == 'album':
        return False
    subgenre = candidate.get('subgenre') or 'metal'
    if subgenre_counts[subgenre] >= 4 and subgenre != 'metal':
        return False
    return True


def select_tracks(ranked, known_ids, lookup, catalog=None):
    catalog = dict(catalog or {})
    catalog.setdefault('ids', set(known_ids))
    catalog.setdefault('pairs', set())
    catalog.setdefault('albums', set())
    chosen, uris = [], []
    known = set(catalog['ids'])
    per_artist = Counter()
    per_album = Counter()
    per_source = Counter()
    subgenre_counts = Counter()
    buckets = Counter()
    pitchfork_only = 0
    single_specialist = 0
    duplicates_rejected = 0

    def _lookup(candidate):
        if lookup.__code__.co_argcount == 1:
            return lookup(candidate)
        return lookup(candidate['artist'], candidate['title'])

    def _is_single_specialist(candidate):
        sources = candidate.get('sources') or set()
        kinds = candidate.get('kinds') or set()
        return len(sources) == 1 and kinds <= {'specialist', 'wire'}

    def _selection_priority(candidate):
        sources = candidate.get('sources') or set()
        kinds = candidate.get('kinds') or set()
        editorial = 2 if 'editorial' in kinds else 0
        multi = min(2, max(0, len(sources) - 1))
        return (editorial + multi, candidate.get('score', 0))

    def consider(candidate):
        nonlocal duplicates_rejected, pitchfork_only, single_specialist
        if len(chosen) >= MAX_TRACKS:
            return False
        if candidate.get('source_scores') and candidate.get('score', 0) < MIN_SCORE:
            return False
        sources = candidate.get('sources') or set()
        if any(per_source[source] >= MAX_PER_SOURCE for source in sources):
            return False
        if sources == {'Pitchfork'} and pitchfork_only >= PITCHFORK_ONLY_CAP:
            return False
        if _is_single_specialist(candidate) and single_specialist >= SINGLE_SPECIALIST_CAP:
            return False
        resolved = _lookup(candidate)
        if resolved is None:
            return False
        tracks = resolved if isinstance(resolved, list) else [resolved]
        added = False
        for track in tracks:
            if not track or not track.get('id'):
                continue
            if track['id'] in known or pair_key(
                ((track.get('artists') or [{}])[0].get('name') or ''),
                track.get('name') or '',
            ) in catalog['pairs']:
                duplicates_rejected += 1
                continue
            artists = track.get('artists') or []
            if not artists:
                continue
            artist = artists[0]['name'].lower()
            album_name = ((track.get('album') or {}).get('name') or candidate.get('album') or '').lower()
            if per_artist[artist] >= MAX_PER_ARTIST:
                continue
            if album_name and per_album[album_name] >= MAX_PER_ALBUM:
                continue
            if not passes_quality_gate(candidate, track, {'ids': known, 'pairs': catalog['pairs'], 'albums': catalog['albums']}, subgenre_counts):
                continue
            chosen.append((candidate, track))
            uris.append(track['uri'])
            per_artist[artist] += 1
            if album_name:
                per_album[album_name] += 1
            known.add(track['id'])
            catalog['pairs'].add(pair_key(artists[0]['name'], track.get('name')))
            if album_name:
                catalog['albums'].add(pair_key(artists[0]['name'], album_name))
            subgenre_counts[candidate.get('subgenre') or 'metal'] += 1
            buckets[candidate.get('bucket') or 'emerging'] += 1
            for source in sources:
                per_source[source] += 1
            if sources == {'Pitchfork'}:
                pitchfork_only += 1
            if _is_single_specialist(candidate):
                single_specialist += 1
            added = True
            if candidate.get('kind') != 'album':
                break
        return added

    remaining = list(ranked)
    for bucket, quota in BUCKET_QUOTAS.items():
        still = []
        for candidate in remaining:
            if buckets[bucket] >= quota:
                still.append(candidate)
                continue
            if (candidate.get('bucket') or 'emerging') != bucket:
                still.append(candidate)
                continue
            if not consider(candidate):
                still.append(candidate)
        remaining = still
    remaining.sort(key=_selection_priority, reverse=True)
    for candidate in remaining:
        if len(chosen) >= MAX_TRACKS:
            break
        consider(candidate)
    catalog['ids'] = known
    return chosen, uris, known, {
        'duplicates_rejected': duplicates_rejected,
        'buckets': dict(buckets),
        'per_source': dict(per_source),
    }


def write_report(chosen, uris, stats):
    lines = [
        f"# Metal Radar — {datetime.now(ROME).strftime('%Y-%m-%d %H:%M %Z')}",
        '',
        f'Playlist: {PLAYLIST_NAME}',
        f'NEW TRACKS THIS WEEK: {len(uris)}',
        f'TOTAL PLAYLIST TRACKS: {stats.get("playlist_total", len(uris))}',
        f'DUPLICATES REJECTED: {stats.get("duplicates_rejected", 0)}',
        f'SOURCES CONSULTED: {", ".join(stats.get("sources_consulted") or []) or "none"}',
        '',
        '## NEW TRACKS ADDED',
        '',
    ]
    if not chosen:
        lines.append('No new tracks this week.')
    for index, (candidate, track) in enumerate(chosen, 1):
        album = ((track.get('album') or {}).get('name') or candidate.get('album') or 'n/a')
        pubs = ', '.join(candidate.get('source_publications') or sorted(candidate.get('sources') or []))
        lines.extend([
            f"{index}. **{track['artists'][0]['name']} — {track['name']}**",
            f"   Album: {album}",
            f"   Subgenre: {candidate.get('subgenre', 'metal')}",
            f"   Score: {candidate.get('score', 0)}",
            f"   Why: {why_selected(candidate)}",
            f"   Sources: {pubs or 'n/a'}",
            '',
        ])
    REPORT_PATH.write_text('\n'.join(lines).rstrip() + '\n')


def main():
    history = load()
    items, consulted = articles()
    access, granted_scope = _refresh_access_token()
    playlist = os.environ.get('SPOTIFY_PLAYLIST_ID', '')
    if not str(playlist).strip():
        raise RuntimeError('SPOTIFY_PLAYLIST_ID missing')
    if diagnostic_mode():
        diagnose_spotify(access, playlist, granted_scope)
        print('Metal Radar diagnostic mode: playlist will not be modified')
        print('Added tracks: 0')
        return
    current = existing(access, playlist)
    catalog = catalog_from_items(current, history)
    ranked = candidates(items)
    chosen, uris, known, extra = select_tracks(
        ranked,
        catalog['ids'],
        lambda candidate: resolve_candidate(access, candidate),
        catalog,
    )
    if len(chosen) < MIN_TRACKS:
        print(f'Selection below target ({len(chosen)} < {MIN_TRACKS}); adding only vetted tracks')
    add(access, playlist, uris)
    update_playlist_profile(access, playlist)
    playlist_total = len(catalog['ids'] | known)
    history['tracks'] = sorted(tid for tid in known if tid)
    history['pairs'] = sorted('|'.join(pair) for pair in catalog['pairs'] if pair[0] and pair[1])
    history['albums'] = sorted('|'.join(pair) for pair in catalog['albums'] if pair[0] and pair[1])
    history['articles'] = [item['link'] for item in items]
    history['last_run'] = datetime.now(timezone.utc).isoformat()
    save(history)
    stats = {
        'duplicates_rejected': extra.get('duplicates_rejected', 0),
        'sources_consulted': consulted,
        'playlist_total': playlist_total,
    }
    write_report(chosen, uris, stats)
    print(f'Added tracks: {len(uris)}')
    print(f'Sources consulted: {len(consulted)}')
    print(f'Duplicates rejected: {stats["duplicates_rejected"]}')


if __name__ == '__main__':
    main()
