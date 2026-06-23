"""Hugging Face: trending models + the daily papers feed. Free, no key."""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Item
from .base import get

MODELS_API = "https://huggingface.co/api/models"
PAPERS_API = "https://huggingface.co/api/daily_papers"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fetch(settings: dict) -> list[Item]:
    items: list[Item] = []

    limit = int(settings.get("models_limit", 40))
    try:
        models = get(
            f"{MODELS_API}?sort=trendingScore&direction=-1&limit={limit}"
        ).json()
        for m in models:
            mid = m.get("modelId") or m.get("id", "")
            if not mid:
                continue
            items.append(
                Item(
                    source="huggingface",
                    title=f"HF model: {mid}",
                    url=f"https://huggingface.co/{mid}",
                    summary=", ".join(m.get("tags", [])[:8]),
                    author=mid.split("/")[0] if "/" in mid else "",
                    created_at=_parse_dt(m.get("createdAt")),
                    engagement=float(m.get("likes", 0) + m.get("downloads", 0) / 1000.0),
                    raw_domain="huggingface.co",
                )
            )
    except Exception:
        pass

    if settings.get("include_daily_papers", True):
        try:
            papers = get(PAPERS_API).json()
            for entry in papers:
                p = entry.get("paper", entry)
                pid = p.get("id", "")
                if not pid:
                    continue
                items.append(
                    Item(
                        source="huggingface",
                        title=f"HF paper: {p.get('title', '').strip()}",
                        url=f"https://huggingface.co/papers/{pid}",
                        summary=(p.get("summary") or "")[:1000],
                        author=", ".join(
                            a.get("name", "") for a in p.get("authors", [])[:3]
                        ),
                        created_at=_parse_dt(entry.get("publishedAt")),
                        engagement=float(p.get("upvotes", 0)),
                        raw_domain="huggingface.co",
                    )
                )
        except Exception:
            pass

    return items
