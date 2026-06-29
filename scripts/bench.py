"""Pipeline micro-benchmark — prints real numbers for the hot paths on the LIVE corpus
(whatever `config.toml` points at). Read-only; safe to run on the Jetson or the desktop.

    python -m scripts.bench               # uses config.toml's db_path
    python scripts/bench.py /path/db      # or bench an explicit state.db copy

It self-demonstrates the Phase 1 optimizations against the same data, no git-stash needed:
  - get_corpus with vs without embedding parsing (the embedding-skip win)
  - velocity the old per-item N+1 way vs the new batched way (the N+1 win)
  - full rank() latency with its per-stage breakdown
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import Config            # noqa: E402
from engine.store import Store              # noqa: E402
from engine.ranking import velocity, velocity_from_endpoints  # noqa: E402
from engine.pipeline import rank            # noqa: E402


def ms(s: float) -> str:
    return f"{s * 1000:8.1f} ms"


def main() -> None:
    cfg = Config.load()
    db = sys.argv[1] if len(sys.argv) > 1 else cfg.db_path
    size = os.path.getsize(db) if os.path.exists(db) else 0
    store = Store(db)
    st = store.stats()
    since = float(cfg.get("ranking", "default_since_hours", default=72))

    print("=== AI Signal pipeline bench ===")
    print(f"db:     {db}  ({size / 1e6:.1f} MB)")
    print(f"corpus: {st['items']} items ({st['enriched']} enriched), {st['snapshots']} snapshots")
    print(f"window: last {since:g}h\n")

    # --- corpus load: with vs without embedding parsing ----------------------
    t = time.perf_counter()
    items = store.get_corpus(since_hours=since, with_embeddings=False)
    t_noemb = time.perf_counter() - t

    t = time.perf_counter()
    store.get_corpus(since_hours=since, with_embeddings=True)
    t_emb = time.perf_counter() - t

    print(f"[get_corpus] {len(items)} items in window")
    print(f"  without embeddings : {ms(t_noemb)}")
    print(f"  with embeddings    : {ms(t_emb)}   (embedding-parse cost avoided per top: {ms(max(t_emb - t_noemb, 0))})")

    # --- velocity: old per-item N+1 vs new batched ---------------------------
    ids = [it.id for it in items]

    t = time.perf_counter()
    for it in items:
        velocity(store.engagement_series(it.id), it.age_hours)
    t_old = time.perf_counter() - t

    t = time.perf_counter()
    ep = store.engagement_endpoints(ids)
    for it in items:
        e = ep.get(it.id)
        if e:
            n, ft, fv, lt, lv = e
            span = ((lt - ft).total_seconds() / 3600.0) if (ft and lt) else 0.0
            velocity_from_endpoints(n, fv, lv, span, it.age_hours)
    t_new = time.perf_counter() - t
    speedup = (t_old / t_new) if t_new > 0 else float("inf")

    print(f"\n[velocity] over {len(items)} items")
    print(f"  old per-item (N+1) : {ms(t_old)}")
    print(f"  new batched        : {ms(t_new)}   (speedup: {speedup:.1f}x)")
    store.close()

    # --- full rank() latency + per-stage breakdown ---------------------------
    # rank() opens cfg.db_path itself; shim it so it targets the benched DB too.
    class _Cfg:
        def __init__(self, base, dbp):
            self._b, self._db = base, dbp

        def __getattr__(self, name):
            return getattr(self._b, name)

        @property
        def db_path(self):
            return self._db

    bcfg = _Cfg(cfg, db)
    print("\n[rank() full]")
    for label in ("cold", "warm"):
        t = time.perf_counter()
        rank(bcfg, n=20)
        dt = time.perf_counter() - t
        tm = getattr(rank, "last_timings", {})
        print(f"  {label:4} : {ms(dt)}   "
              f"(load {ms(tm.get('load_s', 0))}, velocity {ms(tm.get('velocity_s', 0))}, "
              f"score {ms(tm.get('score_s', 0))})")


if __name__ == "__main__":
    main()
