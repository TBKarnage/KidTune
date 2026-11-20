from __future__ import annotations

import re
from typing import Iterable, Optional

from app.models import (
    CatalogTrack,
    ExplicitVerdict,
    PolicyMode,
    SafetyAssessment,
)

# Whole-word explicit profanity & severe explicit theme patterns.
# Keep entries deliberate so ordinary kid-safe words are never false-positived.
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
    ("bullshit", r"\bbull\s*shit\b"),
    ("n-word", r"\bn[i1!\*\u2022@#\$_]+g+g+(?:a|ah|er|ers|as|ahs)\b"),
    ("fag", r"\bfag(?:got|gots|s)?\b"),
    ("porn", r"\bporn(?:ography|o)?\b"),
    ("cocaine", r"\bcocaine\b"),
    ("heroin", r"\bheroin\b"),
    ("meth", r"\bcrystal\s+meth\b"),
    ("explicit-tag", r"\b(?:parental\s+advisory|uncensored|explicit\s+version|dirty\s+version)\b"),
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

    # 2. Check lyrics profanity (catches untagged explicit tracks!)
    if lyrics_text:
        lyric_hits = find_profanity_matches(lyrics_text, blocked_words)
        if lyric_hits:
            matched_profanity.extend(lyric_hits)
            reasons.append(
                f"Explicit language detected in song lyrics ({', '.join(lyric_hits)})"
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

    # 8. Verified CLEAN or CLEANED_EDIT
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
        else "Verified clean in music catalog"
    )
    return SafetyAssessment(
        verdict=track.explicit_verdict,
        allowed_auto=True,
        requires_approval=False,
        blocked=False,
        reasons=[label],
    )
