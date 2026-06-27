"""Reddit via public RSS feeds. Reddit now 403s its `.json` endpoints for
non-OAuth clients, but `.rss` still serves with a browser UA. r/LocalLLaMA in
particular breaks open-model news very early.

Reddit aggressively rate-limits (HTTP 429) unauthenticated RSS from a single IP,
and sends no Retry-After header. We pace requests and retry each sub with a short
exponential backoff so a cycle pulls several subs instead of just one, and we warn
on stderr (-> collect.log) for any sub still throttled — previously these failed
SILENTLY and the collector looked healthy while most subs returned nothing.

Note: RSS carries no vote/comment counts, so Reddit items rank on novelty +
relevance + earliness rather than vote-velocity. The robust fix for both throttling
and vote-velocity is an OAuth (PRAW) path — see README.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone

import feedparser

from ..models import Item
from .base import get, domain_of

_TAG = re.compile(r"<[^>]+>")
_OUTBOUND = re.compile(r'href="(https?://[^"]+)"[^>]*>\[link\]', re.I)


def _fetch_feed(url: str, max_retries: int) -> tuple[feedparser.FeedParserDict | None, int]:
    """Return (parsed_feed, last_status). Retries 429s with exponential backoff,
    honoring Retry-After when present."""
    status = 0
    for attempt in range(max_retries + 1):
        try:
            resp = get(url)
        except Exception:
            return None, status
        status = resp.status_code
        if status == 200:
            return feedparser.parse(resp.text), status
        if status != 429 or attempt == max_retries:
            return None, status
        backoff = 1.5 * (2 ** attempt)
        ra = resp.headers.get("retry-after")
        if ra:
            try:                       # Retry-After may be an HTTP-date, not seconds
                backoff = float(ra)
            except (TypeError, ValueError):
                pass
        time.sleep(backoff)
    return None, status


def fetch(settings: dict) -> list[Item]:
    subs = settings.get("subreddits", ["LocalLLaMA", "MachineLearning"])
    listing = settings.get("listing", "new")
    limit = int(settings.get("limit_per_sub", 40))
    delay = float(settings.get("request_delay", 1.0))   # pacing between subs (s)
    max_retries = int(settings.get("max_retries", 2))

    items: list[Item] = []
    throttled: list[str] = []
    for i, sub in enumerate(subs):
        if i:
            time.sleep(delay)
        url = f"https://www.reddit.com/r/{sub}/{listing}/.rss?limit={limit}"
        feed, status = _fetch_feed(url, max_retries)
        if feed is None:
            throttled.append(f"{sub}({status})")
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
    if throttled:
        print(
            f"  reddit: {len(subs) - len(throttled)}/{len(subs)} subs ok; "
            f"throttled: {', '.join(throttled)}",
            file=sys.stderr, flush=True,
        )
    return items
