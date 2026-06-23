"""Two entry points:

- collect(): scrape every source, upsert into the corpus, snapshot engagement, and
  enrich each NEW item exactly once (embedding + LLM verdict). Cheap per cycle — meant
  to run constantly on a timer.
- rank(): score the accumulated corpus on demand (velocity + freshness decay + cached
  components, plus an optional topic query) and return the best N. Instant.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .config import Config
from .models import Item
from .ollama_client import OllamaClient, cosine
from .ranking import velocity as calc_velocity, composite
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


def _ollama(cfg: Config) -> OllamaClient:
    return OllamaClient(
        host=cfg.get("ollama", "host", default="http://localhost:11434"),
        chat_model=cfg.get("ollama", "chat_model", default="llama3.1:8b"),
        embed_model=cfg.get("ollama", "embed_model", default="nomic-embed-text"),
    )


# ============================== COLLECT ====================================== #
def collect(cfg: Config, progress: Progress | None = None) -> dict:
    log = progress or (lambda _m: None)
    store = Store(cfg.db_path)
    ollama = _ollama(cfg)
    if not ollama.available:
        log("⚠ Ollama unreachable — items will be stored unenriched.")

    # 1. fetch ----------------------------------------------------------------
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

    # 2. freshness + keyword prefilter ----------------------------------------
    max_age = float(cfg.get("general", "max_age_hours", default=48))
    keywords = [k.lower() for k in cfg.get("relevance", "keywords", default=[])]

    def matches_kw(it: Item) -> bool:
        if not keywords:
            return True
        blob = f"{it.title} {it.summary}".lower()
        return any(k in blob for k in keywords)

    fresh = [it for it in raw if it.age_hours <= max_age and matches_kw(it)]
    log(f"  after freshness+keyword filter: {len(fresh)}")

    # 3. upsert + snapshot engagement -----------------------------------------
    for it in fresh:
        store.upsert_item(it)

    # 4. enrich NEW items only (embedding + LLM judge), once ------------------
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

    # 5. prune ----------------------------------------------------------------
    retention = int(cfg.get("collector", "retention_days", default=14))
    pruned = store.prune(retention)
    stats = store.stats()
    store.close()
    log(f"✓ enriched {enriched} new · pruned {pruned} · corpus {stats['items']} items")
    return {"enriched": enriched, "pruned": pruned, **stats}


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

    items = store.get_corpus(since_hours)

    query_vec = None
    if query:
        query_vec = _ollama(cfg).embed(query)

    weights = {
        "velocity": float(cfg.get("ranking", "weight_velocity", default=0.30)),
        "novelty": float(cfg.get("ranking", "weight_novelty", default=0.20)),
        "relevance": float(cfg.get("ranking", "weight_relevance", default=0.25)),
        "earliness": float(cfg.get("ranking", "weight_earliness", default=0.15)),
        "query": float(cfg.get("ranking", "weight_query", default=0.30)),
    }
    halflife = float(cfg.get("ranking", "halflife_hours", default=18))
    mainstream = set(cfg.get("suppression", "mainstream_domains", default=[]))
    penalty = float(cfg.get("suppression", "penalty", default=0.5))

    # live velocity from the engagement snapshots
    for it in items:
        it.velocity = calc_velocity(store.engagement_series(it.id), it.age_hours)
    max_vel = max((it.velocity for it in items), default=1.0) or 1.0

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

    items.sort(key=lambda x: x.score, reverse=True)
    store.close()
    return items[:n]
