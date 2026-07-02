"""The local web-view server (scripts/serve.py) renders the latest digest from the
shared JSON file the extension writes, and degrades to a placeholder when absent."""

import importlib.util
import json
import pathlib
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

# scripts/ is not a package; load serve.py by path so this works regardless of
# how pytest is invoked.
_SERVE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "serve.py"
_spec = importlib.util.spec_from_file_location("ai_signal_serve", _SERVE)
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)


def _payload(tmp_path, **overrides):
    """A realistic `top --json` item dict, including the `id` property that
    to_dict() emits (the server must drop it)."""
    item = {
        "source": "arxiv",
        "title": "A [MoE] breakthrough",
        "url": "http://example.com/p",
        "summary": "the abstract text",
        "author": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engagement": 0.0,
        "raw_domain": "example.com",
        "velocity": 1.0,
        "novelty": 0.5,
        "relevance": 8.0,
        "earliness": 6.0,
        "score": 1.23,
        "reason": "novel",
        "llm_summary": "what it is",
        "tags": ["ml"],
        "id": "deadbeef",  # @property on Item — Item(**d) raises unless filtered
    }
    body = {"items": [item], "query": "agents"}
    body.update(overrides)
    p = tmp_path / "latest.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def test_render_page_from_json(tmp_path):
    html = serve.render_page(_payload(tmp_path), refresh_s=20)
    assert html.startswith("<!doctype html>")
    assert "A [MoE] breakthrough" in html          # rendered via real render_html
    assert 'http-equiv="refresh" content="20"' in html
    assert "topic: agents" in html                 # subtitle derived from query


def test_render_page_drops_id_property(tmp_path):
    # Regression: to_dict() emits `id`, a read-only @property; reconstructing with
    # Item(**dict) raises TypeError unless the server filters non-field keys.
    serve.render_page(_payload(tmp_path))  # must not raise


def test_render_page_missing_file_is_placeholder(tmp_path):
    html = serve.render_page(tmp_path / "nope.json", refresh_s=10)
    assert "no digest yet" in html.lower()
    assert 'http-equiv="refresh" content="10"' in html  # placeholder still self-heals


def test_render_page_malformed_json_is_placeholder(tmp_path):
    p = tmp_path / "latest.json"
    p.write_text("{not json", encoding="utf-8")
    assert "no digest yet" in serve.render_page(p).lower()


def test_render_page_tolerates_bare_list(tmp_path):
    p = tmp_path / "latest.json"
    p.write_text(
        json.dumps([{"source": "hackernews", "title": "Bare item", "url": "http://x/1"}]),
        encoding="utf-8",
    )
    assert "Bare item" in serve.render_page(p)


def test_render_page_refresh_off(tmp_path):
    html = serve.render_page(_payload(tmp_path), refresh_s=0)
    assert 'http-equiv="refresh"' not in html


def test_render_page_empty_items_is_valid(tmp_path):
    p = _payload(tmp_path, items=[])
    html = serve.render_page(p)
    assert html.startswith("<!doctype html>")
    assert "No items matched" in html


# ── malformed-but-structurally-valid payloads must degrade, never crash ──────
def test_render_page_skips_field_deficient_item_keeps_valid(tmp_path):
    # Regression: an item dict missing required fields (source/title/url) makes
    # Item(**d) raise TypeError. The bad item is skipped; valid ones still render.
    p = _payload(tmp_path, items=[
        {"source": "hackernews", "title": "Good one", "url": "http://x/1"},
        {"title": "missing source and url"},
    ])
    out = serve.render_page(p)
    assert "Good one" in out                    # valid item preserved
    assert "missing source and url" not in out  # field-deficient item dropped


def test_render_page_all_invalid_items_no_raise(tmp_path):
    p = _payload(tmp_path, items=[{"title": "only title"}])
    out = serve.render_page(p)                   # must not raise
    assert out.startswith("<!doctype html>")
    assert "No items matched" in out


def test_render_page_bad_field_type_is_placeholder(tmp_path):
    # Regression: a non-numeric value for a float field passes Item(**d) but makes
    # render_html's f-format raise ValueError — must degrade to the placeholder.
    p = _payload(tmp_path, items=[
        {"source": "hackernews", "title": "x", "url": "http://x/1", "score": "BAD"},
    ])
    assert "no digest yet" in serve.render_page(p).lower()


# ── the live HTTP handler (routing, headers, Content-Length on multibyte) ────
def _boot_server(tmp_path):
    """Boot the real _Handler on an ephemeral 127.0.0.1 port in a daemon thread."""
    data = _payload(tmp_path, query="日本語", items=[{
        "source": "arxiv", "title": "Café señales", "url": "http://example.com/x",
        "summary": "s", "score": 0.5, "velocity": 1.0, "novelty": 0.2,
        "relevance": 3.0, "earliness": 2.0, "tags": [], "created_at": None,
    }])
    serve._Handler.data_path = data
    serve._Handler.refresh_s = 30
    httpd = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", httpd


def test_http_handler_serves_routes_and_headers(tmp_path):
    base, httpd = _boot_server(tmp_path)
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            body = r.read()
            assert r.status == 200
            assert r.headers["Content-Type"] == "text/html; charset=utf-8"
            # Content-Length must equal the UTF-8 byte length (multibyte-safe).
            assert int(r.headers["Content-Length"]) == len(body)
            text = body.decode("utf-8")
            assert "Café señales" in text and "topic: 日本語" in text
            assert 'http-equiv="refresh"' in text
        with urllib.request.urlopen(base + "/healthz", timeout=5) as r:
            assert r.status == 200 and r.read() == b"ok"
        try:
            urllib.request.urlopen(base + "/nope", timeout=5)
            assert False, "expected 404 for unknown path"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_webport_default_matches_server_default():
    # Integration guard: the extension's webPort default and the server's
    # DEFAULT_PORT must agree, or "Open web view" points at a dead port.
    pkg = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "extension" / "package.json")
        .read_text(encoding="utf-8")
    )
    web_port = pkg["contributes"]["configuration"]["properties"]["aiSignal.webPort"]["default"]
    assert web_port == serve.DEFAULT_PORT
