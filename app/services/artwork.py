from __future__ import annotations

import hashlib
import io
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from app.models import ExplicitVerdict, LibraryCoverItem, MusicRequestRecord, RequestStatus
from app.services.http_client import make_async_client

logger = logging.getLogger(__name__)

_ALLOWED_HOST_SUFFIXES = (
    "mzstatic.com",
    "dzcdn.net",
    "deezer.com",
    "ytimg.com",
    "ggpht.com",
    "googleusercontent.com",
    "coverartarchive.org",
    "archive.org",
    "scdn.co",
)

_ITUNES_SIZE_RE = re.compile(r"/\d+x\d+bb\.(jpg|png)$", re.IGNORECASE)
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def is_allowed_artwork_url(url: str) -> bool:
    if not url or len(url) > 2048:
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_HOST_SUFFIXES)


def _candidate_artwork_urls(url: str) -> list[str]:
    urls = [url]
    match = _ITUNES_SIZE_RE.search(url)
    if match:
        ext = match.group(1)
        for dim in ("600x600", "300x300", "100x100"):
            cand = _ITUNES_SIZE_RE.sub(f"/{dim}bb.{ext}", url)
            if cand not in urls:
                urls.append(cand)
    return urls


def _validate_and_normalize_image(raw: bytes) -> Optional[tuple[bytes, str, str]]:
    if not raw or len(raw) > _MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as img:
            fmt = (img.format or "").upper()
            if fmt == "PNG":
                return raw, "image/png", "png"
            if fmt in {"JPEG", "JPG"}:
                return raw, "image/jpeg", "jpg"
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=90)
            return buf.getvalue(), "image/jpeg", "jpg"
    except Exception:
        return None


async def fetch_and_cache_artwork(
    url: str,
    cache_dir: Path,
) -> Optional[tuple[bytes, str]]:
    """Fetch remote album art from allowlisted hosts with local disk caching."""
    cleaned = (url or "").strip()
    if not is_allowed_artwork_url(cleaned):
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:24]

    for ext, mime in (("jpg", "image/jpeg"), ("png", "image/png")):
        cached_path = cache_dir / f"{digest}.{ext}"
        if cached_path.is_file():
            try:
                data = cached_path.read_bytes()
                if data:
                    return data, mime
            except OSError:
                pass

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KidTune/1.1; +https://github.com/TBKarnage/KidTune)",
        "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
    }
    async with make_async_client(timeout=10.0, follow_redirects=True, headers=headers) as client:
        for cand_url in _candidate_artwork_urls(cleaned):
            try:
                resp = await client.get(cand_url)
                if resp.status_code != 200 or not resp.content:
                    continue
                normalized = _validate_and_normalize_image(resp.content)
                if normalized is None:
                    continue
                img_bytes, mime, ext = normalized
                try:
                    (cache_dir / f"{digest}.{ext}").write_bytes(img_bytes)
                except OSError:
                    pass
                return img_bytes, mime
            except Exception as exc:
                logger.debug("Artwork proxy failed for %s: %s", cand_url, exc)
    return None


def extract_local_cover(file_path: str) -> Optional[tuple[bytes, str]]:
    """Read local cover.jpg/cover.png next to a track, or embedded MP4 cover art."""
    if not file_path:
        return None
    audio = Path(file_path)
    album_dir = audio.parent

    for name, mime in (("cover.jpg", "image/jpeg"), ("cover.jpeg", "image/jpeg"), ("cover.png", "image/png")):
        cover = album_dir / name
        if cover.is_file():
            try:
                data = cover.read_bytes()
                if data:
                    return data, mime
            except OSError:
                pass

    if audio.is_file():
        try:
            mp4 = MP4(str(audio))
            covr = (mp4.tags or {}).get("covr")
            if covr:
                first = covr[0]
                mime = "image/png" if getattr(first, "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
                return bytes(first), mime
        except Exception:
            pass
    return None


def _cover_hash_for_relpath(rel_path: str) -> str:
    return hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:20]


def list_library_covers(
    *,
    music_dir: Path,
    requests: list[MusicRequestRecord],
    limit: int = 36,
) -> list[LibraryCoverItem]:
    """Build a deduplicated list of family-safe downloaded album covers for the ambient UI background."""
    covers: list[LibraryCoverItem] = []
    seen_keys: set[str] = set()
    explicit_album_dirs: set[str] = set()

    for rec in requests:
        if rec.explicit_verdict == ExplicitVerdict.EXPLICIT and rec.file_path:
            try:
                explicit_album_dirs.add(str(Path(rec.file_path).parent.resolve()))
            except OSError:
                pass

    # 1. Completed non-explicit requests from DB (works even if cover is only cached remotely)
    for rec in requests:
        if rec.status != RequestStatus.COMPLETED:
            continue
        if rec.explicit_verdict == ExplicitVerdict.EXPLICIT:
            continue
        if not rec.artwork_url and not (rec.file_path and Path(rec.file_path).parent.exists()):
            continue

        album_key = (
            f"{(rec.album_artist or rec.artist).strip().lower()}::{(rec.album or rec.title).strip().lower()}"
        )
        if album_key in seen_keys:
            continue
        seen_keys.add(album_key)
        covers.append(
            LibraryCoverItem(
                id=f"req-{rec.id}",
                title=rec.title or rec.query,
                artist=rec.artist,
                album=rec.album,
                url=f"/api/requests/{rec.id}/artwork",
            )
        )
        if len(covers) >= limit:
            return covers

    # 2. Scan music_dir for existing cover.jpg / cover.png files (persists even after request list cleanup!)
    if music_dir.is_dir():
        try:
            cover_files = sorted(
                (p for p in music_dir.rglob("cover.*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            cover_files = []

        for cover_path in cover_files:
            try:
                resolved_parent = str(cover_path.parent.resolve())
                if resolved_parent in explicit_album_dirs:
                    continue
                rel = cover_path.relative_to(music_dir).as_posix()
            except (OSError, ValueError):
                continue

            parts = cover_path.relative_to(music_dir).parts
            artist_name = parts[0] if len(parts) >= 3 else ""
            album_name = parts[1] if len(parts) >= 3 else cover_path.parent.name
            album_key = f"{artist_name.strip().lower()}::{album_name.strip().lower()}"
            if album_key in seen_keys:
                continue
            seen_keys.add(album_key)
            cover_id = _cover_hash_for_relpath(rel)
            covers.append(
                LibraryCoverItem(
                    id=f"fs-{cover_id}",
                    title=album_name,
                    artist=artist_name,
                    album=album_name,
                    url=f"/api/library/cover/{cover_id}",
                )
            )
            if len(covers) >= limit:
                break

    return covers


def resolve_library_cover_path(music_dir: Path, cover_hash: str) -> Optional[Path]:
    if not music_dir.is_dir() or not re.fullmatch(r"[a-f0-9]{20}", cover_hash or ""):
        return None
    for cover_path in music_dir.rglob("cover.*"):
        if cover_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        try:
            rel = cover_path.relative_to(music_dir).as_posix()
        except ValueError:
            continue
        if _cover_hash_for_relpath(rel) == cover_hash and cover_path.is_file():
            return cover_path
    return None
