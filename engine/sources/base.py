"""Shared HTTP helper for source adapters."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

# A browser-like UA: several sources (Reddit especially) 403 obvious bot agents.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_client = httpx.Client(
    headers={"User-Agent": USER_AGENT},
    timeout=30.0,
    follow_redirects=True,
)


def get(url: str, **kwargs) -> httpx.Response:
    return _client.get(url, **kwargs)


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""
