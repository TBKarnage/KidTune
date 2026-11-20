from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import gkeepapi

logger = logging.getLogger(__name__)

_ANNOTATION_SUFFIX_RE = re.compile(
    r"\s*\[(Added|Blocked|Waiting for parent|Failed|Queued)[^\]]*\]\s*$",
    re.IGNORECASE,
)


@dataclass
class KeepListEntry:
    item_id: str
    raw_text: str
    cleaned_query: str


def clean_keep_item_text(raw_text: str) -> str:
    text = _ANNOTATION_SUFFIX_RE.sub("", (raw_text or "").strip())
    return " ".join(text.split())


def _find_target_list(keep: gkeepapi.Keep, list_name: str):
    target_lower = " ".join(list_name.lower().split())
    for note in keep.all():
        if note.trashed or note.archived:
            continue
        if isinstance(note, gkeepapi.node.List):
            title = " ".join((note.title or "").lower().split())
            if title == target_lower:
                return note
    return None


def _poll_keep_sync(
    *,
    email: str,
    master_token: str,
    list_name: str,
) -> list[KeepListEntry]:
    keep = gkeepapi.Keep()
    keep.authenticate(email, master_token, sync=True)
    target = _find_target_list(keep, list_name)
    if target is None:
        logger.debug("Google Keep list %r not found", list_name)
        return []

    entries: list[KeepListEntry] = []
    for item in target.unchecked:
        raw_text = (item.text or "").strip()
        cleaned = clean_keep_item_text(raw_text)
        if not cleaned:
            continue
        entries.append(
            KeepListEntry(
                item_id=str(item.id),
                raw_text=raw_text,
                cleaned_query=cleaned,
            )
        )
    return entries


def _update_keep_item_sync(
    *,
    email: str,
    master_token: str,
    list_name: str,
    item_id: str,
    check_off: bool,
    status_tag: Optional[str] = None,
) -> None:
    keep = gkeepapi.Keep()
    keep.authenticate(email, master_token, sync=True)
    target = _find_target_list(keep, list_name)
    if target is None:
        return

    for item in target.items:
        if str(item.id) == item_id:
            if status_tag:
                base = clean_keep_item_text(item.text or "")
                item.text = f"{base} [{status_tag}]"
            if check_off:
                item.checked = True
            keep.sync()
            return


async def fetch_unchecked_keep_requests(
    *,
    email: str,
    master_token: str,
    list_name: str,
) -> list[KeepListEntry]:
    if not email or not master_token or not list_name:
        return []
    return await asyncio.to_thread(
        _poll_keep_sync,
        email=email,
        master_token=master_token,
        list_name=list_name,
    )


async def update_keep_item_status(
    *,
    email: str,
    master_token: str,
    list_name: str,
    item_id: str,
    check_off: bool,
    status_tag: Optional[str] = None,
) -> None:
    if not email or not master_token or not list_name or not item_id:
        return
    try:
        await asyncio.to_thread(
            _update_keep_item_sync,
            email=email,
            master_token=master_token,
            list_name=list_name,
            item_id=item_id,
            check_off=check_off,
            status_tag=status_tag,
        )
    except Exception as exc:
        logger.warning("Failed updating Google Keep item %s: %s", item_id, exc)
