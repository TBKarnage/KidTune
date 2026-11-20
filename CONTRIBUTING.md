# Contributing to KidTune

Thanks for helping improve KidTune!

## Prerequisites

- **Python 3.11+**
- **FFmpeg** (`ffmpeg` on `PATH`) — required for audio downloads and for the metadata/E2E test suites
- **Node.js** (optional, installed in Docker image for `yt-dlp` JS extractors)

## Local Setup

```bash
git clone https://github.com/TBKarnage/KidTune.git
cd KidTune
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running Tests & Development Server

Run the full test suite before opening a pull request:

```bash
pytest -v
```

Run the development server locally with auto-reload:

```bash
cp .env.example .env
uvicorn app.main:app --reload --port 8484
```

## Code & PR Guidelines

- Follow `.editorconfig` formatting (UTF-8, LF line endings, 4 spaces for Python, 2 spaces for JS/CSS/HTML/YAML).
- Add or update tests under `tests/` when changing safety checks, catalog parsing, API routes, metadata tagging, or worker behavior.
- Never commit `.env`, API tokens, PINs, SQLite databases (`data/`), or downloaded audio (`music/`).
- Keep pull requests focused and include a concise summary of what changed and how it was verified.
