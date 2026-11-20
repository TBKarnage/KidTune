from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from app.config import Settings, resolve_public_base_url
from app.database import Database
from app.models import (
    CatalogTrack,
    ExplicitVerdict,
    MusicRequestCreate,
    MusicRequestRecord,
    RequestSource,
    RequestStatus,
)
from app.services.catalog import enrich_with_musicbrainz
from app.services.downloader import UnsafeDownloadCandidateError, search_and_download_track
from app.services.google_cast import announce_on_google_home
from app.services.google_keep import (
    fetch_unchecked_keep_requests,
    update_keep_item_status,
)
from app.services.lyrics import fetch_lyrics
from app.services.metadata import fetch_artwork_bytes, install_track_into_plex
from app.services.plex import trigger_library_scan
from app.services.request_pipeline import submit_music_request

logger = logging.getLogger(__name__)


def _build_track_from_record(record: MusicRequestRecord) -> CatalogTrack:
    if record.catalog_metadata is not None:
        base = record.catalog_metadata.model_copy(deep=True)
    else:
        base = CatalogTrack(
            provider="manual",
            title=record.title or record.query,
            artist=record.artist or "Unknown Artist",
            explicit_verdict=record.explicit_verdict,
        )

    base.title = record.title or base.title
    base.artist = record.artist or base.artist
    base.album_artist = record.album_artist or base.album_artist or base.artist
    base.album = record.album or base.album or f"{base.title} - Single"
    base.year = record.year or base.year
    base.genre = record.genre or base.genre
    base.track_number = record.track_number or base.track_number or 1
    base.track_count = record.track_count or base.track_count or 1
    base.disc_number = record.disc_number or base.disc_number or 1
    base.disc_count = record.disc_count or base.disc_count or 1
    base.duration_sec = record.duration_sec or base.duration_sec
    base.artwork_url = record.artwork_url or base.artwork_url
    base.explicit_verdict = record.explicit_verdict
    return base


class BackgroundWorker:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def wake(self) -> None:
        self._wake_event.set()

    def start(self) -> None:
        self.db.requeue_interrupted_jobs()
        self._tasks = [
            asyncio.create_task(self._download_loop(), name="kidtune-downloader"),
            asyncio.create_task(self._keep_poll_loop(), name="kidtune-keep-poller"),
        ]

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _maybe_announce(self, message: str) -> None:
        runtime = self.db.get_settings_view()
        if not runtime.google_cast_enabled:
            return
        base_url = resolve_public_base_url(
            runtime.public_base_url or self.settings.public_base_url,
            self.settings.port,
        )
        try:
            await announce_on_google_home(
                message=message,
                tts_dir=self.settings.tts_dir,
                public_base_url=base_url,
                devices=runtime.google_cast_devices,
            )
        except Exception as exc:
            logger.warning("Google Home announcement failed: %s", exc)

    async def process_one_request(self, record: MusicRequestRecord) -> None:
        runtime = self.db.get_settings_view()
        raw_settings = self.db.get_settings_dict()
        staging_dir = self.settings.tmp_dir / f"req_{record.id}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        track = _build_track_from_record(record)
        parent_override = "Approved by parent" in record.status_detail or record.source == RequestSource.PARENT

        try:
            downloaded = await search_and_download_track(
                staging_dir=staging_dir,
                track=track if record.catalog_metadata else None,
                fallback_query=f"{track.artist} {track.title}".strip() or record.query,
                source_url=record.source_url or None,
                max_duration_diff_sec=runtime.max_duration_diff_sec,
                max_track_duration_sec=runtime.max_track_duration_sec,
                blocked_words=runtime.blocked_words,
                allow_unverified_override=parent_override,
            )

            self.db.update_request(
                record.id,
                status=RequestStatus.TAGGING,
                status_detail="Fetching metadata, cover art, and lyrics...",
                youtube_url=downloaded.youtube_url,
                youtube_title=downloaded.youtube_title,
                duration_sec=track.duration_sec or downloaded.duration_sec,
            )

            if track.artist == "Unknown Artist" and downloaded.youtube_channel:
                track.artist = downloaded.youtube_channel.removesuffix(" - Topic").strip()
                track.album_artist = track.artist

            track = await enrich_with_musicbrainz(track)
            lyrics = await fetch_lyrics(
                artist=track.artist,
                title=track.title,
                album=track.album,
                duration_sec=track.duration_sec or downloaded.duration_sec,
            )
            artwork = await fetch_artwork_bytes(
                track.artwork_url,
                fallback_url=downloaded.thumbnail_url,
            )

            plex_paths = install_track_into_plex(
                staged_audio_path=downloaded.audio_path,
                music_dir=self.settings.music_dir,
                track=track,
                lyrics=lyrics,
                artwork=artwork,
            )

            plex_scanned = False
            plex_token = str(raw_settings.get("plex_token") or "")
            plex_url = str(raw_settings.get("plex_url") or "")
            if plex_url and plex_token:
                try:
                    plex_scanned = await trigger_library_scan(
                        plex_url=plex_url,
                        plex_token=plex_token,
                        section_id=str(raw_settings.get("plex_music_section_id") or ""),
                        album_dir=plex_paths.album_dir,
                        local_music_dir=self.settings.music_dir,
                        plex_library_path_prefix=str(
                            raw_settings.get("plex_library_path_prefix") or ""
                        ),
                    )
                except Exception as exc:
                    logger.warning("Plex scan failed for request %s: %s", record.id, exc)

            self.db.update_request(
                record.id,
                status=RequestStatus.COMPLETED,
                status_detail="Added to Plex library",
                title=track.title,
                artist=track.artist,
                album_artist=track.album_artist or track.artist,
                album=track.album,
                year=track.year,
                genre=track.genre,
                duration_sec=track.duration_sec or downloaded.duration_sec,
                file_path=str(plex_paths.audio_path),
                lyrics_embedded=bool(lyrics and (lyrics.plain_lyrics or lyrics.synced_lyrics)),
                plex_scanned=plex_scanned,
                catalog_metadata=track,
            )

            if record.keep_item_id:
                await update_keep_item_status(
                    email=str(raw_settings.get("google_keep_email") or ""),
                    master_token=str(raw_settings.get("google_keep_master_token") or ""),
                    list_name=str(raw_settings.get("google_keep_list_name") or "Music Requests"),
                    item_id=record.keep_item_id,
                    check_off=True,
                    status_tag="Added",
                )

            await self._maybe_announce(
                f"Added {track.title} by {track.artist} to Plex!"
            )

        except UnsafeDownloadCandidateError as exc:
            logger.info("Candidate rejected for request %s: %s", record.id, exc)
            if not parent_override:
                self.db.update_request(
                    record.id,
                    status=RequestStatus.PENDING_APPROVAL,
                    status_detail=f"{exc} — sent to Parent Approval",
                )
            else:
                self.db.update_request(
                    record.id,
                    status=RequestStatus.FAILED,
                    status_detail=str(exc),
                )
        except Exception as exc:
            logger.exception("Processing failed for request %s: %s", record.id, exc)
            self.db.update_request(
                record.id,
                status=RequestStatus.FAILED,
                status_detail=f"Download failed: {exc}",
            )
            if record.keep_item_id:
                await update_keep_item_status(
                    email=str(raw_settings.get("google_keep_email") or ""),
                    master_token=str(raw_settings.get("google_keep_master_token") or ""),
                    list_name=str(raw_settings.get("google_keep_list_name") or "Music Requests"),
                    item_id=record.keep_item_id,
                    check_off=False,
                    status_tag="Failed",
                )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    async def _download_loop(self) -> None:
        while not self._stop_event.is_set():
            record = self.db.claim_next_queued_request()
            if record is not None:
                await self.process_one_request(record)
                continue
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass

    async def poll_keep_once(self) -> int:
        raw_settings = self.db.get_settings_dict()
        if not raw_settings.get("google_keep_enabled"):
            return 0
        email = str(raw_settings.get("google_keep_email") or "")
        master_token = str(raw_settings.get("google_keep_master_token") or "")
        list_name = str(raw_settings.get("google_keep_list_name") or "Music Requests")
        if not email or not master_token or not list_name:
            return 0

        entries = await fetch_unchecked_keep_requests(
            email=email,
            master_token=master_token,
            list_name=list_name,
        )
        created_count = 0
        for entry in entries:
            if self.db.has_seen_keep_item(entry.item_id):
                continue

            record, assessment = await submit_music_request(
                db=self.db,
                settings=self.settings,
                payload=MusicRequestCreate(
                    query=entry.cleaned_query,
                    requester_name="Google Home",
                    source=RequestSource.GOOGLE_HOME,
                ),
                keep_item_id=entry.item_id,
            )
            self.db.mark_keep_item_seen(entry.item_id, entry.raw_text, record.id)
            created_count += 1

            if record.status == RequestStatus.QUEUED:
                self.wake()
                await update_keep_item_status(
                    email=email,
                    master_token=master_token,
                    list_name=list_name,
                    item_id=entry.item_id,
                    check_off=False,
                    status_tag="Queued",
                )
            elif record.status == RequestStatus.BLOCKED:
                await update_keep_item_status(
                    email=email,
                    master_token=master_token,
                    list_name=list_name,
                    item_id=entry.item_id,
                    check_off=True,
                    status_tag="Blocked",
                )
                await self._maybe_announce(
                    f"Sorry, {record.title or entry.cleaned_query} could not be added because it has explicit content."
                )
            elif record.status == RequestStatus.PENDING_APPROVAL:
                await update_keep_item_status(
                    email=email,
                    master_token=master_token,
                    list_name=list_name,
                    item_id=entry.item_id,
                    check_off=False,
                    status_tag="Waiting for parent",
                )
                await self._maybe_announce(
                    f"I sent {record.title or entry.cleaned_query} to a parent to check first."
                )
        return created_count

    async def _keep_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.poll_keep_once()
            except Exception as exc:
                logger.warning("Google Keep poll error: %s", exc)
            raw_settings = self.db.get_settings_dict()
            interval = max(15, int(raw_settings.get("google_keep_poll_seconds") or 45))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=float(interval))
            except asyncio.TimeoutError:
                pass
