from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.database import Database
from app.models import CatalogTrack, ExplicitVerdict, RequestStatus
from app.services.google_keep import KeepListEntry, clean_keep_item_text
from app.services.worker import BackgroundWorker


def test_clean_keep_item_text():
    assert clean_keep_item_text("Shake It Off by Taylor Swift [Waiting for parent]") == "Shake It Off by Taylor Swift"
    assert clean_keep_item_text("  Happy   [Queued] ") == "Happy"


@pytest.mark.asyncio
async def test_keep_poller_processes_and_deduplicates_items(tmp_path: Path, monkeypatch):
    cfg = Settings(
        data_dir=tmp_path / "data",
        music_dir=tmp_path / "music",
        google_keep_enabled=True,
        google_keep_email="parent@example.com",
        google_keep_master_token="fake-master-token",
        google_keep_list_name="Music Requests",
        enable_background_worker=False,
        check_lyrics_on_search=False,
    )
    cfg.ensure_directories()
    db = Database(cfg.db_path)
    db.init_db(cfg)
    worker = BackgroundWorker(db=db, settings=cfg)

    entries = [
        KeepListEntry(item_id="k1", raw_text="Let It Go by Idina Menzel", cleaned_query="Let It Go by Idina Menzel"),
        KeepListEntry(item_id="k2", raw_text="Bad Song", cleaned_query="Bad Song"),
        KeepListEntry(item_id="k3", raw_text="Grandma Lullaby", cleaned_query="Grandma Lullaby"),
    ]

    clean_track = CatalogTrack(
        track_id="c1",
        title="Let It Go",
        artist="Idina Menzel",
        album="Frozen",
        year="2013",
        duration_sec=224,
        explicit_verdict=ExplicitVerdict.CLEAN,
    )
    explicit_track = CatalogTrack(
        track_id="e1",
        title="Bad Song",
        artist="Explicit Artist",
        duration_sec=180,
        explicit_verdict=ExplicitVerdict.EXPLICIT,
        is_explicit=True,
    )

    async def fake_match(query: str):
        if "Let It Go" in query:
            return clean_track, [clean_track]
        if "Bad Song" in query:
            return explicit_track, [explicit_track]
        return None, []

    monkeypatch.setattr(
        "app.services.worker.fetch_unchecked_keep_requests",
        AsyncMock(return_value=entries),
    )
    monkeypatch.setattr(
        "app.services.request_pipeline.match_text_request",
        fake_match,
    )
    status_updates = []

    async def fake_update(**kwargs):
        status_updates.append((kwargs["item_id"], kwargs["check_off"], kwargs["status_tag"]))

    monkeypatch.setattr("app.services.worker.update_keep_item_status", fake_update)

    imported_first = await worker.poll_keep_once()
    assert imported_first == 3

    # Polling again with same unchecked items must not create duplicate requests
    imported_second = await worker.poll_keep_once()
    assert imported_second == 0

    reqs = db.list_requests()
    by_keep = {r.keep_item_id: r for r in reqs}
    assert by_keep["k1"].status == RequestStatus.QUEUED
    assert by_keep["k2"].status == RequestStatus.BLOCKED
    assert by_keep["k3"].status == RequestStatus.PENDING_APPROVAL

    assert ("k1", False, "Queued") in status_updates
    assert ("k2", True, "Blocked") in status_updates
    assert ("k3", False, "Waiting for parent") in status_updates
