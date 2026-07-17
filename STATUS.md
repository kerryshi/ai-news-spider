# STATUS — ai-news-spider

_Last updated: 2026-07-16 late · **DEPLOYED TO THE JETSON** (run 2026-07-16_2239): both hardening waves + the collect-staleness check are live; extension v0.1.6 installed (reload VS Code) · `JETSON_HOST` RESOLVED = `kershy@192.168.55.1` (was in VS Code settings; key auth works from this desktop) · outage recovered: ICS had silently dropped ~07-13, corpus pruned to 0; restored via jetson-ics.ps1 + SharedAccess restart + a LONG (15s) Ethernet carrier drop (short bounces don't retrigger NM's DHCP) · corpus refilled 0→413 items, health verdict live: fresh/11min · known flake: arxiv live-source test returns empty-200 on burst runs — make it rate-limit aware._

## This session (2026-07-16) — collect-staleness health check (working tree)
The deferred "warn if last collect > 25 min ago" check, promoted to next-up by the
2026-07-05 ICS outage. The verdict lives in the **engine** (`digest.collect_health`,
`STALE_AFTER_MINUTES = 25` — one `*/20` cron cycle + slack) reading the corpus's existing
`Store.health()['last_collect']`; `top --json` now ships it as a `health` field, and the
markdown digest prepends a 🚨 banner. Two surfaces render that one verdict — the digest
banner and a warning-state status-bar badge (`statusBarItem.warningBackground`); `status`
reuses it for a `⚠ STALE` marker, so no surface can drift on what "stale" means.
- **The banner renders on the *empty* digest too** — an emptied ranking window IS the
  outage symptom, so a banner after the "no items matched" early return would have
  missed the exact failure it exists to catch. Pinned by a test.
- **Unknown = stale.** Unreachable Jetson, unparseable output, or an engine too old to
  send `health` all warn on **both** surfaces: the badge *and* the digest. The extension
  prepends the banner itself only where the engine has no verdict to give (idempotent via
  a shared `STALE_MARKER`, so failed refreshes can't stack banners). A badge-only warning
  was a review finding — the digest is the surface that actually gets read, and a normal-
  looking digest under a warning badge is the 2026-07-05 split exactly.
  ⚠ **This means the badge warns now, before the deploy** — truthful today (SSH is down,
  collection isn't running), but it is visible-now, not dormant.
- **A failed collect/fetch never clears the warning.** `collectNow`'s error path used to
  call `idleStatus()`, which erased an established stale badge — a failed collect is
  evidence *for* staleness, not a reason to go quiet. (Review finding.) The one path that
  deliberately does *not* warn: `collectNow` exit 0 with an unreadable stats line — the
  collect itself succeeded, so warning there would be a false alarm.
- 19 tests in `tests/test_collect_health.py`, including a node-driven behavioral check of
  the compiled banner logic (CI's engine job now compiles the extension first). Extension
  rebuilt locally via `npm run compile` (`extension/out/` is gitignored — reload VS Code
  to pick it up).
- Not touched: the Jetson deploy path (`JETSON_HOST`), `render_html`/`serve.py` (the
  markdown + badge were the named surfaces — HTML banner is a follow-up).

## Security audit + hardening (2026-07-02) — COMMITTED `c4d06c5`, PUSHED 2026-07-05
A cross-project security audit (multi-agent, adversarially verified — 8 confirmed of 39
raw findings) ran over all three Sandbox-Testing projects. The confirmed ai-news-spider
issues were fixed here, each with a failing-first regression test; full suite green and
the extension was recompiled (`out/extension.js` regenerated):
- **VS Code extension (2× medium — local code-exec):** the status-bar tooltip no longer
  renders remote-scraped titles in a *trusted* MarkdownString (`isTrusted=false` + titles
  `mdEscape`d) — kills the `command:`-URI injection; SSH remote-path config is now
  shell-quoted (`shqRemotePath`), the ssh-related settings are `scope:"machine"`, and
  `capabilities.untrustedWorkspaces.supported:false` — opening an untrusted folder can no
  longer inject a remote command via `.vscode/settings.json`.
- **Cleartext feeds:** arxiv + hackernews endpoints switched http→https.
- **DoS:** `sources/base.get` streams with a 16 MiB body cap (also bounds gzip bombs).
- **Digest injection:** scraped/LLM text is HTML-escaped in the *markdown* path
  (`_md_body` + extended `_md_text`) — the preview renders inline HTML, so an `<img>`
  beacon in a title/summary is now inert. (The HTML path was already escaped.)
- **CI:** least-privilege `permissions: contents: read`; `npm install`→`npm ci --ignore-scripts`.

Reviewed and **accepted as-is** (verified not exploitable, do not re-flag): `deploy.ps1`
interpolates the operator's *own* env vars (not attacker-controlled); `serve.py` is a
loopback-by-use local viewer with no file-serving traversal surface. New tests:
`tests/test_base_http.py`, `tests/test_markdown_digest.py`, `+test_arxiv_and_hn_endpoints_use_https`.
Committed as `c4d06c5` and pushed 2026-07-05. A second wave — redirect-SSRF guard +
`_md_url` scheme restriction, independently reviewed clean — followed as `a164b2f`
(pushed 2026-07-06). **Next: deploy both waves to the Jetson — blocked ONLY on `JETSON_HOST`.**

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
`judge_failures` logging were built to surface — and it bumped the deferred **collect
staleness check** ("warn if last collect > 25 min ago") from nice-to-have to next-up.
**Built 2026-07-16** — see the top section. (It landed as a passive warning on every
refresh, not the standalone command this line originally imagined.)

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
