"""GitHub: newly created repos gaining stars fast — tooling shows up here before
it's announced anywhere. Optional GITHUB_TOKEN env var raises the rate limit."""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from ..models import Item
from .base import get

API = "https://api.github.com/search/repositories"


def fetch(settings: dict) -> list[Item]:
    topics = settings.get("topics", ["llm", "ai-agents"])
    min_stars = int(settings.get("min_stars", 8))
    max_results = int(settings.get("max_results", 50))
    recent = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    per_topic = max(min(max_results // max(len(topics), 1), 50), 10)

    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Multiple `topic:` qualifiers are ANDed by GitHub, so query each topic and
    # merge (dedup by repo id).
    items: dict[int, Item] = {}
    for topic in topics:
        q = f"created:>{recent} stars:>={min_stars} topic:{topic}"
        try:
            data = get(
                API,
                params={"q": q, "sort": "stars", "order": "desc", "per_page": per_topic},
                headers=headers,
            ).json()
        except Exception:
            continue
        for r in data.get("items", []):
            rid = r.get("id")
            if rid is None or rid in items:
                continue
            items[rid] = Item(
                source="github",
                title=f"{r['full_name']} — {r.get('description') or ''}".strip(" —"),
                url=r["html_url"],
                summary=(r.get("description") or "")[:600],
                author=r.get("owner", {}).get("login", ""),
                created_at=datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")),
                engagement=float(r.get("stargazers_count", 0)),
                raw_domain="github.com",
            )
    return list(items.values())
