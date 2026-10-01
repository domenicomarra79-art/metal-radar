# Metal Radar

**No algorithm. No filler. Just the stuff worth hearing.**

Metal Radar is a weekly editorial playlist builder for Spotify. Every Friday it reads the metal press, finds the new tracks that actually matter, and adds them to [METAL RADAR](https://open.spotify.com/) — a living radar of heavy music curated from people who write about it for a living.

## What it does

1. **Scans the press** — Pitchfork, Angry Metal Guy, Decibel, Revolver, Metal Injection, Loudwire, Stereogum, Consequence, BrooklynVegan, Kerrang, Louder, The Quietus, Invisible Oranges, No Clean Singing, Heavy Blog Is Heavy, Metal Storm, Toilet ov Hell, Bandcamp Daily, Metalitalia, Metallus, Metal Hammer Italia
2. **Prefers reviews** — reads review bodies for ratings and standout tracks; premieres and lone lukewarm reviews only top the week up to 8 tracks
3. **Scores and ranks** — quality-source ratings (AMG's harsher scale calibrated), multi-review consensus, multi-outlet premieres and artists already on the playlist beat single-site LISTEN/WATCH dumps
4. **Finds the right track** — accent/punctuation-tolerant Spotify matching, no live/demo/remaster versions, and for albums with no named standout the pre-release single, then the title track
5. **Checks it is metal** — artist genres from Spotify or MusicBrainz tags reject pop/britpop leaks from generalist outlets
6. **Updates Spotify** — up to 15 new tracks a week, deduped against playlist history, with hard caps so discovery outlets never take over

Runs automatically via GitHub Actions every **Friday at 01:00 Europe/Rome** (or on demand with `workflow_dispatch`).

## Playlist identity

- **Name:** `METAL RADAR`
- **Description:** *The essential metal radar. New riffs, heavy sounds and future classics — curated weekly from the best metal press*

## Required GitHub Secrets

| Secret | Purpose |
| --- | --- |
| `SPOTIFY_CLIENT_ID` | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret |
| `SPOTIFY_REFRESH_TOKEN` | OAuth refresh token with playlist scopes |
| `SPOTIFY_PLAYLIST_ID` | Target playlist ID |
| `ANTHROPIC_API_KEY` | *Optional.* Lets Claude read review and premiere articles for artist, album, score and standout tracks. Without it the regex parser runs alone |

With `ANTHROPIC_API_KEY` set, each run makes at most 60 Claude calls (`METAL_RADAR_LLM_MAX_CALLS`) on `claude-opus-5-5` at low effort (`METAL_RADAR_LLM_MODEL` overrides the model).

No credentials live in the repo. Add the secrets under **Settings → Secrets and variables → Actions**, then run the workflow once with **Run workflow**.

## Local check

```bash
pip install -r requirements.txt
python -m unittest discover -s tests
```

## Editorial picks (optional)

Drop a curated list in `data/editorial-picks.json` to force priority tracks on the next run. Pending picks are prepended to the auto-ranked pool, resolved on Spotify, then marked with `applied_at` so they are not re-applied every week.

Example shape: `week`, `notes`, `excluded[]`, and `tracks[]` with `artist`, `title`, `subgenre`, `priority`, `why`.

---

Built for discovery. Powered by the metal press. Delivered to Spotify every week.
