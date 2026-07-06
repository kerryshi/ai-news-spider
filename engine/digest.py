"""Render ranked items as a Markdown digest (clickable in VS Code) and JSON."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Item

# Human-facing digest timestamps render in Eastern wall-clock (the digest is rendered
# on the Jetson, whose system tz may differ). zoneinfo auto-handles EST/EDT; fall back
# to a fixed-offset EST only where tzdata is absent (e.g. a minimal Windows test venv).
try:
    from zoneinfo import ZoneInfo
    _DISPLAY_TZ = ZoneInfo("America/New_York")
except Exception:                                       # pragma: no cover - env-dependent
    _DISPLAY_TZ = timezone(timedelta(hours=-5), "EST")

_EMOJI = {
    "arxiv": "📄",
    "hackernews": "🟧",
    "reddit": "👽",
    "github": "🐙",
    "huggingface": "🤗",
    "lobsters": "🦞",
}


def _clean(text: str) -> str:
    """Collapse whitespace/newlines so summaries read as tidy prose."""
    return " ".join((text or "").split())


def _md_text(text: str) -> str:
    """Escape markdown link text. Two jobs: (1) `[`/`]` — AI titles routinely carry
    `[MoE]`, `[code]`, etc.; an unescaped `]` ends the link early and leaks the URL as
    literal text. (2) `&<>` — VS Code's Markdown preview renders inline HTML, so an
    unescaped `<img src=...>` in a scraped title would fire an outbound request when
    the digest is opened."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _md_body(text: str) -> str:
    """Neutralize raw HTML in untrusted body text (summaries, reasons, excerpts,
    author). Same reason as _md_text's `&<>` handling: the Markdown preview renders
    inline HTML, so scraped/LLM text must not carry a live `<img>`/`<a>` tag."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_url(url: str) -> str:
    """Percent-encode the few chars that break a `(...)` link destination so URLs
    like `.../Foo_(bar)` resolve instead of truncating at the first `)`. Also
    scheme-restrict to http(s): a scraped/LLM-supplied `javascript:`/`data:` URL would
    otherwise become a live link in VS Code's Markdown preview (mirrors _safe_href for
    the HTML path)."""
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return "#"
    return u.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _md_link(text: str, url: str) -> str:
    return f"[{_md_text(text)}]({_md_url(url)})"


def _takeaway(it: Item, max_chars: int = 130) -> str:
    """One-line gist for the scannable index: prefer the readable LLM summary,
    fall back to the earliness reason, then the raw abstract."""
    src = _clean(it.llm_summary) or _clean(it.reason) or _clean(it.summary)
    if len(src) <= max_chars:
        return src
    cut = src[:max_chars].rsplit(" ", 1)[0]
    return f"{cut}…"


def _excerpt(text: str, max_chars: int = 320) -> str:
    """Trim raw source text at a sentence boundary where possible."""
    t = _clean(text)
    if len(t) <= max_chars:
        return t
    window = t[:max_chars]
    end = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
    if end >= 80:
        return window[: end + 1]
    return f"{window.rsplit(' ', 1)[0]}…"


def _unjudged(items: list[Item]) -> int:
    """Items whose LLM judgment is missing: 0/0 scores AND no reason. A failed judge
    call leaves exactly this signature (pipeline stores zeros with an empty reason and
    never retries). Genuinely-judged zeros carry a reason; near-duplicates carry
    "near-duplicate"; the Ollama-down heuristic assigns non-zero scores — so none of
    those are counted."""
    return sum(
        1 for it in items if not _clean(it.reason) and not it.relevance and not it.earliness
    )


def render_markdown(items: list[Item], subtitle: str = "") -> str:
    now = datetime.now(_DISPLAY_TZ).strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        f"# AI early-signal digest — {now}",
        "",
        f"*{len(items)} items · ranked by velocity · novelty · relevance · earliness*",
    ]
    if subtitle:
        lines.append(f"*{subtitle}*")
    lines.append("")

    if not items:
        lines.append("_No items matched. Try a wider `--since` window or run a collect._")
        return "\n".join(lines)

    unjudged = _unjudged(items)
    if unjudged:
        lines.append(
            f"> ⚠ {unjudged} item(s) have no LLM judgment (relevance/earliness scored 0) — "
            "the judge call failed or timed out when they were collected, and they are not "
            "retried. They rank lower than they may deserve."
        )
        lines.append("")

    # ── At a glance: scan every item in seconds, click to open ──────────────
    lines.append("## ⚡ At a glance")
    lines.append("")
    for i, it in enumerate(items, 1):
        emoji = _EMOJI.get(it.source, "•")
        gist = _takeaway(it)
        tail = f" — {_md_body(gist)}" if gist else ""
        lines.append(f"{i}. {emoji} {_md_link(it.title, it.url)} · `{it.score:.2f}`{tail}")
    lines.append("\n---\n")

    # ── Detail cards: readable summary leads, source excerpt supports ───────
    for i, it in enumerate(items, 1):
        emoji = _EMOJI.get(it.source, "•")
        lines.append(f"## {i}. {emoji} {_md_link(it.title, it.url)}")
        meta = [f"**{it.score:.2f}**", it.source]
        if it.relevance or it.earliness:
            meta.append(f"rel {it.relevance:.0f} · early {it.earliness:.0f}")
        meta.append(f"vel {it.velocity:.1f}/h")
        meta.append(f"nov {it.novelty:.2f}")
        lines.append(" · ".join(meta))
        lines.append("")

        # Show BOTH the readable summary and the judge's reason: the reason is the
        # only "why is this ranked here" the digest has, and it used to disappear
        # for exactly the top items (they all have summaries).
        summary = _clean(it.llm_summary)
        reason = _clean(it.reason)
        if summary:
            lines.append(f"**What it is —** {_md_body(summary)}")
        if reason and reason != summary:
            prefix = "\n" if summary else ""
            lines.append(f"{prefix}**Why it's early —** {_md_body(reason)}")
        if it.tags:
            # Strip backticks so a tag can't break out of the inline-code span (its
            # contents are otherwise rendered literally, HTML included).
            safe_tags = [t.replace("`", "") for t in it.tags]
            lines.append(f"\n`{'` `'.join(safe_tags)}`")
        excerpt = _excerpt(it.summary)
        if excerpt and excerpt != summary:
            lines.append(f"\n> {_md_body(excerpt)}")
        byline = f"🔗 {_md_link(it.source, it.url)}"
        if it.author:
            byline += f" · {_md_body(it.author)}"
        lines.append(f"\n{byline}")
        lines.append("\n---\n")
    return "\n".join(lines)


def _h(text: str) -> str:
    """Escape text for an HTML body/attribute context."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _safe_href(url: str) -> str:
    """Allow only http(s) in the shareable HTML; anything else (javascript:, data:) ->
    '#', so the exported artifact can't carry an active non-web link."""
    u = (url or "").strip()
    return _h(u) if u.lower().startswith(("http://", "https://")) else "#"


_HTML_STYLE = (
    ":root{color-scheme:light dark}"
    "body{font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;"
    "margin:2rem auto;padding:0 1rem}"
    "h1{font-size:1.5rem;margin-bottom:.2rem}.sub{color:#888;font-size:.9rem;margin-bottom:1.5rem}"
    "ol.glance{padding-left:1.4rem}ol.glance li{margin:.3rem 0}"
    ".score{font:.8rem monospace;color:#888}"
    ".card{border-top:1px solid #8883;padding:1rem 0}.card h2{font-size:1.1rem;margin:0 0 .3rem}"
    ".meta{color:#888;font-size:.85rem;margin:.2rem 0 .5rem}.tags{color:#777;font:.8rem monospace}"
    "blockquote{border-left:3px solid #8884;margin:.6rem 0;padding-left:.8rem;color:#999}"
    "a{color:#2a7ae2;text-decoration:none}a:hover{text-decoration:underline}"
)


def render_html(items: list[Item], subtitle: str = "") -> str:
    """A self-contained HTML digest (inline CSS, no external assets) — opens in any
    browser with no engine/Jetson/network running. The portable, demoable view."""
    now = datetime.now(_DISPLAY_TZ).strftime("%Y-%m-%d %H:%M %Z")
    sub = f"{len(items)} items · ranked by velocity · novelty · relevance · earliness"
    if subtitle:
        sub += f" · {_h(subtitle)}"
    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>AI early-signal digest — {_h(now)}</title>",
        f"<style>{_HTML_STYLE}</style></head><body>",
        f"<h1>AI early-signal digest — {_h(now)}</h1>",
        f'<p class="sub">{sub}</p>',
    ]
    if not items:
        out.append("<p><em>No items matched.</em></p></body></html>")
        return "\n".join(out)

    unjudged = _unjudged(items)
    if unjudged:
        out.append(
            f'<blockquote>⚠ {unjudged} item(s) have no LLM judgment (relevance/earliness '
            "scored 0) — the judge call failed or timed out when they were collected, and "
            "they are not retried. They rank lower than they may deserve.</blockquote>"
        )

    out.append('<h2>⚡ At a glance</h2><ol class="glance">')
    for it in items:
        emoji = _EMOJI.get(it.source, "•")
        gist = _takeaway(it)
        tail = f" — {_h(gist)}" if gist else ""
        out.append(
            f'<li>{emoji} <a href="{_safe_href(it.url)}">{_h(it.title)}</a> '
            f'<span class="score">{it.score:.2f}</span>{tail}</li>'
        )
    out.append("</ol>")

    for i, it in enumerate(items, 1):
        emoji = _EMOJI.get(it.source, "•")
        out.append('<div class="card">')
        out.append(f'<h2>{i}. {emoji} <a href="{_safe_href(it.url)}">{_h(it.title)}</a></h2>')
        meta = [f"<strong>{it.score:.2f}</strong>", _h(it.source)]
        if it.relevance or it.earliness:
            meta.append(f"rel {it.relevance:.0f} · early {it.earliness:.0f}")
        meta.append(f"vel {it.velocity:.1f}/h")
        meta.append(f"nov {it.novelty:.2f}")
        out.append(f'<div class="meta">{" · ".join(meta)}</div>')
        summary = _clean(it.llm_summary)
        reason = _clean(it.reason)
        if summary:
            out.append(f"<p><strong>What it is —</strong> {_h(summary)}</p>")
        if reason and reason != summary:
            out.append(f"<p><strong>Why it's early —</strong> {_h(reason)}</p>")
        if it.tags:
            out.append(f'<div class="tags">{_h(" ".join(it.tags))}</div>')
        excerpt = _excerpt(it.summary)
        if excerpt and excerpt != summary:
            out.append(f"<blockquote>{_h(excerpt)}</blockquote>")
        byline = f'🔗 <a href="{_safe_href(it.url)}">{_h(it.source)}</a>'
        if it.author:
            byline += f" · {_h(it.author)}"
        out.append(f'<div class="meta">{byline}</div>')
        out.append("</div>")
    out.append("</body></html>")
    return "\n".join(out)


def write(items: list[Item], digest_dir: Path) -> tuple[Path, Path]:
    stamp = datetime.now(_DISPLAY_TZ).strftime("%Y%m%d-%H%M%S")
    md_path = digest_dir / f"digest-{stamp}.md"
    json_path = digest_dir / f"digest-{stamp}.json"
    md = render_markdown(items)
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(
        json.dumps([it.to_dict() for it in items], indent=2, default=str),
        encoding="utf-8",
    )
    # also keep a stable "latest" pointer for the extension to open
    (digest_dir / "latest.md").write_text(md, encoding="utf-8")
    return md_path, json_path
