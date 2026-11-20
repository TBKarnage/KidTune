from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import httpx

from app.services.http_client import make_async_client

logger = logging.getLogger(__name__)


async def discover_music_sections(
    plex_url: str,
    plex_token: str,
) -> list[dict[str, str]]:
    base = plex_url.strip().rstrip("/")
    token = plex_token.strip()
    if not base or not token:
        return []

    async with make_async_client(timeout=8.0) as client:
        resp = await client.get(
            f"{base}/library/sections",
            headers={"X-Plex-Token": token},
        )
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    sections: list[dict[str, str]] = []
    for directory in root.findall(".//Directory"):
        if directory.attrib.get("type") == "artist":
            locations = [loc.attrib.get("path", "") for loc in directory.findall("./Location")]
            sections.append(
                {
                    "id": directory.attrib.get("key", ""),
                    "title": directory.attrib.get("title", "Music"),
                    "paths": ", ".join(p for p in locations if p),
                }
            )
    return sections


def translate_path_for_plex(
    *,
    album_dir: Path,
    local_music_dir: Path,
    plex_library_path_prefix: str = "",
) -> str:
    prefix = plex_library_path_prefix.strip().rstrip("/")
    if not prefix:
        return str(album_dir)
    try:
        rel = album_dir.resolve().relative_to(local_music_dir.resolve())
        return f"{prefix}/{rel.as_posix()}"
    except ValueError:
        return str(album_dir)


async def trigger_library_scan(
    *,
    plex_url: str,
    plex_token: str,
    section_id: str,
    album_dir: Optional[Path] = None,
    local_music_dir: Optional[Path] = None,
    plex_library_path_prefix: str = "",
) -> bool:
    base = plex_url.strip().rstrip("/")
    token = plex_token.strip()
    sec = section_id.strip()
    if not base or not token:
        return False

    if not sec:
        sections = await discover_music_sections(base, token)
        if not sections:
            logger.warning("No Plex Music library section found")
            return False
        sec = sections[0]["id"]

    params: dict[str, str] = {}
    if album_dir and local_music_dir:
        params["path"] = translate_path_for_plex(
            album_dir=album_dir,
            local_music_dir=local_music_dir,
            plex_library_path_prefix=plex_library_path_prefix,
        )

    async with make_async_client(timeout=8.0) as client:
        resp = await client.get(
            f"{base}/library/sections/{sec}/refresh",
            params=params,
            headers={"X-Plex-Token": token},
        )
        resp.raise_for_status()
    return True
