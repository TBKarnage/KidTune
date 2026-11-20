from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.services.catalog import normalize_music_text
from app.services.http_client import make_async_client

logger = logging.getLogger(__name__)

LRCLIB_GET_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"


@dataclass
class LyricsResult:
    plain_lyrics: str = ""
    synced_lyrics: str = ""
    source: str = "lrclib"


async def fetch_lyrics(
    *,
    artist: str,
    title: str,
    album: str = "",
    duration_sec: int = 0,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[LyricsResult]:
    if not artist or not title:
        return None

    own_client = client is None
    if own_client:
        client = make_async_client(
            timeout=8.0,
            headers={"User-Agent": "KidTune/1.0 (https://github.com/local/kidtune)"},
        )
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
        if resp.status_code == 200:
            data = resp.json()
            plain = (data.get("plainLyrics") or "").strip()
            synced = (data.get("syncedLyrics") or "").strip()
            if plain or synced:
                return LyricsResult(plain_lyrics=plain, synced_lyrics=synced)

        # Fallback to fuzzy search endpoint
        search_resp = await client.get(
            LRCLIB_SEARCH_URL,
            params={"q": f"{artist} {title}"},
        )
        if search_resp.status_code != 200:
            return None
        items = search_resp.json()
        if not isinstance(items, list) or not items:
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
                return LyricsResult(plain_lyrics=plain, synced_lyrics=synced)
        return None
    except Exception as exc:
        logger.debug("LRCLIB lookup failed for %r - %r: %s", artist, title, exc)
        return None
    finally:
        if own_client:
            await client.aclose()
