import io
import subprocess
from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from app.models import CatalogTrack, ExplicitVerdict
from app.services.lyrics import LyricsResult
from app.services.metadata import (
    compute_plex_paths,
    install_track_into_plex,
    sanitize_filename_part,
)


def test_sanitize_and_plex_folder_structure(tmp_path: Path):
    assert sanitize_filename_part('AC/DC: "Back*?"..') == "AC_DC_ _Back____"

    single_disc = CatalogTrack(
        title="Let It Go",
        artist="Idina Menzel",
        album_artist="Disney",
        album="Frozen: Soundtrack",
        year="2013",
        track_number=5,
        track_count=10,
        disc_number=1,
        disc_count=1,
        explicit_verdict=ExplicitVerdict.CLEAN,
    )
    p1 = compute_plex_paths(tmp_path, single_disc)
    assert p1.audio_path == tmp_path / "Disney" / "Frozen_ Soundtrack (2013)" / "05 - Let It Go.m4a"

    multi_disc = single_disc.model_copy(update={"disc_number": 2, "disc_count": 2, "track_number": 3})
    p2 = compute_plex_paths(tmp_path, multi_disc)
    assert p2.audio_path.name == "2-03 - Let It Go.m4a"


def test_install_track_embeds_full_mp4_metadata_artwork_and_lyrics(tmp_path: Path):
    staged_m4a = tmp_path / "staged.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "aac",
            str(staged_m4a),
        ],
        check=True,
        capture_output=True,
    )

    img_buf = io.BytesIO()
    Image.new("RGB", (200, 200), color=(229, 90, 60)).save(img_buf, format="JPEG")
    art_bytes = img_buf.getvalue()

    track = CatalogTrack(
        title="Surface Pressure",
        artist="Jessica Darrow",
        album_artist="Lin-Manuel Miranda",
        album="Encanto",
        year="2021",
        genre="Soundtrack",
        track_number=4,
        track_count=12,
        disc_number=1,
        disc_count=1,
        explicit_verdict=ExplicitVerdict.CLEAN,
        musicbrainz_recording_id="mb-rec-123",
        musicbrainz_release_id="mb-rel-456",
    )
    lyrics = LyricsResult(
        plain_lyrics="Under the surface",
        synced_lyrics="[00:01.00] Under the surface",
    )

    music_root = tmp_path / "plex_music"
    paths = install_track_into_plex(
        staged_audio_path=staged_m4a,
        music_dir=music_root,
        track=track,
        lyrics=lyrics,
        artwork=(art_bytes, MP4Cover.FORMAT_JPEG),
    )

    assert paths.audio_path.exists()
    assert paths.cover_path.exists()
    assert paths.lrc_path.exists()
    assert paths.lrc_path.read_text(encoding="utf-8") == "[00:01.00] Under the surface"

    tagged = MP4(str(paths.audio_path))
    assert tagged.tags["\xa9nam"] == ["Surface Pressure"]
    assert tagged.tags["\xa9ART"] == ["Jessica Darrow"]
    assert tagged.tags["aART"] == ["Lin-Manuel Miranda"]
    assert tagged.tags["\xa9alb"] == ["Encanto"]
    assert tagged.tags["\xa9day"] == ["2021"]
    assert tagged.tags["\xa9gen"] == ["Soundtrack"]
    assert tagged.tags["trkn"] == [(4, 12)]
    assert tagged.tags["disk"] == [(1, 1)]
    assert tagged.tags["rtng"] == [2]
    assert tagged.tags["\xa9lyr"] == ["Under the surface"]
    assert tagged.tags["----:com.apple.iTunes:MusicBrainz Track Id"] == [b"mb-rec-123"]
    assert tagged.tags["----:com.apple.iTunes:MusicBrainz Album Id"] == [b"mb-rel-456"]
    assert len(tagged.tags["covr"]) == 1
