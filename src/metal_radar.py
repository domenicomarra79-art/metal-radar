import base64
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from warnings import catch_warnings, filterwarnings
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / 'data' / 'history.json'
REPORT_PATH = ROOT / 'data' / 'latest-report.md'
NOW = datetime.now(timezone.utc)
LOOKBACK = NOW - timedelta(days=7)
ROME = ZoneInfo('Europe/Rome')
MAX_TRACKS = 15
MAX_PER_ARTIST = 2
SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'
SPOTIFY_API = 'https://api.spotify.com/v1'

FEEDS = {
    'Pitchfork': ['https://pitchfork.com/feed/feed-news/rss'],
    'Kerrang': ['https://www.kerrang.com/feed'],
    'Metal Injection': ['https://metalinjection.net/feed'],
    'Metalitalia': ['https://metalitalia.com/feed/'],
    'Louder': ['https://www.loudersound.com/rss'],
    'Revolver': ['https://www.revolvermag.com/feed'],
}
WEIGHTS = {'Pitchfork': 5, 'Kerrang': 5, 'Louder': 4, 'Metal Injection': 4, 'Metalitalia': 4, 'Revolver': 3}
METAL = re.compile(r'metal|doom|thrash|deathcore|metalcore|hardcore|sludge|post-metal|black metal|death metal|heavy|riff', re.I)
PAIR = re.compile(r'(?P<artist>[A-Z][^—–\-:|]{1,80})\s*[—–\-:]\s*["“\']?(?P<title>[^"”\'\n|]{2,100})')
PLAYLIST_IN_URL = re.compile(r'(/playlists/)([^/?#]+)')
REQUIRED_PLAYLIST_SCOPES = (
    'playlist-read-private',
    'playlist-modify-private',
    'playlist-modify-public',
)


def load():
    return json.loads(HISTORY_PATH.read_text()) if HISTORY_PATH.exists() else {'tracks': [], 'articles': [], 'last_run': None}


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


def articles():
    output = []
    for source, urls in FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
            except Exception as exc:
                print(f'RSS source skipped: {source} ({type(exc).__name__})')
                continue
            if getattr(feed, 'bozo', False) and not getattr(feed, 'entries', None):
                print(f'RSS source skipped: {source} (parse error)')
                continue
            for entry in getattr(feed, 'entries', []) or []:
                date = published(entry)
                title = entry.get('title', '') or ''
                with catch_warnings():
                    filterwarnings('ignore', category=MarkupResemblesLocatorWarning)
                    summary = BeautifulSoup(str(entry.get('summary', '')), 'html.parser').get_text(' ')
                text = f'{title} {summary}'
                if date >= LOOKBACK and METAL.search(text):
                    output.append({'source': source, 'title': title, 'link': entry.get('link', ''), 'text': text, 'published': date.isoformat()})
    unique = {}
    for item in output:
        unique[item['link'] or item['title']] = item
    return list(unique.values())


def candidates(items):
    grouped = {}
    for article in items:
        for match in PAIR.finditer(article['text']):
            artist = re.sub(r'\s+', ' ', match.group('artist')).strip(' .,')
            title = re.sub(r'\s+', ' ', match.group('title')).strip(' .,')
            if len(artist) < 2 or len(title) < 2:
                continue
            if any(x in artist.lower() for x in ('the best', 'this week', 'metal hammer')):
                continue
            key = (artist.lower(), title.lower())
            if key not in grouped:
                grouped[key] = {'artist': artist, 'title': title, 'score': 0, 'sources': set(), 'article': article['link']}
            grouped[key]['score'] += WEIGHTS.get(article['source'], 1)
            grouped[key]['sources'].add(article['source'])
    for item in grouped.values():
        item['score'] += max(0, len(item['sources']) - 1) * 3
    return sorted(grouped.values(), key=lambda item: item['score'], reverse=True)


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

    tracks_url = f'{SPOTIFY_API}/playlists/{playlist}/tracks'
    tracks_response = requests.get(
        tracks_url,
        headers=_auth_headers(access),
        params={'limit': 1},
        timeout=30,
    )
    _log_spotify_call('playlist tracks', 'GET', tracks_url, tracks_response)
    if not tracks_response.ok:
        raise _spotify_error('playlist tracks', tracks_response)


def search(access, artist, title):
    response = requests.get(
        f'{SPOTIFY_API}/search',
        headers=_auth_headers(access),
        params={'q': f'track:{title} artist:{artist}', 'type': 'track', 'market': 'IT', 'limit': 5},
        timeout=30,
    )
    if not response.ok:
        raise _spotify_error('search', response)
    tracks = response.json().get('tracks', {}).get('items', [])
    for track in tracks:
        names = ' '.join(a['name'] for a in track.get('artists', []))
        if artist.lower() in names.lower() and title.lower() in track['name'].lower():
            return track
    return tracks[0] if tracks else None


def existing(access, playlist):
    items = []
    url = f'{SPOTIFY_API}/playlists/{playlist}/tracks'
    params = {'limit': 100, 'fields': 'items(track(id)),next'}
    while url:
        response = requests.get(
            url,
            headers=_auth_headers(access),
            params=params,
            timeout=30,
        )
        params = None
        if not response.ok:
            _log_spotify_call('playlist tracks', 'GET', url, response)
            raise _spotify_error('playlist tracks', response)
        payload = response.json()
        items.extend(payload.get('items') or [])
        url = payload.get('next')
    return items


def add(access, playlist, uris):
    if not uris:
        return
    response = requests.post(
        f'{SPOTIFY_API}/playlists/{playlist}/tracks',
        headers={**_auth_headers(access), 'Content-Type': 'application/json'},
        json={'uris': uris},
        timeout=30,
    )
    if not response.ok:
        raise _spotify_error('playlist add', response)


def playlist_ids(items):
    ids = set()
    for item in items:
        track = item.get('track') if isinstance(item, dict) else None
        track_id = track.get('id') if isinstance(track, dict) else None
        if track_id:
            ids.add(track_id)
    return ids


def select_tracks(ranked, known_ids, lookup):
    chosen, uris = [], []
    known = set(known_ids)
    per_artist = Counter()
    for candidate in ranked:
        if len(chosen) >= MAX_TRACKS:
            break
        track = lookup(candidate['artist'], candidate['title'])
        if not track or not track.get('id') or track['id'] in known:
            continue
        artists = track.get('artists') or []
        if not artists:
            continue
        artist = artists[0]['name'].lower()
        if per_artist[artist] >= MAX_PER_ARTIST:
            continue
        chosen.append((candidate, track))
        uris.append(track['uri'])
        per_artist[artist] += 1
        known.add(track['id'])
    return chosen, uris, known


def write_report(chosen, uris):
    lines = [
        f"# Metal Radar — {datetime.now(ROME).strftime('%Y-%m-%d %H:%M %Z')}",
        '',
        f'Added tracks: {len(uris)}',
        '',
        '## Selection',
        '',
    ]
    if not chosen:
        lines.append('No new tracks this week.')
    for candidate, track in chosen:
        lines.append(f"- **{track['artists'][0]['name']} — {track['name']}** — score {candidate['score']}")
    REPORT_PATH.write_text('\n'.join(lines) + '\n')


def main():
    history = load()
    items = articles()
    access, granted_scope = _refresh_access_token()
    playlist = os.environ.get('SPOTIFY_PLAYLIST_ID', '')
    diagnose_spotify(access, playlist, granted_scope)
    if diagnostic_mode():
        print('Metal Radar diagnostic mode: playlist will not be modified')
        print('Added tracks: 0')
        return
    current = existing(access, playlist)
    known = set(history.get('tracks') or []) | playlist_ids(current)
    chosen, uris, known = select_tracks(
        candidates(items),
        known,
        lambda artist, title: search(access, artist, title),
    )
    add(access, playlist, uris)
    history['tracks'] = sorted(tid for tid in known if tid)
    history['articles'] = [item['link'] for item in items]
    history['last_run'] = datetime.now(timezone.utc).isoformat()
    save(history)
    write_report(chosen, uris)
    print(f'Added tracks: {len(uris)}')


if __name__ == '__main__':
    main()
