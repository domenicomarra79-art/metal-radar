import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import llm_extract
import metal_radar
from metal_radar import (
    MIN_PREMIERE_SCORE,
    MIN_TRACKS,
    _spotify_match_ok,
    album_focus_track,
    candidates,
    classify_item_type,
    enrich_reviews,
    extract_pairs,
    extract_review_album,
    genre_verdict,
    name_similarity,
    normalize_name,
    parse_rating,
    resolve_candidate,
    search,
    select_tracks,
)


def sp_track(tid, artist, name, album='Album', album_type='album', duration_ms=240_000):
    return {
        'id': tid,
        'name': name,
        'uri': f'spotify:track:{tid}',
        'duration_ms': duration_ms,
        'artists': [{'name': artist, 'id': f'artist-{artist}'}],
        'album': {'name': album, 'album_type': album_type},
    }


def article(source, title, item_type='review', role='quality', text=None, **extra):
    base = {
        'source': source,
        'title': title,
        'link': f'https://example.com/{abs(hash(title))}',
        'text': text or f'{title} death metal',
        'body': text or '',
        'published': datetime.now(timezone.utc).isoformat(),
        'region': 'US',
        'kind': 'specialist',
        'authority': 8,
        'role': role,
        'item_type': item_type,
        'rating': None,
        'rating_label': None,
        'highlight': '',
        'sentiment': 'positive',
        'body_read': True,
    }
    base.update(extra)
    return base


class NameMatchingTests(unittest.TestCase):
    def test_normalize_folds_accents_punctuation_and_versions(self):
        self.assertEqual(normalize_name('Sólstafir'), 'solstafir')
        self.assertEqual(normalize_name('Høstsol'), 'hostsol')
        self.assertEqual(normalize_name('Ænigmatum'), 'aenigmatum')
        self.assertEqual(normalize_name('Don’t Fear'), normalize_name("Don't Fear"))
        self.assertEqual(normalize_name('Blood & Iron'), normalize_name('Blood and Iron'))
        self.assertEqual(normalize_name('Dead Star (feat. Someone)'), 'dead star')
        self.assertEqual(normalize_name('Dead Star - 2024 Remaster'), 'dead star')
        self.assertEqual(normalize_name('The Ocean'), normalize_name('Ocean'))

    def test_spotify_match_survives_accents_and_title_case(self):
        hit = sp_track('1', 'Sólstafir', 'Tales of Trees (feat. Guest)')
        self.assertTrue(_spotify_match_ok(hit, 'Solstafir', 'Tales Of Trees'))

    def test_spotify_match_rejects_wrong_song_and_wrong_artist(self):
        self.assertFalse(_spotify_match_ok(sp_track('1', 'Gatecreeper', 'Dead Star'), 'Gatecreeper', 'Dead Sea'))
        self.assertFalse(_spotify_match_ok(sp_track('1', 'Gate Keeper', 'Dead Star'), 'Gatecreeper', 'Dead Star'))

    def test_spotify_match_rejects_live_and_demo_versions(self):
        live = sp_track('1', 'Enslaved', 'Isa - Live at Roadburn', album='Live at Roadburn')
        self.assertFalse(_spotify_match_ok(live, 'Enslaved', 'Isa'))
        demo = sp_track('2', 'Enslaved', 'Isa (Demo)')
        self.assertFalse(_spotify_match_ok(demo, 'Enslaved', 'Isa'))
        self.assertTrue(_spotify_match_ok(demo, 'Enslaved', 'Isa (Demo)'))

    def test_name_similarity_scores_near_matches(self):
        self.assertEqual(name_similarity('JÖJJÖN', 'Jöjjön'), 1.0)
        self.assertLess(name_similarity('Mastodon', 'Megadeth'), 0.6)


class SearchTests(unittest.TestCase):
    def test_bare_query_fallback_when_field_filter_misses(self):
        hit = sp_track('ok', 'Veilburner', 'Golgothic Holocaust')
        with patch('metal_radar._search') as api:
            api.side_effect = [
                {'tracks': {'items': []}},
                {'tracks': {'items': [hit]}},
            ]
            self.assertEqual(search('token', 'Veilburner', 'Golgothic Holocaust'), hit)
        self.assertEqual(api.call_count, 2)
        self.assertIn('track:"Golgothic Holocaust"', api.call_args_list[0].args[1])

    def test_search_prefers_best_match_over_first_hit(self):
        live = sp_track('live', 'Enslaved', 'Isa (Live)')
        cover = sp_track('cover', 'Tribute Band', 'Isa')
        studio = sp_track('studio', 'Enslaved', 'Isa')
        with patch('metal_radar._search', return_value={'tracks': {'items': [live, cover, studio]}}):
            self.assertEqual(search('token', 'Enslaved', 'Isa')['id'], 'studio')

    def test_query_strips_quotes_and_colons(self):
        with patch('metal_radar._search', return_value={'tracks': {'items': []}}) as api:
            search('token', 'Band', 'Part I: "The Fall"')
        self.assertNotIn('""', api.call_args_list[0].args[1])
        self.assertEqual(api.call_args_list[0].args[1].count('"'), 4)


class AlbumFocusTests(unittest.TestCase):
    def _album(self):
        return {'id': 'alb', 'name': 'Black Halo', 'album_type': 'album', 'release_date': '2026-09-26'}

    def test_prefers_prerelease_single_and_skips_intro(self):
        tracks = [
            sp_track('t1', 'Iron Tomb', 'Intro', duration_ms=60_000),
            sp_track('t2', 'Iron Tomb', 'Opening Wound'),
            sp_track('t3', 'Iron Tomb', 'Obsidian Crown'),
        ]
        with patch('metal_radar._find_album', return_value=self._album()), patch(
            'metal_radar._album_tracks', return_value=tracks
        ), patch('metal_radar._prerelease_single_names', return_value={'obsidian crown'}):
            self.assertEqual(album_focus_track('token', 'Iron Tomb', 'Black Halo')['id'], 't3')

    def test_falls_back_to_title_track_then_first_solid_track(self):
        tracks = [
            sp_track('t1', 'Iron Tomb', 'Prelude', duration_ms=200_000),
            sp_track('t2', 'Iron Tomb', 'Short One', duration_ms=150_000),
            sp_track('t3', 'Iron Tomb', 'Long One', duration_ms=300_000),
            sp_track('t4', 'Iron Tomb', 'Black Halo', duration_ms=250_000),
        ]
        patches = (
            patch('metal_radar._find_album', return_value=self._album()),
            patch('metal_radar._album_tracks', return_value=tracks),
            patch('metal_radar._prerelease_single_names', return_value=set()),
        )
        with patches[0], patches[1], patches[2]:
            self.assertEqual(album_focus_track('token', 'Iron Tomb', 'Black Halo')['id'], 't4')
        with patches[0], patch('metal_radar._album_tracks', return_value=tracks[:3]), patches[2]:
            self.assertEqual(album_focus_track('token', 'Iron Tomb', 'Black Halo')['id'], 't3')

    def test_review_without_highlight_uses_album_focus(self):
        candidate = {
            'artist': 'Iron Tomb',
            'title': 'Black Halo',
            'album': 'Black Halo',
            'primary_type': 'review',
            'review_sources': {'Angry Metal Guy'},
            'sources': {'Angry Metal Guy'},
        }
        focus = sp_track('focus', 'Iron Tomb', 'Obsidian Crown')
        with patch('metal_radar.search', return_value=None) as plain_search, patch(
            'metal_radar.album_focus_track', return_value=focus
        ), patch('metal_radar.artist_genre_verdict', return_value='metal'):
            self.assertEqual(resolve_candidate('token', candidate), [focus])
        plain_search.assert_not_called()


class GenreGateTests(unittest.TestCase):
    def test_genre_verdicts(self):
        self.assertEqual(genre_verdict({'black metal': 3, 'norwegian': 2}, 3), 'metal')
        self.assertEqual(genre_verdict({'rock': 8, 'alternative rock': 12, 'british': 5}, 3), 'not_metal')
        self.assertEqual(genre_verdict({'british': 1}, 3), 'unknown')
        self.assertEqual(genre_verdict({}, 2), 'unknown')
        self.assertEqual(genre_verdict({'hardcore': 1, 'punk': 2}, 3), 'metal')
        self.assertEqual(genre_verdict({'alternative rock': 11, 'progressive rock': 10, 'metal': -2}, 3), 'adjacent')

    def test_adjacent_artist_needs_metal_press(self):
        hit = sp_track('1', 'A Perfect Circle', 'Song')
        with patch('metal_radar.search', return_value=hit), patch(
            'metal_radar.artist_genre_verdict', return_value='adjacent'
        ):
            self.assertEqual(resolve_candidate('token', {'artist': 'A Perfect Circle', 'title': 'Song', 'sources': {'Louder'}}), [])
            self.assertEqual(
                resolve_candidate('token', {'artist': 'A Perfect Circle', 'title': 'Song', 'sources': {'Louder', 'Metal Injection'}}),
                [hit],
            )

    def test_resolve_drops_non_metal_artist(self):
        candidate = {'artist': 'Oasis Tribute', 'title': 'Wonderwall', 'sources': {'Louder'}}
        hit = sp_track('1', 'Oasis Tribute', 'Wonderwall')
        with patch('metal_radar.search', return_value=hit), patch(
            'metal_radar.artist_genre_verdict', return_value='not_metal'
        ):
            self.assertEqual(resolve_candidate('token', candidate), [])
        self.assertTrue(candidate['genre_rejected'])

    def test_unknown_genre_passes_and_editorial_skips_gate(self):
        hit = sp_track('1', 'Tiny Band', 'Song')
        with patch('metal_radar.search', return_value=hit), patch(
            'metal_radar.artist_genre_verdict', return_value='unknown'
        ):
            self.assertEqual(resolve_candidate('token', {'artist': 'Tiny Band', 'title': 'Song'}), [hit])
        with patch('metal_radar.search', return_value=hit), patch(
            'metal_radar.artist_genre_verdict'
        ) as verdict:
            resolve_candidate('token', {'artist': 'Tiny Band', 'title': 'Song', 'sources': {'Editorial'}})
        verdict.assert_not_called()

    def test_musicbrainz_namesake_with_metal_tags_wins(self):
        metal_radar._GENRE_VERDICTS.clear()
        with patch('metal_radar._spotify_genres', return_value={}), patch(
            'metal_radar.musicbrainz_tag_sets',
            return_value=[{'pop': 5, 'dance': 4}, {'death metal': 2}],
        ):
            self.assertEqual(metal_radar.artist_genre_verdict('token', sp_track('1', 'Namesake', 'X')), 'metal')
        metal_radar._GENRE_VERDICTS.clear()


class RatingTests(unittest.TestCase):
    def test_labelled_and_tail_ratings(self):
        self.assertEqual(parse_rating('Metalitalia', 'Bel disco. Voto: 7,5'), (0.75, '7,5/10'))
        self.assertEqual(parse_rating('Heavy Blog Is Heavy', 'Score: 4.5/5'), (0.9, '4.5/5'))
        self.assertEqual(parse_rating('Metal Storm', 'x ' * 400 + 'Final verdict 8.5/10.'), (0.85, '8.5/10'))

    def test_ignores_stray_fractions_and_ambiguous_scales(self):
        body = 'Only 9/10 songs here are any good. ' + 'words ' * 400
        self.assertEqual(parse_rating('Decibel', body), (None, None))
        self.assertEqual(parse_rating('Decibel', 'x ' * 300 + '9/10 songs rule'), (None, None))
        self.assertEqual(parse_rating('Kerrang', 'Rating: 4.0'), (None, None))
        self.assertEqual(parse_rating('Kerrang', 'A 95% chance of rain'), (None, None))

    def test_qualitative_positive_is_below_strong_review_floor(self):
        rating, label = parse_rating('Louder', 'An outstanding record.')
        self.assertEqual(label, 'qualitative positive')
        self.assertLess(rating, 0.70)

    def test_amg_rating_is_calibrated_after_sentiment(self):
        items, rejected = enrich_reviews([
            article('Angry Metal Guy', 'Good Band – Good Album Review', body='x' * 200 + ' Rating: 3.0/5.0'),
            article('Angry Metal Guy', 'Bad Band – Bad Album Review', body='x' * 200 + ' Rating: 2.0/5.0'),
        ])
        self.assertAlmostEqual(items[0]['rating'], 0.66)
        self.assertEqual(items[0]['rating_label'], 'AMG 3.0/5.0')
        self.assertEqual(items[1]['sentiment'], 'negative')
        self.assertEqual(len(rejected), 1)


class FeedParsingTests(unittest.TestCase):
    def test_feed_tags_and_review_feeds_classify_items(self):
        self.assertEqual(classify_item_type('The Ocean - Solaris', '', 'Heavy Blog Is Heavy', 'quality', 'The Ocean | Reviews'), 'review')
        self.assertEqual(classify_item_type('Madder Mortem - Red In Tooth And Claw', '', 'Metal Storm', 'quality'), 'review')
        self.assertEqual(classify_item_type('Befouled Malediction Perform A Cursed “Ritual”', '', 'Invisible Oranges', 'quality', 'Debuts | Streams'), 'premiere')

    def test_new_outlet_headlines(self):
        self.assertEqual(extract_pairs('AN NCS PREMIERE:  NYRAK — “THE SATURNALIA PROPHECY”'), [('NYRAK', 'THE SATURNALIA PROPHECY')])
        self.assertEqual(extract_pairs('Track Premiere: Veilburner‘s “Golgothic Holocaust”'), [('Veilburner', 'Golgothic Holocaust')])
        self.assertEqual(extract_review_album('SRPNTS:  “SECOND SHAPE”'), ('SRPNTS', 'SECOND SHAPE'))


class PremiereThresholdTests(unittest.TestCase):
    def _premiere(self, n, score):
        return {
            'artist': f'Band {n}', 'title': f'Song {n}', 'score': score,
            'sources': {f'Outlet {n}'}, 'roles': {'regional'}, 'kinds': {'editorial'},
            'review_sources': set(), 'primary_type': 'premiere', 'subgenre': 'metal',
            'source_scores': {'premiere_bonus': 15}, 'highlights': set(),
        }

    def _review(self, n, score):
        item = self._premiere(f'r{n}', score)
        item.update({'review_sources': {f'Outlet r{n}'}, 'primary_type': 'review', 'roles': {'quality'}})
        return item

    def _run(self, ranked):
        def lookup(candidate):
            return sp_track(candidate['artist'], candidate['artist'], candidate['title'], album=candidate['title'])
        chosen, _uris, _known, _extra = select_tracks(ranked, set(), lookup)
        return [candidate['artist'] for candidate, _track in chosen]

    def test_weak_premieres_wait_when_reviews_fill_the_week(self):
        ranked = [self._review(n, 75) for n in range(MIN_TRACKS)] + [self._premiere('weak', MIN_PREMIERE_SCORE - 6)]
        self.assertNotIn('Band weak', self._run(ranked))

    def test_weak_premieres_top_up_a_thin_week_to_min_tracks_only(self):
        ranked = [self._review(n, 75) for n in range(3)] + [
            self._premiere(n, MIN_PREMIERE_SCORE - 6) for n in range(10)
        ]
        picked = self._run(ranked)
        self.assertEqual(len(picked), MIN_TRACKS)
        self.assertEqual(picked[:3], ['Band r0', 'Band r1', 'Band r2'])

    def test_lone_lukewarm_review_only_fills(self):
        weak = self._review('weak', 59)
        weak['best_rating'] = 0.56
        strong = [self._review(n, 75) for n in range(MIN_TRACKS)]
        self.assertNotIn('Band rweak', self._run(strong + [weak]))
        self.assertIn('Band rweak', self._run(strong[:2] + [weak]))

    def test_duplicate_album_and_highlight_entries_merge(self):
        items = [article(
            'Angry Metal Guy', 'Chelsea Wolfe – The Dark Review',
            rating=0.76, rating_label='AMG 3.5/5.0', highlight='Cold',
        )]
        ranked = candidates(items)
        self.assertEqual([(c['artist'], c['title']) for c in ranked], [('Chelsea Wolfe', 'Cold')])

    def test_multi_outlet_premiere_and_known_artist_score_higher(self):
        def premiere(source, link, artist='Iron Tomb'):
            return article(source, f'LISTEN: {artist} Drops "Black Halo"', item_type='premiere', role='discovery',
                           text=f'LISTEN: {artist} Drops "Black Halo" metal')
        single = candidates([premiere('Metal Injection', 'a')])[0]['score']
        double = candidates([premiere('Metal Injection', 'a'), premiere('Revolver', 'b')])[0]['score']
        known = candidates([premiere('Metal Injection', 'a')], known_artists={'iron tomb'})[0]['score']
        self.assertGreater(double, single)
        self.assertGreater(known, single)


class FakeExtractor:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def available(self):
        return True

    def extract(self, source, title, body):
        self.calls += 1
        return llm_extract.parse_response(self.payload)


class LlmExtractionTests(unittest.TestCase):
    def test_parse_response_cleans_payload(self):
        parsed = llm_extract.parse_response({
            'article_type': 'review',
            'is_metal': True,
            'releases': [
                {'artist': ' Iron Tomb ', 'album': 'Black Halo', 'tracks': ['“Obsidian Crown”', ''],
                 'rating': 0.8, 'rating_label': '4.0/5', 'sentiment': 'positive'},
                {'artist': '', 'album': 'x', 'tracks': [], 'rating': -1, 'rating_label': '', 'sentiment': 'mixed'},
                {'artist': 'No Score', 'album': '', 'tracks': ['Song'], 'rating': -1, 'rating_label': '',
                 'sentiment': 'weird'},
            ],
        })
        self.assertEqual(len(parsed['releases']), 2)
        self.assertEqual(parsed['releases'][0]['artist'], 'Iron Tomb')
        self.assertEqual(parsed['releases'][0]['tracks'], ['Obsidian Crown'])
        self.assertIsNone(parsed['releases'][1]['rating'])
        self.assertEqual(parsed['releases'][1]['sentiment'], 'mixed')

    def test_llm_review_reading_drives_candidates(self):
        extractor = FakeExtractor({
            'article_type': 'review',
            'is_metal': True,
            'releases': [{'artist': 'The Ocean', 'album': 'Solaris', 'tracks': ['Belligerence'],
                          'rating': 0.9, 'rating_label': '9/10', 'sentiment': 'positive'}],
        })
        items, _rejected = enrich_reviews(
            [article('Invisible Oranges', 'The Ocean Emerge Reconstructed and Mighty on “Solaris” (Review + Interview)',
                     body='x' * 300)],
            extractor=extractor,
        )
        ranked = candidates(items)
        self.assertEqual(extractor.calls, 1)
        self.assertEqual((ranked[0]['artist'], ranked[0]['title'], ranked[0]['album']), ('The Ocean', 'Belligerence', 'Solaris'))
        self.assertEqual(ranked[0]['best_rating'], 0.9)
        self.assertGreaterEqual(ranked[0]['score'], 70)

    def test_llm_flags_non_metal_generalist_article(self):
        extractor = FakeExtractor({
            'article_type': 'review',
            'is_metal': False,
            'releases': [{'artist': 'Oasis', 'album': 'Definitely Maybe', 'tracks': ['Live Forever'],
                          'rating': 0.9, 'rating_label': '', 'sentiment': 'positive'}],
        })
        items, _ = enrich_reviews([article('Louder', 'Oasis – Definitely Maybe review', body='x' * 300)], extractor=extractor)
        self.assertEqual(candidates(items), [])

    def test_premiere_llm_only_when_regex_misses(self):
        extractor = FakeExtractor({
            'article_type': 'premiere', 'is_metal': True,
            'releases': [{'artist': 'Befouled Malediction', 'album': '', 'tracks': ['Ritual'],
                          'rating': -1, 'rating_label': '', 'sentiment': 'positive'}],
        })
        items, _ = enrich_reviews([
            article('Invisible Oranges', 'Befouled Malediction Perform A Cursed “Ritual” (Track Premiere)', item_type='premiere'),
            article('Metal Injection', 'LISTEN: IRON TOMB Drops "Black Halo"', item_type='premiere', role='discovery'),
        ], extractor=extractor)
        self.assertEqual(extractor.calls, 1)
        pairs = {(item['artist'], item['title']) for item in candidates(items)}
        self.assertIn(('Befouled Malediction', 'Ritual'), pairs)

    def test_extractor_handles_refusal_and_api_errors(self):
        client = MagicMock()
        client.beta.messages.create.return_value = MagicMock(stop_reason='refusal', content=[])
        extractor = llm_extract.Extractor(client=client, max_calls=1)
        self.assertIsNone(extractor.extract('X', 'T', 'B'))
        self.assertEqual(extractor.failures, 1)
        self.assertFalse(extractor.available())
        kwargs = client.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs['output_config']['format']['type'], 'json_schema')
        self.assertEqual(kwargs['fallbacks'], 'default')

    def test_disabled_without_api_key(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(llm_extract.enabled())


if __name__ == '__main__':
    unittest.main()
