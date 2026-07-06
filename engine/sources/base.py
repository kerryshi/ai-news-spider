"""Shared HTTP helper for source adapters."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

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

_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5

# follow_redirects=False: we follow manually so each redirect *target* can be vetted
# against private/loopback/reserved ranges before the next request is sent. The initial
# URL is caller-supplied (hardcoded public feeds); only redirect hops — which a
# malicious/compromised/MITM'd upstream controls — are the SSRF vector we guard against.
_client = httpx.Client(
    headers={"User-Agent": USER_AGENT},
    timeout=30.0,
    follow_redirects=False,
)


def _is_blocked_host(host: str) -> bool:
    """True if host is (or resolves to) a private/loopback/link-local/reserved address —
    an SSRF redirect target we refuse. Protects local services (e.g. Ollama on
    127.0.0.1:11434) from an upstream that 302-redirects a feed fetch at an internal
    endpoint. Unresolvable hosts are NOT blocked here (httpx surfaces that error normally)."""
    if not host:
        return True
    ips = []
    try:
        ips.append(ipaddress.ip_address(host))
    except ValueError:
        try:
            for info in socket.getaddrinfo(host, None):
                try:
                    ips.append(ipaddress.ip_address(info[4][0].split("%")[0]))
                except ValueError:
                    pass
        except (socket.gaierror, OSError):
            return False
    return any(
        not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        for ip in ips
    )


def get(url: str, **kwargs) -> httpx.Response:
    """GET with a hard body-size cap AND SSRF-safe redirect following. Redirects are
    followed manually (max _MAX_REDIRECTS); each hop's target host is checked against
    private/loopback/reserved ranges before the request is sent. The body is streamed and
    refused past MAX_RESPONSE_BYTES (also bounds gzip bombs). .text/.json work as before."""
    current = url
    extra = kwargs  # request options apply to the initial call only, not redirect hops
    for _ in range(_MAX_REDIRECTS + 1):
        with _client.stream("GET", current, **extra) as resp:
            if resp.status_code in _REDIRECT_STATUS and "location" in resp.headers:
                nxt = urljoin(current, resp.headers["location"])
                parts = urlparse(nxt)
                # Follow only http(s) hops to public hosts. Accepted residual risk: httpx
                # re-resolves the host on connect, so a TTL-0 DNS rebind could differ from
                # this check — fine for hardcoded public feeds on a home collector; if the
                # threat model changes, pin the vetted IP instead of re-resolving.
                if parts.scheme not in ("http", "https") or _is_blocked_host(parts.hostname or ""):
                    raise ValueError(f"refusing redirect to non-public/non-http host: {nxt}")
                current, extra = nxt, {}
                continue
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
    raise ValueError(f"too many redirects (>{_MAX_REDIRECTS}): {url}")


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""
