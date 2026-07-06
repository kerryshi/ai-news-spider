"""arXiv: freshest cs.* submissions — research before it's news. Free, no key."""

from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from ..models import Item
from .base import get

API = "https://export.arxiv.org/api/query"


def fetch(settings: dict) -> list[Item]:
    cats = settings.get("categories", ["cs.LG", "cs.AI", "cs.CL"])
    max_results = int(settings.get("max_results", 60))
    search = "+OR+".join(f"cat:{c}" for c in cats)
    url = (
        f"{API}?search_query={search}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )
    resp = get(url)
    feed = feedparser.parse(resp.text)

    items: list[Item] = []
    for e in feed.entries:
        published = None
        if getattr(e, "published_parsed", None):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        authors = ", ".join(a.get("name", "") for a in getattr(e, "authors", [])[:3])
        items.append(
            Item(
                source="arxiv",
                title=e.title.replace("\n", " ").strip(),
                url=e.link,
                summary=getattr(e, "summary", "").replace("\n", " ").strip()[:1200],
                author=authors,
                created_at=published,
                engagement=0.0,  # arXiv has no native engagement; earliness carries it
                raw_domain="arxiv.org",
            )
        )
    return items
