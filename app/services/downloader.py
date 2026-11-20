from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import yt_dlp

from app.models import CatalogTrack, ExplicitVerdict
from app.services.safety import find_profanity_matches

logger = logging.getLogger(__name__)

_EXPLICIT_VIDEO_RE = re.compile(
    r"\b(?:explicit|uncensored|dirty\s+version|parental\s+advisory|nsfw|18\+|not\s+clean)\b",
    re.IGNORECASE,
)
_CLEAN_VIDEO_RE = re.compile(
    r"\b(?:clean|radio\s*edit|edited|kidz\s*bop|family\s+friendly)\b",
    re.IGNORECASE,
)
_UNDESIRED_VERSION_TOKENS = (
    "live",
    "karaoke",
    "instrumental",
    "cover",
    "nightcore",
    "slowed",
    "reverb",
    "8d audio",
    "reaction",
    "parody",
    "1 hour",
    "10 hours",
    "tutorial",
    "piano version",
    "guitar lesson",
)


class UnsafeDownloadCandidateError(RuntimeError):
    """Raised when no YouTube candidate passes explicit/duration safety checks."""


@dataclass
class DownloadedAudio:
    audio_path: Path
    youtube_url: str
    youtube_title: str
    youtube_channel: str
    duration_sec: int
    thumbnail_url: str = ""


def _base_ydl_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": False,
        "noplaylist": True,
        "ignoreerrors": False,
    }
    if shutil.which("node"):
        opts["js_runtimes"] = {"node": {}}
    elif shutil.which("deno"):
        opts["js_runtimes"] = {"deno": {}}
    return opts


def score_youtube_candidate(
    entry: dict[str, Any],
    *,
    target_title: str,
    target_artist: str,
    target_duration_sec: int = 0,
    max_duration_diff_sec: int = 20,
    max_track_duration_sec: int = 600,
    require_clean_title: bool = False,
    blocked_words: Iterable[str] = (),
) -> Optional[float]:
    """Return a preference score for a YouTube search entry, or None if unsafe/invalid."""
    title = str(entry.get("title") or "").strip()
    if not title:
        return None

    age_limit = int(entry.get("age_limit") or 0)
    if age_limit >= 18:
        return None

    channel = str(entry.get("channel") or entry.get("uploader") or "").strip()
    description = str(entry.get("description") or "")
    tags = " ".join(str(t) for t in (entry.get("tags") or []))
    combined_meta = f"{title} {channel} {tags}"

    if _EXPLICIT_VIDEO_RE.search(combined_meta):
        return None
    if find_profanity_matches(f"{title} {channel}", blocked_words):
        return None
    if description and _EXPLICIT_VIDEO_RE.search(description[:600]):
        return None

    duration = int(round(float(entry.get("duration") or 0)))
    if duration <= 0 or duration > max_track_duration_sec:
        return None

    if target_duration_sec > 0 and abs(duration - target_duration_sec) > max_duration_diff_sec:
        return None

    lower_title = title.lower()
    lower_target = f"{target_title} {target_artist}".lower()

    if require_clean_title and not _CLEAN_VIDEO_RE.search(title):
        return None

    score = 100.0

    # Strongly prefer official YouTube Music auto-generated Topic channels
    if channel.endswith(" - Topic"):
        score += 35.0
    elif target_artist and target_artist.lower() in channel.lower():
        score += 20.0

    if entry.get("channel_is_verified"):
        score += 8.0

    if "official audio" in lower_title or "(audio)" in lower_title:
        score += 22.0
    elif "lyric" in lower_title:
        score += 10.0

    if target_title and target_title.lower() in lower_title:
        score += 15.0
    if target_artist and target_artist.lower() in f"{lower_title} {channel.lower()}":
        score += 12.0

    if require_clean_title and _CLEAN_VIDEO_RE.search(title):
        score += 20.0

    for token in _UNDESIRED_VERSION_TOKENS:
        if token in lower_title and token not in lower_target:
            score -= 40.0

    if target_duration_sec > 0:
        score -= abs(duration - target_duration_sec) * 0.75

    return score


def _search_candidates_sync(search_query: str, count: int = 6) -> list[dict[str, Any]]:
    opts = _base_ydl_opts()
    opts["extract_flat"] = "in_playlist"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{count}:{search_query}", download=False)
        entries = info.get("entries") or [] if isinstance(info, dict) else []
        return [e for e in entries if isinstance(e, dict)]


def _probe_video_sync(url: str) -> dict[str, Any]:
    opts = _base_ydl_opts()
    opts["extract_flat"] = False
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            raise UnsafeDownloadCandidateError("Could not inspect audio candidate")
        return info


def _download_audio_sync(url: str, staging_dir: Path) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(staging_dir / "audio.%(ext)s")
    opts = _base_ydl_opts()
    opts.update(
        {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "256",
                }
            ],
        }
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    m4a_files = sorted(staging_dir.glob("*.m4a"))
    if m4a_files:
        return m4a_files[0]

    any_audio = sorted(p for p in staging_dir.iterdir() if p.is_file())
    if any_audio:
        return any_audio[0]
    raise RuntimeError("yt-dlp completed without producing an audio file")


async def search_and_download_track(
    *,
    staging_dir: Path,
    track: Optional[CatalogTrack],
    fallback_query: str,
    source_url: Optional[str] = None,
    max_duration_diff_sec: int = 20,
    max_track_duration_sec: int = 600,
    blocked_words: Iterable[str] = (),
    allow_unverified_override: bool = False,
) -> DownloadedAudio:
    if source_url:
        probed = await asyncio.to_thread(_probe_video_sync, source_url)
        age_limit = int(probed.get("age_limit") or 0)
        duration = int(round(float(probed.get("duration") or 0)))
        title = str(probed.get("title") or fallback_query)
        channel = str(probed.get("channel") or probed.get("uploader") or "")
        if not allow_unverified_override:
            if age_limit >= 18 or _EXPLICIT_VIDEO_RE.search(title):
                raise UnsafeDownloadCandidateError("Source URL is age-restricted or marked explicit")
            if duration <= 0 or duration > max_track_duration_sec:
                raise UnsafeDownloadCandidateError("Source URL exceeds duration limits")
        audio_path = await asyncio.to_thread(_download_audio_sync, source_url, staging_dir)
        return DownloadedAudio(
            audio_path=audio_path,
            youtube_url=str(probed.get("webpage_url") or source_url),
            youtube_title=title,
            youtube_channel=channel,
            duration_sec=duration,
            thumbnail_url=str(probed.get("thumbnail") or ""),
        )

    target_title = track.title if track else fallback_query
    target_artist = track.artist if track else ""
    target_duration = track.duration_sec if track else 0
    require_clean = bool(track and track.explicit_verdict == ExplicitVerdict.CLEANED_EDIT)

    if require_clean:
        search_query = f"{target_artist} {target_title} clean radio edit audio".strip()
    elif target_artist:
        search_query = f"{target_artist} - {target_title} official audio".strip()
    else:
        search_query = f"{target_title} official audio".strip()

    flat_entries = await asyncio.to_thread(_search_candidates_sync, search_query, 6)
    if not flat_entries and require_clean:
        flat_entries = await asyncio.to_thread(
            _search_candidates_sync, f"{target_artist} {target_title} clean".strip(), 6
        )

    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in flat_entries:
        score = score_youtube_candidate(
            entry,
            target_title=target_title,
            target_artist=target_artist,
            target_duration_sec=target_duration,
            max_duration_diff_sec=max_duration_diff_sec,
            max_track_duration_sec=max_track_duration_sec,
            require_clean_title=require_clean,
            blocked_words=blocked_words,
        )
        if score is not None:
            scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    for _, cand in scored[:3]:
        cand_url = str(cand.get("url") or cand.get("webpage_url") or "")
        if not cand_url:
            continue
        if not cand_url.startswith("http"):
            cand_url = f"https://www.youtube.com/watch?v={cand_url}"
        probed = await asyncio.to_thread(_probe_video_sync, cand_url)
        verified_score = score_youtube_candidate(
            probed,
            target_title=target_title,
            target_artist=target_artist,
            target_duration_sec=target_duration,
            max_duration_diff_sec=max_duration_diff_sec,
            max_track_duration_sec=max_track_duration_sec,
            require_clean_title=require_clean,
            blocked_words=blocked_words,
        )
        if verified_score is None and not allow_unverified_override:
            continue

        audio_path = await asyncio.to_thread(_download_audio_sync, cand_url, staging_dir)
        return DownloadedAudio(
            audio_path=audio_path,
            youtube_url=str(probed.get("webpage_url") or cand_url),
            youtube_title=str(probed.get("title") or target_title),
            youtube_channel=str(probed.get("channel") or probed.get("uploader") or ""),
            duration_sec=int(round(float(probed.get("duration") or target_duration or 0))),
            thumbnail_url=str(probed.get("thumbnail") or ""),
        )

    raise UnsafeDownloadCandidateError(
        "No YouTube audio candidate matched safety and duration requirements"
    )
