#!/usr/bin/env python3
"""Helper script to obtain a Google Master Token for gkeepapi.

Run interactively on your host or inside the KidTune container:
    python scripts/get_keep_token.py
"""

from __future__ import annotations

import getpass
import secrets
import sys

import gpsoauth


def main() -> int:
    print("KidTune — Google Keep Master Token Helper")
    print("=" * 45)
    print(
        "Google Keep has no official consumer API, so gkeepapi uses a Master Token.\n"
        "You can authenticate with either:\n"
        "  1) A Google OAuth token (from accounts.google.com/EmbeddedSetup) [Recommended]\n"
        "  2) A Google App Password (myaccount.google.com/apppasswords)\n"
    )
    email = input("Google Account Email: ").strip()
    if not email:
        print("Email is required.")
        return 1

    mode = input("Method [1 = OAuth Token, 2 = App Password] (default 1): ").strip() or "1"
    android_id = secrets.token_hex(8)

    if mode == "1":
        print(
            "\nOpen https://accounts.google.com/EmbeddedSetup in an incognito browser,\n"
            "sign in, and copy the 'oauth_token' cookie value from Developer Tools.\n"
        )
        oauth_token = getpass.getpass("Paste oauth_token: ").strip()
        res = gpsoauth.exchange_token(email, oauth_token, android_id)
    else:
        app_password = getpass.getpass("Paste 16-char Google App Password: ").strip()
        res = gpsoauth.perform_master_login(email, app_password, android_id)

    token = res.get("Token")
    if not token:
        print(f"\nFailed to obtain Master Token: {res}")
        return 1

    print("\nSuccess! Add these to your .env or KidTune Parent Settings:\n")
    print(f"GOOGLE_KEEP_ENABLED=true")
    print(f"GOOGLE_KEEP_EMAIL={email}")
    print(f"GOOGLE_KEEP_MASTER_TOKEN={token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
