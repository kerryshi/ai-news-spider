"""Lobsters (lobste.rs) — a small, high-signal HN-like community. Its AI/ML tag
feeds surface technical posts early. Free JSON, no key."""

from __future__ import annotations

from datetime import datetime

from ..models import Item
from .base import get, domain_of


def fetch(settings: dict) -> list[Item]:
    tags = settings.get("tags", ["ai", "ml"])
    min_score = int(settings.get("min_score", 1))

    items: dict[str, Item] = {}
    for tag in tags:
        try:
            data = get(f"https://lobste.rs/t/{tag}.json").json()
        except Exception:
            continue
        for s in data:
            sid = s.get("short_id")
            if not sid or sid in items:
                continue
            score = s.get("score") or 0
            if score < min_score:
                continue
            url = s.get("url") or f"https://lobste.rs/s/{sid}"
            created = None
            ca = s.get("created_at")
            if ca:
                try:
                    created = datetime.fromisoformat(ca.replace("Z", "+00:00"))
                except Exception:
                    created = None
            items[sid] = Item(
                source="lobsters",
                title=(s.get("title") or "").strip(),
                url=url,
                summary=(s.get("description") or "")[:600],
                author=", ".join(s.get("tags", [])[:4]),
                created_at=created,
                engagement=float(score + (s.get("comment_count") or 0)),
                raw_domain=domain_of(url),
            )
    return list(items.values())
