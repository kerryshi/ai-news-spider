# AI early-signal scraper

[![CI](https://github.com/kerryshi/ai-news-spider/actions/workflows/ci.yml/badge.svg)](https://github.com/kerryshi/ai-news-spider/actions/workflows/ci.yml)

Local, Ollama-powered scraper that surfaces **emerging, not-yet-mainstream AI tech
news** from arXiv, Hacker News, Reddit, GitHub, Hugging Face, and Lobste.rs — ranked by
velocity, novelty, relevance, and earliness. No paid APIs, no cloud LLM cost.

> **See it without running anything:** [live sample digest](https://kerryshi.github.io/ai-news-spider/)
> — a real, self-contained digest ([`docs/sample-digest.html`](docs/sample-digest.html)); no
> engine, Jetson, or network needed.

**At a glance:** 6 free sources · local-LLM enrichment (zero cloud cost) ·
collects every 20 min on a Jetson Nano · embedding-based novelty dedup + velocity ranking ·
a real VS Code extension · CI + one-command deploy.

```mermaid
flowchart LR
  subgraph Jetson["Jetson Nano · cron */20"]
    SC["scrape 6 sources"] --> DB[("SQLite corpus")]
  end
  subgraph Desktop["Desktop · RTX 5070"]
    OL["Ollama<br/>embeddings + LLM judge"]
    VS["VS Code extension"]
  end
  DB -- "enrich: novelty + relevance" --> OL
  VS -- "ssh · engine.cli top" --> DB
  VS --> DG["ranked digest"]
```

## How it finds "not mainstream yet"

1. **Early-signal sources** — arXiv + HN `new` + r/LocalLLaMA surface things before
   the press does.
2. **Velocity over volume** — ranks by engagement *rate* (stars/upvotes per hour),
   tracked across runs in `state.db`, so a fast-rising small item beats a big stale one.
3. **Novelty via embeddings** — every surfaced item is embedded (`nomic-embed-text`);
   new candidates too similar to past ones are dropped, killing repeat coverage.
4. **Mainstream suppression** — items pointing at techcrunch/verge/nyt/etc. are
   downranked as "already broke."
5. **Local LLM judge** — `llama3.1:8b` scores each survivor on relevance + earliness.

## Setup

```bash
cd ai-news-spider
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Make sure Ollama is running with the models pulled:

```bash
ollama pull nomic-embed-text
ollama pull llama3.1:8b
```

## Run

```bash
python -m engine.cli run            # writes digests/latest.md + a timestamped copy
python -m engine.cli run --json     # JSON to stdout (used by the VS Code extension)
```

Tune everything in [`config.toml`](config.toml): sources, subreddits, keywords,
focus prompt, scoring weights, mainstream domains. Optionally set `GITHUB_TOKEN`
to raise the GitHub rate limit.

## VS Code extension

The `extension/` shell gives you a status-bar button + hotkeys (`Ctrl+Alt+A` Top now,
`Ctrl+Alt+T` Top on topic) that SSH into the Jetson and open the ranked digest.

## Architecture (hybrid)

```
JETSON (<user>@<jetson-ip>)             DESKTOP (RTX 5070)
  cron */20 → engine.cli collect          Ollama (llama3.1:8b + nomic-embed-text)
    scrape → store → enrich ─────calls────▶  (enrichment GPU)
    state.db (corpus)                       VS Code extension ──ssh──▶ engine.cli top
```
The Jetson scrapes + stores nonstop; enrichment (embeddings + LLM judge) runs on the
desktop's Ollama. Internet for the Jetson is shared from the desktop (ICS).

## Development & CI/CD

```bash
pip install -r requirements-dev.txt
python -m pytest                 # engine unit tests (ranking, store, config)
```

**Ship a change with one command** (tests gate the deploy):

```powershell
./scripts/deploy.ps1 -Message "tune ranking weights"
#   1. pytest            (abort on failure)
#   2. scp engine + config → Jetson   (cron picks it up next cycle)
#   3. remote smoke test  (top --json must return valid JSON)
#   4. rebuild + reinstall the VS Code extension (auto patch-bump)
#   5. git commit
# flags: -SkipExtension (engine-only), -DryRun (test gate only, stops before any
#        Jetson contact), -NoCommit
# The test gate is unconditional: there is deliberately no -SkipTests switch.
```

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs `ruff check .`,
the pytest suite, and compiles the extension on every push / PR. Deployment stays
local because the Jetson is only reachable from the desktop (USB link), not from
cloud runners.

**What CI green does NOT cover:**

- `TestOllama` self-skips on hosted runners (no Ollama there), so the enrichment
  path (embeddings + LLM judge) is **desktop-verified only** — a green CI run says
  nothing about it.
- Live-source tests follow a skip-with-notice policy: a persistently empty source
  is reported as a skipped-with-notice condition, never a red run. CI green
  therefore does not prove every live source is currently returning items.

**Limitations:**

- The 8B local judge ranks reasonably but summarizes unreliably: in the sample digest it
  describes "GLM 5.2 beats Claude in our benchmarks" as "a comparison of two open-source
  machine learning libraries." Summaries are a convenience, not a source of truth.
- Ranking quality has no labeled evaluation set yet; the velocity and novelty math is
  unit-tested, but whether it surfaces the *right* items is judged by eye.
