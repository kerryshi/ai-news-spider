# STATUS — ai-news-spider

_Last updated: 2026-06-28 · **Portfolio-prep underway; engine in daily production, local-only** · 67 tests + 6 subtests green on the desktop `.venv`._

## Where it is
A working AI early-signal scraper in daily personal use: a Jetson Nano collector
(`*/20` cron) scrapes 6 free sources into a SQLite corpus and calls the desktop's
Ollama (RTX 5070) for embedding-novelty + an LLM relevance/earliness judge; a VS Code
extension SSHes in and renders the ranked digest. See `README.md` (overview),
`RUNBOOK.md` (operator manual), and `docs/plans/` (the measured optimization plan).

## This session (autonomous portfolio prep — 2026-06-28)
The plan = freeze engine features, make the repo publish-ready and demoable. Done so far
(each a verified local commit; engine behavior unchanged):
- **Scrubbed** host/identity specifics → env vars + placeholders (`deploy.ps1`,
  `config.toml`, `README.md`, `RUNBOOK.md`). No `kershy`/desktop-IP/`C:\Users\PC`/secrets
  in tracked files. Fixed the `*/30`→`*/20` cron drift.
- **Fixed the stale `extension/README.md`** (it documented a removed local-Python version)
  to match the real SSH-based extension.
- Added this `STATUS.md`; tracked `docs/plans/pipeline-optimization.html`.

## Next actions (autonomous, local — in progress)
- CI: make the live-source tests self-skip on network error (so a 3rd-party outage can't
  redden CI / block the deploy gate) — also speeds the suite (~66s → fast).
- README "sells in 30s": architecture diagram + headline numbers (+ a GIF once captured).
- Static `--html` digest export + a committed sample (offline, GitHub-Pages-ready).
- Ranking hygiene: normalize weights to sum 1.0 + clamp LLM scores to [0,10] (with tests).
- Perf: benchmark the O(N²) novelty cosine (+ a numpy-vectorized path), no deploy.
- A short case study writeup.

## Gated — needs Kerry's hand (NOT autonomous)
- **Push to a remote** (public/private) — outward-facing.
- **Deploy engine changes to the live Jetson** (`deploy.ps1`) — touches running prod;
  remember to set `JETSON_HOST` + `OLLAMA_LAN_HOST` env vars first (post-scrub).
- **Tailscale** to replace ICS/USB; **Reddit OAuth** (needs a registered app) — both deferred.

## Open questions
- Public vs private remote? (lead data is N/A here; this is portfolio-public-leaning.)
- A real precision@10 eval needs hand-labels — build the harness + a starter labeled set.
