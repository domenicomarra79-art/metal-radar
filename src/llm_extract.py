"""Optional Claude-backed extraction of releases, ratings and standout tracks.

Regex parsing stays the default. When ANTHROPIC_API_KEY is set, review and
premiere articles are also read by Claude, which returns structured JSON that
replaces the fragile headline/body heuristics where it has an answer.
"""
import json
import os

try:
    import anthropic
except ImportError:  # pragma: no cover - dependency is optional at runtime
    anthropic = None

MODEL = os.environ.get('METAL_RADAR_LLM_MODEL', 'claude-opus-5-5')
MAX_CALLS = int(os.environ.get('METAL_RADAR_LLM_MAX_CALLS', '60'))
# Review pages scraped as text carry nav, comments and related-post chrome after
# the review itself; the review body sits well inside this window.
MAX_ARTICLE_CHARS = 24000
SENTIMENTS = ('positive', 'mixed', 'negative')
ARTICLE_TYPES = ('review', 'premiere', 'list', 'news', 'other')

SYSTEM_PROMPT = """You read heavy-music press articles for a weekly metal playlist.

For the article you are given, report:
- article_type: "review" for a critique of one or more releases, "premiere" for a new song/video/stream \
debut, "list" for a curated roundup of recommended releases, "news" for announcements, "other" otherwise.
- is_metal: true when the music covered is metal or a close heavy neighbour (hardcore, sludge, doom, \
grind, post-metal, heavy noise, dungeon synth covered by metal press). False for pop, britpop, indie rock, \
hip hop, wellness/ambient filler and similar.
- releases: every release the article recommends or debuts, at most 10. For each one:
  - artist and album exactly as written in the article (album is "" when only a song is named).
  - tracks: song titles the writer singles out as highlights, or the premiered song, best first. Use \
the exact song title, without quotes. Empty when no song is named.
  - rating: the writer's score normalised to 0-1 (4.0/5 -> 0.8, 7.5/10 -> 0.75, 85% -> 0.85). Use -1 \
when the article gives no explicit score. Never invent a score from tone.
  - rating_label: the score as printed, e.g. "4.0/5", "7.5/10", or "" when there is none.
  - sentiment: the writer's verdict on that release: positive, mixed or negative.

Only report releases the article actually covers; skip passing mentions, influences and tour news."""

RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'article_type': {'type': 'string', 'enum': list(ARTICLE_TYPES)},
        'is_metal': {'type': 'boolean'},
        'releases': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'artist': {'type': 'string'},
                    'album': {'type': 'string'},
                    'tracks': {'type': 'array', 'items': {'type': 'string'}},
                    'rating': {'type': 'number'},
                    'rating_label': {'type': 'string'},
                    'sentiment': {'type': 'string', 'enum': list(SENTIMENTS)},
                },
                'required': ['artist', 'album', 'tracks', 'rating', 'rating_label', 'sentiment'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['article_type', 'is_metal', 'releases'],
    'additionalProperties': False,
}


def enabled():
    return anthropic is not None and bool(os.environ.get('ANTHROPIC_API_KEY'))


def _clean_release(raw):
    artist = str(raw.get('artist') or '').strip()
    if not artist:
        return None
    try:
        rating = float(raw.get('rating'))
    except (TypeError, ValueError):
        rating = -1.0
    tracks = [str(track).strip().strip('"“”‘’') for track in raw.get('tracks') or []]
    sentiment = raw.get('sentiment') if raw.get('sentiment') in SENTIMENTS else 'mixed'
    return {
        'artist': artist,
        'album': str(raw.get('album') or '').strip(),
        'tracks': [track for track in tracks if track][:3],
        'rating': rating if 0.0 <= rating <= 1.0 else None,
        'rating_label': str(raw.get('rating_label') or '').strip(),
        'sentiment': sentiment,
    }


def parse_response(payload):
    """Validate the model's JSON payload into the shape the pipeline uses."""
    if not isinstance(payload, dict):
        return None
    releases = []
    for raw in payload.get('releases') or []:
        if isinstance(raw, dict):
            cleaned = _clean_release(raw)
            if cleaned:
                releases.append(cleaned)
    article_type = payload.get('article_type')
    return {
        'article_type': article_type if article_type in ARTICLE_TYPES else 'other',
        'is_metal': bool(payload.get('is_metal')),
        'releases': releases[:10],
    }


class Extractor:
    def __init__(self, client=None, max_calls=MAX_CALLS):
        self.client = client or anthropic.Anthropic(max_retries=3, timeout=180.0)
        self.max_calls = max_calls
        self.calls = 0
        self.failures = 0

    def available(self):
        return self.calls < self.max_calls

    def extract(self, source, title, body):
        if not self.available():
            return None
        self.calls += 1
        article = (body or '')[:MAX_ARTICLE_CHARS]
        try:
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=8000,
                betas=['server-side-fallback-2026-07-01'],
                fallbacks='default',
                system=SYSTEM_PROMPT,
                output_config={
                    'effort': 'low',
                    'format': {'type': 'json_schema', 'schema': RESPONSE_SCHEMA},
                },
                messages=[{
                    'role': 'user',
                    'content': f'Publication: {source}\nHeadline: {title}\n\nArticle:\n{article}',
                }],
            )
        except anthropic.RateLimitError as exc:
            self.failures += 1
            print(f'LLM extraction rate limited: {exc.status_code}')
            return None
        except anthropic.APIStatusError as exc:
            self.failures += 1
            print(f'LLM extraction failed: HTTP {exc.status_code}')
            return None
        except anthropic.APIConnectionError:
            self.failures += 1
            print('LLM extraction failed: connection error')
            return None
        if response.stop_reason in {'refusal', 'max_tokens'}:
            self.failures += 1
            return None
        text = next((block.text for block in response.content if block.type == 'text'), '')
        try:
            return parse_response(json.loads(text))
        except ValueError:
            self.failures += 1
            return None
