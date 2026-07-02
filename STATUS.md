# STATUS — ai-news-spider

_Last updated: 2026-07-01 · **v0.1.5 shipped (web view + digest transparency), pushed to GitHub; Jetson deploy of the digest changes PENDING (gated)** · 94 tests green on the desktop `.venv` (1 live arxiv subtest excluded — external)._

## Where it is
A working AI early-signal scraper in daily personal use: a Jetson Nano collector
(`*/20` cron) scrapes 6 free sources into a SQLite corpus and calls the desktop's
Ollama (RTX 5070) for embedding-novelty + an LLM relevance/earliness judge; a VS Code
extension SSHes in and renders the ranked digest. See `README.md` (overview),
`RUNBOOK.md` (operator manual), and `docs/plans/` (the measured optimization plan).

## This session (2026-07-01) — v0.1.5 + transparency, committed & pushed
Two commits on `master` (`7b77bb9`, `eeaa7df`), independently reviewed (no blockers/majors;
the one minor — an ambiguous unjudged-signature edge — fixed with a regression test):
- **v0.1.5 bundle landed**: `scripts/serve.py` local web view (127.0.0.1:8765, stdlib-only,
  auto-refresh, `/healthz`; no file serving = no traversal surface) + extension `openWebView`
  command + JSON feed; RUNBOOK §2a. Extension **0.1.5 built + installed** (reload VS Code).
- **Silent failures fixed**: "Collect now" non-zero exit now raises an error toast (before:
  badge went idle and the stale digest looked fresh); parse failures log to the output channel.
- **Digest transparency** (engine — see PENDING deploy below): cards show *both* "What it is"
  and "Why it's early" (the judge reason used to vanish for exactly the top items); failed
  judge calls are counted in collect (`judge_failures` in stats + collect.log warning) and the
  digest shows a header warning for items with the failed-judge signature (0/0 + empty reason;
  judged-zeros / near-dups / Ollama-down heuristic items never trip it).
- **First push to GitHub**: full history → `kerryshi/ai-news-spider` (repo existed empty).

## ✅ DEPLOYED 2026-07-02 — but ⚠ the Jetson is OFFLINE (ICS outage, pre-existing)
The engine (digest transparency + judge-failure counting) was deployed to the Jetson on
2026-07-02 with explicit approval; the deploy smoke returned valid JSON. It also uncovered
an incident: **the collect cron had been failing silently for ~74.5h** — the desktop's ICS
(Wi-Fi → Ethernet) dropped, the Jetson's eth0 lost its 192.168.137.x lease, and every
source fetch died with "Name or service not known". The ranked window (72h) emptied out,
which is why digests "looked quiet". Exactly the failure mode the new digest warning +
`judge_failures` logging were built to surface — and it bumps the deferred **extension
health-check command** ("warn if last collect > 25 min ago") from nice-to-have to next-up.

**Recovery**: run `scripts/jetson-ics.ps1` **elevated** (replaces the temp-cleaned
`%TEMP%\jetics.ps1`; re-enables ICS Wi-Fi→Ethernet + reasserts reboot persistence). Once
eth0 re-leases, the `*/20` cron self-heals the corpus; the deployed digest then renders
"Why it's early —" lines on judged items.

## This session (autonomous portfolio prep — 2026-06-28) — DONE
The plan = freeze engine features, make the repo publish-ready and demoable. All landed
as verified local commits; an independent reviewer pass + fixes included:
- **Scrubbed** host/identity → env vars + placeholders (`deploy.ps1`, `config.toml`,
  `README.md`, `RUNBOOK.md`). No usernames/desktop-IP/home-paths/secrets in tracked
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
- **Deploy engine changes to the live Jetson** (`deploy.ps1`) — touches running prod; see
  the PENDING block above for the exact command. (Push consent was given 2026-07-01 and
  the first push executed; future pushes remain per-session decisions.)
- **Tailscale** to replace ICS/USB; **Reddit OAuth** (needs a registered app) — both deferred.

## Open questions
- A real precision@10 eval needs hand-labels — build the harness + a starter labeled set.
