from __future__ import annotations

import os
from typing import Any

import httpx


def make_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient resilient to unbracketed IPv6 NO_PROXY entries like '::1'."""
    try:
        return httpx.AsyncClient(**kwargs)
    except httpx.InvalidURL:
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        kwargs["trust_env"] = False
        if proxy and "proxy" not in kwargs:
            kwargs["proxy"] = proxy
        return httpx.AsyncClient(**kwargs)
