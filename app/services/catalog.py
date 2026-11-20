from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Optional

import httpx

from app.models import CatalogTrack, ExplicitVerdict
from app.services.http_client import make_async_client

logger = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
MUSICBRAINZ_RECORDING_URL = "https://musicbrainz.org/ws/2/recording"

_CLEAN_TITLE_STRIP_RE = re.compile(
    r"\s*[\(\[]\s*(?:clean|radio edit|edited|album version|explicit|remastered(?:\s+\d+)?|deluxe|feat\.?[^\)\]]*|ft\.?[^\)\]]*)\s*[\)\]]",
    re.IGNORECASE,
)
_VOICE_FILLER_RE = re.compile(
    r"^(?:please\s+)?(?:add|download|request|get|play|find)\s+(?:the\s+song\s+)?",
    re.IGNORECASE,
)
_BY_SPLIT_RE = re.compile(r"\s+by\s+", re.IGNORECASE)
_MB_RATE_LOCK = asyncio.Lock()
_LAST_MB_CALL = 0.0


def normalize_music_text(text: str) -> str:
    cleaned = _CLEAN_TITLE_STRIP_RE.sub("", text or "")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned.lower())
    return " ".join(cleaned.split())


def parse_song_query(raw_query: str) -> tuple[str, str]:
    """Split queries like 'Shake It Off by Taylor Swift' or 'Taylor Swift - Shake It Off'."""
    text = raw_query.strip()
    text = _VOICE_FILLER_RE.sub("", text).strip()
    text = re.sub(r"\s+to\s+(?:my\s+|the\s+)?music\s+requests?(?:\s+list)?$", "", text, flags=re.I).strip()

    by_parts = _BY_SPLIT_RE.split(text, maxsplit=1)
    if len(by_parts) == 2 and by_parts[0].strip() and by_parts[1].strip():
        return by_parts[0].strip(), by_parts[1].strip()

    if " - " in text:
        artist_part, title_part = text.split(" - ", 1)
        if artist_part.strip() and title_part.strip():
            return title_part.strip(), artist_part.strip()

    return text, ""


def _upsize_itunes_artwork(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/1000x1000bb.\1", url)


def _parse_itunes_item(item: dict) -> Optional[CatalogTrack]:
    if item.get("wrapperType") != "track" or item.get("kind") != "song":
        return None
    title = (item.get("trackName") or "").strip()
    artist = (item.get("artistName") or "").strip()
    if not title or not artist:
        return None

    explicitness = (item.get("trackExplicitness") or "").strip()
    if explicitness == "explicit":
        verdict = ExplicitVerdict.EXPLICIT
    elif explicitness == "cleaned":
        verdict = ExplicitVerdict.CLEANED_EDIT
    elif explicitness == "notExplicit":
        verdict = ExplicitVerdict.CLEAN
    else:
        verdict = ExplicitVerdict.UNVERIFIED

    release_date = str(item.get("releaseDate") or "")
    year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""
    duration_ms = int(item.get("trackTimeMillis") or 0)
    artwork = _upsize_itunes_artwork(str(item.get("artworkUrl100") or ""))

    return CatalogTrack(
        provider="itunes",
        track_id=str(item.get("trackId") or ""),
        title=title,
        artist=artist,
        album_artist=str(item.get("collectionArtistName") or artist).strip(),
        album=str(item.get("collectionName") or f"{title} - Single").strip(),
        year=year,
        genre=str(item.get("primaryGenreName") or "").strip(),
        track_number=max(1, int(item.get("trackNumber") or 1)),
        track_count=max(1, int(item.get("trackCount") or 1)),
        disc_number=max(1, int(item.get("discNumber") or 1)),
        disc_count=max(1, int(item.get("discCount") or 1)),
        duration_sec=max(0, round(duration_ms / 1000)) if duration_ms else 0,
        explicit_verdict=verdict,
        is_explicit=(verdict == ExplicitVerdict.EXPLICIT),
        artwork_url=artwork,
        preview_url=str(item.get("previewUrl") or ""),
    )


def _parse_deezer_item(item: dict) -> Optional[CatalogTrack]:
    title = (item.get("title") or "").strip()
    artist_obj = item.get("artist") or {}
    album_obj = item.get("album") or {}
    artist = (artist_obj.get("name") or "").strip()
    if not title or not artist:
        return None

    explicit_lyrics = bool(item.get("explicit_lyrics"))
    explicit_code = item.get("explicit_content_lyrics")
    if explicit_lyrics or explicit_code == 1:
        verdict = ExplicitVerdict.EXPLICIT
    elif explicit_code == 4 or "(clean" in title.lower():
        verdict = ExplicitVerdict.CLEANED_EDIT
    elif explicit_code in {0, 2, 3, 6}:
        verdict = ExplicitVerdict.CLEAN
    else:
        verdict = ExplicitVerdict.UNVERIFIED

    artwork = str(
        album_obj.get("cover_xl")
        or album_obj.get("cover_big")
        or album_obj.get("cover_medium")
        or ""
    )

    return CatalogTrack(
        provider="deezer",
        track_id=str(item.get("id") or ""),
        title=title,
        artist=artist,
        album_artist=artist,
        album=str(album_obj.get("title") or f"{title} - Single").strip(),
        year="",
        genre="",
        track_number=1,
        track_count=1,
        disc_number=1,
        disc_count=1,
        duration_sec=max(0, int(item.get("duration") or 0)),
        explicit_verdict=verdict,
        is_explicit=(verdict == ExplicitVerdict.EXPLICIT),
        artwork_url=artwork,
        preview_url=str(item.get("preview") or ""),
    )


def _attach_clean_alternatives(tracks: list[CatalogTrack]) -> list[CatalogTrack]:
    clean_index: dict[tuple[str, str], CatalogTrack] = {}
    for track in tracks:
        if track.explicit_verdict in {ExplicitVerdict.CLEANED_EDIT, ExplicitVerdict.CLEAN}:
            key = (normalize_music_text(track.artist), normalize_music_text(track.title))
            existing = clean_index.get(key)
            if existing is None or (
                track.explicit_verdict == ExplicitVerdict.CLEANED_EDIT
                and existing.explicit_verdict != ExplicitVerdict.CLEANED_EDIT
            ):
                clean_index[key] = track

    for track in tracks:
        if track.explicit_verdict == ExplicitVerdict.EXPLICIT:
            key = (normalize_music_text(track.artist), normalize_music_text(track.title))
            alt = clean_index.get(key)
            if alt and alt.track_id != track.track_id:
                track.clean_alternative = alt.model_copy(deep=True)
    return tracks


async def search_itunes(
    query: str,
    *,
    limit: int = 15,
    client: Optional[httpx.AsyncClient] = None,
) -> list[CatalogTrack]:
    params = {
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": limit,
        "country": "US",
    }
    own_client = client is None
    if own_client:
        client = make_async_client(timeout=10.0)
    try:
        resp = await client.get(ITUNES_SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        tracks = []
        for item in data.get("results", []):
            parsed = _parse_itunes_item(item)
            if parsed:
                tracks.append(parsed)
        return _attach_clean_alternatives(tracks)
    except Exception as exc:
        logger.warning("iTunes search failed for %r: %s", query, exc)
        return []
    finally:
        if own_client:
            await client.aclose()


async def search_deezer(
    query: str,
    *,
    limit: int = 10,
    client: Optional[httpx.AsyncClient] = None,
) -> list[CatalogTrack]:
    own_client = client is None
    if own_client:
        client = make_async_client(timeout=10.0)
    try:
        resp = await client.get(DEEZER_SEARCH_URL, params={"q": query, "limit": limit})
        resp.raise_for_status()
        data = resp.json()
        tracks = []
        for item in data.get("data", []):
            parsed = _parse_deezer_item(item)
            if parsed:
                tracks.append(parsed)
        return _attach_clean_alternatives(tracks)
    except Exception as exc:
        logger.warning("Deezer search failed for %r: %s", query, exc)
        return []
    finally:
        if own_client:
            await client.aclose()


async def search_catalog(
    query: str,
    *,
    limit: int = 15,
    client: Optional[httpx.AsyncClient] = None,
) -> list[CatalogTrack]:
    tracks = await search_itunes(query, limit=limit, client=client)
    if tracks:
        return tracks
    return await search_deezer(query, limit=min(limit, 10), client=client)


async def find_clean_alternative(
    artist: str,
    title: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[CatalogTrack]:
    target_artist = normalize_music_text(artist)
    target_title = normalize_music_text(title)
    if not target_title:
        return None

    candidates = await search_itunes(f"{artist} {title} clean", limit=15, client=client)
    if not candidates:
        candidates = await search_itunes(f"{artist} {title}", limit=20, client=client)

    best: Optional[CatalogTrack] = None
    best_score = -1.0
    for cand in candidates:
        if cand.explicit_verdict not in {ExplicitVerdict.CLEANED_EDIT, ExplicitVerdict.CLEAN}:
            continue
        artist_sim = difflib.SequenceMatcher(
            None, target_artist, normalize_music_text(cand.artist)
        ).ratio()
        title_sim = difflib.SequenceMatcher(
            None, target_title, normalize_music_text(cand.title)
        ).ratio()
        if artist_sim < 0.72 or title_sim < 0.75:
            continue
        bonus = 0.25 if cand.explicit_verdict == ExplicitVerdict.CLEANED_EDIT else 0.0
        score = artist_sim + title_sim + bonus
        if score > best_score:
            best_score = score
            best = cand
    return best


def _score_candidate(title_q: str, artist_q: str, cand: CatalogTrack) -> float:
    cand_title = normalize_music_text(cand.title)
    cand_artist = normalize_music_text(cand.artist)
    norm_title = normalize_music_text(title_q)
    norm_artist = normalize_music_text(artist_q)

    if norm_artist:
        title_sim = difflib.SequenceMatcher(None, norm_title, cand_title).ratio()
        artist_sim = difflib.SequenceMatcher(None, norm_artist, cand_artist).ratio()
        score = 0.55 * title_sim + 0.45 * artist_sim
    else:
        combined = f"{cand_artist} {cand_title}"
        title_sim = difflib.SequenceMatcher(None, norm_title, cand_title).ratio()
        combined_sim = difflib.SequenceMatcher(None, norm_title, combined).ratio()
        score = max(title_sim * 0.95, combined_sim)

    if cand.explicit_verdict == ExplicitVerdict.CLEANED_EDIT:
        score += 0.02
    elif cand.explicit_verdict == ExplicitVerdict.CLEAN:
        score += 0.03
    return score


async def match_text_request(
    raw_query: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[Optional[CatalogTrack], list[CatalogTrack]]:
    """Match a natural-language text or voice query to the best catalog track."""
    title_q, artist_q = parse_song_query(raw_query)
    search_term = f"{title_q} {artist_q}".strip() if artist_q else title_q
    candidates = await search_catalog(search_term, limit=15, client=client)
    if not candidates:
        return None, []

    ranked = sorted(
        candidates,
        key=lambda c: _score_candidate(title_q, artist_q, c),
        reverse=True,
    )
    best = ranked[0]
    best_score = _score_candidate(title_q, artist_q, best)

    # Require reasonable confidence before auto-linking a text query
    threshold = 0.68 if artist_q else 0.72
    if best_score < threshold:
        return None, ranked

    if best.explicit_verdict == ExplicitVerdict.EXPLICIT and best.clean_alternative is None:
        clean_alt = await find_clean_alternative(best.artist, best.title, client=client)
        if clean_alt:
            best.clean_alternative = clean_alt

    return best, ranked


async def enrich_with_musicbrainz(
    track: CatalogTrack,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> CatalogTrack:
    """Best-effort MusicBrainz ID lookup for Plex agent compatibility."""
    global _LAST_MB_CALL
    if track.musicbrainz_recording_id:
        return track

    own_client = client is None
    if own_client:
        client = make_async_client(
            timeout=6.0,
            headers={"User-Agent": "KidTune/1.0 (https://github.com/local/kidtune)"},
        )
    try:
        async with _MB_RATE_LOCK:
            now = asyncio.get_running_loop().time()
            wait = 1.05 - (now - _LAST_MB_CALL)
            if wait > 0:
                await asyncio.sleep(wait)
            query = f'recording:"{track.title}" AND artist:"{track.artist}"'
            resp = await client.get(
                MUSICBRAINZ_RECORDING_URL,
                params={"query": query, "fmt": "json", "limit": 3},
            )
            _LAST_MB_CALL = asyncio.get_running_loop().time()
        if resp.status_code != 200:
            return track
        recordings = resp.json().get("recordings") or []
        if not recordings:
            return track
        top = recordings[0]
        if int(top.get("score") or 0) < 85:
            return track
        updated = track.model_copy(deep=True)
        updated.musicbrainz_recording_id = str(top.get("id") or "")
        releases = top.get("releases") or []
        if releases:
            updated.musicbrainz_release_id = str(releases[0].get("id") or "")
        return updated
    except Exception as exc:
        logger.debug("MusicBrainz enrichment skipped for %r: %s", track.title, exc)
        return track
    finally:
        if own_client:
            await client.aclose()
