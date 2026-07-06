"""The Markdown digest must neutralize raw HTML in scraped/LLM text: VS Code's
Markdown preview renders inline HTML, so a live `<img>`/`<a>` tag in an item title or
summary would fire an outbound request (tracking beacon) the moment the digest opens.
Regression guard for the digest.py escaping fix."""

from engine.digest import render_markdown
from engine.models import Item


def _item(**kw) -> Item:
    it = Item(source=kw.pop("source", "arxiv"), title=kw.pop("title", "t"),
              url=kw.pop("url", "http://x/1"), raw_domain=kw.pop("raw_domain", ""))
    for k, v in kw.items():
        setattr(it, k, v)
    return it


def test_markdown_escapes_html_in_title():
    it = _item(title='<img src="http://evil/beacon.gif">', url="http://x/1", score=0.5)
    md = render_markdown([it])
    assert '<img src=' not in md          # no live tag survives
    assert "&lt;img" in md                # rendered as literal text instead


def test_markdown_escapes_html_in_summary_and_reason():
    it = _item(
        title="ok",
        url="http://x/1",
        score=0.5,
        llm_summary='hi <img src="http://evil/x.gif?a=1"> there',
        reason='<a href="http://evil">click</a>',
    )
    md = render_markdown([it])
    assert "<img src=" not in md
    assert "<a href=" not in md
    assert "&lt;img" in md and "&lt;a" in md


def test_markdown_escapes_html_in_excerpt_and_author():
    it = _item(
        title="ok",
        url="http://x/1",
        score=0.5,
        summary="body <script>alert(1)</script> text",
        author="<b>evil</b>",
    )
    md = render_markdown([it])
    assert "<script>" not in md
    assert "<b>evil</b>" not in md


def test_markdown_still_links_titles_with_brackets():
    # The escaping must not regress the existing `[MoE]` link-text handling.
    it = _item(title="A [MoE] model", url="http://example.com/p", score=1.2)
    md = render_markdown([it])
    assert "(http://example.com/p)" in md   # link destination intact
    assert "\\[MoE\\]" in md                 # brackets escaped, link not broken


def test_markdown_drops_dangerous_url_schemes():
    # A scraped/LLM-supplied javascript:/data: URL must not become a live link in the
    # Markdown preview — it is neutralized to '#', mirroring the HTML path's _safe_href.
    for bad in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
                "  JavaScript:alert(1)"):
        it = _item(title="click me", url=bad, score=0.5)
        md = render_markdown([it])
        assert "javascript:" not in md.lower()
        assert "data:text/html" not in md.lower()
        assert "](#)" in md                  # destination replaced with a safe anchor


def test_markdown_keeps_https_urls():
    it = _item(title="ok", url="https://example.com/paper", score=0.5)
    md = render_markdown([it])
    assert "(https://example.com/paper)" in md   # legitimate http(s) links untouched
