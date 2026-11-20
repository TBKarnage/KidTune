from app.services.downloader import score_youtube_candidate


def test_score_youtube_candidate_prefers_topic_and_rejects_unsafe():
    topic = {
        "title": "Shake It Off",
        "channel": "Taylor Swift - Topic",
        "duration": 219,
        "age_limit": 0,
    }
    live = {
        "title": "Shake It Off (Live Concert Tour)",
        "channel": "Fan Uploads",
        "duration": 225,
        "age_limit": 0,
    }
    age_restricted = {
        "title": "Shake It Off (Official Audio)",
        "channel": "Some Channel",
        "duration": 219,
        "age_limit": 18,
    }
    explicit_title = {
        "title": "Cool Song (Explicit Uncensored)",
        "channel": "Artist",
        "duration": 219,
        "age_limit": 0,
    }
    too_long = {
        "title": "Shake It Off 1 Hour Loop",
        "channel": "Looper",
        "duration": 3600,
        "age_limit": 0,
    }

    s_topic = score_youtube_candidate(
        topic, target_title="Shake It Off", target_artist="Taylor Swift", target_duration_sec=219
    )
    s_live = score_youtube_candidate(
        live, target_title="Shake It Off", target_artist="Taylor Swift", target_duration_sec=219
    )
    assert s_topic is not None and s_live is not None
    assert s_topic > s_live

    assert (
        score_youtube_candidate(
            age_restricted, target_title="Shake It Off", target_artist="Taylor Swift", target_duration_sec=219
        )
        is None
    )
    assert (
        score_youtube_candidate(
            explicit_title, target_title="Cool Song", target_artist="Artist", target_duration_sec=219
        )
        is None
    )
    assert (
        score_youtube_candidate(
            too_long, target_title="Shake It Off", target_artist="Taylor Swift", target_duration_sec=219
        )
        is None
    )


def test_require_clean_title_enforced_for_cleaned_edits():
    unlabeled = {
        "title": "Pop Song (Official Audio)",
        "channel": "Artist - Topic",
        "duration": 200,
        "age_limit": 0,
    }
    clean_labeled = {
        "title": "Pop Song (Clean Radio Edit)",
        "channel": "Artist Official",
        "duration": 200,
        "age_limit": 0,
    }
    assert (
        score_youtube_candidate(
            unlabeled,
            target_title="Pop Song",
            target_artist="Artist",
            target_duration_sec=200,
            require_clean_title=True,
        )
        is None
    )
    assert (
        score_youtube_candidate(
            clean_labeled,
            target_title="Pop Song",
            target_artist="Artist",
            target_duration_sec=200,
            require_clean_title=True,
        )
        is not None
    )
