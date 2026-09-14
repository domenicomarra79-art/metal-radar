import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from metal_radar import (
    MAX_PER_ARTIST,
    MAX_TRACKS,
    SPOTIFY_TOKEN_URL,
    _mask_id,
    _redact_endpoint,
    articles,
    diagnose_spotify,
    existing,
    playlist_ids,
    select_tracks,
    token,
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
        chosen, uris, known = select_tracks(
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
        chosen, uris, _ = select_tracks(
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
        chosen, uris, known = select_tracks([], {'already'}, lambda artist, title: None)
        self.assertEqual(chosen, [])
        self.assertEqual(uris, [])
        self.assertEqual(known, {'already'})


class PlaylistTests(unittest.TestCase):
    def test_playlist_ids_ignore_missing_tracks(self):
        ids = playlist_ids([
            {'track': {'id': 'abc'}},
            {'track': None},
            {},
        ])
        self.assertEqual(ids, {'abc'})

    @patch('metal_radar.requests.get')
    def test_existing_paginates(self, get):
        first = MagicMock()
        first.ok = True
        first.json.return_value = {
            'items': [{'track': {'id': 'a'}}],
            'next': 'https://api.spotify.com/v1/playlists/xyz/tracks?offset=100',
        }
        second = MagicMock()
        second.ok = True
        second.json.return_value = {
            'items': [{'track': {'id': 'b'}}],
            'next': None,
        }
        get.side_effect = [first, second]
        items = existing('token', 'xyz')
        self.assertEqual(playlist_ids(items), {'a', 'b'})
        self.assertEqual(get.call_count, 2)


class RssTests(unittest.TestCase):
    @patch('metal_radar.feedparser.parse', side_effect=RuntimeError('network down'))
    def test_single_rss_failure_does_not_stop_run(self, _parse):
        items = articles()
        self.assertEqual(items, [])


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
        items.ok = True
        items.status_code = 200
        items.json.return_value = {'items': [], 'total': 0}
        tracks = MagicMock()
        tracks.ok = False
        tracks.status_code = 403
        tracks.json.return_value = {'error': {'status': 403, 'message': 'Forbidden'}}
        get.side_effect = [me, playlist, items, tracks]

        with patch('builtins.print') as printer:
            with self.assertRaises(RuntimeError) as ctx:
                diagnose_spotify('access-token', 'secretPlaylistId123', 'user-read-email')
        logged = '\n'.join(str(call.args[0]) for call in printer.call_args_list if call.args)
        self.assertIn('HTTP 403', logged)
        self.assertIn('Forbidden', logged)
        self.assertIn('/playlists/***/tracks', logged)
        self.assertIn('/playlists/***/items', logged)
        self.assertNotIn('secretPlaylistId123', logged)
        self.assertNotIn('access-token', logged)
        self.assertNotIn('Bearer', logged)
        self.assertIn('Tester', logged)
        self.assertIn('ab***gh', logged)
        self.assertIn('Forbidden', str(ctx.exception))

    @patch('metal_radar.feedparser.parse')
    def test_summary_url_is_parsed_as_markup_string(self, parse):
        class Feed:
            bozo = False
            entries = [{'title': 'A Band — A Song metal', 'summary': 'https://example.com/not-html', 'link': 'https://example.com/a', 'published': '2099-01-01T00:00:00Z'}]
        parse.return_value = Feed()
        items = articles()
        self.assertTrue(any('A Band' in item['title'] for item in items))


if __name__ == '__main__':
    unittest.main()
