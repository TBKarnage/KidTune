from __future__ import annotations

import io
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from app.models import CatalogTrack, ExplicitVerdict
from app.services.http_client import make_async_client
from app.services.lyrics import LyricsResult

logger = logging.getLogger(__name__)

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class PlexTrackPaths:
    artist_dir: Path
    album_dir: Path
    audio_path: Path
    cover_path: Path
    lrc_path: Path


def sanitize_filename_part(value: str, fallback: str = "Unknown") -> str:
    cleaned = _ILLEGAL_CHARS_RE.sub("_", value or "")
    cleaned = cleaned.replace("..", "_")
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:120] if cleaned else fallback


def compute_plex_paths(music_dir: Path, track: CatalogTrack) -> PlexTrackPaths:
    album_artist = sanitize_filename_part(track.album_artist or track.artist, "Unknown Artist")
    album_name = sanitize_filename_part(track.album or f"{track.title} - Single", "Singles")
    year = track.year.strip() if track.year and track.year.strip().isdigit() else ""
    album_folder = f"{album_name} ({year})" if year else album_name

    track_title = sanitize_filename_part(track.title, "Untitled")
    track_num = max(1, int(track.track_number or 1))
    disc_num = max(1, int(track.disc_number or 1))
    disc_count = max(1, int(track.disc_count or 1))

    if disc_count > 1 or disc_num > 1:
        stem = f"{disc_num}-{track_num:02d} - {track_title}"
    else:
        stem = f"{track_num:02d} - {track_title}"

    artist_dir = music_dir / album_artist
    album_dir = artist_dir / album_folder
    return PlexTrackPaths(
        artist_dir=artist_dir,
        album_dir=album_dir,
        audio_path=album_dir / f"{stem}.m4a",
        cover_path=album_dir / "cover.jpg",
        lrc_path=album_dir / f"{stem}.lrc",
    )


async def fetch_artwork_bytes(
    artwork_url: str,
    fallback_url: str = "",
) -> Optional[tuple[bytes, int]]:
    for url in (artwork_url, fallback_url):
        if not url:
            continue
        try:
            async with make_async_client(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw = resp.content
            if not raw:
                continue
            with Image.open(io.BytesIO(raw)) as img:
                fmt = (img.format or "").upper()
                if fmt == "PNG":
                    return raw, MP4Cover.FORMAT_PNG
                if fmt in {"JPEG", "JPG"}:
                    return raw, MP4Cover.FORMAT_JPEG
                # Convert WebP/other formats to JPEG so Plex & MP4 atoms accept it cleanly
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=92)
                return buf.getvalue(), MP4Cover.FORMAT_JPEG
        except Exception as exc:
            logger.debug("Artwork fetch failed for %s: %s", url, exc)
    return None


def tag_m4a_file(
    *,
    audio_path: Path,
    track: CatalogTrack,
    lyrics: Optional[LyricsResult] = None,
    artwork: Optional[tuple[bytes, int]] = None,
    source_note: str = "Added via KidTune",
) -> None:
    mp4 = MP4(str(audio_path))
    if mp4.tags is None:
        mp4.add_tags()

    tags = mp4.tags
    tags["\xa9nam"] = [track.title]
    tags["\xa9ART"] = [track.artist]
    tags["aART"] = [track.album_artist or track.artist]
    tags["\xa9alb"] = [track.album or f"{track.title} - Single"]

    if track.year:
        tags["\xa9day"] = [track.year]
    if track.genre:
        tags["\xa9gen"] = [track.genre]

    track_num = max(1, int(track.track_number or 1))
    track_total = max(track_num, int(track.track_count or 1))
    tags["trkn"] = [(track_num, track_total)]

    disc_num = max(1, int(track.disc_number or 1))
    disc_total = max(disc_num, int(track.disc_count or 1))
    tags["disk"] = [(disc_num, disc_total)]

    # MP4 rating atom: 2 = Clean, 0 = None (never write 4 = Explicit)
    if track.explicit_verdict in {ExplicitVerdict.CLEAN, ExplicitVerdict.CLEANED_EDIT}:
        tags["rtng"] = [2]
    else:
        tags["rtng"] = [0]

    tags["\xa9cmt"] = [source_note]

    if lyrics:
        lyric_text = (lyrics.plain_lyrics or lyrics.synced_lyrics or "").strip()
        if lyric_text:
            tags["\xa9lyr"] = [lyric_text]

    if track.musicbrainz_recording_id:
        tags["----:com.apple.iTunes:MusicBrainz Track Id"] = [
            track.musicbrainz_recording_id.encode("utf-8")
        ]
    if track.musicbrainz_release_id:
        tags["----:com.apple.iTunes:MusicBrainz Album Id"] = [
            track.musicbrainz_release_id.encode("utf-8")
        ]

    if artwork:
        art_bytes, art_format = artwork
        tags["covr"] = [MP4Cover(art_bytes, imageformat=art_format)]

    mp4.save()


def install_track_into_plex(
    *,
    staged_audio_path: Path,
    music_dir: Path,
    track: CatalogTrack,
    lyrics: Optional[LyricsResult] = None,
    artwork: Optional[tuple[bytes, int]] = None,
) -> PlexTrackPaths:
    paths = compute_plex_paths(music_dir, track)
    paths.album_dir.mkdir(parents=True, exist_ok=True)

    tag_m4a_file(
        audio_path=staged_audio_path,
        track=track,
        lyrics=lyrics,
        artwork=artwork,
    )

    shutil.move(str(staged_audio_path), str(paths.audio_path))

    if artwork and not paths.cover_path.exists():
        art_bytes, art_format = artwork
        try:
            if art_format == MP4Cover.FORMAT_JPEG:
                paths.cover_path.write_bytes(art_bytes)
            else:
                with Image.open(io.BytesIO(art_bytes)) as img:
                    img.convert("RGB").save(paths.cover_path, format="JPEG", quality=92)
        except Exception as exc:
            logger.debug("Could not write album cover.jpg: %s", exc)

    if lyrics and lyrics.synced_lyrics:
        try:
            paths.lrc_path.write_text(lyrics.synced_lyrics, encoding="utf-8")
        except Exception as exc:
            logger.debug("Could not write sidecar .lrc lyrics: %s", exc)

    return paths
