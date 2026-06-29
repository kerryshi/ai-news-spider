# STATUS — ai-news-spider

_Last updated: 2026-06-28 · **Portfolio-prep DONE (publish-ready + demoable + reviewed); engine in daily production, local-only** · 73 tests green on the desktop `.venv`._

## Where it is
A working AI early-signal scraper in daily personal use: a Jetson Nano collector
(`*/20` cron) scrapes 6 free sources into a SQLite corpus and calls the desktop's
Ollama (RTX 5070) for embedding-novelty + an LLM relevance/earliness judge; a VS Code
extension SSHes in and renders the ranked digest. See `README.md` (overview),
`RUNBOOK.md` (operator manual), and `docs/plans/` (the measured optimization plan).

## This session (autonomous portfolio prep — 2026-06-28) — DONE
The plan = freeze engine features, make the repo publish-ready and demoable. All landed
as verified local commits; an independent reviewer pass + fixes included:
- **Scrubbed** host/identity → env vars + placeholders (`deploy.ps1`, `config.toml`,
  `README.md`, `RUNBOOK.md`). No `kershy`/desktop-IP/`C:\Users\PC`/secrets in tracked
  files. Fixed the `*/30`→`*/20` cron drift.
- **Rewrote the stale `extension/README.md`** to match the real SSH-based extension.
- **CI**: the live-source smoke now self-skips on a network outage (no false reds; faster).
- **Ranking hygiene**: `normalize_weights` (uniform rescale, base sums to 1.0) + `_clamp10`
  for LLM scores — order-preserving, tested. (Engine-only; NOT deployed.)
- **Self-contained `--html` digest export** + a committed sample (`docs/sample-digest.html`)
  that opens in any browser with no engine/Jetson/network.
- **README sells in 30s**: demo link + headline numbers + a Mermaid architecture diagram.
- **Case study** at `docs/CASE_STUDY.md`; tracked `docs/plans/pipeline-optimization.html`.

## Deferred (deliberate — not worth the cost now)
- **Perf: O(N²) novelty cosine → numpy/hnswlib.** An adversarial review judged this
  premature at the ~2.9k-item corpus (the novelty window is already capped at 4000); it's
  a yak-shave until latency is a measured problem. Revisit when the corpus is much larger.
- **precision@10 eval harness.** Needs a hand-labeled relevance set (human judgment); a
  harness over fake labels has no portfolio value. Build it with real labels when there's
  time to label ~50 items against a written rubric.

## Gated — needs Kerry's hand (NOT autonomous)
- **Push to a remote** (public/private) — outward-facing.
- **Deploy engine changes to the live Jetson** (`deploy.ps1`) — touches running prod;
  remember to set `JETSON_HOST` + `OLLAMA_LAN_HOST` env vars first (post-scrub).
- **Tailscale** to replace ICS/USB; **Reddit OAuth** (needs a registered app) — both deferred.

## Open questions
- Public vs private remote? (lead data is N/A here; this is portfolio-public-leaning.)
- A real precision@10 eval needs hand-labels — build the harness + a starter labeled set.
