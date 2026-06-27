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
    """Escape characters that would prematurely close markdown link text. AI titles
    routinely carry `[MoE]`, `[code]`, etc.; an unescaped `]` ends the link early and
    leaks the URL as literal text."""
    return (text or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _md_url(url: str) -> str:
    """Percent-encode the few chars that break a `(...)` link destination so URLs
    like `.../Foo_(bar)` resolve instead of truncating at the first `)`."""
    return (url or "").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


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

    # ── At a glance: scan every item in seconds, click to open ──────────────
    lines.append("## ⚡ At a glance")
    lines.append("")
    for i, it in enumerate(items, 1):
        emoji = _EMOJI.get(it.source, "•")
        gist = _takeaway(it)
        tail = f" — {gist}" if gist else ""
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

        summary = _clean(it.llm_summary)
        if summary:
            lines.append(f"**What it is —** {summary}")
        elif it.reason:
            lines.append(f"**Why it's early —** {_clean(it.reason)}")
        if it.tags:
            lines.append(f"\n`{'` `'.join(it.tags)}`")
        excerpt = _excerpt(it.summary)
        if excerpt and excerpt != summary:
            lines.append(f"\n> {excerpt}")
        byline = f"🔗 {_md_link(it.source, it.url)}"
        if it.author:
            byline += f" · {it.author}"
        lines.append(f"\n{byline}")
        lines.append("\n---\n")
    return "\n".join(lines)


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
