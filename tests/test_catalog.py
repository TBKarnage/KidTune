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
