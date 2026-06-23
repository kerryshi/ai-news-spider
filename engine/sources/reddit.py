"""Reddit via public RSS feeds. Reddit now 403s its `.json` endpoints for
non-OAuth clients, but `.rss` still serves with a browser UA. r/LocalLLaMA in
particular breaks open-model news very early.

Note: RSS carries no vote/comment counts, so Reddit items rank on novelty +
relevance + earliness rather than vote-velocity. For true vote-velocity, add an
OAuth (PRAW) path later — see README.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import feedparser

from ..models import Item
from .base import get, domain_of

_TAG = re.compile(r"<[^>]+>")
_OUTBOUND = re.compile(r'href="(https?://[^"]+)"[^>]*>\[link\]', re.I)


def fetch(settings: dict) -> list[Item]:
    subs = settings.get("subreddits", ["LocalLLaMA", "MachineLearning"])
    listing = settings.get("listing", "new")
    limit = int(settings.get("limit_per_sub", 40))

    items: list[Item] = []
    for sub in subs:
        url = f"https://www.reddit.com/r/{sub}/{listing}/.rss?limit={limit}"
        try:
            resp = get(url)
            feed = feedparser.parse(resp.text)
        except Exception:
            continue
        for e in feed.entries:
            created = None
            if getattr(e, "published_parsed", None):
                created = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            content = ""
            if getattr(e, "content", None):
                content = e.content[0].value
            elif getattr(e, "summary", None):
                content = e.summary
            # pull the outbound link (if it's a link post) for mainstream suppression
            m = _OUTBOUND.search(content)
            ext_domain = domain_of(m.group(1)) if m else "reddit.com"
            author = getattr(e, "author", "")
            items.append(
                Item(
                    source="reddit",
                    title=getattr(e, "title", "").strip(),
                    url=getattr(e, "link", ""),
                    summary=_TAG.sub(" ", content).strip()[:800],
                    author=f"{author} (r/{sub})",
                    created_at=created,
                    engagement=0.0,  # not exposed via RSS
                    raw_domain=ext_domain,
                )
            )
    return items
