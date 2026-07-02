"""Local web view for the AI early-signal digest.

Serves the latest ranked digest as auto-refreshing HTML on 127.0.0.1 (private to
this machine by design). The VS Code extension writes the latest `top --json`
result to a shared temp file on every Top / Collect now / timer refresh; this
server reads that file fresh on each request and renders it with
`engine.digest.render_html`. So hitting "Collect now" in the extension (which
re-ranks and rewrites the file) updates this page on its next auto-refresh —
hands-free, no page reload needed.

    python scripts/serve.py                       # http://127.0.0.1:8765
    python scripts/serve.py --port 9000 --refresh 15
    python scripts/serve.py --data /path/to/ai-signal-latest.json

Stdlib only (http.server) — no third-party deps. Run it on the DESKTOP, e.g.
    .venv/Scripts/python.exe scripts/serve.py
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import tempfile
from dataclasses import fields as dc_fields
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make `engine` importable when this is run as a loose script from any cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine.digest import render_html  # noqa: E402  (after sys.path setup)
from engine.models import Item  # noqa: E402

DEFAULT_PORT = 8765
DEFAULT_REFRESH_S = 30
DEFAULT_DATA = Path(tempfile.gettempdir()) / "ai-signal-latest.json"

# Item is a dataclass; to_dict() adds an `id` *property* that is not a field, so
# Item(**payload) would raise. Filter to real fields when reconstructing.
_ITEM_FIELDS = {f.name for f in dc_fields(Item)}


def _item_from_dict(d: dict) -> Item:
    """Rebuild an Item from a `to_dict()` payload: drop non-field keys (e.g. `id`)
    and parse the ISO `created_at` back into a datetime."""
    kw = {k: v for k, v in d.items() if k in _ITEM_FIELDS}
    ca = kw.get("created_at")
    if isinstance(ca, str) and ca:
        try:
            kw["created_at"] = datetime.fromisoformat(ca)
        except ValueError:
            kw["created_at"] = None
    return Item(**kw)


def _inject_autorefresh(html: str, seconds: int) -> str:
    """Insert a meta-refresh so an open tab reloads itself and picks up new data.
    Done here, not in render_html, so the portable/portfolio HTML export stays
    static (it must not auto-reload when opened from disk)."""
    if seconds <= 0:
        return html
    tag = f'<meta http-equiv="refresh" content="{seconds}">'
    return html.replace("</head>", f"{tag}</head>", 1)


_PLACEHOLDER = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    "<title>AI Signal — waiting</title>"
    "<style>body{{font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;"
    "max-width:640px;margin:4rem auto;padding:0 1rem;color-scheme:light dark}}"
    "code{{background:#8882;padding:.1rem .3rem;border-radius:3px}}</style></head>"
    "<body><h1>AI Signal — no digest yet</h1>"
    "<p>Waiting for the first ranking. In VS Code run "
    "<strong>AI Signal: Top now</strong> or <strong>Collect now</strong> "
    "(it also auto-fetches on startup); this page then refreshes itself.</p>"
    '<p style="color:#888;font-size:.9rem">Looking for <code>{data}</code></p>'
    "</body></html>"
)


def _placeholder(path: Path, refresh_s: int) -> str:
    # html-escape the path (defense-in-depth; it's interpolated into HTML).
    return _inject_autorefresh(_PLACEHOLDER.format(data=html.escape(str(path))), refresh_s)


def render_page(data_path, refresh_s: int = DEFAULT_REFRESH_S) -> str:
    """Render the current digest page from the latest-JSON file. Returns an
    auto-refreshing HTML string, or a placeholder (also auto-refreshing) when the
    file is missing/unreadable/malformed. Reads fresh on every call so new data
    appears as soon as the extension writes it. NEVER raises — any reconstruction
    or render failure degrades to the placeholder so the served page can't 500."""
    path = Path(data_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return _placeholder(path, refresh_s)

    if isinstance(raw, dict):
        items_raw = raw.get("items", [])
        subtitle = f"topic: {raw['query']}" if raw.get("query") else ""
    else:  # tolerate a bare list of items
        items_raw = raw
        subtitle = ""
    if not isinstance(items_raw, list):
        items_raw = []

    # Skip field-deficient items (Item(**d) needs source/title/url) but keep the
    # rest; then guard the render so a bad field *type* (e.g. score="x") still
    # degrades to the placeholder instead of dropping the HTTP connection.
    items = []
    for d in items_raw:
        if not isinstance(d, dict):
            continue
        try:
            items.append(_item_from_dict(d))
        except (TypeError, ValueError):
            continue
    try:
        rendered = render_html(items, subtitle)
    except (TypeError, ValueError, AttributeError):
        return _placeholder(path, refresh_s)
    return _inject_autorefresh(rendered, refresh_s)


class _Handler(BaseHTTPRequestHandler):
    data_path: Path = DEFAULT_DATA
    refresh_s: int = DEFAULT_REFRESH_S

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path in ("/", "/index.html"):
            body = render_page(self.data_path, self.refresh_s).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_error(404)

    def log_message(self, *args):  # silence the per-request stderr spam
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="ai-signal-serve", description="Local web view for the AI signal digest."
    )
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--host", default="127.0.0.1",
        help="bind address (default localhost-only; do not expose publicly)",
    )
    ap.add_argument(
        "--data", default=str(DEFAULT_DATA),
        help="path to the latest `top --json` file the extension writes",
    )
    ap.add_argument(
        "--refresh", type=int, default=DEFAULT_REFRESH_S,
        help="page auto-reload interval in seconds (0 = no auto-reload)",
    )
    args = ap.parse_args(argv)

    _Handler.data_path = Path(args.data)
    _Handler.refresh_s = args.refresh
    httpd = ThreadingHTTPServer((args.host, args.port), _Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"AI Signal web view → {url}")
    print(f"  data: {args.data}  ·  auto-reload: {args.refresh}s")
    print("  Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
