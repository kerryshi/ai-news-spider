"""Pure ranking functions, separated from I/O so they're easy to test.

The score blends four cached components (novelty, relevance, earliness, plus an
optional topic-query similarity) with a live engagement *velocity*, then multiplies
by a freshness decay so "best now" favours fresh, rising items.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


def velocity(series: list[tuple[datetime, float]], age_hours: float) -> float:
    """Engagement gained per hour.

    With >=2 snapshots, use the slope across the observed window (real velocity).
    With a single snapshot, fall back to total engagement over the item's age.
    """
    if len(series) >= 2:
        (t0, v0), (t1, v1) = series[0], series[-1]
        span_h = (t1 - t0).total_seconds() / 3600.0
        if span_h > 0.05:
            return max(v1 - v0, 0.0) / span_h
        return 0.0
    if series:
        return series[-1][1] / max(age_hours, 0.01)
    return 0.0


def freshness(age_hours: float, halflife_hours: float) -> float:
    """Exponential decay normalised to a true half-life: 1.0 at age 0, exactly
    0.5 at `halflife_hours`, 0.25 at twice that, etc."""
    if halflife_hours <= 0:
        return 1.0
    return math.exp(-math.log(2) * max(age_hours, 0.0) / halflife_hours)


def composite(
    item,
    *,
    age_hours: float,
    weights: dict,
    max_velocity: float,
    halflife_hours: float,
    mainstream_domains: set[str],
    penalty: float,
    query_sim: float | None = None,
) -> float:
    """Final ranking score for one item. `item.velocity` must already be set, and
    `age_hours` is how long since WE discovered it (drives the freshness decay)."""
    norm_vel = item.velocity / max_velocity if max_velocity > 0 else 0.0
    base = (
        weights.get("velocity", 0.0) * norm_vel
        + weights.get("novelty", 0.0) * item.novelty
        + weights.get("relevance", 0.0) * (item.relevance / 10.0)
        + weights.get("earliness", 0.0) * (item.earliness / 10.0)
    )
    if query_sim is not None:
        base += weights.get("query", 0.0) * max(query_sim, 0.0)

    score = base * freshness(age_hours, halflife_hours)
    if item.raw_domain in mainstream_domains:
        score *= penalty
    return score
