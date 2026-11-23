from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Optional

import httpx

from app.models import CatalogAlbum, CatalogTrack, ExplicitVerdict
from app.services.http_client import make_async_client

logger = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
DEEZER_ALBUM_SEARCH_URL = "https://api.deezer.com/search/album"
DEEZER_ALBUM_URL = "https://api.deezer.com/album"
MUSICBRAINZ_RECORDING_URL = "https://musicbrainz.org/ws/2/recording"

_CLEAN_TITLE_STRIP_RE = re.compile(
    r"\s*[\(\[]\s*(?:clean|radio edit|edited|album version|explicit|remastered(?:\s+\d+)?|deluxe(?:\s+edition)?|feat\.?[^\)\]]*|ft\.?[^\)\]]*)\s*[\)\]]",
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

    explicitness = (item.get("trackExplicitness") or "").strip().lower()
    advisory = (item.get("contentAdvisoryRating") or "").strip().lower()
    coll_explicitness = (item.get("collectionExplicitness") or "").strip().lower()

    if explicitness == "explicit" or advisory == "explicit":
        verdict = ExplicitVerdict.EXPLICIT
    elif explicitness == "cleaned" or advisory == "clean":
        verdict = ExplicitVerdict.CLEANED_EDIT
    elif explicitness == "notexplicit":
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
        collection_id=str(item.get("collectionId") or ""),
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
        collection_explicit=(coll_explicitness == "explicit"),
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
        collection_id=str(album_obj.get("id") or ""),
        title=title,
        artist=artist,
        album_artist=artist,
        album=str(album_obj.get("title") or f"{title} - Single").strip(),
        year="",
        genre="",
        track_number=max(1, int(item.get("track_position") or 1)),
        track_count=1,
        disc_number=max(1, int(item.get("disk_number") or 1)),
        disc_count=1,
        duration_sec=max(0, int(item.get("duration") or 0)),
        explicit_verdict=verdict,
        is_explicit=(verdict == ExplicitVerdict.EXPLICIT),
        collection_explicit=bool(album_obj.get("explicit_lyrics")),
        artwork_url=artwork,
        preview_url=str(item.get("preview") or ""),
    )


def _parse_itunes_album(item: dict) -> Optional[CatalogAlbum]:
    if item.get("wrapperType") != "collection":
        return None
    title = (item.get("collectionName") or "").strip()
    artist = (item.get("artistName") or "").strip()
    album_id = str(item.get("collectionId") or "")
    if not title or not artist or not album_id:
        return None

    coll_exp = (item.get("collectionExplicitness") or "").strip().lower()
    advisory = (item.get("contentAdvisoryRating") or "").strip().lower()
    if coll_exp == "explicit" or advisory == "explicit":
        verdict = ExplicitVerdict.EXPLICIT
    elif coll_exp == "cleaned" or advisory == "clean":
        verdict = ExplicitVerdict.CLEANED_EDIT
    elif coll_exp == "notexplicit":
        verdict = ExplicitVerdict.CLEAN
    else:
        verdict = ExplicitVerdict.UNVERIFIED

    release_date = str(item.get("releaseDate") or "")
    year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""
    artwork = _upsize_itunes_artwork(str(item.get("artworkUrl100") or ""))

    return CatalogAlbum(
        provider="itunes",
        album_id=album_id,
        title=title,
        artist=artist,
        year=year,
        genre=str(item.get("primaryGenreName") or "").strip(),
        track_count=max(0, int(item.get("trackCount") or 0)),
        explicit_verdict=verdict,
        is_explicit=(verdict == ExplicitVerdict.EXPLICIT),
        artwork_url=artwork,
    )


def _parse_deezer_album(item: dict) -> Optional[CatalogAlbum]:
    title = (item.get("title") or "").strip()
    artist_obj = item.get("artist") or {}
    artist = (artist_obj.get("name") or "").strip()
    album_id = str(item.get("id") or "")
    if not title or not artist or not album_id:
        return None

    explicit_lyrics = bool(item.get("explicit_lyrics"))
    if explicit_lyrics:
        verdict = ExplicitVerdict.EXPLICIT
    elif "(clean" in title.lower():
        verdict = ExplicitVerdict.CLEANED_EDIT
    else:
        verdict = ExplicitVerdict.CLEAN

    release_date = str(item.get("release_date") or "")
    year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""
    artwork = str(
        item.get("cover_xl")
        or item.get("cover_big")
        or item.get("cover_medium")
        or ""
    )
    genres = (item.get("genres") or {}).get("data") or []
    genre = str(genres[0].get("name") or "") if genres and isinstance(genres[0], dict) else ""

    return CatalogAlbum(
        provider="deezer",
        album_id=album_id,
        title=title,
        artist=artist,
        year=year,
        genre=genre,
        track_count=max(0, int(item.get("nb_tracks") or 0)),
        explicit_verdict=verdict,
        is_explicit=(verdict == ExplicitVerdict.EXPLICIT),
        artwork_url=artwork,
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


def _attach_clean_album_alternatives(albums: list[CatalogAlbum]) -> list[CatalogAlbum]:
    clean_index: dict[tuple[str, str], CatalogAlbum] = {}
    for album in albums:
        if album.explicit_verdict in {ExplicitVerdict.CLEANED_EDIT, ExplicitVerdict.CLEAN}:
            key = (normalize_music_text(album.artist), normalize_music_text(album.title))
            existing = clean_index.get(key)
            if existing is None or (
                album.explicit_verdict == ExplicitVerdict.CLEANED_EDIT
                and existing.explicit_verdict != ExplicitVerdict.CLEANED_EDIT
            ):
                clean_index[key] = album

    for album in albums:
        if album.explicit_verdict == ExplicitVerdict.EXPLICIT:
            key = (normalize_music_text(album.artist), normalize_music_text(album.title))
            alt = clean_index.get(key)
            if alt and alt.album_id != album.album_id:
                album.clean_alternative_id = f"{alt.provider}:{alt.album_id}"
    return albums


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


def _merge_itunes_and_deezer_tracks(
    itunes_tracks: list[CatalogTrack],
    deezer_tracks: list[CatalogTrack],
    *,
    query: str = "",
    limit: int = 15,
) -> list[CatalogTrack]:
    """Cross-check iTunes explicitness with Deezer and surface explicit tracks iTunes omits."""
    deezer_explicit_keys: set[tuple[str, str]] = set()
    for dz in deezer_tracks:
        if dz.is_explicit or dz.explicit_verdict == ExplicitVerdict.EXPLICIT:
            deezer_explicit_keys.add(
                (normalize_music_text(dz.artist), normalize_music_text(dz.title))
            )

    norm_query = normalize_music_text(query)
    itunes_merged: list[CatalogTrack] = []
    seen_keys: set[tuple[str, str, bool]] = set()

    for track in itunes_tracks:
        key_pair = (normalize_music_text(track.artist), normalize_music_text(track.title))
        if (
            key_pair in deezer_explicit_keys
            and track.explicit_verdict != ExplicitVerdict.CLEANED_EDIT
            and "(clean" not in track.title.lower()
        ):
            track = track.model_copy(
                update={
                    "explicit_verdict": ExplicitVerdict.EXPLICIT,
                    "is_explicit": True,
                }
            )
        dedup_key = (key_pair[0], key_pair[1], track.is_explicit)
        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            itunes_merged.append(track)

    extra_deezer: list[CatalogTrack] = []
    for dz in deezer_tracks:
        key_pair = (normalize_music_text(dz.artist), normalize_music_text(dz.title))
        dedup_key = (key_pair[0], key_pair[1], dz.is_explicit)
        if dedup_key in seen_keys:
            continue
        if not dz.is_explicit and (key_pair[0], key_pair[1], False) in seen_keys:
            continue
        seen_keys.add(dedup_key)
        extra_deezer.append(dz)

    # Prioritize Deezer tracks whose artist/title closely matches the query when iTunes hid them
    if extra_deezer:
        def _relevance(t: CatalogTrack) -> int:
            if not norm_query:
                return 0
            na = normalize_music_text(t.artist)
            nt = normalize_music_text(t.title)
            if norm_query in na or na in norm_query:
                return 2
            if norm_query in nt or nt in norm_query:
                return 1
            return 0

        high_rel_dz = [t for t in extra_deezer if _relevance(t) >= 2]
        other_dz = [t for t in extra_deezer if _relevance(t) < 2]
        # Keep top iTunes results first, weave in up to 5 high-relevance omitted Deezer tracks
        combined = itunes_merged[: max(6, limit - min(len(high_rel_dz), 5))] + high_rel_dz[:5]
        for t in itunes_merged[max(6, limit - min(len(high_rel_dz), 5)) :] + other_dz:
            if len(combined) >= limit:
                break
            combined.append(t)
    else:
        combined = itunes_merged[:limit]

    return _attach_clean_alternatives(combined)[:limit]


async def search_catalog(
    query: str,
    *,
    limit: int = 15,
    client: Optional[httpx.AsyncClient] = None,
) -> list[CatalogTrack]:
    own_client = client is None
    if own_client:
        client = make_async_client(timeout=10.0)
    try:
        itunes_tracks, deezer_tracks = await asyncio.gather(
            search_itunes(query, limit=limit, client=client),
            search_deezer(query, limit=min(limit, 12), client=client),
        )
        if itunes_tracks and deezer_tracks:
            return _merge_itunes_and_deezer_tracks(
                itunes_tracks, deezer_tracks, query=query, limit=limit
            )
        if itunes_tracks:
            return itunes_tracks[:limit]
        return deezer_tracks[:limit]
    finally:
        if own_client:
            await client.aclose()


async def search_itunes_albums(
    query: str,
    *,
    limit: int = 12,
    client: Optional[httpx.AsyncClient] = None,
) -> list[CatalogAlbum]:
    params = {
        "term": query,
        "media": "music",
        "entity": "album",
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
        albums = []
        for item in data.get("results", []):
            parsed = _parse_itunes_album(item)
            if parsed:
                albums.append(parsed)
        return albums
    except Exception as exc:
        logger.warning("iTunes album search failed for %r: %s", query, exc)
        return []
    finally:
        if own_client:
            await client.aclose()


async def search_deezer_albums(
    query: str,
    *,
    limit: int = 10,
    client: Optional[httpx.AsyncClient] = None,
) -> list[CatalogAlbum]:
    own_client = client is None
    if own_client:
        client = make_async_client(timeout=10.0)
    try:
        resp = await client.get(DEEZER_ALBUM_SEARCH_URL, params={"q": query, "limit": limit})
        resp.raise_for_status()
        data = resp.json()
        albums = []
        for item in data.get("data", []):
            parsed = _parse_deezer_album(item)
            if parsed:
                albums.append(parsed)
        return albums
    except Exception as exc:
        logger.warning("Deezer album search failed for %r: %s", query, exc)
        return []
    finally:
        if own_client:
            await client.aclose()


async def search_albums(
    query: str,
    *,
    limit: int = 12,
    client: Optional[httpx.AsyncClient] = None,
) -> list[CatalogAlbum]:
    own_client = client is None
    if own_client:
        client = make_async_client(timeout=10.0)
    try:
        itunes_albums, deezer_albums = await asyncio.gather(
            search_itunes_albums(query, limit=limit, client=client),
            search_deezer_albums(query, limit=min(limit, 10), client=client),
        )
        dz_explicit_keys = {
            (normalize_music_text(a.artist), normalize_music_text(a.title))
            for a in deezer_albums
            if a.is_explicit
        }

        merged: list[CatalogAlbum] = []
        seen: set[tuple[str, str, bool]] = set()
        for album in itunes_albums:
            key = (normalize_music_text(album.artist), normalize_music_text(album.title))
            if (
                key in dz_explicit_keys
                and album.explicit_verdict != ExplicitVerdict.CLEANED_EDIT
                and "(clean" not in album.title.lower()
            ):
                album = album.model_copy(
                    update={"explicit_verdict": ExplicitVerdict.EXPLICIT, "is_explicit": True}
                )
            dedup = (key[0], key[1], album.is_explicit)
            if dedup not in seen:
                seen.add(dedup)
                merged.append(album)

        for album in deezer_albums:
            key = (normalize_music_text(album.artist), normalize_music_text(album.title))
            dedup = (key[0], key[1], album.is_explicit)
            if dedup not in seen:
                seen.add(dedup)
                merged.append(album)

        # Prefer real multi-track albums over 1-track singles while preserving relevance order
        merged.sort(key=lambda a: (0 if a.track_count >= 2 else 1))
        return _attach_clean_album_alternatives(merged)[:limit]
    finally:
        if own_client:
            await client.aclose()


async def get_album_tracks(
    provider: str,
    album_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[tuple[CatalogAlbum, list[CatalogTrack]]]:
    """Fetch complete album metadata and ordered track list from iTunes or Deezer."""
    own_client = client is None
    if own_client:
        client = make_async_client(timeout=12.0)
    try:
        if provider == "itunes":
            resp = await client.get(
                ITUNES_LOOKUP_URL,
                params={"id": album_id, "entity": "song", "country": "US"},
            )
            resp.raise_for_status()
            items = resp.json().get("results") or []
            album: Optional[CatalogAlbum] = None
            tracks: list[CatalogTrack] = []
            for item in items:
                if item.get("wrapperType") == "collection" and album is None:
                    album = _parse_itunes_album(item)
                else:
                    parsed = _parse_itunes_item(item)
                    if parsed:
                        tracks.append(parsed)
            if album is None:
                return None
            tracks.sort(key=lambda t: (t.disc_number, t.track_number))
            if tracks:
                total_tracks = len(tracks)
                max_disc = max(t.disc_number for t in tracks)
                for idx, t in enumerate(tracks, start=1):
                    if t.track_count < total_tracks and max_disc == 1:
                        t.track_count = total_tracks
                    if not t.artwork_url and album.artwork_url:
                        t.artwork_url = album.artwork_url
                    if not t.album_artist:
                        t.album_artist = album.artist
                if album.track_count == 0:
                    album.track_count = total_tracks
            return album, _attach_clean_alternatives(tracks)

        if provider == "deezer":
            resp = await client.get(f"{DEEZER_ALBUM_URL}/{album_id}")
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or data.get("error"):
                return None
            album = _parse_deezer_album(data)
            if album is None:
                return None
            raw_tracks = (data.get("tracks") or {}).get("data") or []
            tracks = []
            total_tracks = len(raw_tracks)
            for idx, item in enumerate(raw_tracks, start=1):
                enriched = {
                    **item,
                    "album": {
                        "id": data.get("id"),
                        "title": data.get("title"),
                        "cover_xl": data.get("cover_xl"),
                        "cover_big": data.get("cover_big"),
                        "cover_medium": data.get("cover_medium"),
                        "explicit_lyrics": data.get("explicit_lyrics"),
                    },
                }
                parsed = _parse_deezer_item(enriched)
                if parsed:
                    parsed.album_artist = album.artist
                    parsed.year = album.year
                    parsed.genre = album.genre
                    parsed.track_number = max(1, int(item.get("track_position") or idx))
                    parsed.track_count = max(total_tracks, parsed.track_number)
                    parsed.disc_number = max(1, int(item.get("disk_number") or 1))
                    if album.is_explicit:
                        parsed.collection_explicit = True
                    tracks.append(parsed)
            tracks.sort(key=lambda t: (t.disc_number, t.track_number))
            if tracks:
                max_disc = max(t.disc_number for t in tracks)
                for t in tracks:
                    t.disc_count = max(t.disc_count, max_disc)
            return album, _attach_clean_alternatives(tracks)

        return None
    except Exception as exc:
        logger.warning("Album lookup failed for %s/%s: %s", provider, album_id, exc)
        return None
    finally:
        if own_client:
            await client.aclose()


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
            headers={"User-Agent": "KidTune/1.1 (https://github.com/TBKarnage/KidTune)"},
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
