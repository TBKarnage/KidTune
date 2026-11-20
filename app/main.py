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
    ApproveRequestPayload,
    CastAnnounceRequest,
    DeclineRequestPayload,
    ExplicitVerdict,
    MusicRequestCreate,
    MusicRequestRecord,
    ParentLoginRequest,
    PinChangeRequest,
    RequestSource,
    RequestStatus,
    RuntimeSettingsUpdate,
    RuntimeSettingsView,
    SearchTrackResult,
)
from app.services.catalog import find_clean_alternative, search_catalog
from app.services.google_cast import announce_on_google_home, discover_cast_devices
from app.services.google_keep import update_keep_item_status
from app.services.plex import discover_music_sections, trigger_library_scan
from app.services.request_pipeline import submit_music_request
from app.services.safety import assess_request
from app.services.worker import BackgroundWorker

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
_TTS_FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.mp3$")


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
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request) -> dict:
        runtime = db.get_settings_view()
        pending = db.list_requests(status=RequestStatus.PENDING_APPROVAL, limit=200)
        is_parent = _is_parent_authenticated(request)
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
        }

    @app.get("/api/search", response_model=list[SearchTrackResult])
    async def search_tracks(q: str = Query(..., min_length=1, max_length=200)) -> list[SearchTrackResult]:
        runtime = db.get_settings_view()
        tracks = await search_catalog(q.strip(), limit=15)
        results: list[SearchTrackResult] = []
        for track in tracks:
            assessment = assess_request(
                query=q,
                track=track,
                policy_mode=runtime.policy_mode,
                blocked_words=runtime.blocked_words,
                blocked_artists=runtime.blocked_artists,
                max_track_duration_sec=runtime.max_track_duration_sec,
            )
            results.append(SearchTrackResult(track=track, safety=assessment))
        return results

    @app.get("/api/requests", response_model=list[MusicRequestRecord])
    async def list_requests(
        status_filter: Optional[RequestStatus] = Query(default=None, alias="status"),
        requester_name: Optional[str] = Query(default=None),
        limit: int = Query(default=80, ge=1, le=200),
    ) -> list[MusicRequestRecord]:
        return db.list_requests(
            status=status_filter,
            requester_name=requester_name,
            limit=limit,
        )

    @app.post("/api/requests", response_model=MusicRequestRecord, status_code=status.HTTP_201_CREATED)
    async def create_request(
        request: Request,
        payload: MusicRequestCreate,
    ) -> MusicRequestRecord:
        # Prevent unauthenticated clients from spoofing parent source privileges
        effective_source = payload.source
        if effective_source == RequestSource.PARENT and not _is_parent_authenticated(request):
            effective_source = RequestSource.IPAD
        if payload.source_url and not _is_parent_authenticated(request):
            # Direct URL submissions by kids are routed through safety check -> approval/block
            effective_source = RequestSource.IPAD

        sanitized_payload = payload.model_copy(update={"source": effective_source})
        record, _ = await submit_music_request(
            db=db,
            settings=cfg,
            payload=sanitized_payload,
        )
        if record.status == RequestStatus.QUEUED:
            worker.wake()
        return record

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
