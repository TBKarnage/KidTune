from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from app.auth import hash_pin
from app.config import Settings
from app.models import (
    CatalogTrack,
    ExplicitVerdict,
    MusicRequestRecord,
    PolicyMode,
    RequestSource,
    RequestStatus,
    RuntimeSettingsView,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                yield conn
                conn.commit()
            finally:
                conn.close()

    def init_db(self, settings: Settings) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    requester_name TEXT NOT NULL DEFAULT 'Kids',
                    source TEXT NOT NULL DEFAULT 'ipad',
                    status TEXT NOT NULL,
                    status_detail TEXT NOT NULL DEFAULT '',
                    explicit_verdict TEXT NOT NULL DEFAULT 'unverified',
                    safety_reasons_json TEXT NOT NULL DEFAULT '[]',
                    title TEXT NOT NULL DEFAULT '',
                    artist TEXT NOT NULL DEFAULT '',
                    album_artist TEXT NOT NULL DEFAULT '',
                    album TEXT NOT NULL DEFAULT '',
                    year TEXT NOT NULL DEFAULT '',
                    genre TEXT NOT NULL DEFAULT '',
                    track_number INTEGER NOT NULL DEFAULT 1,
                    track_count INTEGER NOT NULL DEFAULT 1,
                    disc_number INTEGER NOT NULL DEFAULT 1,
                    disc_count INTEGER NOT NULL DEFAULT 1,
                    duration_sec INTEGER NOT NULL DEFAULT 0,
                    artwork_url TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    youtube_url TEXT NOT NULL DEFAULT '',
                    youtube_title TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    lyrics_embedded INTEGER NOT NULL DEFAULT 0,
                    plex_scanned INTEGER NOT NULL DEFAULT 0,
                    keep_item_id TEXT NOT NULL DEFAULT '',
                    catalog_metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_requests_status
                    ON requests(status, created_at);

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS keep_seen_items (
                    keep_item_id TEXT PRIMARY KEY,
                    raw_text TEXT NOT NULL,
                    request_id INTEGER,
                    processed_at TEXT NOT NULL
                );
                """
            )

        self._bootstrap_settings(settings)

    def _bootstrap_settings(self, settings: Settings) -> None:
        devices = [d.strip() for d in settings.google_cast_devices.split(",") if d.strip()]
        defaults: dict[str, Any] = {
            "policy_mode": settings.policy_mode.value,
            "prefer_clean_edits": settings.prefer_clean_edits,
            "auto_substitute_clean_edit": settings.auto_substitute_clean_edit,
            "max_track_duration_sec": settings.max_track_duration_sec,
            "max_duration_diff_sec": settings.max_duration_diff_sec,
            "kid_profiles": ["Alex", "Sam", "Family"],
            "blocked_words": [],
            "blocked_artists": [],
            "plex_url": settings.plex_url,
            "plex_token": settings.plex_token,
            "plex_music_section_id": settings.plex_music_section_id,
            "plex_library_path_prefix": settings.plex_library_path_prefix,
            "google_keep_enabled": settings.google_keep_enabled,
            "google_keep_email": settings.google_keep_email,
            "google_keep_master_token": settings.google_keep_master_token,
            "google_keep_list_name": settings.google_keep_list_name,
            "google_keep_poll_seconds": settings.google_keep_poll_seconds,
            "google_cast_enabled": settings.google_cast_enabled,
            "google_cast_devices": devices,
            "public_base_url": settings.public_base_url,
            "parent_pin_hash": hash_pin(settings.parent_pin),
            "parent_pin_is_default": settings.parent_pin == "1234",
        }
        with self._conn() as conn:
            existing = {
                row["key"]
                for row in conn.execute("SELECT key FROM app_settings").fetchall()
            }
            for key, value in defaults.items():
                if key not in existing:
                    conn.execute(
                        "INSERT INTO app_settings(key, value_json) VALUES (?, ?)",
                        (key, json.dumps(value)),
                    )

    def get_settings_dict(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
        return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def update_settings_dict(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._conn() as conn:
            for key, value in updates.items():
                conn.execute(
                    """
                    INSERT INTO app_settings(key, value_json)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                    """,
                    (key, json.dumps(value)),
                )
        return self.get_settings_dict()

    def get_settings_view(self) -> RuntimeSettingsView:
        raw = self.get_settings_dict()
        return RuntimeSettingsView(
            policy_mode=PolicyMode(raw.get("policy_mode", PolicyMode.HYBRID.value)),
            prefer_clean_edits=bool(raw.get("prefer_clean_edits", True)),
            auto_substitute_clean_edit=bool(raw.get("auto_substitute_clean_edit", False)),
            max_track_duration_sec=int(raw.get("max_track_duration_sec", 600)),
            max_duration_diff_sec=int(raw.get("max_duration_diff_sec", 20)),
            kid_profiles=list(raw.get("kid_profiles", ["Alex", "Sam", "Family"])),
            blocked_words=list(raw.get("blocked_words", [])),
            blocked_artists=list(raw.get("blocked_artists", [])),
            plex_url=str(raw.get("plex_url", "")),
            plex_token_configured=bool(raw.get("plex_token", "")),
            plex_music_section_id=str(raw.get("plex_music_section_id", "")),
            plex_library_path_prefix=str(raw.get("plex_library_path_prefix", "")),
            google_keep_enabled=bool(raw.get("google_keep_enabled", False)),
            google_keep_email=str(raw.get("google_keep_email", "")),
            google_keep_token_configured=bool(raw.get("google_keep_master_token", "")),
            google_keep_list_name=str(raw.get("google_keep_list_name", "Music Requests")),
            google_keep_poll_seconds=int(raw.get("google_keep_poll_seconds", 45)),
            google_cast_enabled=bool(raw.get("google_cast_enabled", False)),
            google_cast_devices=list(raw.get("google_cast_devices", [])),
            public_base_url=str(raw.get("public_base_url", "")),
            parent_pin_is_default=bool(raw.get("parent_pin_is_default", True)),
        )

    def _row_to_record(self, row: sqlite3.Row) -> MusicRequestRecord:
        catalog_meta = None
        if row["catalog_metadata_json"]:
            catalog_meta = CatalogTrack.model_validate_json(row["catalog_metadata_json"])
        return MusicRequestRecord(
            id=row["id"],
            query=row["query"],
            requester_name=row["requester_name"],
            source=RequestSource(row["source"]),
            status=RequestStatus(row["status"]),
            status_detail=row["status_detail"],
            explicit_verdict=ExplicitVerdict(row["explicit_verdict"]),
            safety_reasons=json.loads(row["safety_reasons_json"] or "[]"),
            title=row["title"],
            artist=row["artist"],
            album_artist=row["album_artist"],
            album=row["album"],
            year=row["year"],
            genre=row["genre"],
            track_number=row["track_number"],
            track_count=row["track_count"],
            disc_number=row["disc_number"],
            disc_count=row["disc_count"],
            duration_sec=row["duration_sec"],
            artwork_url=row["artwork_url"],
            source_url=row["source_url"],
            youtube_url=row["youtube_url"],
            youtube_title=row["youtube_title"],
            file_path=row["file_path"],
            lyrics_embedded=bool(row["lyrics_embedded"]),
            plex_scanned=bool(row["plex_scanned"]),
            keep_item_id=row["keep_item_id"],
            catalog_metadata=catalog_meta,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def create_request(
        self,
        *,
        query: str,
        requester_name: str,
        source: RequestSource,
        status: RequestStatus,
        status_detail: str = "",
        explicit_verdict: ExplicitVerdict = ExplicitVerdict.UNVERIFIED,
        safety_reasons: Optional[list[str]] = None,
        track: Optional[CatalogTrack] = None,
        source_url: str = "",
        keep_item_id: str = "",
    ) -> MusicRequestRecord:
        now = _utc_now_iso()
        catalog_json = track.model_dump_json() if track else None
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO requests (
                    query, requester_name, source, status, status_detail,
                    explicit_verdict, safety_reasons_json,
                    title, artist, album_artist, album, year, genre,
                    track_number, track_count, disc_number, disc_count,
                    duration_sec, artwork_url, source_url, keep_item_id,
                    catalog_metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query,
                    requester_name,
                    source.value,
                    status.value,
                    status_detail,
                    explicit_verdict.value,
                    json.dumps(safety_reasons or []),
                    track.title if track else query,
                    track.artist if track else "",
                    (track.album_artist or track.artist) if track else "",
                    track.album if track else "",
                    track.year if track else "",
                    track.genre if track else "",
                    track.track_number if track else 1,
                    track.track_count if track else 1,
                    track.disc_number if track else 1,
                    track.disc_count if track else 1,
                    track.duration_sec if track else 0,
                    track.artwork_url if track else "",
                    source_url,
                    keep_item_id,
                    catalog_json,
                    now,
                    now,
                ),
            )
            request_id = int(cursor.lastrowid)
        rec = self.get_request(request_id)
        assert rec is not None
        return rec

    def get_request(self, request_id: int) -> Optional[MusicRequestRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_requests(
        self,
        *,
        status: Optional[RequestStatus] = None,
        requester_name: Optional[str] = None,
        limit: int = 100,
    ) -> list[MusicRequestRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if requester_name:
            clauses.append("requester_name = ?")
            params.append(requester_name)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM requests {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def update_request(self, request_id: int, **fields: Any) -> Optional[MusicRequestRecord]:
        if not fields:
            return self.get_request(request_id)
        allowed = {
            "status",
            "status_detail",
            "explicit_verdict",
            "safety_reasons",
            "title",
            "artist",
            "album_artist",
            "album",
            "year",
            "genre",
            "track_number",
            "track_count",
            "disc_number",
            "disc_count",
            "duration_sec",
            "artwork_url",
            "source_url",
            "youtube_url",
            "youtube_title",
            "file_path",
            "lyrics_embedded",
            "plex_scanned",
            "catalog_metadata",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unsupported request field update: {key}")
            if key == "safety_reasons":
                assignments.append("safety_reasons_json = ?")
                values.append(json.dumps(value))
            elif key == "catalog_metadata":
                assignments.append("catalog_metadata_json = ?")
                values.append(value.model_dump_json() if value else None)
            elif key in {"status", "explicit_verdict"} and hasattr(value, "value"):
                assignments.append(f"{key} = ?")
                values.append(value.value)
            elif key in {"lyrics_embedded", "plex_scanned"}:
                assignments.append(f"{key} = ?")
                values.append(1 if value else 0)
            else:
                assignments.append(f"{key} = ?")
                values.append(value)
        assignments.append("updated_at = ?")
        values.append(_utc_now_iso())
        values.append(request_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE requests SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        return self.get_request(request_id)

    def claim_next_queued_request(self) -> Optional[MusicRequestRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM requests WHERE status = ? ORDER BY id ASC LIMIT 1",
                (RequestStatus.QUEUED.value,),
            ).fetchone()
            if not row:
                return None
            request_id = int(row["id"])
            conn.execute(
                "UPDATE requests SET status = ?, status_detail = ?, updated_at = ? WHERE id = ? AND status = ?",
                (
                    RequestStatus.DOWNLOADING.value,
                    "Finding clean audio stream...",
                    _utc_now_iso(),
                    request_id,
                    RequestStatus.QUEUED.value,
                ),
            )
        return self.get_request(request_id)

    def requeue_interrupted_jobs(self) -> int:
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE requests
                SET status = ?, status_detail = ?, updated_at = ?
                WHERE status IN (?, ?)
                """,
                (
                    RequestStatus.QUEUED.value,
                    "Re-queued after server restart",
                    _utc_now_iso(),
                    RequestStatus.DOWNLOADING.value,
                    RequestStatus.TAGGING.value,
                ),
            )
            return cursor.rowcount

    def has_seen_keep_item(self, keep_item_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM keep_seen_items WHERE keep_item_id = ?",
                (keep_item_id,),
            ).fetchone()
        return row is not None

    def mark_keep_item_seen(
        self, keep_item_id: str, raw_text: str, request_id: Optional[int] = None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO keep_seen_items(keep_item_id, raw_text, request_id, processed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(keep_item_id) DO NOTHING
                """,
                (keep_item_id, raw_text, request_id, _utc_now_iso()),
            )
