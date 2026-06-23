"""Core data model shared across all sources."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class Item:
    """A single candidate piece of AI news from any source."""

    source: str                 # "arxiv" | "hackernews" | "reddit" | "github" | "huggingface"
    title: str
    url: str
    summary: str = ""
    author: str = ""
    created_at: datetime | None = None   # when the item was published, UTC
    engagement: float = 0.0     # source-native count: points, upvotes, stars, etc.
    raw_domain: str = ""        # domain the item ultimately points to (for mainstream suppression)

    # ---- filled in by the pipeline ----
    velocity: float = 0.0       # engagement per hour of age
    novelty: float = 1.0        # 0..1, 1 = nothing like it seen before
    relevance: float = 0.0      # 0..10 from the local LLM
    earliness: float = 0.0      # 0..10 from the local LLM
    score: float = 0.0          # final ranking score
    reason: str = ""            # one-line LLM justification
    tags: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        """Stable id based on the canonical URL (used for dedup across runs)."""
        return hashlib.sha1(self.url.strip().lower().encode("utf-8")).hexdigest()

    @property
    def age_hours(self) -> float:
        if not self.created_at:
            return 999.0
        now = datetime.now(timezone.utc)
        return max((now - self.created_at).total_seconds() / 3600.0, 0.01)

    def text_for_embedding(self) -> str:
        return f"{self.title}\n\n{self.summary}".strip()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        d["id"] = self.id
        return d
