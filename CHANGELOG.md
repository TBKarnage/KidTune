# Changelog

All notable changes to KidTune will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Full Album Search & Downloads**: Switch between **Songs** and **Albums** search in both Kid View and Parent Mode, preview full tracklists with per-track safety checks, and queue entire albums (skipping explicit tracks automatically for kids).
- **Parent-Only Queue Manager & Cleanup**: Added a **Queues & Cleanup** tab inside Parent Mode with a **Kid Song Requests | Parent Downloads** queue switch, status filters, bulk **Clean Up Queue** actions, and individual row dismiss buttons (Kid View stays read-only and music stays in Plex).
- **Dedicated Parent Search & Downloads**: Parent Mode now has full song & album search (plus Direct URL) and its own private **Parent Downloads** queue, completely separated from the kids' **Song Requests** view.
- **Floating Album Cover Background**: Softly drifting left-to-right album covers from downloaded family-safe songs, scaled cleanly for desktop, iPad, and mobile screens.
- **Same-Origin Artwork Proxy & Disk Cache**: New `/api/artwork`, `/api/requests/{id}/artwork`, and `/api/library/covers` endpoints cache and serve album art reliably from KidTune itself.

### Changed
- **Calmer Slate-Gray Theme**: Redesigned the UI in a low-glare slate-gray palette with frosted card surfaces and subtle steel-blue accents.

### Fixed
- **Broken Song & Album Images**: Fixed `sw.js` intercepting cross-origin image requests, proxied artwork through KidTune's backend cache, and added automatic fallback handling.
- **Explicit Content Screening**: Added real-time LRCLIB lyrics screening during search, Deezer explicitness cross-checking, expanded profanity/sexual/drug/violence word-boundary filters, explicit-album unverified lyrics gating, explicit artwork blur for kids, and a pre-install lyrics safety check in the download worker.

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
