import io
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from app.config import Settings
from app.database import Database
from app.models import CatalogTrack, ExplicitVerdict, RequestSource, RequestStatus
from app.services.downloader import DownloadedAudio
from app.services.lyrics import LyricsResult
from app.services.worker import BackgroundWorker


@pytest.mark.asyncio
async def test_worker_full_download_tag_and_plex_scan_pipeline(tmp_path: Path, monkeypatch):
    cfg = Settings(
        data_dir=tmp_path / "data",
        music_dir=tmp_path / "music",
        plex_url="http://plex.local:32400",
        plex_token="secret-plex-token",
        plex_music_section_id="7",
        plex_library_path_prefix="/media/kids-music",
        enable_background_worker=False,
    )
    cfg.ensure_directories()
    db = Database(cfg.db_path)
    db.init_db(cfg)
    worker = BackgroundWorker(db=db, settings=cfg)

    track = CatalogTrack(
        track_id="99",
        title="Try Everything",
        artist="Shakira",
        album_artist="Shakira",
        album="Zootopia",
        year="2016",
        genre="Soundtrack",
        track_number=1,
        track_count=12,
        duration_sec=196,
        explicit_verdict=ExplicitVerdict.CLEAN,
        artwork_url="https://example.com/art.jpg",
    )

    rec = db.create_request(
        query="Try Everything by Shakira",
        requester_name="Sam",
        source=RequestSource.IPAD,
        status=RequestStatus.QUEUED,
        explicit_verdict=ExplicitVerdict.CLEAN,
        track=track,
    )

    async def fake_download(**kwargs):
        staging_dir: Path = kwargs["staging_dir"]
        staged = staging_dir / "audio.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=523:duration=1",
                "-c:a",
                "aac",
                str(staged),
            ],
            check=True,
            capture_output=True,
        )
        return DownloadedAudio(
            audio_path=staged,
            youtube_url="https://www.youtube.com/watch?v=test123",
            youtube_title="Shakira - Try Everything (Official Audio)",
            youtube_channel="Shakira - Topic",
            duration_sec=196,
        )

    img_buf = io.BytesIO()
    Image.new("RGB", (120, 120), color=(15, 118, 110)).save(img_buf, format="JPEG")

    plex_scan_calls = []

    async def fake_plex_scan(**kwargs):
        plex_scan_calls.append(kwargs)
        return True

    monkeypatch.setattr("app.services.worker.search_and_download_track", fake_download)
    monkeypatch.setattr(
        "app.services.worker.enrich_with_musicbrainz",
        AsyncMock(return_value=track),
    )
    monkeypatch.setattr(
        "app.services.worker.fetch_lyrics",
        AsyncMock(
            return_value=LyricsResult(
                plain_lyrics="I won't give up",
                synced_lyrics="[00:00.50] I won't give up",
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.worker.fetch_artwork_bytes",
        AsyncMock(return_value=(img_buf.getvalue(), MP4Cover.FORMAT_JPEG)),
    )
    monkeypatch.setattr("app.services.worker.trigger_library_scan", fake_plex_scan)

    claimed = db.claim_next_queued_request()
    assert claimed is not None and claimed.id == rec.id
    await worker.process_one_request(claimed)

    final = db.get_request(rec.id)
    assert final is not None
    assert final.status == RequestStatus.COMPLETED
    assert final.lyrics_embedded is True
    assert final.plex_scanned is True

    expected_file = cfg.music_dir / "Shakira" / "Zootopia (2016)" / "01 - Try Everything.m4a"
    assert Path(final.file_path) == expected_file
    assert expected_file.exists()
    assert (expected_file.parent / "cover.jpg").exists()
    assert (expected_file.parent / "01 - Try Everything.lrc").exists()

    mp4 = MP4(str(expected_file))
    assert mp4.tags["\xa9nam"] == ["Try Everything"]
    assert mp4.tags["\xa9ART"] == ["Shakira"]
    assert mp4.tags["\xa9alb"] == ["Zootopia"]
    assert mp4.tags["\xa9lyr"] == ["I won't give up"]
    assert len(mp4.tags["covr"]) == 1

    assert len(plex_scan_calls) == 1
    assert plex_scan_calls[0]["section_id"] == "7"
    assert plex_scan_calls[0]["album_dir"] == expected_file.parent
