"""Ranking math — the heart of the scoring. Guards against regressions in the
velocity / freshness / composite logic."""

from datetime import datetime, timezone, timedelta

from engine.models import Item
from engine.ranking import velocity, freshness, composite, normalize_weights


def _item(**kw) -> Item:
    it = Item(source=kw.pop("source", "hackernews"), title=kw.pop("title", "t"),
              url=kw.pop("url", "http://x/1"), raw_domain=kw.pop("raw_domain", ""))
    for k, v in kw.items():
        setattr(it, k, v)
    return it


def test_freshness_is_a_true_halflife():
    assert freshness(0, 18) == 1.0
    assert abs(freshness(18, 18) - 0.5) < 1e-9
    assert abs(freshness(36, 18) - 0.25) < 1e-9
    assert freshness(10, 0) == 1.0  # guard against div-by-zero halflife


def test_velocity_multi_snapshot_is_slope():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=5)
    # +50 engagement over 5h = 10/h
    assert velocity([(t0, 10.0), (t1, 60.0)], age_hours=5.0) == 10.0


def test_velocity_single_snapshot_falls_back_to_age():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert velocity([(t0, 20.0)], age_hours=4.0) == 5.0


def test_velocity_empty_series_is_zero():
    assert velocity([], age_hours=4.0) == 0.0


def test_velocity_never_negative():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=2)
    # engagement dropped — velocity floors at 0, not negative
    assert velocity([(t0, 50.0), (t1, 10.0)], age_hours=2.0) == 0.0


_W = {"velocity": 0.0, "novelty": 0.0, "relevance": 1.0, "earliness": 0.0, "query": 1.0}


def test_mainstream_penalty_halves_score():
    it = _item(raw_domain="techcrunch.com", relevance=10.0, novelty=1.0)
    base = composite(it, age_hours=0, weights=_W, max_velocity=1.0, halflife_hours=18,
                     mainstream_domains=set(), penalty=0.5)
    pen = composite(it, age_hours=0, weights=_W, max_velocity=1.0, halflife_hours=18,
                    mainstream_domains={"techcrunch.com"}, penalty=0.5)
    assert abs(pen - base * 0.5) < 1e-9


def test_query_similarity_blends_in():
    it = _item(relevance=0.0, novelty=0.0)
    s = composite(it, age_hours=0, weights=_W, max_velocity=1.0, halflife_hours=18,
                  mainstream_domains=set(), penalty=0.5, query_sim=0.8)
    assert abs(s - 0.8) < 1e-9  # only the query term contributes


def test_age_decays_score():
    it = _item(relevance=10.0)
    fresh = composite(it, age_hours=0, weights=_W, max_velocity=1.0, halflife_hours=18,
                      mainstream_domains=set(), penalty=0.5)
    old = composite(it, age_hours=18, weights=_W, max_velocity=1.0, halflife_hours=18,
                    mainstream_domains=set(), penalty=0.5)
    assert old < fresh
    assert abs(old - fresh * 0.5) < 1e-9


def test_normalize_weights_base_sums_to_one():
    w = normalize_weights(
        {"velocity": 0.30, "novelty": 0.20, "relevance": 0.25, "earliness": 0.15, "query": 0.30}
    )
    assert abs(sum(w[k] for k in ("velocity", "novelty", "relevance", "earliness")) - 1.0) < 1e-9
    assert w["query"] == 0.30  # additive bump left untouched
    assert abs(w["velocity"] - 0.30 / 0.90) < 1e-9  # proportions preserved


def test_normalize_weights_zero_base_falls_back_to_equal():
    w = normalize_weights({"query": 0.3})
    assert abs(sum(w[k] for k in ("velocity", "novelty", "relevance", "earliness")) - 1.0) < 1e-9
    assert abs(w["velocity"] - 0.25) < 1e-9


def test_composite_clamps_out_of_range_llm_score():
    # A malformed relevance (e.g. 50) must not inflate the score past the in-range max.
    w = {"velocity": 0.0, "novelty": 0.0, "relevance": 1.0, "earliness": 0.0, "query": 0.0}
    sane = composite(_item(relevance=10.0), age_hours=0, weights=w, max_velocity=1.0,
                     halflife_hours=18, mainstream_domains=set(), penalty=0.5)
    crazy = composite(_item(relevance=50.0), age_hours=0, weights=w, max_velocity=1.0,
                      halflife_hours=18, mainstream_domains=set(), penalty=0.5)
    assert sane == crazy           # 50 clamped to 10 -> identical score
    assert abs(sane - 1.0) < 1e-9
