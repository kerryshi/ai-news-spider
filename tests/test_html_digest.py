"""The self-contained HTML digest renders offline with no external assets."""

import re

from engine.digest import render_html
from engine.models import Item


def _item(**kw) -> Item:
    it = Item(source=kw.pop("source", "arxiv"), title=kw.pop("title", "t"),
              url=kw.pop("url", "http://x/1"), raw_domain=kw.pop("raw_domain", ""))
    for k, v in kw.items():
        setattr(it, k, v)
    return it


def test_render_html_is_self_contained_and_clickable():
    it = _item(title="A [MoE] model", url="http://example.com/p", score=1.2)
    html = render_html([it])
    assert html.startswith("<!doctype html>")
    assert '<a href="http://example.com/p">' in html          # clickable link
    assert "<style>" in html                                  # CSS is inlined
    # no EXTERNAL stylesheet/script/font/image assets — fully offline
    assert not re.search(
        r'(href|src)\s*=\s*"https?://[^"]+\.(css|js|png|jpg|woff2?)"', html, re.I
    )


def test_render_html_escapes_markup_in_titles():
    it = _item(title="<script>alert(1)</script>", url="http://x/1", score=0.5)
    html = render_html([it])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_html_empty_is_valid():
    html = render_html([])
    assert html.startswith("<!doctype html>")
    assert "No items matched" in html
