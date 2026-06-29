"""Pure ranking functions, separated from I/O so they're easy to test.

The score blends four cached components (novelty, relevance, earliness, plus an
optional topic-query similarity) with a live engagement *velocity*, then multiplies
by a freshness decay so "best now" favours fresh, rising items.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

_BASE_KEYS = ("velocity", "novelty", "relevance", "earliness")


def normalize_weights(weights: dict) -> dict:
    """Return a copy scaled so the four base components sum to 1.0.

    ALL weights — including the additive 'query' bump — are scaled by the same factor,
    so this is a pure uniform rescale: ranking ORDER is unchanged (query-mode included),
    it just puts the base on a clean 0..1 footing. A zero/empty base falls back to equal
    base weights so ranking never divides by zero or silently sums to <1."""
    total = sum(float(weights.get(k, 0.0)) for k in _BASE_KEYS)
    out = dict(weights)
    if total > 0:
        for k in out:
            out[k] = float(out[k]) / total
    else:
        for k in _BASE_KEYS:
            out[k] = 1.0 / len(_BASE_KEYS)
    return out


def _clamp10(value: float) -> float:
    """Clamp a 0-10 LLM score into range; a malformed value -> 0. Guards against a model
    that returns e.g. 50 or a non-number, which would otherwise inflate the composite."""
    try:
        return max(0.0, min(10.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def velocity_from_endpoints(
    n: int, first_val: float, last_val: float, span_hours: float, age_hours: float
) -> float:
    """Engagement gained per hour, from just the first & last snapshot of a series.

    Identical math to velocity() but driven by precomputed endpoints, so ranking can
    resolve all items in ONE batched query instead of one query per item (the N+1).
    """
    if n >= 2:
        if span_hours > 0.05:
            return max(last_val - first_val, 0.0) / span_hours
        return 0.0
    if n == 1:
        return last_val / max(age_hours, 0.01)
    return 0.0


def velocity(series: list[tuple[datetime, float]], age_hours: float) -> float:
    """Engagement gained per hour.

    With >=2 snapshots, use the slope across the observed window (real velocity).
    With a single snapshot, fall back to total engagement over the item's age.
    """
    if len(series) >= 2:
        (t0, v0), (t1, v1) = series[0], series[-1]
        span_h = (t1 - t0).total_seconds() / 3600.0
        return velocity_from_endpoints(len(series), v0, v1, span_h, age_hours)
    if series:
        return velocity_from_endpoints(1, series[-1][1], series[-1][1], 0.0, age_hours)
    return velocity_from_endpoints(0, 0.0, 0.0, 0.0, age_hours)


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
        + weights.get("relevance", 0.0) * (_clamp10(item.relevance) / 10.0)
        + weights.get("earliness", 0.0) * (_clamp10(item.earliness) / 10.0)
    )
    if query_sim is not None:
        base += weights.get("query", 0.0) * max(query_sim, 0.0)

    score = base * freshness(age_hours, halflife_hours)
    if item.raw_domain in mainstream_domains:
        score *= penalty
    return score
