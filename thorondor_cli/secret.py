"""Secret generation helpers for Thorondor env files."""

from __future__ import annotations

import base64
import os


def generate_searxng_secret() -> str:
    """Generate a deploy-script-compatible SearXNG secret."""
    return base64.b64encode(os.urandom(32)).decode("ascii").translate(
        str.maketrans("", "", "+/=")
    )
