from __future__ import annotations

import logging
from typing import Optional

from app.config import Settings
from app.database import Database
from app.models import (
    CatalogTrack,
    ExplicitVerdict,
    MusicRequestCreate,
    MusicRequestRecord,
    RequestSource,
    RequestStatus,
    SafetyAssessment,
)
from app.services.catalog import find_clean_alternative, match_text_request
from app.services.lyrics import fetch_lyrics
from app.services.safety import assess_request

logger = logging.getLogger(__name__)


async def submit_music_request(
    *,
    db: Database,
    settings: Settings,
    payload: MusicRequestCreate,
    keep_item_id: str = "",
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
        ):
            alt = await find_clean_alternative(track.artist, track.title)
            if alt is not None:
                track = track.model_copy(update={"clean_alternative": alt})

        if (
            track.explicit_verdict == ExplicitVerdict.EXPLICIT
            and runtime.auto_substitute_clean_edit
            and track.clean_alternative is not None
        ):
            track = track.clean_alternative
    elif not payload.source_url:
        matched, _ = await match_text_request(payload.query)
        if matched is not None:
            if (
                matched.explicit_verdict == ExplicitVerdict.EXPLICIT
                and runtime.auto_substitute_clean_edit
                and matched.clean_alternative is not None
            ):
                track = matched.clean_alternative
            else:
                track = matched

    lyrics_text = ""
    if (
        track is not None
        and track.explicit_verdict != ExplicitVerdict.EXPLICIT
        and settings.check_lyrics_on_search
    ):
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
        source_url=payload.source_url,
        policy_mode=runtime.policy_mode,
        blocked_words=runtime.blocked_words,
        blocked_artists=runtime.blocked_artists,
        max_track_duration_sec=runtime.max_track_duration_sec,
    )

    if payload.source == RequestSource.PARENT and not assessment.blocked:
        status = RequestStatus.QUEUED
        detail = "Queued by parent"
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
