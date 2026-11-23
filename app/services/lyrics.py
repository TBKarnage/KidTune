from __future__ import annotations

import asyncio
import difflib
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from app.models import CatalogTrack, ExplicitVerdict
from app.services.catalog import normalize_music_text
from app.services.http_client import make_async_client

logger = logging.getLogger(__name__)

LRCLIB_GET_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"

_CACHE_TTL_SECONDS = 1800.0
_CACHE_MAX_SIZE = 500
_LYRICS_CACHE: dict[tuple[str, str], tuple[float, Optional["LyricsResult"]]] = {}


@dataclass
class LyricsResult:
    plain_lyrics: str = ""
    synced_lyrics: str = ""
    source: str = "lrclib"


def _cache_key(artist: str, title: str) -> tuple[str, str]:
    return (normalize_music_text(artist), normalize_music_text(title))


def _get_cached_lyrics(artist: str, title: str) -> tuple[bool, Optional[LyricsResult]]:
    key = _cache_key(artist, title)
    entry = _LYRICS_CACHE.get(key)
    if entry is None:
        return False, None
    ts, val = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        _LYRICS_CACHE.pop(key, None)
        return False, None
    return True, val


def _set_cached_lyrics(artist: str, title: str, result: Optional[LyricsResult]) -> None:
    if len(_LYRICS_CACHE) >= _CACHE_MAX_SIZE:
        # Evict oldest entry
        oldest_key = min(_LYRICS_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _LYRICS_CACHE.pop(oldest_key, None)
    _LYRICS_CACHE[_cache_key(artist, title)] = (time.monotonic(), result)


async def fetch_lyrics(
    *,
    artist: str,
    title: str,
    album: str = "",
    duration_sec: int = 0,
    client: Optional[httpx.AsyncClient] = None,
    use_cache: bool = True,
) -> Optional[LyricsResult]:
    if not artist or not title:
        return None

    if use_cache:
        hit, cached = _get_cached_lyrics(artist, title)
        if hit:
            return cached

    own_client = client is None
    if own_client:
        client = make_async_client(
            timeout=6.0,
            headers={"User-Agent": "KidTune/1.1 (https://github.com/TBKarnage/KidTune)"},
        )
    result: Optional[LyricsResult] = None
    try:
        params: dict[str, str | int] = {
            "artist_name": artist,
            "track_name": title,
        }
        if album:
            params["album_name"] = album
        if duration_sec > 0:
            params["duration"] = duration_sec

        resp = await client.get(LRCLIB_GET_URL, params=params)
        if resp.status_code != 200 and (album or duration_sec > 0):
            resp = await client.get(
                LRCLIB_GET_URL,
                params={"artist_name": artist, "track_name": title},
            )
        if resp.status_code == 200:
            data = resp.json()
            plain = (data.get("plainLyrics") or "").strip()
            synced = (data.get("syncedLyrics") or "").strip()
            if plain or synced:
                result = LyricsResult(plain_lyrics=plain, synced_lyrics=synced)
                if use_cache:
                    _set_cached_lyrics(artist, title, result)
                return result

        # Fallback to fuzzy search endpoint
        search_resp = await client.get(
            LRCLIB_SEARCH_URL,
            params={"q": f"{artist} {title}"},
        )
        if search_resp.status_code != 200:
            if use_cache:
                _set_cached_lyrics(artist, title, None)
            return None
        items = search_resp.json()
        if not isinstance(items, list) or not items:
            if use_cache:
                _set_cached_lyrics(artist, title, None)
            return None

        target_artist = normalize_music_text(artist)
        target_title = normalize_music_text(title)
        best_item = None
        best_score = -1.0

        for item in items[:10]:
            item_artist = normalize_music_text(str(item.get("artistName") or ""))
            item_title = normalize_music_text(str(item.get("trackName") or ""))
            a_sim = difflib.SequenceMatcher(None, target_artist, item_artist).ratio()
            t_sim = difflib.SequenceMatcher(None, target_title, item_title).ratio()
            if a_sim < 0.72 or t_sim < 0.75:
                continue
            item_dur = int(round(float(item.get("duration") or 0)))
            if duration_sec > 0 and item_dur > 0 and abs(item_dur - duration_sec) > 12:
                continue
            score = a_sim + t_sim + (0.2 if item.get("syncedLyrics") else 0.0)
            if score > best_score:
                best_score = score
                best_item = item

        if best_item:
            plain = (best_item.get("plainLyrics") or "").strip()
            synced = (best_item.get("syncedLyrics") or "").strip()
            if plain or synced:
                result = LyricsResult(plain_lyrics=plain, synced_lyrics=synced)
        if use_cache:
            _set_cached_lyrics(artist, title, result)
        return result
    except Exception as exc:
        logger.debug("LRCLIB lookup failed for %r - %r: %s", artist, title, exc)
        return None
    finally:
        if own_client:
            await client.aclose()


async def fetch_lyrics_batch(
    tracks: list[CatalogTrack],
    *,
    timeout: float = 4.0,
    concurrency: int = 6,
) -> dict[str, Optional[LyricsResult]]:
    """Concurrently fetch lyrics for non-explicit catalog tracks with bounded latency."""
    targets = [
        t for t in tracks
        if t.artist and t.title and t.explicit_verdict != ExplicitVerdict.EXPLICIT and not t.is_explicit
    ]
    if not targets:
        return {}

    results: dict[str, Optional[LyricsResult]] = {}
    sem = asyncio.Semaphore(concurrency)

    async with make_async_client(
        timeout=timeout,
        headers={"User-Agent": "KidTune/1.1 (https://github.com/TBKarnage/KidTune)"},
    ) as client:
        async def _one(track: CatalogTrack) -> None:
            key = track.track_id or f"{track.artist}::{track.title}"
            async with sem:
                try:
                    res = await asyncio.wait_for(
                        fetch_lyrics(
                            artist=track.artist,
                            title=track.title,
                            album=track.album,
                            duration_sec=track.duration_sec,
                            client=client,
                        ),
                        timeout=timeout,
                    )
                    results[key] = res
                except Exception:
                    results[key] = None

        await asyncio.gather(*(_one(t) for t in targets), return_exceptions=True)

    return results
