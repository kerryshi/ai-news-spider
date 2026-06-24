"""Render ranked items as a Markdown digest (clickable in VS Code) and JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Item

_EMOJI = {
    "arxiv": "📄",
    "hackernews": "🟧",
    "reddit": "👽",
    "github": "🐙",
    "huggingface": "🤗",
    "lobsters": "🦞",
}


def render_markdown(items: list[Item], subtitle: str = "") -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# AI early-signal digest — {now}",
        "",
        f"*{len(items)} items, ranked by velocity · novelty · relevance · earliness.*",
    ]
    if subtitle:
        lines.append(f"*{subtitle}*")
    lines.append("")
    for i, it in enumerate(items, 1):
        emoji = _EMOJI.get(it.source, "•")
        lines.append(f"## {i}. {emoji} {it.title}")
        meta = [f"**score {it.score:.2f}**", f"{it.source}"]
        if it.relevance or it.earliness:
            meta.append(f"rel {it.relevance:.0f}/10 · early {it.earliness:.0f}/10")
        meta.append(f"vel {it.velocity:.1f}/h")
        meta.append(f"nov {it.novelty:.2f}")
        lines.append(" · ".join(meta))
        lines.append("")
        if it.reason:
            lines.append(f"> {it.reason}")
        if it.tags:
            lines.append(f"`{'` `'.join(it.tags)}`")
        if it.summary:
            lines.append(f"\n{it.summary[:280].strip()}…")
        lines.append(f"\n🔗 {it.url}")
        if it.author:
            lines.append(f"— {it.author}")
        lines.append("\n---\n")
    return "\n".join(lines)


def write(items: list[Item], digest_dir: Path) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = digest_dir / f"digest-{stamp}.md"
    json_path = digest_dir / f"digest-{stamp}.json"
    md_path.write_text(render_markdown(items), encoding="utf-8")
    json_path.write_text(
        json.dumps([it.to_dict() for it in items], indent=2, default=str),
        encoding="utf-8",
    )
    # also keep a stable "latest" pointer for the extension to open
    latest = digest_dir / "latest.md"
    latest.write_text(render_markdown(items), encoding="utf-8")
    return md_path, json_path
