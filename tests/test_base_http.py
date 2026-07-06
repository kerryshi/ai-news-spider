"""The shared HTTP helper caps response bodies so a malicious/MITM'd upstream can't
OOM the low-RAM Jetson collector, and still exposes .text/.json for normal bodies.
Regression guard for the base.get() streaming size cap."""

import httpx
import pytest

from engine.sources import base


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_rejects_oversized_body(monkeypatch):
    big = b"x" * (base.MAX_RESPONSE_BYTES + 1024)
    monkeypatch.setattr(base, "_client", _client(lambda req: httpx.Response(200, content=big)))
    with pytest.raises(ValueError, match="MiB cap"):
        base.get("https://example.test/huge")


def test_get_allows_normal_body_and_exposes_text_json(monkeypatch):
    payload = b'{"hits": [{"objectID": "1"}]}'
    monkeypatch.setattr(
        base, "_client",
        _client(lambda req: httpx.Response(200, content=payload,
                                           headers={"content-type": "application/json"})),
    )
    resp = base.get("https://example.test/ok")
    assert resp.json()["hits"][0]["objectID"] == "1"   # .json() works post-stream
    assert "objectID" in resp.text                       # .text works post-stream


def test_get_allows_body_at_the_limit(monkeypatch):
    exact = b"y" * base.MAX_RESPONSE_BYTES
    monkeypatch.setattr(base, "_client", _client(lambda req: httpx.Response(200, content=exact)))
    resp = base.get("https://example.test/edge")
    assert len(resp.content) == base.MAX_RESPONSE_BYTES   # at-limit is allowed


def test_get_blocks_redirect_to_loopback(monkeypatch):
    # A compromised/MITM'd upstream that 302-redirects a feed fetch at an internal
    # endpoint (e.g. Ollama on 127.0.0.1:11434) must be refused before the hop is sent.
    monkeypatch.setattr(
        base, "_client",
        _client(lambda req: httpx.Response(302, headers={"location": "http://127.0.0.1:11434/api"})),
    )
    with pytest.raises(ValueError, match="non-public"):
        base.get("https://feeds.example.test/start")


def test_get_blocks_redirect_to_private_range(monkeypatch):
    monkeypatch.setattr(
        base, "_client",
        _client(lambda req: httpx.Response(301, headers={"location": "http://10.0.0.5/secret"})),
    )
    with pytest.raises(ValueError, match="non-public"):
        base.get("https://feeds.example.test/start")


def test_get_blocks_redirect_to_non_http_scheme(monkeypatch):
    # A redirect to file:// / gopher:// etc. must be refused, not followed.
    monkeypatch.setattr(
        base, "_client",
        _client(lambda req: httpx.Response(302, headers={"location": "file:///etc/passwd"})),
    )
    with pytest.raises(ValueError, match="non-http"):
        base.get("https://feeds.example.test/start")


def test_get_follows_public_redirect(monkeypatch):
    # Legitimate public redirects (e.g. http->https, link shorteners) still work.
    def handler(req):
        if req.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.test/final"})
        return httpx.Response(200, content=b"landed")
    monkeypatch.setattr(base, "_client", _client(handler))
    resp = base.get("https://example.test/start")
    assert resp.content == b"landed"   # followed the hop to a public target


def test_is_blocked_host_classification():
    assert base._is_blocked_host("127.0.0.1")
    assert base._is_blocked_host("10.0.0.1")
    assert base._is_blocked_host("169.254.169.254")   # cloud-metadata / link-local
    assert base._is_blocked_host("::1")
    assert base._is_blocked_host("")                   # empty host -> refuse
    assert not base._is_blocked_host("93.184.216.34")  # a public literal is allowed
