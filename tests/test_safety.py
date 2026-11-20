from app.models import CatalogTrack, ExplicitVerdict, PolicyMode
from app.services.safety import assess_request, find_profanity_matches


def _track(
    *,
    title: str = "Happy",
    artist: str = "Pharrell Williams",
    verdict: ExplicitVerdict = ExplicitVerdict.CLEAN,
    duration_sec: int = 233,
    clean_alt: CatalogTrack | None = None,
) -> CatalogTrack:
    return CatalogTrack(
        track_id="123",
        title=title,
        artist=artist,
        album="G I R L",
        year="2014",
        duration_sec=duration_sec,
        explicit_verdict=verdict,
        is_explicit=(verdict == ExplicitVerdict.EXPLICIT),
        clean_alternative=clean_alt,
    )


def test_profanity_word_boundaries_avoid_false_positives():
    assert find_profanity_matches("Classical music with grapes, fork, flick, batch, and cocktails") == []
    hits = find_profanity_matches("This is some bullshit and f*ck f**k sh!t b!tch")
    assert "bullshit" in hits
    assert "f-word" in hits
    assert "s-word" in hits
    assert "b-word" in hits


def test_explicit_track_blocked_with_clean_alternative_attached():
    clean_edit = _track(title="Song (Clean)", verdict=ExplicitVerdict.CLEANED_EDIT)
    explicit = _track(title="Song", verdict=ExplicitVerdict.EXPLICIT, clean_alt=clean_edit)

    res = assess_request(query="Song", track=explicit, policy_mode=PolicyMode.HYBRID)
    assert res.blocked is True
    assert res.allowed_auto is False
    assert res.clean_alternative is not None
    assert res.clean_alternative.explicit_verdict == ExplicitVerdict.CLEANED_EDIT


def test_lyrics_profanity_blocks_even_untagged_clean_track():
    clean_looking = _track(title="Mystery Song", verdict=ExplicitVerdict.CLEAN)
    res = assess_request(
        query="Mystery Song",
        track=clean_looking,
        lyrics_text="Sing along\nNo bad words until bitch shows up\nEnd",
        policy_mode=PolicyMode.HYBRID,
    )
    assert res.blocked is True
    assert "lyrics" in res.reasons[0].lower()


def test_custom_blocked_words_and_artists():
    track = _track(title="Lullaby", artist="Blocked Band", verdict=ExplicitVerdict.CLEAN)
    res = assess_request(
        query="Lullaby",
        track=track,
        blocked_artists=["blocked band"],
        policy_mode=PolicyMode.HYBRID,
    )
    assert res.blocked is True

    res_word = assess_request(
        query="Halloween Night",
        track=_track(title="Halloween Night", artist="Kids Choir"),
        blocked_words=["halloween"],
        policy_mode=PolicyMode.HYBRID,
    )
    assert res_word.blocked is True


def test_policy_modes_for_clean_and_unmatched():
    clean = _track()
    assert assess_request(query="Happy", track=clean, policy_mode=PolicyMode.HYBRID).allowed_auto is True
    strict = assess_request(query="Happy", track=clean, policy_mode=PolicyMode.STRICT)
    assert strict.allowed_auto is False
    assert strict.requires_approval is True

    hybrid_unmatched = assess_request(query="Obscure Song", track=None, policy_mode=PolicyMode.HYBRID)
    assert hybrid_unmatched.requires_approval is True
    assert hybrid_unmatched.blocked is False

    auto_clean_unmatched = assess_request(
        query="Obscure Song", track=None, policy_mode=PolicyMode.AUTO_CLEAN
    )
    assert auto_clean_unmatched.blocked is True
    assert auto_clean_unmatched.requires_approval is False


def test_duration_limit_routes_to_approval_or_block():
    long_mix = _track(title="2 Hour Mix", duration_sec=900)
    hybrid = assess_request(
        query="2 Hour Mix", track=long_mix, policy_mode=PolicyMode.HYBRID, max_track_duration_sec=600
    )
    assert hybrid.requires_approval is True

    auto_clean = assess_request(
        query="2 Hour Mix", track=long_mix, policy_mode=PolicyMode.AUTO_CLEAN, max_track_duration_sec=600
    )
    assert auto_clean.blocked is True
