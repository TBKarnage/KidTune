from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
import uuid
from pathlib import Path
from typing import Iterable

import pychromecast
from gtts import gTTS

logger = logging.getLogger(__name__)


def _split_devices(devices: Iterable[str]) -> tuple[list[str], list[str]]:
    friendly_names: list[str] = []
    known_hosts: list[str] = []
    for entry in devices:
        cleaned = entry.strip()
        if not cleaned:
            continue
        try:
            ipaddress.ip_address(cleaned)
            known_hosts.append(cleaned)
        except ValueError:
            friendly_names.append(cleaned)
    return friendly_names, known_hosts


def _discover_devices_sync(timeout_sec: float = 4.0) -> list[dict[str, str]]:
    chromecasts, browser = pychromecast.get_chromecasts(discovery_timeout=timeout_sec)
    try:
        results: list[dict[str, str]] = []
        for cast in chromecasts:
            info = cast.cast_info
            results.append(
                {
                    "name": info.friendly_name or cast.name or "Google Home",
                    "model": info.model_name or "",
                    "host": info.host or "",
                    "uuid": str(info.uuid),
                }
            )
        return results
    finally:
        pychromecast.discovery.stop_discovery(browser)


def _generate_tts_sync(message: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tts = gTTS(text=message, lang="en")
    tts.save(str(output_path))
    return output_path


def _cleanup_old_tts(tts_dir: Path, max_age_sec: int = 3600) -> None:
    if not tts_dir.exists():
        return
    cutoff = time.time() - max_age_sec
    for path in tts_dir.glob("*.mp3"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _cast_audio_sync(
    *,
    audio_url: str,
    devices: list[str],
    title: str = "KidTune Announcement",
) -> list[str]:
    friendly_names, known_hosts = _split_devices(devices)
    if friendly_names:
        casts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=friendly_names,
            known_hosts=known_hosts or None,
            discovery_timeout=5.0,
        )
    elif known_hosts:
        casts, browser = pychromecast.get_chromecasts(
            known_hosts=known_hosts,
            discovery_timeout=5.0,
        )
    else:
        casts, browser = pychromecast.get_chromecasts(discovery_timeout=5.0)

    announced_to: list[str] = []
    try:
        for cast in casts:
            try:
                cast.wait(timeout=5.0)
                mc = cast.media_controller
                mc.play_media(audio_url, "audio/mpeg", title=title)
                mc.block_until_active(timeout=5.0)
                announced_to.append(cast.cast_info.friendly_name or cast.name)
            except Exception as exc:
                logger.warning("Cast playback failed on %s: %s", cast.name, exc)
    finally:
        pychromecast.discovery.stop_discovery(browser)

    return announced_to


async def discover_cast_devices() -> list[dict[str, str]]:
    return await asyncio.to_thread(_discover_devices_sync, 4.0)


async def announce_on_google_home(
    *,
    message: str,
    tts_dir: Path,
    public_base_url: str,
    devices: list[str],
) -> list[str]:
    if not message.strip() or not public_base_url.strip():
        return []
    _cleanup_old_tts(tts_dir)
    filename = f"{uuid.uuid4().hex}.mp3"
    output_path = tts_dir / filename
    await asyncio.to_thread(_generate_tts_sync, message.strip(), output_path)

    audio_url = f"{public_base_url.rstrip('/')}/api/tts/{filename}"
    return await asyncio.to_thread(
        _cast_audio_sync,
        audio_url=audio_url,
        devices=devices,
        title="KidTune",
    )
