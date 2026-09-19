SOURCES = {
    'Pitchfork': {
        'tier': 1,
        'authority': 9,
        'region': 'US',
        'kind': 'editorial',
        'role': 'quality',
        'feeds': [
            # Prefer album reviews over homepage news.
            'https://pitchfork.com/feed/feed-album-reviews/rss',
            'https://pitchfork.com/feed/feed-news/rss',
        ],
    },
    'Angry Metal Guy': {
        'tier': 1,
        'authority': 10,
        'region': 'US',
        'kind': 'editorial',
        'role': 'quality',
        'feeds': [
            'https://www.angrymetalguy.com/category/reviews/feed/',
            'https://www.angrymetalguy.com/feed/',
        ],
    },
    'Decibel': {
        'tier': 1,
        'authority': 10,
        'region': 'US',
        'kind': 'editorial',
        'role': 'quality',
        'feeds': ['https://www.decibelmagazine.com/feed/'],
    },
    'Revolver': {
        'tier': 1,
        'authority': 8,
        'region': 'US',
        'kind': 'editorial',
        'role': 'discovery',
        'feeds': ['https://www.revolvermag.com/feed/'],
    },
    'Metal Injection': {
        'tier': 1,
        'authority': 7,
        'region': 'US',
        'kind': 'editorial',
        'role': 'discovery',
        'feeds': [
            'https://metalinjection.net/feed/',
        ],
    },
    'Loudwire': {
        'tier': 1,
        'authority': 6,
        'region': 'US',
        'kind': 'editorial',
        'role': 'discovery',
        'feeds': ['https://loudwire.com/feed/'],
    },
    'Stereogum': {
        'tier': 1,
        'authority': 7,
        'region': 'US',
        'kind': 'editorial',
        'role': 'quality',
        'feeds': ['https://www.stereogum.com/feed/'],
    },
    'Consequence': {
        'tier': 1,
        'authority': 7,
        'region': 'US',
        'kind': 'editorial',
        'role': 'discovery',
        'feeds': ['https://consequence.net/feed/'],
    },
    'BrooklynVegan': {
        'tier': 1,
        'authority': 6,
        'region': 'US',
        'kind': 'editorial',
        'role': 'discovery',
        'feeds': ['https://www.brooklynvegan.com/feed/'],
    },
    'Kerrang': {
        'tier': 1,
        'authority': 8,
        'region': 'UK',
        'kind': 'editorial',
        'role': 'quality',
        'feeds': [
            'https://www.kerrang.com/feed',
            'https://www.kerrang.com/feed.rss',
        ],
    },
    'Louder': {
        'tier': 1,
        'authority': 8,
        'region': 'UK',
        'kind': 'editorial',
        'role': 'quality',
        'feeds': [
            # Prefer Metal Hammer / Louder metal coverage.
            'https://www.loudersound.com/metal-hammer/feed',
            'https://www.loudersound.com/rss',
        ],
    },
    'The Quietus': {
        'tier': 1,
        'authority': 9,
        'region': 'UK',
        'kind': 'editorial',
        'role': 'quality',
        'feeds': ['https://thequietus.com/feed/'],
    },
    'Metalitalia': {
        'tier': 1,
        'authority': 8,
        'region': 'IT',
        'kind': 'specialist',
        'role': 'regional',
        'feeds': ['https://metalitalia.com/feed/'],
    },
    'Metallus': {
        'tier': 1,
        'authority': 6,
        'region': 'IT',
        'kind': 'specialist',
        'role': 'regional',
        'feeds': ['https://metallus.it/feed'],
    },
}

SUBGENRES = (
    'heavy metal',
    'thrash metal',
    'death metal',
    'black metal',
    'doom metal',
    'sludge',
    'stoner metal',
    'progressive metal',
    'post-metal',
    'metalcore',
    'deathcore',
    'industrial metal',
    'nu metal',
    'alternative metal',
    'gothic metal',
    'speed metal',
    'symphonic metal',
    'avant-garde metal',
    'experimental metal',
)

QUALITY_SOURCES = {name for name, cfg in SOURCES.items() if cfg.get('role') == 'quality'}
DISCOVERY_SOURCES = {name for name, cfg in SOURCES.items() if cfg.get('role') == 'discovery'}
REGIONAL_SOURCES = {name for name, cfg in SOURCES.items() if cfg.get('role') == 'regional'}
METAL_NATIVE_SOURCES = set(SOURCES)  # all current outlets are metal-native or metal-focused
