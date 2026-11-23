from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PolicyMode(str, Enum):
    HYBRID = "hybrid"
    STRICT = "strict"
    AUTO_CLEAN = "auto_clean"


class RequestSource(str, Enum):
    IPAD = "ipad"
    GOOGLE_HOME = "google_home"
    PARENT = "parent"


class RequestStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    TAGGING = "tagging"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    DECLINED = "declined"
    FAILED = "failed"


class ExplicitVerdict(str, Enum):
    CLEAN = "clean"
    CLEANED_EDIT = "cleaned_edit"
    EXPLICIT = "explicit"
    UNVERIFIED = "unverified"


class CatalogTrack(BaseModel):
    provider: str = "itunes"
    track_id: str = ""
    collection_id: str = ""
    title: str
    artist: str
    album_artist: str = ""
    album: str = ""
    year: str = ""
    genre: str = ""
    track_number: int = 1
    track_count: int = 1
    disc_number: int = 1
    disc_count: int = 1
    duration_sec: int = 0
    explicit_verdict: ExplicitVerdict = ExplicitVerdict.UNVERIFIED
    is_explicit: bool = False
    collection_explicit: bool = False
    artwork_url: str = ""
    preview_url: str = ""
    musicbrainz_recording_id: str = ""
    musicbrainz_release_id: str = ""
    clean_alternative: Optional["CatalogTrack"] = None


CatalogTrack.model_rebuild()


class CatalogAlbum(BaseModel):
    provider: str = "itunes"
    album_id: str = ""
    title: str
    artist: str
    year: str = ""
    genre: str = ""
    track_count: int = 0
    explicit_verdict: ExplicitVerdict = ExplicitVerdict.UNVERIFIED
    is_explicit: bool = False
    artwork_url: str = ""
    clean_alternative_id: str = ""


class SafetyAssessment(BaseModel):
    verdict: ExplicitVerdict
    allowed_auto: bool
    requires_approval: bool
    blocked: bool
    reasons: list[str] = Field(default_factory=list)
    matched_profanity: list[str] = Field(default_factory=list)
    clean_alternative: Optional[CatalogTrack] = None


class SearchTrackResult(BaseModel):
    track: CatalogTrack
    safety: SafetyAssessment


class SearchAlbumResult(BaseModel):
    album: CatalogAlbum
    safety: SafetyAssessment


class AlbumDetailsResult(BaseModel):
    album: CatalogAlbum
    safety: SafetyAssessment
    tracks: list[SearchTrackResult] = Field(default_factory=list)


class MusicRequestCreate(BaseModel):
    query: str = Field(..., min_length=1, max_length=300)
    requester_name: str = Field(default="Kids", min_length=1, max_length=40)
    source: RequestSource = RequestSource.IPAD
    catalog_track: Optional[CatalogTrack] = None
    source_url: Optional[str] = None
    use_clean_alternative: bool = False
    parent_override: bool = False


class AlbumRequestCreate(BaseModel):
    provider: str = Field(default="itunes", pattern="^(itunes|deezer)$")
    album_id: str = Field(..., min_length=1, max_length=80)
    requester_name: str = Field(default="Kids", min_length=1, max_length=40)
    source: RequestSource = RequestSource.IPAD
    skip_explicit: bool = True
    parent_override: bool = False


class AlbumRequestResponse(BaseModel):
    album_title: str
    artist: str
    total_tracks: int
    queued_count: int
    pending_count: int
    blocked_count: int
    skipped_explicit_count: int = 0
    requests: list["MusicRequestRecord"] = Field(default_factory=list)


class RequestCleanupPayload(BaseModel):
    scope: str = Field(default="finished", pattern="^(completed|errors|finished)$")


class RequestCleanupResponse(BaseModel):
    deleted_count: int
    scope: str


class LibraryCoverItem(BaseModel):
    id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    url: str


class MusicRequestRecord(BaseModel):
    id: int
    query: str
    requester_name: str
    source: RequestSource
    status: RequestStatus
    status_detail: str = ""
    explicit_verdict: ExplicitVerdict = ExplicitVerdict.UNVERIFIED
    safety_reasons: list[str] = Field(default_factory=list)
    title: str = ""
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    year: str = ""
    genre: str = ""
    track_number: int = 1
    track_count: int = 1
    disc_number: int = 1
    disc_count: int = 1
    duration_sec: int = 0
    artwork_url: str = ""
    source_url: str = ""
    youtube_url: str = ""
    youtube_title: str = ""
    file_path: str = ""
    lyrics_embedded: bool = False
    plex_scanned: bool = False
    keep_item_id: str = ""
    catalog_metadata: Optional[CatalogTrack] = None
    created_at: datetime
    updated_at: datetime


AlbumRequestResponse.model_rebuild()


class ParentLoginRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=20)


class PinChangeRequest(BaseModel):
    current_pin: str = Field(..., min_length=4, max_length=20)
    new_pin: str = Field(..., min_length=4, max_length=20)


class ApproveRequestPayload(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    album_artist: Optional[str] = None
    album: Optional[str] = None
    year: Optional[str] = None
    genre: Optional[str] = None
    track_number: Optional[int] = Field(default=None, ge=1, le=999)
    disc_number: Optional[int] = Field(default=None, ge=1, le=99)
    source_url: Optional[str] = None
    use_clean_alternative: bool = False
    override_explicit: bool = False


class DeclineRequestPayload(BaseModel):
    reason: str = Field(default="Not approved by parent", max_length=200)


class RuntimeSettingsView(BaseModel):
    policy_mode: PolicyMode = PolicyMode.HYBRID
    prefer_clean_edits: bool = True
    auto_substitute_clean_edit: bool = False
    max_track_duration_sec: int = 600
    max_duration_diff_sec: int = 20
    kid_profiles: list[str] = Field(default_factory=lambda: ["Alex", "Sam", "Family"])
    blocked_words: list[str] = Field(default_factory=list)
    blocked_artists: list[str] = Field(default_factory=list)
    plex_url: str = ""
    plex_token_configured: bool = False
    plex_music_section_id: str = ""
    plex_library_path_prefix: str = ""
    google_keep_enabled: bool = False
    google_keep_email: str = ""
    google_keep_token_configured: bool = False
    google_keep_list_name: str = "Music Requests"
    google_keep_poll_seconds: int = 45
    google_cast_enabled: bool = False
    google_cast_devices: list[str] = Field(default_factory=list)
    public_base_url: str = ""
    parent_pin_is_default: bool = True


class RuntimeSettingsUpdate(BaseModel):
    policy_mode: Optional[PolicyMode] = None
    prefer_clean_edits: Optional[bool] = None
    auto_substitute_clean_edit: Optional[bool] = None
    max_track_duration_sec: Optional[int] = Field(default=None, ge=60, le=3600)
    max_duration_diff_sec: Optional[int] = Field(default=None, ge=5, le=120)
    kid_profiles: Optional[list[str]] = None
    blocked_words: Optional[list[str]] = None
    blocked_artists: Optional[list[str]] = None
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    plex_music_section_id: Optional[str] = None
    plex_library_path_prefix: Optional[str] = None
    google_keep_enabled: Optional[bool] = None
    google_keep_email: Optional[str] = None
    google_keep_master_token: Optional[str] = None
    google_keep_list_name: Optional[str] = None
    google_keep_poll_seconds: Optional[int] = Field(default=None, ge=15, le=3600)
    google_cast_enabled: Optional[bool] = None
    google_cast_devices: Optional[list[str]] = None
    public_base_url: Optional[str] = None


class CastAnnounceRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=300)
    devices: Optional[list[str]] = None
