from __future__ import annotations

import re
from typing import Iterable, Optional

from app.models import (
    CatalogAlbum,
    CatalogTrack,
    ExplicitVerdict,
    PolicyMode,
    SafetyAssessment,
)

# Whole-word explicit profanity & mature theme patterns.
# Strict word boundaries ensure ordinary kid-safe words (e.g. Classical, grapes,
# cocktails, something, hella) are never false-positived.
_BUILTIN_PROFANITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("f-word", r"\bf(?:[u\*\u2022@#\$!_]+(?:c|[\*\u2022@#\$!_])k+|[\*\u2022@#\$!_]{2}k+)(?:ing|ed|er|ers|s)?\b"),
    ("s-word", r"\bs+h[i1!\*\u2022@#\$_]+t+(?:ty|ting|ted|head|s)?\b"),
    ("b-word", r"\bb(?:[i1!\*\u2022@#\$_]+t+c+h+|[\*\u2022@#\$!_]{2}ch)(?:es|ing|ed|y)?\b"),
    ("c-word", r"\bc[u\*\u2022@#\$!_]+n+t+s?\b"),
    ("motherfucker", r"\bmother\s*f[u\*\u2022@#\$!_]+c?k+(?:er|ers|ing)?\b"),
    ("dick", r"\bdick(?:head|heads|s)?\b"),
    ("cock", r"\bcock(?:sucker|suckers|s)?\b"),
    ("pussy", r"\bpuss(?:y|ies)\b"),
    ("whore", r"\bwhore(?:s)?\b"),
    ("slut", r"\bslut(?:s|ty)?\b"),
    ("bastard", r"\bbastard(?:s)?\b"),
    ("asshole", r"\basshole(?:s)?\b"),
    ("ass", r"\b(?:ass|badass|dumbass|jackass|smartass|fatass)(?:es)?\b"),
    ("bullshit", r"\bbull\s*shit\b"),
    ("damn", r"\b(?:god)?damn(?:ed|it|s)?\b"),
    ("piss", r"\bpiss(?:ed|ing|es)?\b"),
    (
        "hell-profanity",
        r"\b(?:go(?:ing)?\s+(?:straight\s+)?to\s+hell|burn\s+in\s+hell|hell\s+of\s+a|what\s+the\s+hell|from\s+hell)\b",
    ),
    ("n-word", r"\bn[i1!\*\u2022@#\$_]+g+g+(?:a|ah|er|ers|as|ahs)\b"),
    ("fag", r"\bfag(?:got|gots|s)?\b"),
    ("sexual", r"\bsex(?:y|ual|ually|uality|iest|ier)?\b"),
    (
        "explicit-sexual",
        r"\b(?:porn(?:ography|o)?|horny|orgasm|stripper|strippers|erotic|nudes|boobs|tits|titties|blowjob|handjob|masturbat(?:e|ing|ion)|condom|dildo|threesome|foreplay)\b",
    ),
    ("drugs", r"\bdrugs?\b"),
    ("cocaine", r"\bcocaine\b"),
    ("heroin", r"\bheroin\b"),
    ("meth", r"\b(?:crystal\s+)?meth(?:amphetamine)?\b"),
    (
        "substances",
        r"\b(?:weed|marijuana|xanax|percocet|oxycontin|fentanyl|codeine|ecstasy|overdose|bong|crackhead)\b",
    ),
    (
        "self-harm",
        r"\b(?:suicid(?:e|al)|(?:kill|shoot|hang)\s+myself|slit\s+(?:my|your|their)\s+wrists?)\b",
    ),
    (
        "graphic-violence",
        r"\b(?:spill\s+my\s+blood|shot\s+me\s+dead|homicide)\b",
    ),
    ("explicit-tag", r"\b(?:parental\s+advisory|uncensored|explicit\s+version|dirty\s+version|nsfw)\b"),
)

_COMPILED_BUILTIN = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in _BUILTIN_PROFANITY_PATTERNS
)


def find_profanity_matches(
    text: str,
    custom_blocked_words: Iterable[str] = (),
) -> list[str]:
    if not text:
        return []
    matches: list[str] = []
    for label, regex in _COMPILED_BUILTIN:
        if regex.search(text):
            matches.append(label)

    for word in custom_blocked_words:
        cleaned = word.strip()
        if not cleaned:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(cleaned)}(?!\w)", re.IGNORECASE)
        if pattern.search(text):
            matches.append(cleaned)

    # Deduplicate preserving order
    return list(dict.fromkeys(matches))


def is_artist_blocked(artist: str, blocked_artists: Iterable[str]) -> bool:
    if not artist:
        return False
    norm_artist = " ".join(artist.lower().split())
    for blocked in blocked_artists:
        norm_blocked = " ".join(blocked.lower().split())
        if not norm_blocked:
            continue
        if norm_blocked == norm_artist or norm_blocked in norm_artist:
            return True
    return False


def assess_request(
    *,
    query: str,
    track: Optional[CatalogTrack],
    lyrics_text: str = "",
    lyrics_checked: bool = False,
    source_url: Optional[str] = None,
    policy_mode: PolicyMode = PolicyMode.HYBRID,
    blocked_words: Iterable[str] = (),
    blocked_artists: Iterable[str] = (),
    max_track_duration_sec: int = 600,
) -> SafetyAssessment:
    reasons: list[str] = []
    matched_profanity: list[str] = []

    # 1. Check query and track metadata against blocked words / artists
    text_to_scan = " ".join(
        part
        for part in [
            query,
            track.title if track else "",
            track.artist if track else "",
            track.album if track else "",
        ]
        if part
    )
    query_hits = find_profanity_matches(text_to_scan, blocked_words)
    if query_hits:
        matched_profanity.extend(query_hits)
        reasons.append(
            f"Blocked term detected ({', '.join(query_hits)})"
        )

    if track and is_artist_blocked(track.artist, blocked_artists):
        reasons.append(f"Artist '{track.artist}' is on the parent blocklist")
    elif is_artist_blocked(query, blocked_artists):
        reasons.append("Requested artist is on the parent blocklist")

    if reasons:
        return SafetyAssessment(
            verdict=ExplicitVerdict.EXPLICIT,
            allowed_auto=False,
            requires_approval=False,
            blocked=True,
            reasons=reasons,
            matched_profanity=matched_profanity,
            clean_alternative=track.clean_alternative if track else None,
        )

    # 2. Check lyrics profanity / mature themes (catches untagged explicit tracks!)
    if lyrics_text:
        lyric_hits = find_profanity_matches(lyrics_text, blocked_words)
        if lyric_hits:
            matched_profanity.extend(lyric_hits)
            reasons.append(
                f"Explicit or mature language detected in song lyrics ({', '.join(lyric_hits)})"
            )
            return SafetyAssessment(
                verdict=ExplicitVerdict.EXPLICIT,
                allowed_auto=False,
                requires_approval=False,
                blocked=True,
                reasons=reasons,
                matched_profanity=matched_profanity,
                clean_alternative=track.clean_alternative if track else None,
            )

    # 3. Direct custom URL from kids always waits for parent (or blocks in auto_clean)
    if source_url:
        if policy_mode == PolicyMode.AUTO_CLEAN:
            return SafetyAssessment(
                verdict=ExplicitVerdict.UNVERIFIED,
                allowed_auto=False,
                requires_approval=False,
                blocked=True,
                reasons=["Direct URLs cannot be automatically verified as clean"],
            )
        return SafetyAssessment(
            verdict=ExplicitVerdict.UNVERIFIED,
            allowed_auto=False,
            requires_approval=True,
            blocked=False,
            reasons=["Direct URL requires parent approval"],
        )

    # 4. Unmatched text query
    if track is None:
        if policy_mode == PolicyMode.AUTO_CLEAN:
            return SafetyAssessment(
                verdict=ExplicitVerdict.UNVERIFIED,
                allowed_auto=False,
                requires_approval=False,
                blocked=True,
                reasons=["Song could not be matched in clean music catalog"],
            )
        return SafetyAssessment(
            verdict=ExplicitVerdict.UNVERIFIED,
            allowed_auto=False,
            requires_approval=True,
            blocked=False,
            reasons=["Song could not be verified automatically — sent to Parent Approval"],
        )

    # 5. Explicit catalog tag
    if track.explicit_verdict == ExplicitVerdict.EXPLICIT or track.is_explicit:
        msg = "Marked explicit in music catalog"
        if track.clean_alternative:
            msg += " (clean edit available)"
        return SafetyAssessment(
            verdict=ExplicitVerdict.EXPLICIT,
            allowed_auto=False,
            requires_approval=False,
            blocked=True,
            reasons=[msg],
            clean_alternative=track.clean_alternative,
        )

    # 6. Duration limit check
    if track.duration_sec > max_track_duration_sec:
        if policy_mode == PolicyMode.AUTO_CLEAN:
            return SafetyAssessment(
                verdict=track.explicit_verdict,
                allowed_auto=False,
                requires_approval=False,
                blocked=True,
                reasons=[
                    f"Track is longer than {max_track_duration_sec // 60} minute limit"
                ],
            )
        return SafetyAssessment(
            verdict=track.explicit_verdict,
            allowed_auto=False,
            requires_approval=True,
            blocked=False,
            reasons=[
                f"Track exceeds {max_track_duration_sec // 60} min limit — sent to Parent Approval"
            ],
        )

    # 7. Unverified catalog rating
    if track.explicit_verdict == ExplicitVerdict.UNVERIFIED:
        if policy_mode == PolicyMode.AUTO_CLEAN:
            return SafetyAssessment(
                verdict=ExplicitVerdict.UNVERIFIED,
                allowed_auto=False,
                requires_approval=False,
                blocked=True,
                reasons=["Explicitness rating unavailable in catalog"],
            )
        return SafetyAssessment(
            verdict=ExplicitVerdict.UNVERIFIED,
            allowed_auto=False,
            requires_approval=True,
            blocked=False,
            reasons=["Explicitness unverified — sent to Parent Approval"],
        )

    # 8. Track from a Parental Advisory / Explicit album whose lyrics could not be verified clean
    if (
        track.collection_explicit
        and track.explicit_verdict != ExplicitVerdict.CLEANED_EDIT
        and not lyrics_text
    ):
        if policy_mode == PolicyMode.AUTO_CLEAN:
            return SafetyAssessment(
                verdict=ExplicitVerdict.UNVERIFIED,
                allowed_auto=False,
                requires_approval=False,
                blocked=True,
                reasons=["Track is on an Explicit album and lyrics could not be verified"],
            )
        return SafetyAssessment(
            verdict=ExplicitVerdict.UNVERIFIED,
            allowed_auto=False,
            requires_approval=True,
            blocked=False,
            reasons=[
                "From a Parental Advisory album (lyrics unverified) — sent to Parent Approval"
            ],
        )

    # 9. Verified CLEAN or CLEANED_EDIT
    if policy_mode == PolicyMode.STRICT:
        return SafetyAssessment(
            verdict=track.explicit_verdict,
            allowed_auto=False,
            requires_approval=True,
            blocked=False,
            reasons=["Strict Parent Approval mode is enabled"],
        )

    label = (
        "Verified clean / radio edit"
        if track.explicit_verdict == ExplicitVerdict.CLEANED_EDIT
        else "Verified clean (lyrics checked)" if (lyrics_checked and lyrics_text)
        else "Verified clean in music catalog"
    )
    return SafetyAssessment(
        verdict=track.explicit_verdict,
        allowed_auto=True,
        requires_approval=False,
        blocked=False,
        reasons=[label],
    )


def assess_album(
    *,
    query: str,
    album: CatalogAlbum,
    policy_mode: PolicyMode = PolicyMode.HYBRID,
    blocked_words: Iterable[str] = (),
    blocked_artists: Iterable[str] = (),
) -> SafetyAssessment:
    reasons: list[str] = []
    matched_profanity: list[str] = []

    text_to_scan = " ".join(part for part in [query, album.title, album.artist] if part)
    hits = find_profanity_matches(text_to_scan, blocked_words)
    if hits:
        matched_profanity.extend(hits)
        reasons.append(f"Blocked term detected ({', '.join(hits)})")

    if is_artist_blocked(album.artist, blocked_artists):
        reasons.append(f"Artist '{album.artist}' is on the parent blocklist")

    if reasons:
        return SafetyAssessment(
            verdict=ExplicitVerdict.EXPLICIT,
            allowed_auto=False,
            requires_approval=False,
            blocked=True,
            reasons=reasons,
            matched_profanity=matched_profanity,
        )

    if album.is_explicit or album.explicit_verdict == ExplicitVerdict.EXPLICIT:
        msg = "Album is marked Parental Advisory / Explicit"
        if album.clean_alternative_id:
            msg += " (clean edition available)"
        return SafetyAssessment(
            verdict=ExplicitVerdict.EXPLICIT,
            allowed_auto=False,
            requires_approval=False,
            blocked=True,
            reasons=[msg],
        )

    if album.explicit_verdict == ExplicitVerdict.UNVERIFIED:
        if policy_mode == PolicyMode.AUTO_CLEAN:
            return SafetyAssessment(
                verdict=ExplicitVerdict.UNVERIFIED,
                allowed_auto=False,
                requires_approval=False,
                blocked=True,
                reasons=["Album explicitness could not be verified"],
            )
        return SafetyAssessment(
            verdict=ExplicitVerdict.UNVERIFIED,
            allowed_auto=False,
            requires_approval=True,
            blocked=False,
            reasons=["Album rating unverified — tracks checked individually"],
        )

    if policy_mode == PolicyMode.STRICT:
        return SafetyAssessment(
            verdict=album.explicit_verdict,
            allowed_auto=False,
            requires_approval=True,
            blocked=False,
            reasons=["Strict Parent Approval mode is enabled"],
        )

    label = (
        "Clean / Edited Album"
        if album.explicit_verdict == ExplicitVerdict.CLEANED_EDIT
        else "Clean Album (each track checked)"
    )
    return SafetyAssessment(
        verdict=album.explicit_verdict,
        allowed_auto=True,
        requires_approval=False,
        blocked=False,
        reasons=[label],
    )
