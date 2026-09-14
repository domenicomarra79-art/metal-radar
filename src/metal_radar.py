import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / 'data' / 'history.json'
REPORT_PATH = ROOT / 'data' / 'latest-report.md'
NOW = datetime.now(timezone.utc)
LOOKBACK = NOW - timedelta(days=7)
ROME = ZoneInfo('Europe/Rome')

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
            except (ValueError, TypeError):
                pass
    return NOW


def articles():
    output = []
    for source, urls in FEEDS.items():
        for url in urls:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                date = published(entry)
                title = entry.get('title', '')
                summary = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text(' ')
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


def token():
    auth = base64.b64encode(f"{os.environ['SPOTIFY_CLIENT_ID']}:{os.environ['SPOTIFY_CLIENT_SECRET']}".encode()).decode()
    response = requests.post('https://accounts.spotify.com/api/token', headers={'Authorization': f'Basic {auth}'}, data={'grant_type': 'refresh_token', 'refresh_token': os.environ['SPOTIFY_REFRESH_TOKEN']}, timeout=30)
    response.raise_for_status()
    return response.json()['access_token']


def search(access, artist, title):
    response = requests.get('https://api.spotify.com/v1/search', headers={'Authorization': f'Bearer {access}'}, params={'q': f'track:{title} artist:{artist}', 'type': 'track', 'market': 'IT', 'limit': 5}, timeout=30)
    response.raise_for_status()
    tracks = response.json().get('tracks', {}).get('items', [])
    for track in tracks:
        names = ' '.join(a['name'] for a in track.get('artists', []))
        if artist.lower() in names.lower() and title.lower() in track['name'].lower():
            return track
    return tracks[0] if tracks else None


def existing(access, playlist):
    response = requests.get(f'https://api.spotify.com/v1/playlists/{playlist}/tracks', headers={'Authorization': f'Bearer {access}'}, params={'limit': 100}, timeout=30)
    response.raise_for_status()
    return response.json().get('items', [])


def add(access, playlist, uris):
    if uris:
        response = requests.post(f'https://api.spotify.com/v1/playlists/{playlist}/tracks', headers={'Authorization': f'Bearer {access}', 'Content-Type': 'application/json'}, json={'uris': uris}, timeout=30)
        response.raise_for_status()


def main():
    history = load()
    items = articles()
    access = token()
    playlist = os.environ['SPOTIFY_PLAYLIST_ID']
    current = existing(access, playlist)
    known = set(history.get('tracks', [])) | {item.get('track', {}).get('id') for item in current}
    chosen, uris, artists = [], [], set()
    for candidate in candidates(items):
        if len(chosen) >= 15:
            break
        track = search(access, candidate['artist'], candidate['title'])
        if not track or track['id'] in known:
            continue
        artist = track['artists'][0]['name'].lower()
        if artist in artists:
            continue
        chosen.append((candidate, track))
        uris.append(track['uri'])
        artists.add(artist)
        known.add(track['id'])
    add(access, playlist, uris)
    history['tracks'] = sorted(set(history.get('tracks', [])) | known)
    history['articles'] = [item['link'] for item in items]
    history['last_run'] = datetime.now(timezone.utc).isoformat()
    save(history)
    lines = [f"# Metal Radar — {datetime.now(ROME).strftime('%Y-%m-%d %H:%M %Z')}", '', f'Added tracks: {len(uris)}', '', '## Selection', '']
    for candidate, track in chosen:
        lines.append(f"- **{track['artists'][0]['name']} — {track['name']}** — score {candidate['score']}")
    REPORT_PATH.write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
