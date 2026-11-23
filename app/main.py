from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    create_session_token,
    hash_pin,
    make_parent_auth_dependency,
    verify_pin,
    verify_session_token,
)
from app.config import Settings, get_settings, resolve_public_base_url
from app.database import Database
from app.models import (
    AlbumDetailsResult,
    AlbumRequestCreate,
    AlbumRequestResponse,
    ApproveRequestPayload,
    CastAnnounceRequest,
    DeclineRequestPayload,
    ExplicitVerdict,
    LibraryCoverItem,
    MusicRequestCreate,
    MusicRequestRecord,
    ParentLoginRequest,
    PinChangeRequest,
    RequestCleanupPayload,
    RequestCleanupResponse,
    RequestSource,
    RequestStatus,
    RuntimeSettingsUpdate,
    RuntimeSettingsView,
    SearchAlbumResult,
    SearchTrackResult,
)
from app.services.artwork import (
    extract_local_cover,
    fetch_and_cache_artwork,
    is_allowed_artwork_url,
    list_library_covers,
    resolve_library_cover_path,
)
from app.services.catalog import (
    find_clean_alternative,
    get_album_tracks,
    search_albums,
    search_catalog,
)
from app.services.google_cast import announce_on_google_home, discover_cast_devices
from app.services.google_keep import update_keep_item_status
from app.services.lyrics import fetch_lyrics_batch
from app.services.plex import discover_music_sections, trigger_library_scan
from app.services.request_pipeline import submit_album_request, submit_music_request
from app.services.safety import assess_album, assess_request
from app.services.worker import BackgroundWorker

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
FALLBACK_ICON_PATH = STATIC_DIR / "icons" / "icon-192.png"
_TTS_FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.mp3$")
_FINISHED_STATUSES = {
    RequestStatus.COMPLETED,
    RequestStatus.BLOCKED,
    RequestStatus.DECLINED,
    RequestStatus.FAILED,
}


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    cfg = settings or get_settings()
    cfg.ensure_directories()
    session_secret = cfg.resolve_session_secret()

    db = Database(cfg.db_path)
    db.init_db(cfg)
    worker = BackgroundWorker(db=db, settings=cfg)
    require_parent = make_parent_auth_dependency(session_secret)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if cfg.enable_background_worker:
            worker.start()
        try:
            yield
        finally:
            await worker.stop()

    app = FastAPI(
        title="KidTune",
        version=__version__,
        description="Family-safe music request portal for Plex & Plexamp",
        lifespan=lifespan,
    )
    app.state.db = db
    app.state.settings = cfg
    app.state.worker = worker
    app.state.session_secret = session_secret

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _is_parent_authenticated(request: Request) -> bool:
        token = request.headers.get("x-parent-token") or request.cookies.get(
            SESSION_COOKIE_NAME
        )
        return verify_session_token(token, session_secret)

    @app.get("/", include_in_schema=False)
    async def serve_index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def serve_manifest() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    async def serve_sw() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request) -> dict:
        runtime = db.get_settings_view()
        pending = db.list_requests(
            status=RequestStatus.PENDING_APPROVAL,
            exclude_source=RequestSource.PARENT,
            limit=200,
        )
        is_parent = _is_parent_authenticated(request)
        parent_active = 0
        if is_parent:
            parent_recs = db.list_requests(source=RequestSource.PARENT, limit=200)
            parent_active = sum(
                1
                for r in parent_recs
                if r.status in {RequestStatus.QUEUED, RequestStatus.DOWNLOADING, RequestStatus.TAGGING}
            )
        return {
            "app_name": cfg.app_name,
            "version": __version__,
            "kid_profiles": runtime.kid_profiles,
            "policy_mode": runtime.policy_mode.value,
            "google_keep_enabled": runtime.google_keep_enabled,
            "google_keep_list_name": runtime.google_keep_list_name,
            "google_cast_enabled": runtime.google_cast_enabled,
            "parent_authenticated": is_parent,
            "parent_pin_is_default": runtime.parent_pin_is_default if is_parent else False,
            "pending_approval_count": len(pending),
            "parent_active_downloads_count": parent_active,
        }

    @app.get("/api/artwork", include_in_schema=False)
    async def proxy_artwork(url: str = Query(..., min_length=8, max_length=2048)) -> Response:
        if not is_allowed_artwork_url(url):
            raise HTTPException(status_code=400, detail="Unsupported artwork host")
        cached = await fetch_and_cache_artwork(url, cfg.artwork_cache_dir)
        if cached is not None:
            img_bytes, mime = cached
            return Response(
                content=img_bytes,
                media_type=mime,
                headers={"Cache-Control": "public, max-age=86400"},
            )
        return FileResponse(
            FALLBACK_ICON_PATH,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/api/requests/{request_id}/artwork", include_in_schema=False)
    async def serve_request_artwork(request_id: int) -> Response:
        rec = db.get_request(request_id)
        if rec is None:
            return FileResponse(FALLBACK_ICON_PATH, media_type="image/png")

        local = extract_local_cover(rec.file_path)
        if local is not None:
            img_bytes, mime = local
            return Response(
                content=img_bytes,
                media_type=mime,
                headers={"Cache-Control": "public, max-age=3600"},
            )

        if rec.artwork_url and is_allowed_artwork_url(rec.artwork_url):
            cached = await fetch_and_cache_artwork(rec.artwork_url, cfg.artwork_cache_dir)
            if cached is not None:
                img_bytes, mime = cached
                return Response(
                    content=img_bytes,
                    media_type=mime,
                    headers={"Cache-Control": "public, max-age=86400"},
                )

        return FileResponse(
            FALLBACK_ICON_PATH,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/api/library/covers", response_model=list[LibraryCoverItem])
    async def get_library_covers(limit: int = Query(default=36, ge=1, le=60)) -> list[LibraryCoverItem]:
        all_requests = db.list_requests(limit=300)
        return list_library_covers(
            music_dir=cfg.music_dir,
            requests=all_requests,
            limit=limit,
        )

    @app.get("/api/library/cover/{cover_hash}", include_in_schema=False)
    async def serve_library_cover(cover_hash: str) -> Response:
        path = resolve_library_cover_path(cfg.music_dir, cover_hash)
        if path is None:
            raise HTTPException(status_code=404, detail="Cover not found")
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(
            path,
            media_type=mime,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/search", response_model=list[SearchTrackResult])
    async def search_tracks(q: str = Query(..., min_length=1, max_length=200)) -> list[SearchTrackResult]:
        runtime = db.get_settings_view()
        tracks = await search_catalog(q.strip(), limit=15)
        lyrics_map = {}
        if cfg.check_lyrics_on_search and tracks:
            lyrics_map = await fetch_lyrics_batch(tracks, timeout=3.5, concurrency=6)

        results: list[SearchTrackResult] = []
        for track in tracks:
            key = track.track_id or f"{track.artist}::{track.title}"
            lyrics_obj = lyrics_map.get(key)
            lyrics_text = (
                (lyrics_obj.plain_lyrics or lyrics_obj.synced_lyrics) if lyrics_obj else ""
            )
            assessment = assess_request(
                query=q,
                track=track,
                lyrics_text=lyrics_text,
                lyrics_checked=cfg.check_lyrics_on_search,
                policy_mode=runtime.policy_mode,
                blocked_words=runtime.blocked_words,
                blocked_artists=runtime.blocked_artists,
                max_track_duration_sec=runtime.max_track_duration_sec,
            )
            results.append(SearchTrackResult(track=track, safety=assessment))
        return results

    @app.get("/api/search/albums", response_model=list[SearchAlbumResult])
    async def search_catalog_albums(
        q: str = Query(..., min_length=1, max_length=200),
    ) -> list[SearchAlbumResult]:
        runtime = db.get_settings_view()
        albums = await search_albums(q.strip(), limit=12)
        results: list[SearchAlbumResult] = []
        for album in albums:
            assessment = assess_album(
                query=q,
                album=album,
                policy_mode=runtime.policy_mode,
                blocked_words=runtime.blocked_words,
                blocked_artists=runtime.blocked_artists,
            )
            results.append(SearchAlbumResult(album=album, safety=assessment))
        return results

    @app.get("/api/albums/{provider}/{album_id}", response_model=AlbumDetailsResult)
    async def get_album_details(provider: str, album_id: str) -> AlbumDetailsResult:
        if provider not in {"itunes", "deezer"}:
            raise HTTPException(status_code=400, detail="Unsupported catalog provider")
        fetched = await get_album_tracks(provider, album_id)
        if fetched is None:
            raise HTTPException(status_code=404, detail="Album not found")

        album, tracks = fetched
        runtime = db.get_settings_view()
        album_safety = assess_album(
            query=album.title,
            album=album,
            policy_mode=runtime.policy_mode,
            blocked_words=runtime.blocked_words,
            blocked_artists=runtime.blocked_artists,
        )

        lyrics_map = {}
        if cfg.check_lyrics_on_search and tracks:
            lyrics_map = await fetch_lyrics_batch(tracks, timeout=3.5, concurrency=6)

        assessed_tracks: list[SearchTrackResult] = []
        for track in tracks:
            key = track.track_id or f"{track.artist}::{track.title}"
            lyrics_obj = lyrics_map.get(key)
            lyrics_text = (
                (lyrics_obj.plain_lyrics or lyrics_obj.synced_lyrics) if lyrics_obj else ""
            )
            track_safety = assess_request(
                query=f"{track.title} by {track.artist}",
                track=track,
                lyrics_text=lyrics_text,
                lyrics_checked=cfg.check_lyrics_on_search,
                policy_mode=runtime.policy_mode,
                blocked_words=runtime.blocked_words,
                blocked_artists=runtime.blocked_artists,
                max_track_duration_sec=runtime.max_track_duration_sec,
            )
            assessed_tracks.append(SearchTrackResult(track=track, safety=track_safety))

        return AlbumDetailsResult(
            album=album,
            safety=album_safety,
            tracks=assessed_tracks,
        )

    @app.get("/api/requests", response_model=list[MusicRequestRecord])
    async def list_requests(
        request: Request,
        status_filter: Optional[RequestStatus] = Query(default=None, alias="status"),
        requester_name: Optional[str] = Query(default=None),
        include_parent: bool = Query(default=False),
        limit: int = Query(default=80, ge=1, le=200),
    ) -> list[MusicRequestRecord]:
        # Parent downloads stay separated from the main Kid Song Requests view
        allow_parent_items = include_parent and _is_parent_authenticated(request)
        return db.list_requests(
            status=status_filter,
            requester_name=requester_name,
            exclude_source=None if allow_parent_items else RequestSource.PARENT,
            limit=limit,
        )

    @app.post("/api/requests", response_model=MusicRequestRecord, status_code=status.HTTP_201_CREATED)
    async def create_request(
        request: Request,
        payload: MusicRequestCreate,
    ) -> MusicRequestRecord:
        is_parent = _is_parent_authenticated(request)
        # Prevent unauthenticated clients from spoofing parent source or override privileges
        effective_source = payload.source
        effective_override = payload.parent_override and is_parent and effective_source == RequestSource.PARENT
        if effective_source == RequestSource.PARENT and not is_parent:
            effective_source = RequestSource.IPAD
        if payload.source_url and not is_parent:
            effective_source = RequestSource.IPAD

        sanitized_payload = payload.model_copy(
            update={"source": effective_source, "parent_override": effective_override}
        )
        record, _ = await submit_music_request(
            db=db,
            settings=cfg,
            payload=sanitized_payload,
        )
        if record.status == RequestStatus.QUEUED:
            worker.wake()
        return record

    @app.post("/api/requests/album", response_model=AlbumRequestResponse, status_code=status.HTTP_201_CREATED)
    async def create_album_request(
        request: Request,
        payload: AlbumRequestCreate,
    ) -> AlbumRequestResponse:
        is_parent = _is_parent_authenticated(request)
        effective_source = payload.source
        if effective_source == RequestSource.PARENT and not is_parent:
            effective_source = RequestSource.IPAD
        effective_override = (
            payload.parent_override and is_parent and effective_source == RequestSource.PARENT
        )
        effective_skip_explicit = False if effective_override else (
            payload.skip_explicit if is_parent else True
        )

        sanitized = payload.model_copy(
            update={
                "source": effective_source,
                "parent_override": effective_override,
                "skip_explicit": effective_skip_explicit,
            }
        )
        result = await submit_album_request(db=db, settings=cfg, payload=sanitized)
        if result is None:
            raise HTTPException(status_code=404, detail="Album not found")
        if result.queued_count > 0:
            worker.wake()
        return result

    @app.post("/api/requests/cleanup", response_model=RequestCleanupResponse)
    async def cleanup_requests(
        payload: RequestCleanupPayload,
        queue: str = Query(default="kid", pattern="^(kid|parent|all)$"),
        _: None = Depends(require_parent),
    ) -> RequestCleanupResponse:
        if queue == "kid":
            deleted = db.cleanup_requests(
                scope=payload.scope,
                exclude_source=RequestSource.PARENT,
            )
        elif queue == "parent":
            deleted = db.cleanup_requests(
                scope=payload.scope,
                source=RequestSource.PARENT,
            )
        else:
            deleted = db.cleanup_requests(scope=payload.scope)
        return RequestCleanupResponse(deleted_count=deleted, scope=payload.scope)

    @app.delete("/api/requests/{request_id}")
    async def delete_single_request(
        request_id: int,
        _: None = Depends(require_parent),
    ) -> dict[str, bool]:
        rec = db.get_request(request_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Request not found")
        if rec.status not in _FINISHED_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Only finished requests (completed, blocked, declined, or failed) can be removed",
            )
        db.delete_request(request_id)
        return {"deleted": True}

    @app.get("/api/tts/{filename}", include_in_schema=False)
    async def serve_tts_audio(filename: str) -> FileResponse:
        if not _TTS_FILENAME_RE.match(filename):
            raise HTTPException(status_code=404, detail="Audio not found")
        path = cfg.tts_dir / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Audio not found")
        return FileResponse(path, media_type="audio/mpeg")

    @app.post("/api/parent/login")
    async def parent_login(payload: ParentLoginRequest) -> JSONResponse:
        raw = db.get_settings_dict()
        stored_hash = str(raw.get("parent_pin_hash") or "")
        if not verify_pin(payload.pin, stored_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect Parent PIN",
            )
        token = create_session_token(session_secret)
        resp = JSONResponse({"authenticated": True, "token": token})
        resp.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
        )
        return resp

    @app.post("/api/parent/logout")
    async def parent_logout() -> JSONResponse:
        resp = JSONResponse({"authenticated": False})
        resp.delete_cookie(key=SESSION_COOKIE_NAME)
        return resp

    @app.post("/api/parent/pin")
    async def change_parent_pin(
        payload: PinChangeRequest,
        _: None = Depends(require_parent),
    ) -> dict[str, bool]:
        raw = db.get_settings_dict()
        stored_hash = str(raw.get("parent_pin_hash") or "")
        if not verify_pin(payload.current_pin, stored_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current PIN is incorrect",
            )
        db.update_settings_dict(
            {
                "parent_pin_hash": hash_pin(payload.new_pin),
                "parent_pin_is_default": False,
            }
        )
        return {"updated": True}

    @app.get("/api/parent/downloads", response_model=list[MusicRequestRecord])
    async def list_parent_downloads(
        limit: int = Query(default=100, ge=1, le=200),
        _: None = Depends(require_parent),
    ) -> list[MusicRequestRecord]:
        return db.list_requests(source=RequestSource.PARENT, limit=limit)

    @app.post("/api/parent/downloads/cleanup", response_model=RequestCleanupResponse)
    async def cleanup_parent_downloads(
        payload: RequestCleanupPayload,
        _: None = Depends(require_parent),
    ) -> RequestCleanupResponse:
        deleted = db.cleanup_requests(
            scope=payload.scope,
            source=RequestSource.PARENT,
        )
        return RequestCleanupResponse(deleted_count=deleted, scope=payload.scope)

    @app.get("/api/parent/settings", response_model=RuntimeSettingsView)
    async def get_parent_settings(
        _: None = Depends(require_parent),
    ) -> RuntimeSettingsView:
        return db.get_settings_view()

    @app.put("/api/parent/settings", response_model=RuntimeSettingsView)
    async def update_parent_settings(
        payload: RuntimeSettingsUpdate,
        _: None = Depends(require_parent),
    ) -> RuntimeSettingsView:
        updates: dict = {}
        data = payload.model_dump(exclude_none=True)
        for key, value in data.items():
            if key == "policy_mode":
                updates[key] = value.value if hasattr(value, "value") else str(value)
            elif key in {"plex_token", "google_keep_master_token"}:
                if str(value).strip():
                    updates[key] = str(value).strip()
            elif key in {"kid_profiles", "blocked_words", "blocked_artists", "google_cast_devices"}:
                cleaned = [item.strip() for item in value if str(item).strip()]
                if key == "kid_profiles" and not cleaned:
                    cleaned = ["Family"]
                updates[key] = cleaned
            else:
                updates[key] = value

        if updates:
            db.update_settings_dict(updates)
        return db.get_settings_view()

    @app.post("/api/parent/requests/{request_id}/approve", response_model=MusicRequestRecord)
    async def approve_request(
        request_id: int,
        payload: ApproveRequestPayload,
        _: None = Depends(require_parent),
    ) -> MusicRequestRecord:
        rec = db.get_request(request_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Request not found")

        if rec.status == RequestStatus.BLOCKED and not (
            payload.override_explicit or payload.use_clean_alternative
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Blocked explicit requests require override_explicit or use_clean_alternative",
            )

        updates: dict = {
            "status": RequestStatus.QUEUED,
            "status_detail": "Approved by parent — queued for download",
        }

        if payload.use_clean_alternative:
            alt = (
                rec.catalog_metadata.clean_alternative
                if rec.catalog_metadata
                else None
            )
            if alt is None and rec.artist and rec.title:
                alt = await find_clean_alternative(rec.artist, rec.title)
            if alt is None:
                raise HTTPException(
                    status_code=400,
                    detail="No verified clean version found for this track",
                )
            updates.update(
                {
                    "explicit_verdict": alt.explicit_verdict,
                    "title": alt.title,
                    "artist": alt.artist,
                    "album_artist": alt.album_artist or alt.artist,
                    "album": alt.album,
                    "year": alt.year,
                    "genre": alt.genre,
                    "track_number": alt.track_number,
                    "track_count": alt.track_count,
                    "disc_number": alt.disc_number,
                    "disc_count": alt.disc_count,
                    "duration_sec": alt.duration_sec,
                    "artwork_url": alt.artwork_url,
                    "catalog_metadata": alt,
                    "status_detail": "Approved clean version — queued for download",
                }
            )

        for field in (
            "title",
            "artist",
            "album_artist",
            "album",
            "year",
            "genre",
            "track_number",
            "disc_number",
            "source_url",
        ):
            val = getattr(payload, field)
            if val is not None and (not isinstance(val, str) or val.strip()):
                updates[field] = val.strip() if isinstance(val, str) else val

        if payload.override_explicit and rec.explicit_verdict == ExplicitVerdict.EXPLICIT:
            updates["explicit_verdict"] = ExplicitVerdict.UNVERIFIED

        updated = db.update_request(request_id, **updates)
        assert updated is not None
        worker.wake()
        return updated

    @app.post("/api/parent/requests/{request_id}/decline", response_model=MusicRequestRecord)
    async def decline_request(
        request_id: int,
        payload: DeclineRequestPayload,
        _: None = Depends(require_parent),
    ) -> MusicRequestRecord:
        rec = db.get_request(request_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Request not found")

        updated = db.update_request(
            request_id,
            status=RequestStatus.DECLINED,
            status_detail=payload.reason,
        )
        assert updated is not None

        if rec.keep_item_id:
            raw = db.get_settings_dict()
            await update_keep_item_status(
                email=str(raw.get("google_keep_email") or ""),
                master_token=str(raw.get("google_keep_master_token") or ""),
                list_name=str(raw.get("google_keep_list_name") or "Music Requests"),
                item_id=rec.keep_item_id,
                check_off=True,
                status_tag="Declined",
            )
        return updated

    @app.post("/api/parent/requests/{request_id}/retry", response_model=MusicRequestRecord)
    async def retry_request(
        request_id: int,
        _: None = Depends(require_parent),
    ) -> MusicRequestRecord:
        rec = db.get_request(request_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Request not found")
        updated = db.update_request(
            request_id,
            status=RequestStatus.QUEUED,
            status_detail="Re-queued by parent",
        )
        assert updated is not None
        worker.wake()
        return updated

    @app.post("/api/parent/plex/test")
    async def test_plex_connection(_: None = Depends(require_parent)) -> dict:
        raw = db.get_settings_dict()
        plex_url = str(raw.get("plex_url") or "")
        plex_token = str(raw.get("plex_token") or "")
        if not plex_url or not plex_token:
            raise HTTPException(status_code=400, detail="Plex URL and Token are required")
        try:
            sections = await discover_music_sections(plex_url, plex_token)
            return {"ok": True, "sections": sections}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Plex connection failed: {exc}")

    @app.post("/api/parent/plex/scan")
    async def manual_plex_scan(_: None = Depends(require_parent)) -> dict:
        raw = db.get_settings_dict()
        try:
            scanned = await trigger_library_scan(
                plex_url=str(raw.get("plex_url") or ""),
                plex_token=str(raw.get("plex_token") or ""),
                section_id=str(raw.get("plex_music_section_id") or ""),
            )
            return {"ok": scanned}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Plex scan failed: {exc}")

    @app.get("/api/parent/cast/discover")
    async def discover_speakers(_: None = Depends(require_parent)) -> dict:
        try:
            devices = await discover_cast_devices()
            return {"devices": devices}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Discovery failed: {exc}")

    @app.post("/api/parent/cast/announce")
    async def test_cast_announce(
        payload: CastAnnounceRequest,
        _: None = Depends(require_parent),
    ) -> dict:
        runtime = db.get_settings_view()
        base_url = resolve_public_base_url(
            runtime.public_base_url or cfg.public_base_url,
            cfg.port,
        )
        devices = payload.devices if payload.devices is not None else runtime.google_cast_devices
        try:
            announced = await announce_on_google_home(
                message=payload.message,
                tts_dir=cfg.tts_dir,
                public_base_url=base_url,
                devices=devices,
            )
            return {"announced_to": announced, "audio_base_url": base_url}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Cast failed: {exc}")

    @app.post("/api/parent/keep/sync")
    async def manual_keep_sync(_: None = Depends(require_parent)) -> dict:
        try:
            count = await worker.poll_keep_once()
            return {"imported": count}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Google Keep sync failed: {exc}")

    return app


app = create_app()
