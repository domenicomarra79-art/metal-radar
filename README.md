# Metal Radar

**No algorithm. No filler. Just the stuff worth hearing.**

Metal Radar is a weekly editorial playlist builder for Spotify. Every Friday it reads the metal press, finds the new tracks that actually matter, and adds them to [METAL RADAR](https://open.spotify.com/) — a living radar of heavy music curated from people who write about it for a living.

## What it does

1. **Scans the press** — Pitchfork, Decibel, Revolver, Metal Injection, Loudwire, Stereogum, Consequence, BrooklynVegan, Kerrang, Louder, The Quietus, Metalitalia, Metallus
2. **Extracts real releases** — premieres, singles, and album tracks from headlines (not tour noise, giveaways, or boilerplate)
3. **Scores and ranks** — authority, multi-source consensus, and editorial signals beat single-site dumps
4. **Updates Spotify** — up to 15 new tracks a week, deduped against playlist history, with hard caps so one outlet never takes over

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

No credentials live in the repo. Add the secrets under **Settings → Secrets and variables → Actions**, then run the workflow once with **Run workflow**.

## Local check

```bash
pip install -r requirements.txt
python -m unittest tests.test_metal_radar
```

---

Built for discovery. Powered by the metal press. Delivered to Spotify every week.
