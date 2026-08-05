"""Secret generation helpers for Thorondor env files."""

from __future__ import annotations

import base64
import os
import secrets


def generate_searxng_secret() -> str:
    """Generate a deploy-script-compatible SearXNG secret."""
    return base64.b64encode(os.urandom(32)).decode("ascii").translate(
        str.maketrans("", "", "+/=")
    )


def generate_crawl4ai_api_key() -> str:
    return secrets.token_hex(32)
