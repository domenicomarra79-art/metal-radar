import base64
import json
import os
import re
import sys
import time
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
from sources import (
    DISCOVERY_SOURCES,
    METAL_NATIVE_SOURCES,
    QUALITY_SOURCES,
    REGIONAL_SOURCES,
    SOURCES,
    SUBGENRES,
)

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
MAX_PER_ALBUM = 1
MAX_PER_ALBUM_TOP = 2
MAX_PER_SOURCE_QUALITY = 3
MAX_PER_SOURCE_DISCOVERY = 2
MAX_PER_SOURCE_REGIONAL = 3
CANDIDATE_POOL = 80
MIN_SCORE = 40
PITCHFORK_ONLY_CAP = 3
SINGLE_DISCOVERY_CAP = 3
PREMIERE_SCORE_CAP = 55
MAX_REVIEW_BODIES = 40
SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'
SPOTIFY_API = 'https://api.spotify.com/v1'
SEARCH_LIMIT = 5
FEED_HEADERS = {
    'User-Agent': 'MetalRadar/1.0 (+https://github.com/domenicomarra79-art/metal-radar)',
    'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml, */*',
}
HTML_HEADERS = {
    'User-Agent': 'MetalRadar/1.0 (+https://github.com/domenicomarra79-art/metal-radar)',
    'Accept': 'text/html,application/xhtml+xml',
}
PLAYLIST_NAME = 'METAL RADAR'
PLAYLIST_DESCRIPTION = (
    'The essential metal radar. New riffs, heavy sounds and future classics — '
    'curated weekly from the best metal press'
)
FILL_ORDER = (
    ('review', 8),
    ('premiere', 4),
    ('european', 2),
    ('underground', 1),
)

METAL = re.compile(
    r'metal|doom|thrash|deathcore|metalcore|hardcore|sludge|stoner|post-metal|'
    r'black metal|death metal|heavy metal|riff|grind|djent|screamo',
    re.I,
)
PAIR = re.compile(r'(?P<artist>[A-Z][^—–\-:|]{1,80})\s*[—–\-:]\s*["“\']?(?P<title>[^"”\'\n|]{2,100})')
PREMIERE_PAIR = re.compile(
    r'(?:Track Premiere|Video Premiere|Full(?: Album)? Stream)\s*[:—–-]\s*'
    r'(?P<artist>[^–\—"“\']+?)\s*[–—\-:]\s*["“\']?(?P<title>[^"”\'\n]+)',
    re.I,
)
MI_QUOTED_PAIR = re.compile(
    r'(?:LISTEN|WATCH)\s*[:—–-]\s*'
    r'(?P<artist>(?:[A-Z0-9][A-Z0-9.&\'’/-]*)(?:\s+(?:&|AND|THE|OF|[A-Z0-9][A-Z0-9.&\'’/-]*)){0,6})\b'
    r'[^"“]{0,120}?["“](?P<title>[^"”]{2,80})["”]',
)
HEAR_QUOTED_PAIR = re.compile(
    r'\bHear\s+(?P<artist>[A-Z][\w.&’/-]*(?:\s+[A-Z][\w.&’/-]*){0,3})[\'’]?s?\s+'
    r'(?:first\s+|new\s+)?(?:album|song|single|track).{0,80}?["“‘](?P<title>[^"”’]{2,80})["”’]',
)
ANNOUNCE_QUOTED_PAIR = re.compile(
    r'(?P<artist>[A-Z][A-Z0-9][A-Z0-9\s.&\'’/-]{0,40}?)\s+Announce\b[^"“]{0,100}?["“](?P<title>[^"”]{2,80})["”]',
)
IT_SINGLE_PAIR = re.compile(
    r'^(?P<artist>[A-ZÀ-Ü0-9][^:]{1,60}?)\s*:\s*'
    r'(?:il nuovo singolo|il video(?: del nuovo singolo)?(?: di)?|la nuova versione di)\s*'
    r'["“](?P<title>[^"”]{2,80})["”]',
    re.I,
)
REVIEW_HEADLINE = re.compile(
    r'^(?:Review:\s*)?(?P<artist>.+?)\s*[—–-]\s*(?P<album>.+?)(?:\s+Review|\s+Recensione)?\s*$',
    re.I,
)
REVIEW_HEADLINE_FLIP = re.compile(
    r'^Review:\s*(?P<artist>.+?)\s*[—–-]\s*(?P<album>.+)$',
    re.I,
)
PLAYLIST_IN_URL = re.compile(r'(/playlists/)([^/?#]+)')
REQUIRED_PLAYLIST_SCOPES = (
    'playlist-read-private',
    'playlist-modify-private',
    'playlist-modify-public',
)
NOISE = re.compile(
    r'the best|this week|metal hammer|new music friday|albums you need|tracklist|'
    r'best metal|new metal albums|new metal releases|you need to hear|'
    r'the post\b|appeared first|track premieres?|news roundup|'
    r'der beitrag\b|erschien zuerst|l[\'’]articolo\b|black listed friday|'
    r'premiere des musikvideos|premiere der single|veröffentlichen|erhältlich|'
    r'cambio di location|una data al|live il\b|biglietti|dettagli dell|'
    r'enter now|win a trip|share new version|album dal vivo|live at\b|'
    r'chart recap|giveaway|ticket|festival lineup',
    re.I,
)
EDITORIAL = re.compile(
    r'album of the week|song of the week|track of the week|premiere|exclusive|'
    r'essential|best new|must hear|album of the month|highly recommended',
    re.I,
)
ALBUMISH = re.compile(r'\balbum\b|\blp\b|\bfull[- ]length\b|\breview\b|\brecensione\b', re.I)
CURRENT = re.compile(r'\b(premiere|out now|released|reissue|new single|announced)\b', re.I)
NOSTALGIA = re.compile(r'\b(throwback|classic album|best of 19|anniversary unless)\b', re.I)
SUBGENRE_RE = re.compile('|'.join(re.escape(name) for name in sorted(SUBGENRES, key=len, reverse=True)), re.I)

REVIEW_HINT = re.compile(r'\breview\b|\brecensione\b|\brated?\b|\bscore\b', re.I)
LIST_HINT = re.compile(
    r'track of the week|song of the week|best new|album of the week|essential tracks|'
    r'tracks of the week|heavy song of the week',
    re.I,
)
PREMIERE_HINT = re.compile(
    r'\bpremiere\b|\blisten\b|\bwatch\b|\bfull(?: album)? stream\b|\bhear\b|'
    r'il nuovo singolo|video premiere|track premiere',
    re.I,
)
POSITIVE_QUAL = re.compile(
    r'\b(essential|masterpiece|aoty|album of the year|highly recommended|must hear|'
    r'best new music|outstanding|excellent|great\.|instant classic)\b',
    re.I,
)
NEGATIVE_QUAL = re.compile(
    r'\b(disappointment|disappointing|skip|avoid|generic|filler|waste|mediocre|'
    r'failure|disastrous|forgettable|dud)\b',
    re.I,
)
HIGHLIGHT_HINT = re.compile(
    r'(?:standout|highlight|best track|title track|opener|closes? with|shines|'
    r'finest (?:moment|track)|peak|centerpiece).{0,40}?["“‘](?P<title>[^"”’]{2,60})["”’]',
    re.I,
)
HIGHLIGHT_QUOTED = re.compile(
    r'["“‘](?P<title>[^"”’]{2,60})["”’]\s+(?:shines|stands out|is the highlight|hits hard)',
    re.I,
)
AMG_RATING = re.compile(r'Rating\s*:?\s*([0-5](?:\.\d+)?)', re.I)
PITCHFORK_RATING = re.compile(r'\b([0-9](?:\.\d+)?)\s*(?:/|\s+out of\s+)10\b', re.I)
DECIBEL_RATING = re.compile(r'\b([0-9]{1,2})\s*/\s*10\b')
PERCENT_RATING = re.compile(r'\b([6-9][0-9]|100)\s*%')
BNM = re.compile(r'best new music', re.I)


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


def _html_to_text(value):
    with catch_warnings():
        filterwarnings('ignore', category=MarkupResemblesLocatorWarning)
        return BeautifulSoup(str(value or ''), 'html.parser').get_text(' ')


def entry_body(entry):
    chunks = []
    for block in entry.get('content') or []:
        if isinstance(block, dict) and block.get('value'):
            chunks.append(block['value'])
    if entry.get('summary'):
        chunks.append(entry.get('summary'))
    if entry.get('description'):
        chunks.append(entry.get('description'))
    raw = '\n'.join(chunks)
    return _html_to_text(raw).strip()


def clean_text(entry):
    title = entry.get('title', '') or ''
    summary = _html_to_text(entry.get('summary', ''))
    body = entry_body(entry)
    text = f'{title} {summary} {body}'.strip()
    return title, text, body


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


def classify_item_type(title, text, source, role):
    blob = f'{title} {text}'
    if LIST_HINT.search(blob):
        return 'list'
    if REVIEW_HINT.search(title or '') or (role == 'quality' and REVIEW_HINT.search(blob)):
        return 'review'
    if source == 'Angry Metal Guy' and re.search(r'\breview\b', title or '', re.I):
        return 'review'
    if PREMIERE_HINT.search(title or '') or PREMIERE_HINT.search((text or '')[:180]):
        return 'premiere'
    if role == 'quality' and ALBUMISH.search(title or ''):
        return 'review'
    return 'news'


def articles():
    output = []
    consulted = []
    for source, config in SOURCES.items():
        fetched = False
        failures = []
        role = config.get('role') or 'discovery'
        for url in config['feeds']:
            feed, error = fetch_feed(url)
            if error:
                failures.append(error)
                continue
            fetched = True
            for entry in feed.entries:
                date = published(entry)
                title, text, body = clean_text(entry)
                item_type = classify_item_type(title, text, source, role)
                metal_ok = (
                    source in METAL_NATIVE_SOURCES
                    or role == 'quality'
                    or item_type in {'review', 'list'}
                    or METAL.search(text)
                )
                if not metal_ok:
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
                    'body': body,
                    'published': date.isoformat(),
                    'region': config['region'],
                    'kind': config['kind'],
                    'authority': config['authority'],
                    'role': role,
                    'item_type': item_type,
                    'body_read': bool(body and len(body) > 120),
                })
        if fetched:
            consulted.append(source)
        elif failures:
            print(f'RSS source skipped: {source} ({failures[0]})')
    unique = {}
    for item in output:
        unique[item['link'] or item['title']] = item
    return list(unique.values()), consulted


def fetch_html_body(url):
    try:
        response = requests.get(url, headers=HTML_HEADERS, timeout=20)
    except Exception:
        return ''
    if not response.ok:
        return ''
    with catch_warnings():
        filterwarnings('ignore', category=MarkupResemblesLocatorWarning)
        soup = BeautifulSoup(response.text, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'aside']):
        tag.decompose()
    return soup.get_text(' ', strip=True)


def parse_rating(source, text):
    blob = text or ''
    if source == 'Angry Metal Guy':
        match = AMG_RATING.search(blob)
        if match:
            return float(match.group(1)) / 5.0, f'AMG {match.group(1)}/5.0'
    if source == 'Pitchfork':
        match = PITCHFORK_RATING.search(blob)
        if match:
            value = float(match.group(1)) / 10.0
            label = f'Pitchfork {match.group(1)}/10'
            if BNM.search(blob):
                value = min(1.0, value + 0.05)
                label += ' BNM'
            return value, label
    match = DECIBEL_RATING.search(blob)
    if match:
        return float(match.group(1)) / 10.0, f'{match.group(1)}/10'
    match = PERCENT_RATING.search(blob)
    if match:
        return float(match.group(1)) / 100.0, f'{match.group(1)}%'
    if POSITIVE_QUAL.search(blob):
        return 0.82, 'qualitative positive'
    if NEGATIVE_QUAL.search(blob):
        return 0.25, 'qualitative negative'
    return None, None


def parse_sentiment(rating, text):
    if rating is not None and rating < 0.45:
        return 'negative'
    if NEGATIVE_QUAL.search(text or '') and (rating is None or rating < 0.6):
        return 'negative'
    if rating is not None and rating >= 0.7:
        return 'positive'
    if POSITIVE_QUAL.search(text or ''):
        return 'positive'
    if rating is not None and rating < 0.65:
        return 'mixed'
    return 'mixed' if rating is not None else 'positive'


def parse_highlight(text, album=''):
    blob = text or ''
    for pattern in (HIGHLIGHT_HINT, HIGHLIGHT_QUOTED):
        match = pattern.search(blob)
        if match:
            title = match.group('title').strip(' .,;:')
            if album and title.lower() == album.lower():
                continue
            if 2 <= len(title) <= 70:
                return title
    if album:
        # Title-track heuristic: album name often matches a track.
        return album
    return ''


def enrich_reviews(items, max_bodies=MAX_REVIEW_BODIES):
    rejected_negatives = []
    fetches = 0
    for item in items:
        if item.get('item_type') not in {'review', 'list'}:
            item.setdefault('rating', None)
            item.setdefault('rating_label', None)
            item.setdefault('sentiment', 'positive')
            item.setdefault('highlight', '')
            continue
        body = item.get('body') or ''
        if len(body) < 160 and item.get('link') and fetches < max_bodies:
            fetched = fetch_html_body(item['link'])
            fetches += 1
            if fetched:
                body = fetched
                item['body'] = body
                item['text'] = f"{item.get('title', '')} {body}"
                item['body_read'] = True
        rating, label = parse_rating(item['source'], body or item.get('text', ''))
        sentiment = parse_sentiment(rating, body or item.get('text', ''))
        album_guess = ''
        extracted = extract_review_album(item.get('title', ''))
        if extracted:
            album_guess = extracted[1]
        highlight = parse_highlight(body or item.get('text', ''), album_guess)
        item['rating'] = rating
        item['rating_label'] = label
        item['sentiment'] = sentiment
        item['highlight'] = highlight
        if sentiment == 'negative':
            rejected_negatives.append({
                'title': item.get('title', ''),
                'source': item.get('source', ''),
                'rating_label': label or 'negative',
            })
    return items, rejected_negatives


def detect_subgenre(text):
    match = SUBGENRE_RE.search(text or '')
    return match.group(0).lower() if match else 'metal'


def is_album(text, title):
    return bool(ALBUMISH.search(text) or ALBUMISH.search(title))


def source_bucket(sources, regions, kinds, roles=None):
    roles = roles or set()
    if regions <= {'IT', 'EU'} and 'editorial' not in kinds:
        return 'european'
    if roles <= {'regional'} or (len(sources) == 1 and 'specialist' in kinds):
        return 'underground' if 'regional' not in roles else 'european'
    if kinds <= {'specialist', 'wire'} or (len(sources) == 1 and 'specialist' in kinds):
        return 'underground'
    if len(sources) >= 3 or ('Pitchfork' in sources and len(sources) >= 2):
        return 'established'
    quality_hit = bool(sources & QUALITY_SOURCES) or bool(roles & {'quality'})
    if quality_hit:
        return 'established' if len(sources) >= 2 else 'emerging'
    return 'emerging'


def extract_review_album(title):
    for pattern in (REVIEW_HEADLINE_FLIP, REVIEW_HEADLINE):
        match = pattern.match((title or '').strip())
        if not match:
            continue
        artist = match.group('artist')
        album = match.group('album')
        album = re.sub(r'\s+Review\s*$', '', album, flags=re.I).strip()
        album = re.sub(r'\s*\[.*?\]\s*$', '', album).strip()
        parsed = valid_pair(artist, album)
        if parsed:
            return parsed
    return None


def extract_pairs(text):
    pairs = []
    for pattern in (PREMIERE_PAIR, MI_QUOTED_PAIR, HEAR_QUOTED_PAIR, ANNOUNCE_QUOTED_PAIR, IT_SINGLE_PAIR, PAIR):
        for match in pattern.finditer(text or ''):
            parsed = valid_pair(match.group('artist'), match.group('title'))
            if parsed:
                pairs.append(parsed)
    unique = []
    seen = set()
    for artist, title in pairs:
        key = (artist.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append((artist, title))
    return unique


def valid_pair(artist, title):
    artist = re.sub(r'\s+', ' ', artist).strip(' .,:"\'’')
    title = re.sub(r'\s+', ' ', title).strip(' .,:"\'’')
    artist = re.sub(r'^(review|interview|news|album)\s*:\s*', '', artist, flags=re.I)
    artist = re.sub(r'[\'’]s$', '', artist).strip(' .,:"\'’')
    title = re.sub(r'\s+Review\s*$', '', title, flags=re.I).strip()
    title = re.sub(r'\s*[–—-]\s*$', '', title).strip()
    if re.search(r'\breviews?\b|\bbehind\b|\bdigging deep\b|\btheir new\b', artist, re.I):
        return None
    if len(title.split()) > 8 and not re.search(r'[A-Za-z]', title[:20]):
        return None
    if len(title) > 40 and artist.lower() in title.lower()[len(artist):]:
        return None
    if len(artist) < 2 or len(title) < 2 or len(artist) > 50 or len(title) > 70:
        return None
    if len(artist.split()) > 6:
        return None
    if NOISE.search(artist) or NOISE.search(title):
        return None
    if artist.lower() in {'metal', 'metal scene', 'news', 'review', 'album', 'premiere', 'first look'}:
        return None
    if re.search(r'\*{2,}|f\*\*+', artist, re.I):
        return None
    if re.search(
        r'^(watch|listen|read|enter|full|video|news|premiere|stream|welcome|hear|first look|'
        r'photo credit|kenbassador)\b',
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
        r'rivelati i dettagli|neues video|szene gefeiert|horror nights|'
        r'barbie|mattel|use your illusion|photo credit|how to get tickets)\b|'
        r'\b202[7-9]\b|\b199[0-9]\b',
        title,
        re.I,
    ):
        return None
    if len(artist.split()) >= 3 and len(title.split()) <= 2 and title.isupper():
        return None
    if not re.search(r'[A-Za-zÀ-ÿ]', artist) or not re.search(r'[A-Za-zÀ-ÿ]', title):
        return None
    return artist, title


def _strong_review(rating, source):
    if rating is None:
        return False
    if source == 'Pitchfork':
        return rating >= 0.74
    if source == 'Angry Metal Guy':
        return rating >= 0.70  # 3.5/5
    return rating >= 0.70


def score_candidate(item):
    sources = item['sources']
    roles = item.get('roles') or set()
    item_types = item.get('item_types') or set()
    rating = item.get('best_rating')
    review_sources = item.get('review_sources') or set()
    premiere_only = item_types <= {'premiere', 'news'} and not review_sources
    authority = max(item['authorities'] or [5])

    rating_pts = int(round((rating or 0) * 40)) if rating is not None else 0
    quality_auth = 0
    for source in sources:
        if source in QUALITY_SOURCES or source in {'Angry Metal Guy', 'Pitchfork', 'Decibel', 'The Quietus'}:
            quality_auth = max(quality_auth, 12 + (2 if source in {'Angry Metal Guy', 'Pitchfork', 'Decibel'} else 0))
        else:
            quality_auth = max(quality_auth, min(12, 6 + authority // 2))
    quality_auth = min(20, quality_auth)

    review_consensus = len(review_sources)
    # Two premieres are NOT critical consensus.
    consensus_pts = 0
    if review_consensus >= 3:
        consensus_pts = 20
    elif review_consensus == 2:
        consensus_pts = 15
    elif review_consensus == 1 and rating is not None:
        consensus_pts = 8

    recency = 10 if item.get('recent') else 5
    premiere_bonus = 0
    if 'premiere' in item_types and not (rating and rating >= 0.7):
        premiere_bonus = 12 if len(sources) >= 2 else 10
    if premiere_only:
        # Give discovery fills a floor inside the soft cap.
        premiere_bonus = max(premiere_bonus, 15)
    subgenre_pts = 5 if item.get('subgenre') != 'metal' else 3
    total = rating_pts + quality_auth + consensus_pts + recency + premiere_bonus + subgenre_pts
    total = min(100, total)

    if item.get('sentiment') == 'negative':
        total = 0
    elif premiere_only or (roles <= {'discovery'} and not review_sources):
        total = min(total, PREMIERE_SCORE_CAP)
    elif rating is not None and _strong_review(rating, next(iter(review_sources), '')):
        total = max(total, 70)
        total = min(100, total)
    if 'Pitchfork' in sources and len(sources) == 1 and 'news' in item_types and not review_sources:
        total = min(total, 50)

    return total, {
        'rating': rating_pts,
        'quality_authority': quality_auth,
        'review_consensus': consensus_pts,
        'recency': recency,
        'premiere_bonus': premiere_bonus,
        'subgenre': subgenre_pts,
    }


def candidates(items):
    grouped = {}
    for article in items:
        if article.get('sentiment') == 'negative':
            continue
        item_type = article.get('item_type') or 'news'
        role = article.get('role') or 'discovery'
        found = []
        if item_type in {'review', 'list'}:
            review_album = extract_review_album(article.get('title', ''))
            highlight = (article.get('highlight') or '').strip()
            if review_album:
                artist, album = review_album
                track_title = highlight if highlight and highlight.lower() != album.lower() else album
                found.append((artist, track_title, album, True))
            elif highlight:
                # Highlight without clean album parse — try artist from headline pair.
                for artist, title in extract_pairs(article.get('title', '')):
                    found.append((artist, highlight, title, True))
                    break
        for artist, title in extract_pairs(article.get('title', '')):
            found.append((artist, title, title if is_album(article['text'], article['title']) else '', False))
        for artist, title in extract_pairs((article.get('text') or '')[:500]):
            found.append((artist, title, '', False))

        for artist, title, album, from_review in found:
            key = (artist.lower(), title.lower())
            if key not in grouped:
                grouped[key] = {
                    'artist': artist,
                    'title': title,
                    'album': album or (title if from_review else ''),
                    'kind': 'album' if (from_review or is_album(article['text'], article['title'])) else 'track',
                    'sources': set(),
                    'source_urls': [],
                    'kinds': set(),
                    'regions': set(),
                    'roles': set(),
                    'authorities': [],
                    'item_types': set(),
                    'review_sources': set(),
                    'ratings': [],
                    'rating_labels': [],
                    'highlights': set(),
                    'body_read': False,
                    'sentiment': 'positive',
                    'editorial': False,
                    'recent': False,
                    'subgenre': detect_subgenre(article['text']),
                    'summary': article['title'],
                }
            entry = grouped[key]
            entry['sources'].add(article['source'])
            entry['roles'].add(role)
            entry['item_types'].add(item_type)
            if article['link']:
                entry['source_urls'].append(article['link'])
            entry['kinds'].add(article['kind'])
            entry['regions'].add(article['region'])
            entry['authorities'].append(article['authority'])
            entry['editorial'] = entry['editorial'] or bool(EDITORIAL.search(article['text']))
            entry['body_read'] = entry['body_read'] or bool(article.get('body_read'))
            published_at = date_parser.parse(article['published'])
            entry['recent'] = entry['recent'] or published_at >= LOOKBACK
            if item_type in {'review', 'list'}:
                entry['review_sources'].add(article['source'])
                if article.get('rating') is not None:
                    entry['ratings'].append(article['rating'])
                if article.get('rating_label'):
                    entry['rating_labels'].append(article['rating_label'])
                if article.get('highlight'):
                    entry['highlights'].add(article['highlight'])
            if from_review and album:
                entry['album'] = entry['album'] or album
                entry['kind'] = 'album'
            if is_album(article['text'], article['title']):
                entry['kind'] = 'album'
                entry['album'] = entry['album'] or title

    ranked = []
    for item in grouped.values():
        item['best_rating'] = max(item['ratings']) if item['ratings'] else None
        item['rating_label'] = item['rating_labels'][0] if item['rating_labels'] else None
        if item['highlights'] and not item.get('title'):
            item['title'] = next(iter(item['highlights']))
        # Prefer highlight as playable title when album-keyed.
        if item['highlights']:
            highlight = next(iter(item['highlights']))
            if highlight.lower() != (item.get('album') or '').lower():
                item['title'] = highlight
                item['kind'] = 'track'
        item['bucket'] = source_bucket(item['sources'], item['regions'], item['kinds'], item['roles'])
        item['score'], item['source_scores'] = score_candidate(item)
        item['source_publications'] = sorted(item['sources'])
        item['primary_type'] = (
            'review' if item['review_sources']
            else 'list' if 'list' in item['item_types']
            else 'premiere' if 'premiere' in item['item_types']
            else 'news'
        )
        if item['score'] <= 0:
            continue
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
    last_error = None
    for attempt in range(3):
        response = requests.get(
            f'{SPOTIFY_API}/search',
            headers=_auth_headers(access),
            params={'q': query, 'type': kind, 'market': 'IT', 'limit': SEARCH_LIMIT},
            timeout=30,
        )
        if response.ok:
            return response.json()
        if response.status_code in {502, 503, 504} and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            last_error = response
            continue
        raise _spotify_error('search', response)
    raise _spotify_error('search', last_error)


def search(access, artist, title):
    payload = _search(access, f'track:{title} artist:{artist}', 'track')
    tracks = payload.get('tracks', {}).get('items', [])
    for track in tracks:
        names = ' '.join(a['name'] for a in track.get('artists', []))
        if artist.lower() in names.lower() and title.lower() in track['name'].lower():
            return track
    return tracks[0] if tracks else None


def album_named_track(access, artist, album, track_name):
    payload = _search(access, f'album:{album} artist:{artist}', 'album')
    albums = payload.get('albums', {}).get('items', []) or []
    album_obj = None
    for item in albums:
        names = ' '.join(a['name'] for a in item.get('artists', []))
        if artist.lower() in names.lower() and album.lower() in (item.get('name') or '').lower():
            album_obj = item
            break
    album_obj = album_obj or (albums[0] if albums else None)
    if not album_obj or not album_obj.get('id'):
        return None
    response = requests.get(
        f'{SPOTIFY_API}/albums/{album_obj["id"]}/tracks',
        headers=_auth_headers(access),
        params={'limit': 50, 'market': 'IT'},
        timeout=30,
    )
    if not response.ok:
        return None
    wanted = (track_name or '').lower()
    for item in response.json().get('items') or []:
        if not item or not item.get('id'):
            continue
        name = (item.get('name') or '').lower()
        if wanted and wanted in name:
            item = dict(item)
            item['album'] = {'name': album_obj.get('name', album), 'id': album_obj.get('id')}
            if not item.get('artists'):
                item['artists'] = album_obj.get('artists') or [{'name': artist}]
            return item
    return None


def resolve_candidate(access, candidate):
    artist = candidate['artist']
    title = candidate['title']
    album = candidate.get('album') or ''
    highlights = candidate.get('highlights') or set()
    highlight = title if title else (next(iter(highlights)) if highlights else '')

    # Review albums: highlight-first, never spray first N tracks.
    if candidate.get('primary_type') == 'review' or candidate.get('review_sources'):
        if highlight:
            track = search(access, artist, highlight)
            if track:
                return [track]
        if album and highlight and highlight.lower() != album.lower():
            track = album_named_track(access, artist, album, highlight)
            if track:
                return [track]
        if album:
            track = search(access, artist, album)
            if track:
                return [track]
        return []

    track = search(access, artist, title)
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
    """Keep Spotify name/description pinned to the fixed identity."""
    url = f'{SPOTIFY_API}/playlists/{playlist}'
    response = requests.put(
        url,
        headers={**_auth_headers(access), 'Content-Type': 'application/json'},
        json={'name': PLAYLIST_NAME, 'description': PLAYLIST_DESCRIPTION},
        timeout=30,
    )
    _log_spotify_call('playlist profile', 'PUT', url, response)
    if not response.ok:
        raise _spotify_error('playlist profile', response)


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
    rating = candidate.get('rating_label')
    primary = candidate.get('primary_type', 'news')
    if candidate.get('review_sources') and rating:
        return f'{rating} review highlight via {pubs}'
    if candidate.get('review_sources'):
        return f'review-backed pick via {pubs}'
    if primary == 'premiere':
        return f'premiere fill via {pubs}'
    return f'Score {candidate.get("score", 0)}; {primary} via {pubs or "press coverage"}'


def max_for_source(source, candidate):
    roles = candidate.get('roles') or set()
    if source in QUALITY_SOURCES or 'quality' in roles:
        return MAX_PER_SOURCE_QUALITY
    if source in DISCOVERY_SOURCES or 'discovery' in roles:
        return MAX_PER_SOURCE_DISCOVERY
    if source in REGIONAL_SOURCES or 'regional' in roles:
        return MAX_PER_SOURCE_REGIONAL
    return MAX_PER_SOURCE_DISCOVERY


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
        # Allow one album representation unless top-tier dual highlights handled by per_album.
        pass
    subgenre = candidate.get('subgenre') or 'metal'
    if subgenre_counts[subgenre] >= 4 and subgenre != 'metal':
        return False
    return True


def _fill_bucket_name(candidate):
    primary = candidate.get('primary_type')
    if primary == 'review' or candidate.get('review_sources'):
        return 'review'
    if primary == 'premiere':
        return 'premiere'
    if candidate.get('bucket') == 'european' or (candidate.get('roles') or set()) <= {'regional'}:
        return 'european'
    if candidate.get('bucket') == 'underground' and candidate.get('review_sources'):
        return 'underground'
    if primary == 'list':
        return 'review'
    return 'premiere'


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
    fill_counts = Counter()
    pitchfork_only = 0
    single_discovery = 0
    duplicates_rejected = 0

    def _lookup(candidate):
        if lookup.__code__.co_argcount == 1:
            return lookup(candidate)
        return lookup(candidate['artist'], candidate['title'])

    def _is_single_discovery(candidate):
        sources = candidate.get('sources') or set()
        roles = candidate.get('roles') or set()
        if candidate.get('review_sources'):
            return False
        return len(sources) == 1 and (
            sources <= DISCOVERY_SOURCES
            or roles <= {'discovery'}
            or (candidate.get('kinds') or set()) <= {'specialist', 'wire'}
        )

    def _album_limit(candidate):
        if candidate.get('score', 0) >= 85 and len(candidate.get('highlights') or []) >= 2:
            return MAX_PER_ALBUM_TOP
        return MAX_PER_ALBUM

    def consider(candidate):
        nonlocal duplicates_rejected, pitchfork_only, single_discovery
        if len(chosen) >= MAX_TRACKS:
            return False
        if candidate.get('source_scores') and candidate.get('score', 0) < MIN_SCORE:
            return False
        sources = candidate.get('sources') or set()
        for source in sources:
            if per_source[source] >= max_for_source(source, candidate):
                return False
        if sources == {'Pitchfork'} and pitchfork_only >= PITCHFORK_ONLY_CAP:
            return False
        if _is_single_discovery(candidate) and single_discovery >= SINGLE_DISCOVERY_CAP:
            return False
        # Weak Loudwire-only crossover without quality review.
        if sources == {'Loudwire'} and not candidate.get('review_sources'):
            return False
        resolved = _lookup(candidate)
        if resolved is None:
            return False
        tracks = resolved if isinstance(resolved, list) else [resolved]
        # Never take more than album limit; reviews resolve to 0-1 tracks anyway.
        tracks = tracks[:_album_limit(candidate)]
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
            if album_name and per_album[album_name] >= _album_limit(candidate):
                continue
            if not passes_quality_gate(
                candidate,
                track,
                {'ids': known, 'pairs': catalog['pairs'], 'albums': catalog['albums']},
                subgenre_counts,
            ):
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
            fill_counts[_fill_bucket_name(candidate)] += 1
            for source in sources:
                per_source[source] += 1
            if sources == {'Pitchfork'}:
                pitchfork_only += 1
            if _is_single_discovery(candidate):
                single_discovery += 1
            added = True
            break
        return added

    remaining = list(ranked)
    # Reviews outrank bucket quotas: fill by review/premiere/regional targets first.
    for fill_name, quota in FILL_ORDER:
        still = []
        for candidate in remaining:
            if fill_counts[fill_name] >= quota:
                still.append(candidate)
                continue
            if _fill_bucket_name(candidate) != fill_name:
                still.append(candidate)
                continue
            if not consider(candidate):
                still.append(candidate)
        remaining = still

    remaining.sort(
        key=lambda c: (
            3 if c.get('review_sources') else 0,
            2 if c.get('primary_type') == 'premiere' and len(c.get('sources') or []) >= 2 else 0,
            c.get('score', 0),
        ),
        reverse=True,
    )
    for candidate in remaining:
        if len(chosen) >= MAX_TRACKS:
            break
        consider(candidate)
    catalog['ids'] = known
    return chosen, uris, known, {
        'duplicates_rejected': duplicates_rejected,
        'fill_counts': dict(fill_counts),
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
            f"   Type: {candidate.get('primary_type', 'news')}",
            f"   Subgenre: {candidate.get('subgenre', 'metal')}",
            f"   Score: {candidate.get('score', 0)}",
            f"   Rating: {candidate.get('rating_label') or 'n/a'}",
            f"   Body read: {str(bool(candidate.get('body_read'))).lower()}",
            f"   Why: {why_selected(candidate)}",
            f"   Sources: {pubs or 'n/a'}",
            '',
        ])
    rejected = stats.get('rejected_negatives') or []
    if rejected:
        lines.extend(['## REJECTED NEGATIVE REVIEWS', ''])
        for item in rejected[:12]:
            lines.append(
                f"- {item.get('title') or 'unknown'} "
                f"({item.get('source') or 'n/a'}; {item.get('rating_label') or 'negative'})"
            )
        lines.append('')
    REPORT_PATH.write_text('\n'.join(lines).rstrip() + '\n')


def main():
    history = load()
    items, consulted = articles()
    items, rejected_negatives = enrich_reviews(items)
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
        'rejected_negatives': rejected_negatives,
    }
    write_report(chosen, uris, stats)
    print(f'Added tracks: {len(uris)}')
    print(f'Sources consulted: {len(consulted)}')
    print(f'Duplicates rejected: {stats["duplicates_rejected"]}')
    print(f'Rejected negatives: {len(rejected_negatives)}')


if __name__ == '__main__':
    main()
