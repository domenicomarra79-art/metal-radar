import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from metal_radar import (
    MAX_PER_ARTIST,
    MAX_PER_SOURCE_DISCOVERY,
    MAX_TRACKS,
    PREMIERE_SCORE_CAP,
    SINGLE_DISCOVERY_CAP,
    SPOTIFY_TOKEN_URL,
    _mask_id,
    _redact_endpoint,
    add,
    articles,
    candidates,
    diagnose_spotify,
    existing,
    extract_pairs,
    extract_review_album,
    parse_rating,
    parse_sentiment,
    playlist_ids,
    resolve_candidate,
    select_tracks,
    token,
    valid_pair,
)


def track(tid, artist, name, uri=None):
    return {
        'id': tid,
        'name': name,
        'uri': uri or f'spotify:track:{tid}',
        'artists': [{'name': artist}],
    }


class TokenTests(unittest.TestCase):
    @patch.dict(os.environ, {
        'SPOTIFY_CLIENT_ID': 'test-client-id',
        'SPOTIFY_CLIENT_SECRET': 'test-client-secret',
        'SPOTIFY_REFRESH_TOKEN': 'test-refresh-token',
    })
    @patch('metal_radar.requests.post')
    def test_token_uses_basic_auth_and_refresh_grant(self, post):
        response = MagicMock()
        response.ok = True
        response.json.return_value = {'access_token': 'test-access-token'}
        post.return_value = response

        access = token()

        self.assertEqual(access, 'test-access-token')
        args, kwargs = post.call_args
        self.assertEqual(args[0], SPOTIFY_TOKEN_URL)
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/x-www-form-urlencoded')
        self.assertEqual(kwargs['data']['grant_type'], 'refresh_token')
        self.assertEqual(kwargs['data']['refresh_token'], 'test-refresh-token')
        expected = base64.b64encode(b'test-client-id:test-client-secret').decode()
        self.assertEqual(kwargs['headers']['Authorization'], f'Basic {expected}')

    @patch.dict(os.environ, {
        'SPOTIFY_CLIENT_ID': 'test-client-id',
        'SPOTIFY_CLIENT_SECRET': 'test-client-secret',
        'SPOTIFY_REFRESH_TOKEN': 'test-refresh-token',
    })
    @patch('metal_radar.requests.post')
    def test_token_error_is_redacted(self, post):
        response = MagicMock()
        response.ok = False
        response.status_code = 400
        response.json.return_value = {
            'error': 'invalid_grant',
            'error_description': 'secret-should-not-appear',
            'refresh_token': 'leaked-token',
        }
        post.return_value = response

        with self.assertRaises(RuntimeError) as ctx:
            token()
        message = str(ctx.exception)
        self.assertIn('HTTP 400', message)
        self.assertIn('invalid_grant', message)
        self.assertNotIn('secret-should-not-appear', message)
        self.assertNotIn('leaked-token', message)
        self.assertNotIn('test-refresh-token', message)


class PlaylistIdentityTests(unittest.TestCase):
    def test_playlist_identity_is_fixed(self):
        from metal_radar import PLAYLIST_DESCRIPTION, PLAYLIST_NAME
        self.assertEqual(PLAYLIST_NAME, 'METAL RADAR')
        self.assertEqual(
            PLAYLIST_DESCRIPTION,
            'The essential metal radar. New riffs, heavy sounds and future classics — '
            'curated weekly from the best metal press',
        )

    @patch('metal_radar.requests.put')
    def test_update_playlist_profile_sends_fixed_identity(self, put):
        from metal_radar import update_playlist_profile
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {}
        put.return_value = response
        update_playlist_profile('token', 'playlist-id')
        self.assertEqual(put.call_args.kwargs['json']['name'], 'METAL RADAR')
        self.assertEqual(
            put.call_args.kwargs['json']['description'],
            'The essential metal radar. New riffs, heavy sounds and future classics — '
            'curated weekly from the best metal press',
        )


class SelectionTests(unittest.TestCase):
    def test_skips_duplicate_spotify_ids(self):
        ranked = [
            {'artist': 'Band A', 'title': 'Song 1', 'score': 10},
            {'artist': 'Band B', 'title': 'Song 2', 'score': 9},
        ]
        catalog = {
            ('Band A', 'Song 1'): track('id-1', 'Band A', 'Song 1'),
            ('Band B', 'Song 2'): track('id-2', 'Band B', 'Song 2'),
        }
        chosen, uris, known, extra = select_tracks(
            ranked,
            {'id-1'},
            lambda artist, title: catalog[(artist, title)],
        )
        self.assertEqual([item[1]['id'] for item in chosen], ['id-2'])
        self.assertEqual(uris, ['spotify:track:id-2'])
        self.assertEqual(known, {'id-1', 'id-2'})

    def test_max_two_tracks_per_artist_and_max_fifteen(self):
        ranked = []
        catalog = {}
        for artist_n in range(12):
            artist = f'Artist {artist_n}'
            for song_n in range(3):
                title = f'Song {song_n}'
                tid = f'{artist_n}-{song_n}'
                ranked.append({'artist': artist, 'title': title, 'score': 100 - len(ranked)})
                catalog[(artist, title)] = track(tid, artist, title)
        chosen, uris, _known, _extra = select_tracks(
            ranked,
            set(),
            lambda artist, title: catalog[(artist, title)],
        )
        self.assertLessEqual(len(chosen), MAX_TRACKS)
        self.assertEqual(len(chosen), MAX_TRACKS)
        counts = {}
        for _, item in chosen:
            name = item['artists'][0]['name']
            counts[name] = counts.get(name, 0) + 1
        self.assertTrue(all(value <= MAX_PER_ARTIST for value in counts.values()))
        self.assertEqual(len(uris), 15)

    def test_empty_selection_is_success(self):
        chosen, uris, known, extra = select_tracks([], {'already'}, lambda artist, title: None)
        self.assertEqual(chosen, [])
        self.assertEqual(uris, [])
        self.assertEqual(known, {'already'})
        self.assertEqual(extra['duplicates_rejected'], 0)


class PlaylistTests(unittest.TestCase):
    def test_playlist_ids_ignore_missing_tracks(self):
        ids = playlist_ids([
            {'item': {'id': 'abc'}},
            {'track': {'id': 'legacy'}},
            {'item': None},
            {},
        ])
        self.assertEqual(ids, {'abc', 'legacy'})

    @patch('metal_radar.requests.get')
    def test_existing_paginates_items_endpoint(self, get):
        first = MagicMock()
        first.ok = True
        first.json.return_value = {
            'items': [{'item': {'id': 'a'}}],
            'next': 'https://api.spotify.com/v1/playlists/xyz/items?offset=100',
        }
        second = MagicMock()
        second.ok = True
        second.json.return_value = {
            'items': [{'item': {'id': 'b'}}],
            'next': None,
        }
        get.side_effect = [first, second]
        items = existing('token', 'xyz')
        self.assertEqual(playlist_ids(items), {'a', 'b'})
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].args[0], 'https://api.spotify.com/v1/playlists/xyz/items')

    @patch('metal_radar.requests.post')
    def test_add_posts_to_items_endpoint(self, post):
        response = MagicMock()
        response.ok = True
        post.return_value = response
        add('token', 'xyz', ['spotify:track:abc'])
        self.assertEqual(post.call_args.args[0], 'https://api.spotify.com/v1/playlists/xyz/items')
        self.assertEqual(post.call_args.kwargs['json'], {'uris': ['spotify:track:abc']})


class RssTests(unittest.TestCase):
    @patch('metal_radar.requests.get', side_effect=RuntimeError('network down'))
    def test_single_rss_failure_does_not_stop_run(self, _get):
        items, consulted = articles()
        self.assertEqual(items, [])
        self.assertEqual(consulted, [])

    @patch('metal_radar.requests.get')
    def test_partial_feed_failure_still_consults_source(self, get):
        ok = MagicMock()
        ok.status_code = 200
        ok.headers = {'content-type': 'application/rss+xml'}
        ok.content = (
            '<?xml version="1.0"?>'
            '<rss><channel><item>'
            '<title>Iron Tomb — Black Halo metal premiere</title>'
            '<link>https://example.com/ok</link>'
            '<pubDate>Fri, 18 Sep 2026 10:00:00 GMT</pubDate>'
            '<description>death metal premiere</description>'
            '</item></channel></rss>'
        ).encode('utf-8')
        blocked = MagicMock()
        blocked.status_code = 403
        blocked.headers = {'content-type': 'text/html'}
        blocked.content = b'<html>blocked</html>'

        def _side_effect(url, **_kwargs):
            if 'thequietus' in url:
                return blocked
            if 'metalinjection.net/feed' in url:
                return ok
            empty = MagicMock()
            empty.status_code = 404
            empty.headers = {'content-type': 'text/html'}
            empty.content = b'missing'
            return empty

        get.side_effect = _side_effect
        items, consulted = articles()
        self.assertIn('Metal Injection', consulted)
        self.assertTrue(any(item['source'] == 'Metal Injection' for item in items))
        self.assertNotIn('The Quietus', consulted)


class DiagnosticTests(unittest.TestCase):
    def test_redact_endpoint_hides_playlist_id(self):
        url = 'https://api.spotify.com/v1/playlists/secretPlaylistId123/tracks'
        self.assertEqual(_redact_endpoint(url), 'https://api.spotify.com/v1/playlists/***/tracks')
        self.assertNotIn('secretPlaylistId123', _redact_endpoint(url))

    def test_mask_id_partial(self):
        self.assertEqual(_mask_id('abcdefgh'), 'ab***gh')
        self.assertEqual(_mask_id(''), 'missing')

    @patch('metal_radar.requests.get')
    def test_diagnose_logs_status_and_spotify_error_without_playlist_id(self, get):
        me = MagicMock()
        me.ok = True
        me.status_code = 200
        me.json.return_value = {'display_name': 'Tester', 'id': 'abcdefgh'}
        playlist = MagicMock()
        playlist.ok = False
        playlist.status_code = 403
        playlist.json.return_value = {'error': {'status': 403, 'message': 'Insufficient client scope'}}
        items = MagicMock()
        items.ok = False
        items.status_code = 403
        items.json.return_value = {'error': {'status': 403, 'message': 'Forbidden'}}
        get.side_effect = [me, playlist, items]

        with patch('builtins.print') as printer:
            with self.assertRaises(RuntimeError) as ctx:
                diagnose_spotify('access-token', 'secretPlaylistId123', 'user-read-email')
        logged = '\n'.join(str(call.args[0]) for call in printer.call_args_list if call.args)
        self.assertIn('HTTP 403', logged)
        self.assertIn('Forbidden', logged)
        self.assertIn('/playlists/***/items', logged)
        self.assertNotIn('/playlists/***/tracks', logged)
        self.assertNotIn('secretPlaylistId123', logged)
        self.assertNotIn('access-token', logged)
        self.assertNotIn('Bearer', logged)
        self.assertIn('Tester', logged)
        self.assertIn('ab***gh', logged)
        self.assertIn('Forbidden', str(ctx.exception))

    @patch('metal_radar.requests.get')
    def test_summary_url_is_parsed_as_markup_string(self, get):
        response = MagicMock()
        response.status_code = 200
        response.headers = {'content-type': 'application/rss+xml'}
        response.content = (
            '<?xml version="1.0"?><rss><channel><item>'
            '<title>A Band — A Song metal</title>'
            '<link>https://example.com/a</link>'
            '<description>https://example.com/not-html</description>'
            '<pubDate>Fri, 18 Sep 2026 10:00:00 GMT</pubDate>'
            '</item></channel></rss>'
        ).encode('utf-8')
        get.return_value = response
        items, _consulted = articles()
        self.assertTrue(any('A Band' in item['title'] for item in items))


class ScoringTests(unittest.TestCase):
    def _article(
        self,
        source,
        link,
        authority=8,
        kind='editorial',
        region='US',
        role='discovery',
        item_type='premiere',
        title='Iron Tomb — Black Halo',
        text=None,
        rating=None,
        rating_label=None,
        highlight='',
        sentiment='positive',
        body_read=False,
    ):
        return {
            'source': source,
            'title': title,
            'link': link,
            'text': text or f'{title} death metal premiere album of the week',
            'body': text or '',
            'published': datetime.now(timezone.utc).isoformat(),
            'region': region,
            'kind': kind,
            'authority': authority,
            'role': role,
            'item_type': item_type,
            'rating': rating,
            'rating_label': rating_label,
            'highlight': highlight,
            'sentiment': sentiment,
            'body_read': body_read,
        }

    def test_amg_review_outranks_mi_listen_premiere(self):
        ranked = candidates([
            self._article(
                'Angry Metal Guy',
                'https://example.com/amg',
                authority=10,
                role='quality',
                item_type='review',
                title='Iron Tomb — Black Halo Review',
                text='Iron Tomb Black Halo Rating: 4.0 standout "Obsidian Crown" shines',
                rating=0.8,
                rating_label='AMG 4.0/5.0',
                highlight='Obsidian Crown',
                body_read=True,
            ),
            self._article(
                'Metal Injection',
                'https://example.com/mi',
                authority=7,
                role='discovery',
                item_type='premiere',
                title='LISTEN: OTHER BAND Drops "Different Song"',
                text='LISTEN: OTHER BAND Drops "Different Song" metal premiere',
            ),
        ])
        by_artist = {item['artist'].lower(): item for item in ranked}
        self.assertIn('iron tomb', by_artist)
        self.assertIn('other band', by_artist)
        self.assertGreater(by_artist['iron tomb']['score'], by_artist['other band']['score'])
        self.assertGreaterEqual(by_artist['iron tomb']['score'], 70)
        self.assertLessEqual(by_artist['other band']['score'], PREMIERE_SCORE_CAP)

    def test_negative_review_is_dropped(self):
        ranked = candidates([
            self._article(
                'Angry Metal Guy',
                'https://example.com/bad',
                authority=10,
                role='quality',
                item_type='review',
                title='Dull Band — Filler Album Review',
                text='This is a disappointment. Rating: 1.5',
                rating=0.3,
                rating_label='AMG 1.5/5.0',
                sentiment='negative',
                body_read=True,
            ),
        ])
        self.assertEqual(ranked, [])

    def test_album_review_without_highlight_does_not_dump_three_tracks(self):
        candidate = {
            'artist': 'Iron Tomb',
            'title': 'Black Halo',
            'album': 'Black Halo',
            'kind': 'album',
            'primary_type': 'review',
            'review_sources': {'Angry Metal Guy'},
            'highlights': set(),
            'score': 88,
            'sources': {'Angry Metal Guy'},
            'roles': {'quality'},
            'kinds': {'editorial'},
            'source_scores': {'rating': 32},
            'subgenre': 'death metal',
        }
        album_tracks = [
            track('a', 'Iron Tomb', 'One'),
            track('b', 'Iron Tomb', 'Two'),
            track('c', 'Iron Tomb', 'Three'),
        ]
        with patch('metal_radar.search', return_value=None), patch(
            'metal_radar.album_named_track', return_value=None
        ):
            # No highlight and album-name search fails => empty, not 3 tracks.
            resolved = resolve_candidate('token', candidate)
        self.assertEqual(resolved, [])

        # Even if a mistaken list were returned, select_tracks album limit is 1.
        chosen, uris, _known, _extra = select_tracks(
            [candidate],
            set(),
            lambda _candidate: album_tracks,
        )
        self.assertEqual(len(chosen), 1)
        self.assertEqual(len(uris), 1)

    def test_two_premieres_are_not_review_consensus(self):
        ranked = candidates([
            self._article(
                'Metal Injection',
                'https://example.com/mi1',
                role='discovery',
                item_type='premiere',
                title='LISTEN: Iron Tomb Drops "Black Halo"',
                text='LISTEN: Iron Tomb Drops "Black Halo" metal',
            ),
            self._article(
                'Revolver',
                'https://example.com/rev',
                role='discovery',
                item_type='premiere',
                title='Hear Iron Tomb new song "Black Halo"',
                text='Hear Iron Tomb new song "Black Halo" metal',
            ),
        ])
        self.assertTrue(ranked)
        self.assertFalse(ranked[0].get('review_sources'))
        self.assertLessEqual(ranked[0]['score'], PREMIERE_SCORE_CAP)

    def test_parse_amg_rating_and_sentiment(self):
        rating, label = parse_rating('Angry Metal Guy', 'Some text Rating: 4.0 more')
        self.assertEqual(rating, 0.8)
        self.assertIn('4.0', label)
        self.assertEqual(parse_sentiment(0.3, 'a disappointment'), 'negative')
        self.assertEqual(parse_sentiment(0.8, 'highly recommended'), 'positive')

    def test_extract_review_album_headline(self):
        self.assertEqual(
            extract_review_album('Anthrax – Cursum Perficio Review'),
            ('Anthrax', 'Cursum Perficio'),
        )

    def test_multiple_publications_outrank_a_single_source(self):
        one = candidates([self._article('Pitchfork', 'https://example.com/p', role='quality', item_type='review')])
        many = candidates([
            self._article('Pitchfork', 'https://example.com/p', role='quality', item_type='review', rating=0.8, rating_label='8.0/10'),
            self._article('Decibel', 'https://example.com/d', authority=10, role='quality', item_type='review', rating=0.8),
            self._article('Louder', 'https://example.com/l', authority=8, region='UK', role='quality', item_type='review'),
        ])
        self.assertGreater(many[0]['score'], one[0]['score'])
        self.assertGreaterEqual(many[0]['score'], 40)

    def test_rejects_feed_boilerplate_pairs(self):
        junk = candidates([{
            'source': 'Revolver',
            'title': 'The post Track Premieres — Noroth Preview appeared first on Revolver',
            'link': 'https://example.com/junk',
            'text': 'The post Track Premieres — Noroth Preview appeared first on Revolver death metal',
            'published': datetime.now(timezone.utc).isoformat(),
            'region': 'US',
            'kind': 'editorial',
            'authority': 8,
            'role': 'discovery',
            'item_type': 'premiere',
            'sentiment': 'positive',
            'body_read': False,
        }])
        self.assertEqual(junk, [])

    def test_extracts_premiere_listen_and_italian_single_pairs(self):
        self.assertEqual(
            extract_pairs('Track Premiere: Vredensdal – “Consortium of Fools”'),
            [('Vredensdal', 'Consortium of Fools')],
        )
        self.assertEqual(
            extract_pairs('LISTEN: THE OCEAN Teams Up With TANGERINE DREAM For Cinematic "Milk of My Dreams"'),
            [('THE OCEAN', 'Milk of My Dreams')],
        )
        self.assertEqual(
            extract_pairs('Hear ARCHITECTs’ first song of 2026 “Machine”'),
            [('ARCHITECTs', 'Machine')],
        )
        self.assertEqual(
            extract_pairs('PEELINGFLESH Announce New Self-Titled Album with song "Murderous Intent"'),
            [('PEELINGFLESH', 'Murderous Intent')],
        )
        self.assertEqual(
            extract_pairs('LACRIMAS PROFUNDERE: il nuovo singolo “Faceless”'),
            [('LACRIMAS PROFUNDERE', 'Faceless')],
        )

    def test_rejects_german_and_italian_boilerplate_pairs(self):
        self.assertIsNone(valid_pair('Der Beitrag Ludgar', 'Violent Visions erschien zuerst'))
        self.assertIsNone(valid_pair('HAVOK', 'cambio di location per la data di Milano'))
        self.assertIsNone(valid_pair('Archetype X', 'Premiere des Musikvideos zu Void'))
        self.assertIsNone(valid_pair('WATCH', 'ANTHRAX Gleefully Torture Ex-SAMHAIN Drummer'))
        self.assertIsNone(valid_pair('LISTEN', 'THE OCEAN Teams Up With TANGERINE DREAM'))
        self.assertIsNone(valid_pair('ENTER NOW', 'Win a Trip to See Metallica'))
        self.assertIsNone(valid_pair('PeelingFlesh Announce New Self', 'Titled Album: Hear Murderous Intent'))
        self.assertIsNone(valid_pair("Metallica's M72 tour returns", 'with the support act they thanked for'))
        self.assertIsNone(valid_pair('CELESTIAL SCOURGE', 'il nuovo singolo “Dreamstate'))
        self.assertEqual(valid_pair('Ludgar', 'Violent Visions'), ('Ludgar', 'Violent Visions'))
        self.assertEqual(valid_pair('Letterbombs', "I'm Not Here To Enjoy My Life"), ('Letterbombs', "I'm Not Here To Enjoy My Life"))

    def test_discovery_only_score_cannot_exceed_premiere_cap(self):
        ranked = candidates([
            self._article(
                'Metal Injection',
                'https://example.com/mi',
                authority=10,
                role='discovery',
                item_type='premiere',
                title='LISTEN: HUGE BAND Drops "Huge Song"',
                text='LISTEN: HUGE BAND Drops "Huge Song" essential exclusive premiere metal',
            ),
        ])
        self.assertTrue(ranked)
        self.assertLessEqual(ranked[0]['score'], PREMIERE_SCORE_CAP)

    def test_max_tracks_per_discovery_source_cap(self):
        ranked = []
        catalog = {}
        for index in range(8):
            artist = f'MI Band {index}'
            title = 'Song'
            ranked.append({
                'artist': artist,
                'title': title,
                'score': 55,
                'sources': {'Metal Injection'},
                'roles': {'discovery'},
                'kinds': {'editorial'},
                'review_sources': set(),
                'primary_type': 'premiere',
                'source_publications': ['Metal Injection'],
                'bucket': 'emerging',
                'subgenre': 'black metal',
                'kind': 'track',
                'source_scores': {'quality': 20},
                'highlights': set(),
            })
            catalog[(artist, title)] = track(f'mi-{index}', artist, title)
        chosen, uris, _known, extra = select_tracks(
            ranked,
            set(),
            lambda artist, title: catalog[(artist, title)],
        )
        self.assertLessEqual(len(chosen), SINGLE_DISCOVERY_CAP)
        self.assertLessEqual(extra['per_source'].get('Metal Injection', 0), MAX_PER_SOURCE_DISCOVERY)
        self.assertEqual(len(uris), len(chosen))

    def test_pitchfork_does_not_dominate_selection(self):
        ranked = []
        catalog = {}
        for index in range(8):
            artist = f'Pitchfork Band {index}'
            title = 'Song'
            ranked.append({
                'artist': artist,
                'title': title,
                'score': 90 - index,
                'sources': {'Pitchfork'},
                'roles': {'quality'},
                'kinds': {'editorial'},
                'review_sources': {'Pitchfork'},
                'primary_type': 'review',
                'source_publications': ['Pitchfork'],
                'bucket': 'established',
                'subgenre': 'death metal',
                'kind': 'track',
                'source_scores': {'quality': 30},
                'highlights': set(),
            })
            catalog[(artist, title)] = track(f'pf-{index}', artist, title)
        chosen, uris, _known, _extra = select_tracks(
            ranked,
            set(),
            lambda artist, title: catalog[(artist, title)],
        )
        self.assertLessEqual(len(chosen), 3)
        self.assertEqual(len(uris), len(chosen))


if __name__ == '__main__':
    unittest.main()
