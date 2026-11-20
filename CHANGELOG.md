# Changelog

All notable changes to KidTune will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-15

### Added
- **iPad-Friendly PWA**: Installable Home Screen web app with kid profile switching, song search, live request queue status, and PIN-protected Parent Mode.
- **Multi-Layer Explicit Content Safeguards**:
  - Catalog explicitness checks via iTunes Search API with Deezer fallback.
  - Automatic Clean / Radio Edit alternative matching.
  - Pre-download lyrics profanity screening via LRCLIB.
  - Custom parent-managed blocked words and blocked artists lists.
  - YouTube safety scoring preferring `- Topic` / Official Audio channels and rejecting age-restricted or explicit-titled videos.
  - Configurable `hybrid`, `strict`, and `auto_clean` policy modes.
- **Plex & Plexamp Pipeline**:
  - Downloads AAC 256kbps `.m4a` tracks via `yt-dlp` and `ffmpeg`.
  - Embeds full MP4 metadata (`©nam`, `©ART`, `aART`, `©alb`, `©day`, `©gen`, `trkn`, `disk`, `rtng`, MusicBrainz IDs, cover art, and lyrics) plus `cover.jpg` and synced `.lrc` sidecars.
  - Organizes tracks into `<Music Root>/<Album Artist>/<Album> (<Year>)/<Track> - <Title>.m4a` and triggers targeted Plex library scans.
- **Google Home Integrations**:
  - Google Keep list polling (`Music Requests`) with helper script (`scripts/get_keep_token.py`).
  - Optional spoken status announcements to LAN Google Cast speakers via TTS.
- **Docker & Testing**:
  - Production `Dockerfile` and `docker-compose.yml` with healthcheck.
  - Automated `pytest` suite covering API, catalog, safety, downloader scoring, Google Keep sync, MP4 metadata tagging, and end-to-end worker pipeline.
