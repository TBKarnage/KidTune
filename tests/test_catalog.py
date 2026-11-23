from app.models import ExplicitVerdict
from app.services.catalog import (
    _attach_clean_alternatives,
    _parse_itunes_item,
    parse_song_query,
)


def test_parse_song_query_variants():
    assert parse_song_query("Shake It Off by Taylor Swift") == ("Shake It Off", "Taylor Swift")
    assert parse_song_query("Taylor Swift - Shake It Off") == ("Shake It Off", "Taylor Swift")
    assert parse_song_query("Please add Let It Go by Idina Menzel to my music requests list") == (
        "Let It Go",
        "Idina Menzel",
    )
    assert parse_song_query("Encanto Bruno") == ("Encanto Bruno", "")


def test_parse_itunes_item_and_attach_clean_alternative():
    explicit_raw = {
        "wrapperType": "track",
        "kind": "song",
        "trackId": 1001,
        "trackName": "Cool Song",
        "artistName": "Pop Star",
        "collectionName": "Summer Hits",
        "trackExplicitness": "explicit",
        "releaseDate": "2024-06-01T07:00:00Z",
        "primaryGenreName": "Pop",
        "trackNumber": 3,
        "trackCount": 12,
        "discNumber": 1,
        "discCount": 1,
        "trackTimeMillis": 205000,
        "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/100x100bb.jpg",
    }
    clean_raw = {
        **explicit_raw,
        "trackId": 1002,
        "trackName": "Cool Song (Clean)",
        "trackExplicitness": "cleaned",
    }

    t1 = _parse_itunes_item(explicit_raw)
    t2 = _parse_itunes_item(clean_raw)
    assert t1 is not None and t2 is not None
    assert t1.explicit_verdict == ExplicitVerdict.EXPLICIT
    assert t1.year == "2024"
    assert t1.duration_sec == 205
    assert t1.artwork_url.endswith("/1000x1000bb.jpg")
    assert t2.explicit_verdict == ExplicitVerdict.CLEANED_EDIT

    attached = _attach_clean_alternatives([t1, t2])
    assert attached[0].clean_alternative is not None
    assert attached[0].clean_alternative.track_id == "1002"


def test_merge_itunes_and_deezer_cross_checks_explicitness():
    from app.models import CatalogTrack
    from app.services.catalog import _merge_itunes_and_deezer_tracks

    itunes_tracks = [
        CatalogTrack(
            provider="itunes",
            track_id="i1",
            title="Popular Monster",
            artist="Falling In Reverse",
            explicit_verdict=ExplicitVerdict.CLEAN,
            is_explicit=False,
        )
    ]
    deezer_tracks = [
        CatalogTrack(
            provider="deezer",
            track_id="d1",
            title="Popular Monster",
            artist="Falling In Reverse",
            explicit_verdict=ExplicitVerdict.EXPLICIT,
            is_explicit=True,
        ),
        CatalogTrack(
            provider="deezer",
            track_id="d2",
            title="Watch The World Burn",
            artist="Falling In Reverse",
            explicit_verdict=ExplicitVerdict.EXPLICIT,
            is_explicit=True,
        ),
    ]

    merged = _merge_itunes_and_deezer_tracks(
        itunes_tracks, deezer_tracks, query="falling in reverse", limit=10
    )
    by_title = {t.title: t for t in merged}
    # iTunes track upgraded to EXPLICIT because Deezer flagged it explicit
    assert by_title["Popular Monster"].is_explicit is True
    assert by_title["Popular Monster"].explicit_verdict == ExplicitVerdict.EXPLICIT
    # Omitted Deezer explicit hit is also surfaced
    assert "Watch The World Burn" in by_title
    assert by_title["Watch The World Burn"].is_explicit is True


def test_parse_albums_and_attach_clean_edition():
    from app.services.catalog import (
        _attach_clean_album_alternatives,
        _parse_itunes_album,
    )

    exp_album = _parse_itunes_album(
        {
            "wrapperType": "collection",
            "collectionId": 5001,
            "collectionName": "Teenage Dream",
            "artistName": "Katy Perry",
            "collectionExplicitness": "explicit",
            "trackCount": 12,
            "releaseDate": "2010-08-24T07:00:00Z",
            "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/100x100bb.jpg",
        }
    )
    clean_album = _parse_itunes_album(
        {
            "wrapperType": "collection",
            "collectionId": 5002,
            "collectionName": "Teenage Dream (Clean)",
            "artistName": "Katy Perry",
            "collectionExplicitness": "cleaned",
            "trackCount": 12,
            "releaseDate": "2010-08-24T07:00:00Z",
            "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/100x100bb.jpg",
        }
    )
    assert exp_album is not None and clean_album is not None
    assert exp_album.is_explicit is True
    assert clean_album.explicit_verdict == ExplicitVerdict.CLEANED_EDIT
    linked = _attach_clean_album_alternatives([exp_album, clean_album])
    assert linked[0].clean_alternative_id == "itunes:5002"

