"""Hacker News via Algolia: the `by date` feed catches rising stories early.
Free, no key."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ..models import Item
from .base import get, domain_of

API = "http://hn.algolia.com/api/v1/search_by_date"


def fetch(settings: dict) -> list[Item]:
    # Algolia's `query` is full-text, not boolean — so run one query per term and
    # merge. `queries` is a list; falls back to splitting a legacy `query` string.
    queries = settings.get("queries")
    if not queries:
        queries = [t.strip(' "') for t in settings.get("query", "AI").split(" OR ")]
    per_query = int(settings.get("max_results", 60))
    min_points = int(settings.get("min_points", 3))
    since = int(time.time()) - 3 * 24 * 3600

    items: dict[str, Item] = {}
    for term in queries:
        try:
            data = get(
                API,
                params={
                    "query": term,
                    "tags": "story",
                    "numericFilters": f"created_at_i>{since}",
                    "hitsPerPage": per_query,
                },
            ).json()
        except Exception:
            continue
        for h in data.get("hits", []):
            oid = h.get("objectID")
            if not oid or oid in items:
                continue
            points = h.get("points") or 0
            if points < min_points:
                continue
            story_url = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
            items[oid] = Item(
                source="hackernews",
                title=(h.get("title") or "").strip(),
                url=story_url,
                summary=(h.get("story_text") or "")[:800],
                author=h.get("author", ""),
                created_at=datetime.fromtimestamp(h["created_at_i"], tz=timezone.utc),
                engagement=float(points + (h.get("num_comments") or 0)),
                raw_domain=domain_of(story_url),
            )
    return list(items.values())
