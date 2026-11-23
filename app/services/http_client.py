from __future__ import annotations

import os
import ssl
from typing import Any

import httpx


def _default_ssl_context() -> ssl.SSLContext:
    ca_file = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca_file and os.path.isfile(ca_file):
        return ssl.create_default_context(cafile=ca_file)
    return ssl.create_default_context()


def make_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient resilient to unbracketed IPv6 NO_PROXY entries like '::1'."""
    if "verify" not in kwargs:
        kwargs["verify"] = _default_ssl_context()
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
