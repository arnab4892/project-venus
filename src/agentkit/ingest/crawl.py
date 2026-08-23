"""Fetching + hashing (LLD-ING-01/04).

The shipped crawler is driven by ``sources.yaml`` (the depth-2 discovery that
produced it is a one-time bootstrap). Each source is fetched once, hashed with
SHA-256 of its raw bytes, and a failed fetch is *recorded, not fatal*.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx

from .sources import kind_for


@dataclass
class Fetched:
    url: str
    kind: str
    ok: bool
    content: bytes | None
    sha256: str | None
    error: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, *, client: httpx.Client, user_agent: str | None = None) -> Fetched:
    """Fetch one URL; never raises — network/HTTP errors return ``ok=False``."""
    headers = {"User-Agent": user_agent} if user_agent else {}
    try:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — any fetch failure is recorded, not fatal
        return Fetched(url=url, kind=kind_for(url), ok=False, content=None, sha256=None, error=str(exc))

    content = resp.content
    kind = kind_for(url)
    if "pdf" in resp.headers.get("content-type", "").lower():
        kind = "pdf"
    return Fetched(url=url, kind=kind, ok=True, content=content, sha256=sha256_bytes(content))
