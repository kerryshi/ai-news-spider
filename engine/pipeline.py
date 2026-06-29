"""Two entry points:

- collect(): scrape every source, upsert into the corpus, snapshot engagement, and
  enrich each NEW item exactly once (embedding + LLM verdict). Cheap per cycle — meant
  to run constantly on a timer.
- rank(): score the accumulated corpus on demand (velocity + freshness decay + cached
  components, plus an optional topic query) and return the best N. Instant.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Callable

from .config import Config
from .models import Item
from .ollama_client import OllamaClient, cosine
from .ranking import velocity_from_endpoints, composite, normalize_weights
from .store import Store
from .sources import REGISTRY

Progress = Callable[[str], None]

JUDGE_SYSTEM = (
    "You are a ruthless AI-news triage analyst. You score items for a builder who "
    "wants EARLY, technical, not-yet-mainstream AI signal. Respond ONLY with JSON: "
    '{"relevance": <0-10>, "earliness": <0-10>, "reason": "<=12 words", '
    '"tags": ["..."]}. relevance = fit to the focus. earliness = how pre-mainstream '
    "/ under-the-radar it is (10 = almost nobody is talking about it yet)."
)

SUMMARY_SYSTEM = (
    "Brief a busy AI builder so they can decide whether to open a link WITHOUT reading "
    "it. Write two short sentences, <=40 words total, plain English. First sentence: "
    "what it actually is. Second sentence: why it matters or who should care. Do not "
    "number or label the sentences. Start directly with the content — no 'Here', 'Sure', "
    "'This article/post/repo', or 'Summary:' opener. No markdown, no hype. If the input "
    "is thin, infer from the title."
)

# Models (llama3.1:8b) still leak boilerplate openers despite the prompt; strip them.
_PREAMBLE = re.compile(
    r"^\s*(here(?:'s| is| are)[^:.]{0,50}[:.]\s*|sure[,!.]?\s*|okay[,!.]?\s*|"
    r"summary[:.]\s*|in (?:summary|short)[,:]\s*|tl;?dr[:.]?\s*)",
    re.I,
)


def _strip_preamble(s: str) -> str:
    s = " ".join((s or "").split())
    # Drop leaked "(1)"/"(2)" sentence labels, but ONLY at the start or after a
    # sentence boundary — otherwise real prose like "scores 1) first 2) second"
    # gets gutted. Keep the captured boundary (group 1), drop just the label.
    s = re.sub(r"(^|[.!?]\s+)\(?[12]\)\s*", r"\1", s)
    prev = None
    while s and s != prev:          # peel stacked openers, e.g. "Sure! Here's a brief:"
        prev = s
        s = _PREAMBLE.sub("", s)
    return s[:1].upper() + s[1:] if s else s


def _ollama(cfg: Config) -> OllamaClient:
    return OllamaClient(
        host=cfg.get("ollama", "host", default="http://localhost:11434"),
        chat_model=cfg.get("ollama", "chat_model", default="llama3.1:8b"),
        embed_model=cfg.get("ollama", "embed_model", default="nomic-embed-text"),
    )


# ============================== COLLECT ====================================== #
def collect(cfg: Config, progress: Progress | None = None) -> dict:
    log = progress or (lambda _m: None)
    timings: dict[str, float] = {}
    store = Store(cfg.db_path)
    ollama = _ollama(cfg)
    if not ollama.available:
        log("⚠ Ollama unreachable — items will be stored unenriched.")

    # 1. fetch ----------------------------------------------------------------
    t0 = time.perf_counter()
    raw: list[Item] = []
    for name, fetch in REGISTRY.items():
        if not cfg.source_enabled(name):
            continue
        try:
            got = fetch(cfg.source(name))
            log(f"  {name}: {len(got)} items")
            raw.extend(got)
        except Exception as e:
            log(f"  {name}: ERROR {e}")
    timings["fetch_s"] = time.perf_counter() - t0

    # 2. freshness + keyword prefilter ----------------------------------------
    max_age = float(cfg.get("general", "max_age_hours", default=48))
    keywords = [k.lower() for k in cfg.get("relevance", "keywords", default=[])]

    def matches_kw(it: Item) -> bool:
        if not keywords:
            return True
        blob = f"{it.title} {it.summary}".lower()
        return any(k in blob for k in keywords)

    t0 = time.perf_counter()
    fresh = [it for it in raw if it.age_hours <= max_age and matches_kw(it)]
    timings["filter_s"] = time.perf_counter() - t0
    log(f"  after freshness+keyword filter: {len(fresh)}")

    # 3. upsert + snapshot engagement (one batched commit, not one per item) --
    t0 = time.perf_counter()
    for it in fresh:
        store.upsert_item(it)
    store.commit()
    timings["upsert_s"] = time.perf_counter() - t0

    # 4. enrich NEW items only (embedding + LLM judge), once ------------------
    t0 = time.perf_counter()
    pending = store.needs_enrichment()
    log(f"  enriching {len(pending)} new items…")
    sim_threshold = float(cfg.get("novelty", "similarity_threshold", default=0.86))
    focus = cfg.get("relevance", "focus", default="emerging AI technology")
    use_llm = bool(cfg.get("ollama", "use_llm_scoring", default=True)) and ollama.available
    prior_vecs = store.enriched_embeddings()

    enriched = 0
    for row in pending:
        text = f"{row['title']}\n\n{row['summary'] or ''}".strip()
        vec = ollama.embed(text) if ollama.available else None

        novelty = 1.0
        is_dup = False
        if vec and prior_vecs:
            max_sim = max(cosine(vec, pv) for pv in prior_vecs)
            novelty = max(0.0, 1.0 - max_sim)
            is_dup = max_sim >= sim_threshold

        if is_dup:
            # near-duplicate of something already in the corpus: keep it out of the
            # rankings without burning an LLM call, and don't reprocess next cycle.
            store.save_enrichment(row["id"], vec, 0.0, 0.0, "near-duplicate", [], novelty)
            continue

        relevance, earliness, reason, tags = 0.0, 0.0, "", []
        if use_llm:
            verdict = ollama.judge(
                JUDGE_SYSTEM,
                f"FOCUS:\n{focus}\n\nITEM\nsource: {row['source']}\n"
                f"title: {row['title']}\nsummary: {(row['summary'] or '')[:700]}",
            )
            if verdict:
                relevance = float(verdict.get("relevance", 0) or 0)
                earliness = float(verdict.get("earliness", 0) or 0)
                reason = str(verdict.get("reason", ""))[:120]
                tags = [str(t) for t in (verdict.get("tags") or [])][:5]
        else:
            relevance = 6.0
            earliness = 8.0 if row["source"] in ("arxiv", "huggingface") else 5.0

        store.save_enrichment(row["id"], vec, relevance, earliness, reason, tags, novelty)
        if vec:
            prior_vecs.append(vec)
        enriched += 1
    timings["enrich_s"] = time.perf_counter() - t0

    # 5. prune ----------------------------------------------------------------
    t0 = time.perf_counter()
    retention = int(cfg.get("collector", "retention_days", default=14))
    pruned = store.prune(retention)
    timings["prune_s"] = time.perf_counter() - t0
    stats = store.stats()
    store.close()
    ollama.close()
    timings["total_s"] = sum(timings.values())
    log(f"✓ enriched {enriched} new · pruned {pruned} · corpus {stats['items']} items "
        f"· {timings['total_s']:.1f}s (fetch {timings['fetch_s']:.1f} · "
        f"upsert {timings['upsert_s']:.1f} · enrich {timings['enrich_s']:.1f})")
    return {"enriched": enriched, "pruned": pruned, "timings": timings, **stats}


# =============================== RANK ======================================== #
def rank(
    cfg: Config,
    query: str | None = None,
    since_hours: float | None = None,
    n: int | None = None,
) -> list[Item]:
    store = Store(cfg.db_path)
    if since_hours is None:
        since_hours = float(cfg.get("ranking", "default_since_hours", default=72))
    if n is None:
        n = int(cfg.get("ranking", "default_top_n", default=20))

    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    # embeddings are only needed for topic-query similarity; skip parsing them otherwise
    items = store.get_corpus(since_hours, with_embeddings=query is not None)
    timings["load_s"] = time.perf_counter() - t0

    query_vec = None
    if query:
        oc = _ollama(cfg)
        query_vec = oc.embed(query)
        oc.close()

    weights = {
        "velocity": float(cfg.get("ranking", "weight_velocity", default=0.30)),
        "novelty": float(cfg.get("ranking", "weight_novelty", default=0.20)),
        "relevance": float(cfg.get("ranking", "weight_relevance", default=0.25)),
        "earliness": float(cfg.get("ranking", "weight_earliness", default=0.15)),
        "query": float(cfg.get("ranking", "weight_query", default=0.30)),
    }
    weights = normalize_weights(weights)  # base sums to 1.0 via a uniform rescale (order preserved)
    halflife = float(cfg.get("ranking", "halflife_hours", default=18))
    mainstream = set(cfg.get("suppression", "mainstream_domains", default=[]))
    penalty = float(cfg.get("suppression", "penalty", default=0.5))

    # live velocity from engagement snapshots — ONE batched query, not one per item
    t0 = time.perf_counter()
    endpoints = store.engagement_endpoints([it.id for it in items])
    for it in items:
        ep = endpoints.get(it.id)
        if ep:
            n_snap, first_ts, first_val, last_ts, last_val = ep
            span_h = ((last_ts - first_ts).total_seconds() / 3600.0
                      if (first_ts and last_ts) else 0.0)
            it.velocity = velocity_from_endpoints(n_snap, first_val, last_val, span_h, it.age_hours)
        else:
            it.velocity = 0.0
    timings["velocity_s"] = time.perf_counter() - t0
    max_vel = max((it.velocity for it in items), default=1.0) or 1.0

    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    for it in items:
        # recency = hours since we first saw it (falls back to publish age)
        first_seen = getattr(it, "_first_seen", None)
        age_hours = (now - first_seen).total_seconds() / 3600.0 if first_seen else it.age_hours
        qsim = None
        if query_vec is not None:
            qsim = cosine(query_vec, getattr(it, "_embedding", None) or [])
        it.score = round(
            composite(
                it, age_hours=max(age_hours, 0.0), weights=weights, max_velocity=max_vel,
                halflife_hours=halflife, mainstream_domains=mainstream, penalty=penalty,
                query_sim=qsim,
            ),
            4,
        )
    timings["score_s"] = time.perf_counter() - t0

    items.sort(key=lambda x: x.score, reverse=True)
    store.close()
    rank.last_timings = timings  # type: ignore[attr-defined]  # read by scripts/bench.py
    return items[:n]


# ========================= READABLE SUMMARIES ================================ #
def attach_summaries(
    cfg: Config,
    items: list[Item],
    progress: Progress | None = None,
    cap: int | None = None,
    timeout: float | None = None,
) -> list[Item]:
    """Ensure the TOP shown items have a readable LLM summary, generating + caching
    any that are missing. Only the top `cap` items (default `ranking.summary_top_n`)
    cost a call — this bounds a cold-corpus `top` click to ~cap serial llama3.1 calls
    instead of len(items); lower-ranked items render via the reason/abstract fallback
    in digest.py. Each summary is paid for exactly once — cached in the DB thereafter.
    `timeout` (default `ollama.summary_timeout_s`) bounds each individual call."""
    log = progress or (lambda _m: None)
    if cap is None:
        cap = int(cfg.get("ranking", "summary_top_n", default=8))
    if timeout is None:
        timeout = float(cfg.get("ollama", "summary_timeout_s", default=20.0))
    # items arrive rank-sorted; only the top `cap` are summarized on demand.
    pool = items[:cap] if cap and cap > 0 else items
    missing = [it for it in pool if not getattr(it, "llm_summary", "")]
    if not missing:
        return items
    ollama = _ollama(cfg)
    if not ollama.available:
        log("⚠ Ollama unreachable — showing items without readable summaries.")
        ollama.close()          # don't leak the httpx client on the early return
        return items
    store = Store(cfg.db_path)
    log(f"  summarizing {len(missing)} new item(s) for readability…")
    for it in missing:
        user = (
            f"source: {it.source}\ntitle: {it.title}\n"
            f"details: {(it.summary or '')[:900]}"
        )
        s = ollama.summarize(SUMMARY_SYSTEM, user, timeout=timeout)
        if s:
            s = _strip_preamble(s)
            if len(s) > 320:                       # never cut mid-word
                s = s[:320].rsplit(" ", 1)[0].rstrip(",;:") + "…"
            it.llm_summary = s
            store.set_summary(it.id, s)
    store.close()
    ollama.close()
    return items
