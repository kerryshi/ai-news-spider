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
