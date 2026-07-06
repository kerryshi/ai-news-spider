"""Shared HTTP helper for source adapters."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

# A browser-like UA: several sources (Reddit especially) 403 obvious bot agents.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Feeds and JSON APIs here are small (KBs–low MBs). A malicious or MITM'd upstream
# could otherwise return a multi-GB body (or a decompression bomb) that httpx reads
# straight into memory before we ever call .json()/.text — OOM-killing the low-RAM
# Jetson collector. Cap the body while streaming and abort past the limit.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MiB

_client = httpx.Client(
    headers={"User-Agent": USER_AGENT},
    timeout=30.0,
    follow_redirects=True,
)


def get(url: str, **kwargs) -> httpx.Response:
    """GET with a hard body-size cap. The response is streamed and refused once it
    exceeds MAX_RESPONSE_BYTES (measured on the decompressed bytes, so it also bounds
    gzip bombs). The fully-read bytes are attached to the response, so callers keep
    using .text / .json() exactly as before."""
    with _client.stream("GET", url, **kwargs) as resp:
        body = bytearray()
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"response body exceeded {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB cap: {url}"
                )
        # Mirrors what httpx.Response.read() does internally; makes .content/.text/.json
        # available after we've consumed the stream ourselves.
        resp._content = bytes(body)
    return resp


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""
