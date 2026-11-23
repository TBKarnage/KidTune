from __future__ import annotations

import secrets
import socket
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models import PolicyMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "KidTune"
    host: str = "0.0.0.0"
    port: int = 8484
    public_base_url: str = ""

    data_dir: Path = Path("./data")
    music_dir: Path = Path("./music")

    parent_pin: str = "1234"
    session_secret: str = ""

    policy_mode: PolicyMode = PolicyMode.HYBRID
    prefer_clean_edits: bool = True
    auto_substitute_clean_edit: bool = False
    max_track_duration_sec: int = 600
    max_duration_diff_sec: int = 20

    plex_url: str = ""
    plex_token: str = ""
    plex_music_section_id: str = ""
    plex_library_path_prefix: str = ""

    google_keep_enabled: bool = False
    google_keep_email: str = ""
    google_keep_master_token: str = ""
    google_keep_list_name: str = "Music Requests"
    google_keep_poll_seconds: int = 45

    google_cast_enabled: bool = False
    google_cast_devices: str = ""

    enable_background_worker: bool = True
    check_lyrics_on_search: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kidtune.db"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def tts_dir(self) -> Path:
        return self.data_dir / "tts"

    @property
    def artwork_cache_dir(self) -> Path:
        return self.data_dir / "artwork_cache"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.tts_dir.mkdir(parents=True, exist_ok=True)
        self.artwork_cache_dir.mkdir(parents=True, exist_ok=True)
        self.music_dir.mkdir(parents=True, exist_ok=True)

    def resolve_session_secret(self) -> str:
        if self.session_secret:
            return self.session_secret
        self.ensure_directories()
        secret_file = self.data_dir / ".session_secret"
        if secret_file.exists():
            stored = secret_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        generated = secrets.token_urlsafe(48)
        secret_file.write_text(generated, encoding="utf-8")
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
        return generated


def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def resolve_public_base_url(configured: str, port: int) -> str:
    cleaned = configured.strip().rstrip("/")
    if cleaned:
        return cleaned
    return f"http://{detect_lan_ip()}:{port}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
