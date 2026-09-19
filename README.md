# Metal Radar

**No algorithm. No filler. Just the stuff worth hearing.**

Metal Radar is a weekly editorial playlist builder for Spotify. Every Friday it reads the metal press, finds the new tracks that actually matter, and adds them to [METAL RADAR](https://open.spotify.com/) — a living radar of heavy music curated from people who write about it for a living.

## What it does

1. **Scans the press** — Pitchfork, Angry Metal Guy, Decibel, Revolver, Metal Injection, Loudwire, Stereogum, Consequence, BrooklynVegan, Kerrang, Louder, The Quietus, Metalitalia, Metallus
2. **Prefers reviews** — reads review bodies for ratings and standout tracks; premieres only fill leftover slots
3. **Scores and ranks** — quality-source ratings and multi-review consensus beat single-site LISTEN/WATCH dumps
4. **Updates Spotify** — up to 15 new tracks a week, deduped against playlist history, with hard caps so discovery outlets never take over

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
