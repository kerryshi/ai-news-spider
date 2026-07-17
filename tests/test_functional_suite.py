"""Functional test suite for ai-signal. Stdlib unittest only (no pytest dep).

  python test_suite.py            # all tests
  python test_suite.py -v         # verbose

Unit tests use a throwaway temp DB and never touch state.db. Live tests (sources,
Ollama, end-to-end rank) hit the network/real corpus read-only and self-skip if a
dependency is down, but report what they saw.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from engine.config import Config
from engine.models import Item
from engine.ranking import velocity, freshness, composite
from engine.ollama_client import cosine, OllamaClient
from engine.store import Store
from engine.digest import render_markdown, _takeaway, _excerpt
from engine.sources import REGISTRY
from engine.pipeline import rank as run_rank, attach_summaries, _strip_preamble

CFG = Config.load()


def _item(**kw):
    base = dict(source="hackernews", title="t", url="http://x/1", summary="s",
                raw_domain="x", created_at=datetime.now(timezone.utc))
    base.update(kw)
    return Item(**base)


# ───────────────────────── pure ranking math ──────────────────────────────
class TestRankingMath(unittest.TestCase):
    def test_freshness_true_halflife(self):
        self.assertAlmostEqual(freshness(0, 18), 1.0, places=6)
        self.assertAlmostEqual(freshness(18, 18), 0.5, places=6)
        self.assertAlmostEqual(freshness(36, 18), 0.25, places=6)

    def test_freshness_monotonic_decreasing(self):
        vals = [freshness(h, 18) for h in range(0, 100, 5)]
        self.assertTrue(all(a >= b for a, b in zip(vals, vals[1:])))

    def test_freshness_nonpositive_halflife(self):
        self.assertEqual(freshness(50, 0), 1.0)

    def test_velocity_two_snapshots_slope(self):
        t0 = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=2)
        self.assertAlmostEqual(velocity([(t0, 10.0), (t1, 30.0)], 5), 10.0)  # 20 over 2h

    def test_velocity_single_snapshot(self):
        t0 = datetime.now(timezone.utc)
        self.assertAlmostEqual(velocity([(t0, 20.0)], 4.0), 5.0)

    def test_velocity_empty(self):
        self.assertEqual(velocity([], 4.0), 0.0)

    def test_velocity_negative_clamped(self):
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=2)
        self.assertEqual(velocity([(t0, 30.0), (t1, 10.0)], 5), 0.0)

    def test_cosine(self):
        self.assertAlmostEqual(cosine([1, 0, 0], [1, 0, 0]), 1.0)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(cosine([1, 2, 3], [1, 2]), 0.0)   # mismatched length
        self.assertEqual(cosine([], [1]), 0.0)

    def test_composite_mainstream_penalty(self):
        w = dict(velocity=0.3, novelty=0.2, relevance=0.25, earliness=0.15)
        normal = _item(raw_domain="obscure.io"); normal.velocity = 1.0
        normal.novelty = 1.0; normal.relevance = 8.0; normal.earliness = 8.0
        main = _item(raw_domain="techcrunch.com"); main.velocity = 1.0
        main.novelty = 1.0; main.relevance = 8.0; main.earliness = 8.0
        kw = dict(age_hours=0.0, weights=w, max_velocity=1.0, halflife_hours=18,
                  mainstream_domains={"techcrunch.com"}, penalty=0.5)
        s_norm = composite(normal, **kw)
        s_main = composite(main, **kw)
        self.assertAlmostEqual(s_main, s_norm * 0.5, places=6)

    def test_composite_query_bump(self):
        w = dict(velocity=0.3, novelty=0.2, relevance=0.25, earliness=0.15, query=0.3)
        it = _item(); it.velocity = 0.0; it.novelty = 0.5; it.relevance = 5.0; it.earliness = 5.0
        kw = dict(age_hours=0.0, weights=w, max_velocity=1.0, halflife_hours=18,
                  mainstream_domains=set(), penalty=0.5)
        self.assertGreater(composite(it, query_sim=0.9, **kw), composite(it, query_sim=None, **kw))


# ───────────────────────── model / dedup invariant ────────────────────────
class TestModel(unittest.TestCase):
    def test_id_canonical_and_stable(self):
        a = _item(url="https://Example.com/Post ")
        b = _item(url="https://example.com/post")
        self.assertEqual(a.id, b.id)   # case/whitespace-insensitive dedup key

    def test_id_distinct_urls(self):
        self.assertNotEqual(_item(url="http://x/1").id, _item(url="http://x/2").id)

    def test_age_hours_none(self):
        self.assertEqual(_item(created_at=None).age_hours, 999.0)

    def test_age_hours_positive(self):
        it = _item(created_at=datetime.now(timezone.utc) - timedelta(hours=3))
        self.assertAlmostEqual(it.age_hours, 3.0, delta=0.05)


# ───────────────────────── store lifecycle (temp db) ──────────────────────
class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.store = Store(self.dir / "t.db")

    def tearDown(self):
        self.store.close()

    def test_upsert_dedup_single_row(self):
        it = _item(url="http://x/dup")
        self.store.upsert_item(it)
        self.store.upsert_item(it)   # same id again
        self.assertEqual(self.store.stats()["items"], 1)

    def test_needs_enrichment_gate(self):
        it = _item(url="http://x/new")
        self.store.upsert_item(it)
        self.assertEqual(len(self.store.needs_enrichment()), 1)
        self.store.save_enrichment(it.id, [0.1, 0.2], 7.0, 6.0, "ok", ["tag"], 0.9)
        self.assertEqual(len(self.store.needs_enrichment()), 0)   # enriched once, never again

    def test_get_corpus_excludes_unenriched(self):
        self.store.upsert_item(_item(url="http://x/raw"))
        self.assertEqual(len(self.store.get_corpus(since_hours=None)), 0)
        it = _item(url="http://x/rich")
        self.store.upsert_item(it)
        self.store.save_enrichment(it.id, [0.1], 5.0, 5.0, "r", [], 1.0)
        self.assertEqual(len(self.store.get_corpus(since_hours=None)), 1)

    def test_enriched_embeddings_roundtrip(self):
        it = _item(url="http://x/emb")
        self.store.upsert_item(it)
        self.store.save_enrichment(it.id, [0.5, 0.5], 5.0, 5.0, "r", [], 1.0)
        self.assertEqual(self.store.enriched_embeddings(), [[0.5, 0.5]])

    def test_prune_drops_stale(self):
        it = _item(url="http://x/old")
        self.store.upsert_item(it)
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.store.conn.execute("UPDATE items SET last_seen=? WHERE id=?", (old, it.id))
        self.store.conn.commit()
        self.assertEqual(self.store.prune(retention_days=14), 1)
        self.assertEqual(self.store.stats()["items"], 0)


# ───────────────────────── digest rendering ───────────────────────────────
class TestDigest(unittest.TestCase):
    def test_render_contains_core_fields(self):
        it = _item(title="My Title", url="http://x/9")
        it.score = 1.23; it.relevance = 8; it.earliness = 7; it.velocity = 2.0; it.novelty = 0.9
        md = render_markdown([it], subtitle="topic: x")
        self.assertIn("My Title", md)
        self.assertIn("http://x/9", md)
        self.assertIn("1.23", md)
        self.assertIn("1 items", md)

    def test_header_renders_in_eastern_not_utc(self):
        from engine.digest import _DISPLAY_TZ
        it = _item(title="X", url="http://x/1"); it.score = 1.0
        header = render_markdown([it]).splitlines()[0]
        self.assertNotIn("UTC", header)                      # no longer UTC
        label = datetime.now(_DISPLAY_TZ).strftime("%Z")     # 'EDT' (summer) / 'EST' (winter)
        self.assertTrue(label and label in header, f"expected {label!r} in {header!r}")


# ───────────────────────── config integrity ───────────────────────────────
class TestConfig(unittest.TestCase):
    def test_sources_enabled(self):
        for s in ("arxiv", "hackernews", "reddit", "github", "huggingface", "lobsters"):
            self.assertTrue(CFG.source_enabled(s), f"{s} should be enabled")

    def test_widened_breadth_present(self):
        self.assertGreaterEqual(len(CFG.source("reddit")["subreddits"]), 20)
        self.assertGreaterEqual(CFG.source("arxiv")["max_results"], 250)
        self.assertGreaterEqual(len(CFG.source("hackernews")["queries"]), 20)


# ───────────────────────── live smoke: sources ────────────────────────────
def _fetch_with_retry(fetch, retries=1, pause=3.0):
    """arXiv answers burst queries with an empty 200 (run 2026-07-16_2239), so an
    empty result gets one paused retry to tell throttling from a broken parser."""
    got = fetch()
    for _ in range(retries):
        if got:
            break
        time.sleep(pause)
        got = fetch()
    return got


class TestFetchWithRetry(unittest.TestCase):
    def test_empty_then_items_returns_items(self):
        calls = []
        def fetch():
            calls.append(1)
            return [] if len(calls) == 1 else ["item"]
        self.assertEqual(_fetch_with_retry(fetch, pause=0), ["item"])
        self.assertEqual(len(calls), 2)

    def test_no_retry_when_first_fetch_has_items(self):
        calls = []
        def fetch():
            calls.append(1)
            return ["item"]
        self.assertEqual(_fetch_with_retry(fetch, pause=0), ["item"])
        self.assertEqual(len(calls), 1)

    def test_gives_up_empty_after_retry(self):
        self.assertEqual(_fetch_with_retry(lambda: [], pause=0), [])

    def test_zero_retries_never_refetches(self):
        calls = []
        def fetch():
            calls.append(1)
            return []
        self.assertEqual(_fetch_with_retry(fetch, retries=0, pause=0), [])
        self.assertEqual(len(calls), 1)


class TestLiveSources(unittest.TestCase):
    def test_each_source_returns_items(self):
        import httpx
        reached = 0
        for name in REGISTRY:
            if not CFG.source_enabled(name):
                continue
            try:
                # reddit is known-throttled and often legitimately empty; don't
                # burn a 3s retry on it.
                got = _fetch_with_retry(
                    lambda: list(REGISTRY[name](CFG.source(name))),
                    retries=0 if name == "reddit" else 1)
            except (httpx.HTTPError, OSError):
                # Source/network unreachable -> don't redden CI on a 3rd-party outage.
                # A real code bug raises something else and still fails the test.
                continue
            if not got and name != "reddit":
                # Empty twice = rate-limited or down, not a parser bug; same
                # treatment as unreachable, but visibly.
                print(f"    [{name}] empty after retry (rate-limited?) — treated as unreachable")
                continue
            reached += 1
            with self.subTest(source=name):
                self.assertIsInstance(got, list)
                for it in got[:5]:
                    self.assertIsInstance(it, Item)
                    self.assertTrue(it.url and it.title is not None)
                    _ = it.id; _ = it.age_hours   # must not raise
                print(f"    [{name}] {len(got)} items")
        if reached == 0:
            self.skipTest("no sources reachable (offline?) — skipping live source smoke")


# ───────────────────────── live: ollama ───────────────────────────────────
class TestOllama(unittest.TestCase):
    def setUp(self):
        self.oll = OllamaClient(host=CFG.get("ollama", "host"),
                                chat_model=CFG.get("ollama", "chat_model"),
                                embed_model=CFG.get("ollama", "embed_model"))

    def tearDown(self):
        self.oll.close()

    def test_embed(self):
        if not self.oll.available:
            self.skipTest("ollama unreachable")
        v = self.oll.embed("a new open-weight reasoning model")
        self.assertIsInstance(v, list)
        self.assertGreater(len(v), 100)

    def test_judge_schema(self):
        if not self.oll.available:
            self.skipTest("ollama unreachable")
        v = self.oll.judge(
            'Respond ONLY JSON {"relevance":<0-10>,"earliness":<0-10>,"reason":"..","tags":[]}',
            "title: A novel agent framework for tool use",
        )
        self.assertIsInstance(v, dict)
        self.assertIn("relevance", v)
        self.assertIn("earliness", v)


# ───────────────────────── live: end-to-end rank ──────────────────────────
class TestRankEndToEnd(unittest.TestCase):
    def test_top_sorted_and_shaped(self):
        items = run_rank(CFG, n=10)
        self.assertLessEqual(len(items), 10)
        scores = [it.score for it in items]
        self.assertEqual(scores, sorted(scores, reverse=True), "not sorted by score desc")
        for it in items:
            self.assertTrue(it.title and it.url)
        print(f"    ranked {len(items)} items, top score={scores[0] if scores else 'n/a'}")

    def test_query_path_runs(self):
        items = run_rank(CFG, query="open-weight agent models", n=5)
        self.assertLessEqual(len(items), 5)


# ──────── regression: markdown links survive special chars (ship v0.1.4) ───
class TestDigestLinkSafety(unittest.TestCase):
    """A `]` in a title used to close the link early and leak the URL as text;
    a `)` in a URL used to truncate the destination. Both corrupt the digest."""
    def test_bracketed_title_is_escaped(self):
        it = _item(title="Mixtral [MoE] (8x7B) drops", url="http://x/a")
        it.score = 1.0
        md = render_markdown([it])
        self.assertIn(r"\[MoE\]", md)                 # ] escaped, link text intact
        self.assertNotIn("[MoE](", md)                # no premature link close

    def test_paren_url_is_encoded(self):
        it = _item(title="Plain", url="http://x/Foo_(bar)")
        it.score = 1.0
        md = render_markdown([it])
        self.assertIn("%28bar%29", md)                # ( ) percent-encoded
        self.assertNotIn("(http://x/Foo_(bar))", md)  # raw paren URL not emitted


# ──────── regression: _strip_preamble keeps mid-sentence enumerations ──────
class TestStripPreamble(unittest.TestCase):
    def test_leading_opener_stripped(self):
        self.assertEqual(_strip_preamble("Here's a brief: A new model."), "A new model.")

    def test_stacked_openers_peeled(self):
        self.assertEqual(_strip_preamble("Sure! Summary: It works."), "It works.")

    def test_leading_numbered_labels_removed(self):
        self.assertEqual(
            _strip_preamble("(1) It is fast. (2) It matters."),
            "It is fast. It matters.",
        )

    def test_midsentence_enumeration_preserved(self):
        # the bug: this used to become "A model that scores first second."
        s = "A model that scores 1) first 2) second on benchmarks."
        self.assertEqual(_strip_preamble(s), s)


# ──────── regression: digest truncation helpers ───────────────────────────
class TestDigestTruncation(unittest.TestCase):
    def test_takeaway_truncates_with_ellipsis(self):
        it = _item(); it.llm_summary = "word " * 60       # well over 130 chars
        out = _takeaway(it, max_chars=130)
        self.assertLessEqual(len(out), 131)
        self.assertTrue(out.endswith("…"))

    def test_takeaway_short_passes_through(self):
        it = _item(); it.llm_summary = "Short and sweet."
        self.assertEqual(_takeaway(it), "Short and sweet.")

    def test_excerpt_breaks_on_sentence_boundary(self):
        t = ("This opening sentence is written to comfortably pass eighty "
             "characters before it ends right here. " + "Z" * 300)
        out = _excerpt(t, max_chars=120)
        self.assertTrue(out.endswith("here."))   # cut at the sentence boundary
        self.assertNotIn("Z", out)               # trailing filler dropped


# ──────── regression: attach_summaries closes its client when Ollama down ──
class TestAttachSummaries(unittest.TestCase):
    def test_unreachable_ollama_returns_items_and_closes_client(self):
        import engine.pipeline as P
        closed = {"v": False}

        class _FakeOllama:
            available = False
            def close(self):
                closed["v"] = True

        orig = P._ollama
        P._ollama = lambda cfg: _FakeOllama()
        try:
            it = _item(title="x")
            out = attach_summaries(CFG, [it])
            self.assertIs(out[0], it)        # unchanged, no crash
            self.assertEqual(out[0].llm_summary, "")
            self.assertTrue(closed["v"], "httpx client must be closed on early return")
        finally:
            P._ollama = orig


# ──────── regression: attach_summaries caps generation + bounds each call ──
class TestAttachSummariesCap(unittest.TestCase):
    """Cold-load fix (2026-06-27): a `top` click summarized ALL ranked items
    synchronously (up to default_top_n=20 serial llama3.1 calls), blocking ~20-60s.
    attach_summaries now generates only the top `cap` and passes a per-call timeout."""

    class _FakeOllama:
        available = True

        def __init__(self):
            self.timeouts = []

        def summarize(self, system, user, timeout=None):
            self.timeouts.append(timeout)
            return "A concise readable summary."

        def close(self):
            pass

    class _FakeStore:           # don't touch the real state.db
        def __init__(self, _path):
            self.saved = []

        def set_summary(self, item_id, s):
            self.saved.append((item_id, s))

        def close(self):
            pass

    def _patch(self, fake_oll):
        import engine.pipeline as P
        self._orig = (P._ollama, P.Store)
        P._ollama = lambda cfg: fake_oll
        P.Store = self._FakeStore

    def _unpatch(self):
        import engine.pipeline as P
        P._ollama, P.Store = self._orig

    def test_caps_generation_to_top_n(self):
        items = [_item(url=f"http://x/c{i}") for i in range(12)]
        fake = self._FakeOllama()
        self._patch(fake)
        try:
            attach_summaries(CFG, items, cap=3, timeout=9.5)
        finally:
            self._unpatch()
        self.assertEqual(len(fake.timeouts), 3)                  # only top 3 summarized
        self.assertTrue(all(items[i].llm_summary for i in range(3)))
        self.assertTrue(all(items[i].llm_summary == "" for i in range(3, 12)))  # rest untouched

    def test_per_call_timeout_threaded_through(self):
        items = [_item(url=f"http://x/t{i}") for i in range(2)]
        fake = self._FakeOllama()
        self._patch(fake)
        try:
            attach_summaries(CFG, items, cap=5, timeout=7.0)
        finally:
            self._unpatch()
        self.assertEqual(fake.timeouts, [7.0, 7.0])              # bound passed to every call

    def test_already_cached_top_skips_generation(self):
        items = [_item(url=f"http://x/s{i}") for i in range(5)]
        for it in items[:3]:
            it.llm_summary = "cached"
        fake = self._FakeOllama()
        self._patch(fake)
        try:
            attach_summaries(CFG, items, cap=3)                 # top 3 already have summaries
        finally:
            self._unpatch()
        self.assertEqual(len(fake.timeouts), 0)                 # nothing to generate

    def test_config_exposes_summary_cap_and_timeout(self):
        self.assertGreaterEqual(int(CFG.get("ranking", "summary_top_n", default=0)), 1)
        self.assertGreater(float(CFG.get("ollama", "summary_timeout_s", default=0)), 0)


# ──────── regression: reddit backoff survives an HTTP-date Retry-After ─────
class TestRedditBackoff(unittest.TestCase):
    def test_http_date_retry_after_does_not_raise(self):
        import engine.sources.reddit as R

        class _Resp:
            def __init__(self, code, headers=None, text=""):
                self.status_code = code
                self.headers = headers or {}
                self.text = text

        calls = {"n": 0}

        def _fake_get(url):
            calls["n"] += 1
            if calls["n"] == 1:   # first hit: 429 with a date (not seconds) Retry-After
                return _Resp(429, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
            return _Resp(200, text="")

        orig_get, orig_sleep = R.get, R.time.sleep
        R.get = _fake_get
        R.time.sleep = lambda _s: None     # don't actually wait
        try:
            feed, status = R._fetch_feed("http://x", max_retries=1)
            self.assertEqual(status, 200)  # retried cleanly instead of crashing
            self.assertEqual(calls["n"], 2)
        finally:
            R.get, R.time.sleep = orig_get, orig_sleep


# ──────── Phase 1 perf: indexes, batched commit, N+1 fix, embedding-skip ───
class TestPhase1Optimizations(unittest.TestCase):
    """2026-06-28 pipeline optimizations — each must preserve behavior exactly."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.store = Store(self.dir / "t.db")

    def tearDown(self):
        self.store.close()

    def test_hot_path_indexes_exist(self):
        idx = {r["name"] for r in self.store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        for want in ("idx_items_enriched_first", "idx_items_enriched_last",
                     "idx_items_last_seen", "idx_items_source"):
            self.assertIn(want, idx)

    def test_velocity_endpoints_parity(self):
        from engine.ranking import velocity, velocity_from_endpoints
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=2)
        self.assertAlmostEqual(velocity([(t0, 10.0), (t1, 30.0)], 5),       # slope 20/2h
                               velocity_from_endpoints(2, 10.0, 30.0, 2.0, 5))
        self.assertAlmostEqual(velocity([(t0, 20.0)], 4.0),                 # single: val/age
                               velocity_from_endpoints(1, 20.0, 20.0, 0.0, 4.0))
        self.assertEqual(velocity_from_endpoints(0, 0.0, 0.0, 0.0, 4.0), 0.0)

    def test_engagement_endpoints_one_query_matches_series(self):
        from engine.ranking import velocity, velocity_from_endpoints
        it = _item(url="http://x/ep")
        self.store.upsert_item(it); self.store.commit()
        self.store.conn.execute("DELETE FROM engagement WHERE id=?", (it.id,))
        self.store.conn.executemany(
            "INSERT INTO engagement(id, ts, value) VALUES (?,?,?)",
            [(it.id, "2026-01-01T00:00:00+00:00", 10.0),
             (it.id, "2026-01-01T02:00:00+00:00", 30.0)])
        self.store.conn.commit()
        ep = self.store.engagement_endpoints([it.id])
        n, ft, fv, lt, lv = ep[it.id]
        self.assertEqual((n, fv, lv), (2, 10.0, 30.0))
        series = self.store.engagement_series(it.id)
        span = (lt - ft).total_seconds() / 3600.0
        self.assertAlmostEqual(velocity(series, it.age_hours),
                               velocity_from_endpoints(n, fv, lv, span, it.age_hours))

    def test_engagement_endpoints_empty(self):
        self.assertEqual(self.store.engagement_endpoints([]), {})

    def test_get_corpus_since_filter_in_sql(self):
        recent = _item(url="http://x/recent"); self.store.upsert_item(recent)
        old = _item(url="http://x/old"); self.store.upsert_item(old)
        self.store.commit()
        self.store.save_enrichment(recent.id, [0.1], 5, 5, "r", [], 1.0)
        self.store.save_enrichment(old.id, [0.2], 5, 5, "r", [], 1.0)
        stale = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
        self.store.conn.execute("UPDATE items SET first_seen=? WHERE id=?", (stale, old.id))
        self.store.conn.commit()
        got = {it.url for it in self.store.get_corpus(since_hours=72)}
        self.assertIn("http://x/recent", got)
        self.assertNotIn("http://x/old", got)        # excluded by SQL, not Python

    def test_get_corpus_embedding_skip(self):
        it = _item(url="http://x/emb"); self.store.upsert_item(it); self.store.commit()
        self.store.save_enrichment(it.id, [0.5, 0.5], 5, 5, "r", [], 1.0)
        self.assertIsNone(self.store.get_corpus(with_embeddings=False)[0]._embedding)
        self.assertEqual(self.store.get_corpus(with_embeddings=True)[0]._embedding, [0.5, 0.5])

    def test_upsert_batches_commit(self):
        it = _item(url="http://x/batch"); self.store.upsert_item(it)
        self.assertEqual(self.store.stats()["items"], 1)    # visible same-connection
        self.store.commit()
        other = Store(self.dir / "t.db")
        self.assertEqual(other.stats()["items"], 1)         # durable for a new connection
        other.close()


# ──────── ranking transparency: reason line + unjudged-item warning ────────
class TestDigestTransparency(unittest.TestCase):
    """The judge's `reason` is the only "why is this ranked here" the digest has, and
    it used to disappear for exactly the top items (any item with an llm_summary).
    And a failed judge call (0/0 scores, empty reason, never retried) used to be
    indistinguishable from a genuinely low-ranked item."""

    def _judged(self, **kw):
        it = _item(**kw)
        it.score = 1.0
        return it

    def test_reason_renders_alongside_summary(self):
        it = self._judged(title="Both", url="http://x/b")
        it.llm_summary = "A tiny new model. It matters."
        it.reason = "no coverage outside one Discord"
        it.relevance, it.earliness = 8, 9
        md = render_markdown([it])
        self.assertIn("**What it is —** A tiny new model.", md)
        self.assertIn("**Why it's early —** no coverage outside one Discord", md)

    def test_reason_still_renders_without_summary(self):
        it = self._judged(title="OnlyReason", url="http://x/r")
        it.reason = "preprint, zero stars yet"
        it.relevance = 5
        md = render_markdown([it])
        self.assertIn("**Why it's early —** preprint, zero stars yet", md)

    def test_duplicate_reason_not_repeated(self):
        it = self._judged(title="Dup", url="http://x/d")
        it.llm_summary = "Same text."
        it.reason = "Same text."
        it.relevance = 5
        md = render_markdown([it])
        # The card shows the summary once; an identical reason adds no second line.
        self.assertIn("**What it is —** Same text.", md)
        self.assertNotIn("Why it's early", md)

    def test_unjudged_items_get_a_header_warning(self):
        judged = self._judged(title="Judged", url="http://x/j")
        judged.relevance, judged.earliness, judged.reason = 7, 6, "fresh preprint"
        failed = self._judged(title="Failed", url="http://x/f")  # 0/0 + empty reason
        md = render_markdown([judged, failed])
        self.assertIn("1 item(s) have no LLM judgment", md)

    def test_no_warning_for_judged_heuristic_or_duplicate_items(self):
        judged_zero = self._judged(title="JudgedZero", url="http://x/z")
        judged_zero.reason = "mainstream rehash"           # judged AS zero, has a reason
        heuristic = self._judged(title="Heuristic", url="http://x/h")
        heuristic.relevance, heuristic.earliness = 6.0, 8.0  # Ollama-down defaults
        near_dup = self._judged(title="Dup", url="http://x/n")
        near_dup.reason = "near-duplicate"
        md = render_markdown([judged_zero, heuristic, near_dup])
        self.assertNotIn("no LLM judgment", md)

    def test_html_mirrors_reason_and_warning(self):
        from engine.digest import render_html
        it = self._judged(title="Both", url="http://x/b")
        it.llm_summary = "A tiny new model."
        it.reason = "no coverage yet"
        failed = self._judged(title="Failed", url="http://x/f")
        html = render_html([it, failed])
        self.assertIn("Why it's early", html)
        self.assertIn("no coverage yet", html)
        self.assertIn("1 item(s) have no LLM judgment", html)


# ──────── collect: judge failures are counted and reported ─────────────────
class _FakeCfg:
    """Minimal Config stand-in: every get() returns its default, all sources
    disabled (no network), db_path pointed at a temp store."""

    def __init__(self, db_path):
        self.db_path = db_path

    def get(self, *path, default=None):
        return default

    def source_enabled(self, _name):
        return False

    def source(self, _name):
        return {}


class _FailingJudgeOllama:
    available = True

    def embed(self, _text):
        return None  # no embedding -> novelty path stays inert

    def judge(self, _system, _user):
        return None  # every judge call fails

    def close(self):
        pass


class _EmptyReasonJudgeOllama(_FailingJudgeOllama):
    def judge(self, _system, _user):
        # A parseable verdict scoring 0/0 with no reason: judged, NOT a failure.
        return {"relevance": 0, "earliness": 0, "reason": "", "tags": []}


class TestCollectJudgeFailures(unittest.TestCase):
    def test_collect_counts_and_logs_failed_judgments(self):
        from unittest.mock import patch
        from engine.pipeline import collect

        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "t.db"
            seed = Store(db)
            seed.upsert_item(_item(url="http://x/1"))
            seed.upsert_item(_item(url="http://x/2"))
            seed.commit()
            seed.close()

            logs: list[str] = []
            with patch("engine.pipeline._ollama", return_value=_FailingJudgeOllama()):
                stats = collect(_FakeCfg(db), progress=logs.append)

            self.assertEqual(stats["judge_failures"], 2)
            self.assertTrue(any("judge call(s) returned nothing" in m for m in logs))
            check = Store(db)
            for it in check.get_corpus(since_hours=48):
                self.assertEqual((it.relevance, it.earliness, it.reason), (0.0, 0.0, ""))
            check.close()

    def test_judged_zero_with_empty_reason_is_not_a_failure(self):
        """A parseable 0/0 verdict with an empty reason is a JUDGMENT: it must not count
        as a judge failure, and it must not wear the failed-judge signature (the stored
        reason gets a placeholder so digest._unjudged can't over-count it)."""
        from unittest.mock import patch
        from engine.pipeline import collect
        from engine.digest import _unjudged

        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "t.db"
            seed = Store(db)
            seed.upsert_item(_item(url="http://x/1"))
            seed.commit()
            seed.close()

            with patch("engine.pipeline._ollama", return_value=_EmptyReasonJudgeOllama()):
                stats = collect(_FakeCfg(db), progress=None)

            self.assertEqual(stats["judge_failures"], 0)
            check = Store(db)
            corpus = check.get_corpus(since_hours=48)
            self.assertEqual(corpus[0].reason, "(no reason given)")
            self.assertEqual(_unjudged(corpus), 0)
            check.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
