# Case study: a distributed, local-LLM AI early-signal radar

**What it is.** A system that surfaces **emerging, not-yet-mainstream AI news** —
ranking arXiv / HN / Reddit / GitHub / Hugging Face / lobste.rs by how fast they're
rising, not how big they already are — with **zero cloud LLM cost**. See it without
running anything: [`docs/sample-digest.html`](sample-digest.html).

**The problem.** "What's about to matter" is drowned out by "what already went viral."
Surfacing early signal means measuring *velocity* (engagement rate, not totals),
*novelty* (is this genuinely new?), and *earliness* (has the press already broken it?) —
and doing it continuously, cheaply, on hardware I already own.

## Architecture — a 3-machine hybrid

```
Jetson Nano (cron */20)         Desktop (RTX 5070)
  scrape 6 sources  ─────────▶  Ollama: embeddings + LLM judge
  store SQLite corpus  ◀─────── (enrichment GPU)
        ▲
        └──ssh── VS Code extension → ranked digest
```

The Jetson is too old to run LLMs (Maxwell / CUDA 10.2, EOL), so the design **splits
collection from inference**: the Jetson scrapes nonstop and owns the corpus; the desktop's
GPU does the embedding + LLM work over the LAN. A published VS Code extension is the
client. It's a real distributed system built from constrained, mismatched hardware.

## Engineering decisions worth a look

- **Two-phase pipeline.** `collect` scrapes + enriches each new item *exactly once*
  (cheap, timer-driven); `rank` scores the cached corpus *on demand* (instant). Expensive
  work is paid once; reading is free.
- **Velocity over volume.** Ranking uses engagement *slope* across snapshots tracked in
  SQLite, a **true half-life freshness decay** (exactly 0.5 at the half-life), and an
  LLM relevance/earliness judge — base weights normalized to a clean 0..1 footing and
  model scores clamped, so a hallucinated value can't dominate.
- **Novelty without LLM cost.** Every item is embedded (`nomic-embed-text`); a cosine
  gate drops near-duplicates *before* spending an LLM call, killing repeat coverage.
- **Graceful degradation everywhere.** If Ollama is unreachable the whole pipeline still
  runs on heuristics; readable summaries are generated lazily for only the top-N shown and
  cached; the digest is written atomically (temp + rename) so an open preview never reads a
  half-written file; the extension self-heals a failed startup fetch on a refresh timer.
- **Operable.** A `RUNBOOK.md` troubleshooting tree, a `status` health command, CI
  (pytest + extension compile), and a one-command tests-gated deploy. The live-source
  smoke test self-skips on a network outage so a third-party hiccup can't redden CI.

## Discipline

73 tests on the desktop `.venv`; every bug fix ships with a regression test; an independent
skeptical review of the publish-prep diff (caught a query-mode ranking subtlety and a link-
scheme gap, both fixed). The repo was scrubbed of host/identity specifics and a
self-contained HTML export added, so it's demoable with no hardware.

## What it demonstrates

Distributed-systems design under real hardware constraints; local-LLM systems (embeddings +
judge) with end-to-end graceful degradation; information-retrieval ranking (velocity,
novelty, freshness decay); and operational maturity (health checks, CI/CD, a runbook). The
artifact is the architecture and the demoable digest — not a feature count.
