from __future__ import annotations

import logging
from typing import Optional

from app.config import Settings
from app.database import Database
from app.models import (
    AlbumRequestCreate,
    AlbumRequestResponse,
    CatalogTrack,
    ExplicitVerdict,
    MusicRequestCreate,
    MusicRequestRecord,
    RequestSource,
    RequestStatus,
    SafetyAssessment,
)
from app.services.catalog import (
    find_clean_alternative,
    get_album_tracks,
    match_text_request,
)
from app.services.lyrics import fetch_lyrics, fetch_lyrics_batch
from app.services.safety import assess_request

logger = logging.getLogger(__name__)


async def submit_music_request(
    *,
    db: Database,
    settings: Settings,
    payload: MusicRequestCreate,
    keep_item_id: str = "",
    preloaded_lyrics: Optional[str] = None,
) -> tuple[MusicRequestRecord, SafetyAssessment]:
    runtime = db.get_settings_view()
    track: Optional[CatalogTrack] = payload.catalog_track

    if track is not None:
        if payload.use_clean_alternative:
            if track.clean_alternative is not None:
                track = track.clean_alternative
            elif track.explicit_verdict == ExplicitVerdict.EXPLICIT:
                alt = await find_clean_alternative(track.artist, track.title)
                if alt is not None:
                    track = alt
        elif (
            track.explicit_verdict == ExplicitVerdict.EXPLICIT
            and track.clean_alternative is None
            and not payload.parent_override
        ):
            alt = await find_clean_alternative(track.artist, track.title)
            if alt is not None:
                track = track.model_copy(update={"clean_alternative": alt})

        if (
            track.explicit_verdict == ExplicitVerdict.EXPLICIT
            and runtime.auto_substitute_clean_edit
            and track.clean_alternative is not None
            and not payload.parent_override
        ):
            track = track.clean_alternative
    elif not payload.source_url:
        matched, _ = await match_text_request(payload.query)
        if matched is not None:
            if (
                matched.explicit_verdict == ExplicitVerdict.EXPLICIT
                and runtime.auto_substitute_clean_edit
                and matched.clean_alternative is not None
                and not payload.parent_override
            ):
                track = matched.clean_alternative
            else:
                track = matched

    lyrics_text = preloaded_lyrics or ""
    lyrics_checked = preloaded_lyrics is not None
    if (
        not lyrics_checked
        and track is not None
        and track.explicit_verdict != ExplicitVerdict.EXPLICIT
        and settings.check_lyrics_on_search
        and not payload.parent_override
    ):
        lyrics_checked = True
        lyrics_res = await fetch_lyrics(
            artist=track.artist,
            title=track.title,
            album=track.album,
            duration_sec=track.duration_sec,
        )
        if lyrics_res:
            lyrics_text = lyrics_res.plain_lyrics or lyrics_res.synced_lyrics

    assessment = assess_request(
        query=payload.query,
        track=track,
        lyrics_text=lyrics_text,
        lyrics_checked=lyrics_checked,
        source_url=payload.source_url,
        policy_mode=runtime.policy_mode,
        blocked_words=runtime.blocked_words,
        blocked_artists=runtime.blocked_artists,
        max_track_duration_sec=runtime.max_track_duration_sec,
    )

    if payload.source == RequestSource.PARENT and (
        not assessment.blocked or payload.parent_override
    ):
        status = RequestStatus.QUEUED
        detail = (
            "Queued by parent (explicit override)"
            if assessment.blocked and payload.parent_override
            else "Queued by parent"
        )
    elif assessment.blocked:
        status = RequestStatus.BLOCKED
        detail = "; ".join(assessment.reasons) or "Blocked by explicit content filter"
    elif assessment.requires_approval:
        status = RequestStatus.PENDING_APPROVAL
        detail = "; ".join(assessment.reasons) or "Waiting for parent approval"
    else:
        status = RequestStatus.QUEUED
        detail = "Approved automatically — queued for download"

    record = db.create_request(
        query=payload.query,
        requester_name=payload.requester_name.strip() or "Kids",
        source=payload.source,
        status=status,
        status_detail=detail,
        explicit_verdict=assessment.verdict,
        safety_reasons=assessment.reasons,
        track=track,
        source_url=payload.source_url or "",
        keep_item_id=keep_item_id,
    )
    return record, assessment


async def submit_album_request(
    *,
    db: Database,
    settings: Settings,
    payload: AlbumRequestCreate,
) -> Optional[AlbumRequestResponse]:
    """Queue every track in an album with full safety enforcement for kids or direct queuing for parents."""
    fetched = await get_album_tracks(payload.provider, payload.album_id)
    if fetched is None:
        return None
    album, tracks = fetched
    if not tracks:
        return AlbumRequestResponse(
            album_title=album.title,
            artist=album.artist,
            total_tracks=0,
            queued_count=0,
            pending_count=0,
            blocked_count=0,
            skipped_explicit_count=0,
            requests=[],
        )

    is_parent = payload.source == RequestSource.PARENT
    allow_override = is_parent and payload.parent_override

    lyrics_map = {}
    if settings.check_lyrics_on_search and not allow_override:
        lyrics_map = await fetch_lyrics_batch(tracks, timeout=4.0, concurrency=6)

    runtime = db.get_settings_view()
    created_records: list[MusicRequestRecord] = []
    queued_count = 0
    pending_count = 0
    blocked_count = 0
    skipped_explicit_count = 0

    for track in tracks:
        key = track.track_id or f"{track.artist}::{track.title}"
        lyrics_obj = lyrics_map.get(key)
        lyrics_text = (
            (lyrics_obj.plain_lyrics or lyrics_obj.synced_lyrics) if lyrics_obj else ""
        )

        # Pre-check if explicit so skip_explicit avoids cluttering the request queue
        if payload.skip_explicit and not allow_override:
            pre_check = assess_request(
                query=f"{track.title} by {track.artist}",
                track=track,
                lyrics_text=lyrics_text,
                lyrics_checked=bool(settings.check_lyrics_on_search),
                policy_mode=runtime.policy_mode,
                blocked_words=runtime.blocked_words,
                blocked_artists=runtime.blocked_artists,
                max_track_duration_sec=runtime.max_track_duration_sec,
            )
            if pre_check.blocked and not (
                runtime.auto_substitute_clean_edit and pre_check.clean_alternative
            ):
                skipped_explicit_count += 1
                continue

        track_payload = MusicRequestCreate(
            query=f"{track.title} by {track.artist}",
            requester_name=payload.requester_name,
            source=payload.source,
            catalog_track=track,
            parent_override=allow_override,
        )
        record, _ = await submit_music_request(
            db=db,
            settings=settings,
            payload=track_payload,
            preloaded_lyrics=lyrics_text if settings.check_lyrics_on_search else None,
        )
        created_records.append(record)
        if record.status == RequestStatus.QUEUED:
            queued_count += 1
        elif record.status == RequestStatus.PENDING_APPROVAL:
            pending_count += 1
        elif record.status == RequestStatus.BLOCKED:
            blocked_count += 1

    return AlbumRequestResponse(
        album_title=album.title,
        artist=album.artist,
        total_tracks=len(tracks),
        queued_count=queued_count,
        pending_count=pending_count,
        blocked_count=blocked_count,
        skipped_explicit_count=skipped_explicit_count,
        requests=created_records,
    )
