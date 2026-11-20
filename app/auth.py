from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from fastapi import Cookie, Header, HTTPException, status

SESSION_COOKIE_NAME = "kidtune_parent_session"
SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 hours


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        pin.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return (
        "scrypt$"
        + base64.urlsafe_b64encode(salt).decode("ascii")
        + "$"
        + base64.urlsafe_b64encode(digest).decode("ascii")
    )


def verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored_hash.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.scrypt(
            pin.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_session_token(secret: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    payload = {
        "role": "parent",
        "exp": int(time.time()) + ttl_seconds,
        "nonce": secrets.token_hex(8),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    sig = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def verify_session_token(token: Optional[str], secret: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        padded_sig = sig_b64 + "=" * (-len(sig_b64) % 4)
        actual_sig = base64.urlsafe_b64decode(padded_sig.encode("ascii"))
        if not hmac.compare_digest(actual_sig, expected_sig):
            return False
        padded_payload = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded_payload.encode("ascii")))
        return (
            payload.get("role") == "parent"
            and int(payload.get("exp", 0)) > int(time.time())
        )
    except Exception:
        return False


def make_parent_auth_dependency(secret: str):
    def require_parent(
        kidtune_parent_session: Optional[str] = Cookie(default=None),
        x_parent_token: Optional[str] = Header(default=None),
    ) -> None:
        token = x_parent_token or kidtune_parent_session
        if not verify_session_token(token, secret):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Parent PIN authentication required",
            )

    return require_parent
