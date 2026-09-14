# Metal Radar

Automated weekly editorial metal radar for Spotify.

Every Friday at 08:00 Europe/Rome, GitHub Actions collects recent metal coverage, selects up to 15 tracks, removes duplicates, and appends new tracks to the Spotify playlist.

## Required GitHub Secrets

- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `SPOTIFY_REFRESH_TOKEN`
- `SPOTIFY_PLAYLIST_ID`

The repository contains no credentials. Add the secrets under Settings → Secrets and variables → Actions, then run the workflow manually once with `workflow_dispatch`.
