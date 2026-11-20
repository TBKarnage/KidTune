<p align="center">
  <img src="app/static/icons/icon-192.png" alt="KidTune Icon" width="96" height="96" />
</p>

<h1 align="center">KidTune — Family-Safe Music Requests for Plex &amp; Plexamp</h1>

<p align="center">
  <a href="https://github.com/TBKarnage/KidTune/actions/workflows/ci.yml"><img src="https://github.com/TBKarnage/KidTune/actions/workflows/ci.yml/badge.svg" alt="CI Status" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/docker-ready-2496ED" alt="Docker Ready" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" /></a>
</p>

KidTune is a self-hosted music request portal designed for kids on iPads and family Google Home setups. It enforces multi-layer explicit content safeguards, downloads clean studio audio via `yt-dlp` + `ffmpeg`, embeds full ID3/MP4 metadata, high-resolution cover art, and synced lyrics, organizes tracks into Plex's artist/album folder structure, and automatically triggers a Plex library scan so new songs show up right away in **Plexamp**.

## Features

- **iPad-Friendly PWA**: Runs full-screen on iPads via Safari -> **Share** -> **Add to Home Screen** (no Mac, Xcode, or App Store account required), with kid profile switching and live status updates.
- **Multi-Layer Explicit Content Safeguards**:
  - **Catalog check** via iTunes Search API (`trackExplicitness`) & Deezer fallback.
  - **Clean Edit matching**: surfaces official Clean / Radio Edit versions when an explicit track is searched.
  - **Lyrics check** via LRCLIB before downloading (catches untagged explicit lyrics).
  - **Custom blocklists**: parents can add extra blocked words and blocked artists.
  - **YouTube safety scoring**: prefers `- Topic` / Official Audio channels, matches catalog duration, and rejects `18+` age-restricted or explicit-titled videos.
  - **Three policy modes**:
    - `Hybrid` *(default)*: Auto-downloads verified clean songs, auto-blocks explicit tracks, and sends unmatched/unverified requests to the PIN-protected **Parent Approval** queue.
    - `Strict`: Every request requires Parent Approval; explicit tracks are blocked automatically.
    - `Auto-Clean`: Only auto-downloads verified clean tracks; rejects explicit and unverified requests.
- **Full Plex / Plexamp Metadata & Structure**:
  - Outputs `.m4a` (AAC 256kbps) organized as `<Music Root>/<Album Artist>/<Album> (<Year>)/<Track> - <Title>.m4a`.
  - Embeds title, artist, album artist, album, year, genre, track/disc numbers, clean rating atom (`rtng`), high-res cover art (`covr` + `cover.jpg`), MusicBrainz IDs, and lyrics (`©lyr` + `.lrc` synced sidecar).
  - Triggers targeted Plex library scans automatically via the Plex HTTP API.
- **Google Home Support (Google Keep + Cast TTS)**:
  - **Voice requests via Google Keep**: Kids say *"Hey Google, add Shake It Off by Taylor Swift to my Music Requests list"* -> KidTune polls the Google Keep list, applies the exact same safety rules, and annotates/checks off items.
  - **Spoken announcements via Google Cast**: Broadcasts completion, approval, or explicit-block messages back to your Google Home speakers on the LAN.

---

## Quick Start (Docker Compose)

1. Clone the repository:
   ```bash
   git clone https://github.com/TBKarnage/KidTune.git
   cd KidTune
   ```
2. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env` and set:
   - `PLEX_MUSIC_PATH`: host path to your Plex **Kids Music** folder (e.g. `/mnt/media/music/Kids`).
   - `PUBLIC_BASE_URL`: your server's LAN IP and port (e.g. `http://192.168.1.50:8484`).
   - `PARENT_PIN`: change from `1234` (or update it inside **Parent Mode** after first login).
   - `PLEX_URL` and `PLEX_TOKEN`: optional in `.env` (can also be configured and tested inside **Parent Mode**).
4. Start KidTune:
   ```bash
   docker compose up -d --build
   ```
5. Open `http://<SERVER_IP>:8484` in Safari on your kids' iPads, tap **Share** -> **Add to Home Screen**, and open **KidTune**!

---

## Plex & Plexamp Setup for Kids

1. In Plex Media Server, create a dedicated **Kids Music** library pointing to the same folder mounted as `/music` in KidTune.
2. In the library's **Advanced** settings, enable **Prefer local metadata** (optional, recommended so embedded tags and cover art always match what you approved).
3. Create a **Plex Managed User** for your kids in Plex Home and share **only** the **Kids Music** library with that profile.
4. Sign into **Plexamp** on their iPads under that Managed User profile.

---

## Google Home Setup

Because Google retired custom Conversational Actions and arbitrary text webhooks in IFTTT, KidTune integrates with Google Home in two ways:

### 1. Voice Requests via Google Keep List
1. In the **Google Home** mobile app, verify Google Keep is enabled under **Settings -> Notes & lists**.
2. Create a list named **`Music Requests`** in Google Keep.
3. Obtain a Google Master Token using the included helper:
   ```bash
   docker compose exec kidtune python scripts/get_keep_token.py
   ```
4. Enter your Google email and Master Token in `.env` or **Parent Mode -> Safety & Integrations**, and enable Google Keep polling.
5. Kids can now say:
   > *"Hey Google, add Happy by Pharrell Williams to my Music Requests list"*

### 2. Spoken Announcements on Google Home Speakers (Cast TTS)
1. If running Docker on Linux and using automatic mDNS speaker discovery, uncomment `network_mode: host` in `docker-compose.yml` (and comment out `ports:`). Alternatively, enter your speakers' static LAN IP addresses in **Target Speakers**.
2. Set `PUBLIC_BASE_URL` to `http://<YOUR_SERVER_LAN_IP>:8484` so speakers can stream the generated TTS MP3 audio.
3. Enable **Broadcast voice announcements** in Parent Settings and click **Send Test Announcement**.

---

## Project Structure

```text
KidTune/
├── app/
│   ├── auth.py                   # PIN hashing and signed Parent Mode session tokens
│   ├── config.py                 # Environment configuration (pydantic-settings)
│   ├── database.py               # SQLite persistence for requests, settings, and blocklists
│   ├── main.py                   # FastAPI app, REST endpoints, and PWA routes
│   ├── models.py                 # Pydantic schemas and enums
│   ├── services/
│   │   ├── catalog.py            # iTunes Search API & Deezer catalog matching
│   │   ├── downloader.py         # Safe YouTube candidate scoring and yt-dlp download
│   │   ├── google_cast.py        # Google Cast speaker discovery & TTS broadcast
│   │   ├── google_keep.py        # Google Keep list polling & status annotation
│   │   ├── http_client.py        # Shared async HTTP client helpers
│   │   ├── lyrics.py             # LRCLIB plain & synced lyrics fetching
│   │   ├── metadata.py           # MP4 tagging, cover art, .lrc sidecar & Plex folder layout
│   │   ├── plex.py               # Plex Media Server library scan integration
│   │   ├── request_pipeline.py   # End-to-end safety assessment & request routing
│   │   ├── safety.py             # Explicit filtering, profanity checks, and policy rules
│   │   └── worker.py             # Background download/tag/scan & Keep polling worker
│   └── static/                   # iPad PWA frontend (HTML, CSS, JS, service worker, icons)
├── scripts/
│   └── get_keep_token.py         # Interactive helper to obtain a Google Keep Master Token
├── tests/                        # Unit and E2E pytest test suite
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
└── requirements.txt
```

---

## Local Development & Tests

Make sure `ffmpeg` is installed on your system, then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -v
uvicorn app.main:app --reload --port 8484
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines and [SECURITY.md](SECURITY.md) for self-hosting security recommendations.

## License

This project is licensed under the [MIT License](LICENSE).
