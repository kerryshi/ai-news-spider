# AI Signal — Operator's Manual

A field guide to **running, viewing, fixing, and shipping** the AI early-signal scraper.
Written for "I came back after a break and something's off." Skim the table, jump to the section.

| I want to… | Go to |
|---|---|
| Understand how the pieces fit | [1. The system in 60 seconds](#1-the-system-in-60-seconds) |
| See today's signal | [2. Daily use](#2-daily-use) |
| Fix **"it's not updating"** | [3. Troubleshooting (the big one)](#3-troubleshooting--its-not-updating) |
| Change the code and push it live | [4. Shipping a change](#4-shipping-a-change) |
| Copy-paste commands | [5. Cheat sheet](#5-cheat-sheet) |
| Know the traps before they bite | [6. Gotchas](#6-gotchas-memorize-these) |
| See what changed this session | [7. Session recap](#7-session-recap-2026-06-27) |

---

## 1. The system in 60 seconds

Three machines, one job: surface **not-yet-mainstream** AI news.

```
   JETSON NANO  (jetson@192.168.55.1, USB link)        DESKTOP  (this PC, RTX 5070)
   ───────────────────────────────────────            ──────────────────────────────
   cron  */20 min →  engine.cli collect                Ollama  (llama3.1:8b + nomic-embed-text)
        scrape 6 sources                                  ▲   enrichment GPU
        store in state.db  ───── calls for ──────────────┘
        (the corpus)        embeddings + LLM judge

                        VS Code extension  ──ssh──▶  engine.cli top  (ranks the corpus)
                        writes %TEMP%\ai-signal-latest.md  →  opens markdown preview
```

- **Jetson** scrapes nonstop (arXiv, HN, Reddit, GitHub, Hugging Face, lobste.rs) and stores the corpus. It's too old to run LLMs.
- **Desktop** runs Ollama; the Jetson calls it at `http://<your-desktop-lan-ip>:11434` for embeddings + scoring.
- **VS Code extension** (`kerryshi.ai-signal-scraper`) SSHes into the Jetson, runs `top`, and shows you the ranked digest.
- Internet for the Jetson is **shared from the desktop** (ICS). No remote — everything is local.

**The golden rule:** the *corpus* (Jetson) and the *view* (VS Code) are separate. "Not updating" is almost always the **view**, not the corpus.

---

## 2. Daily use

In VS Code (on the desktop):

| Action | How |
|---|---|
| Open today's signal | Click the **`📡 AI Signal`** status-bar item (bottom-left) |
| Top now | `Ctrl+Alt+A` |
| Top on a topic | `Ctrl+Alt+T` → type e.g. "agent frameworks" |
| Collect right now (don't wait for cron) | Command palette → **AI Signal: Collect now** |
| Re-open the last digest | Command palette → **AI Signal: Open last digest** |
| Open the local web view | Command palette → **AI Signal: Open web view** (start the server first — see §2a) |

The badge **auto-refreshes every 20 min** (matches the Jetson's collect cron). Digest timestamps are in **Eastern (EST/EDT)**.

### 2a. Local web view (browser)

A private, browser-based view of the latest digest, served on **localhost only**
(`127.0.0.1` — not reachable from other machines). Start the server once on the
desktop; it keeps running:

```powershell
# from the repo root, on the desktop
.\.venv\Scripts\python.exe scripts\serve.py        # → http://127.0.0.1:8765
#   --port 9000     bind a different port (also set aiSignal.webPort to match)
#   --refresh 15    page auto-reload interval in seconds (0 = off)
```

`serve.py` also accepts `--host`, but **leave it at the `127.0.0.1` default** —
binding `0.0.0.0` would expose your digest to every machine on the LAN.

Then run **AI Signal: Open web view** (or browse to `http://127.0.0.1:8765`).
The page **auto-reloads itself** (every 30 s by default), so each time you hit
**Top now / Collect now** — or the 20-min timer fires — the extension rewrites
`%TEMP%\ai-signal-latest.json` and the page picks it up on its next reload. No
manual refresh, no Jetson involvement (HTML is rendered on the desktop from the
ranked JSON). If the page shows "no digest yet," run **Top now** once.

---

## 3. Troubleshooting — "it's not updating"

Work top to bottom. **90% of the time it's Step A.**

### ⭐ Step A — The window is running stale extension code (the #1 cause)
After the extension updates, the **running VS Code window keeps the old code until it re-activates.** Old code = no auto-refresh + the badge opens a cached file. Result: the digest freezes on an old date even though the Jetson is fine.

✅ **Fix: fully QUIT VS Code and reopen it.**
> "Developer: Reload Window" *should* also work — but if it doesn't, **quitting and reopening always does.** This is different from reload.

### Step B — The preview tab is stale
An open markdown preview won't always pick up an external file change.
✅ **Fix:** close the digest tab → Command palette → **AI Signal: Open last digest**.

### Step C — Is the corpus actually fresh? (check the Jetson)
```powershell
ssh jetson@192.168.55.1 "cd /home/jetson/ai-signal && /home/jetson/miniforge3/bin/python -m engine.cli status"
```
Look at **`last collect`** — it should be **< 20 min ago**. If it's hours/days old, the cron stalled or the Jetson lost internet (re-run `$env:TEMP\jetics.ps1` elevated to restore ICS).

### Step D — SSH from VS Code is failing
Open the **"AI Signal" output channel** (View → Output → pick "AI Signal"). It logs the exact `ssh` line and any error.

### 🚑 Emergency unstick (no restart available)
Force-refresh the cached file the extension reads, straight from the Jetson:
```powershell
$out = ssh jetson@192.168.55.1 "cd /home/jetson/ai-signal && /home/jetson/miniforge3/bin/python -m engine.cli top --json --n 20 --since 72h" 2>$null | Out-String
$md  = ($out | ConvertFrom-Json).digest_markdown
[System.IO.File]::WriteAllText("$env:TEMP\ai-signal-latest.md", $md, (New-Object System.Text.UTF8Encoding($false)))
```
Then **AI Signal: Open last digest**. (This is a band-aid — Step A is the real fix.)

---

## 4. Shipping a change

> **The Jetson runs a *file copy* of the engine — NOT a git checkout.** A desktop commit does **not** reach the Jetson. Deploy = `scp`. `deploy.ps1` does this for you.

1. **Edit** the engine in the desktop repo (`engine\…`).
2. **Test** (always on the desktop `.venv`, never the Jetson):
   ```powershell
   cd "<path-to-repo>"
   .venv\Scripts\python.exe -m pytest
   ```
3. **Commit** locally (trunk-based, straight to `master`).
4. **Deploy** — one command (tests-gated):
   ```powershell
   .\scripts\deploy.ps1 -SkipExtension      # engine-only change
   ```
   It runs: pytest → `scp` engine + config to the Jetson → remote smoke test (`top --json`) → git commit.
   - `deploy.ps1` (no flag) also rebuilds + reinstalls the **extension** (bumps its version).
   - Useful flags: `-SkipTests`, `-SkipExtension`, `-NoCommit`.
5. **If the extension changed:** reinstall happens automatically, but you **must quit+reopen VS Code** to run it (see Step A).

**Every bug fix ships with a regression test.** Suite lives in `tests\`; run via the `.venv`.

---

## 5. Cheat sheet

Run from the desktop, project root = `<path-to-repo>`.

```powershell
# --- look ---
ssh jetson@192.168.55.1 "cd /home/jetson/ai-signal && /home/jetson/miniforge3/bin/python -m engine.cli status"   # corpus health
.venv\Scripts\python.exe -m pytest                          # run the test suite

# --- ship ---
.\scripts\deploy.ps1 -SkipExtension                         # deploy engine to the Jetson (tests-gated)
git log --oneline -5                                        # recent history

# --- unstick the view ---
#   (see §3 "Emergency unstick", or just quit+reopen VS Code)
```

| Thing | Where |
|---|---|
| Project repo (desktop) | `<path-to-repo>` |
| Jetson engine (file copy) | `jetson@192.168.55.1:/home/jetson/ai-signal` |
| Jetson python | `/home/jetson/miniforge3/bin/python` |
| The file VS Code opens | `%TEMP%\ai-signal-latest.md` |
| Extension settings | `%APPDATA%\Code\User\settings.json` (keys: `aiSignal.*`) |
| Tune the engine | `config.toml` (sources, weights, `summary_top_n`, etc.) |

---

## 6. Gotchas (memorize these)

- 🔁 **The "I fixed it but it's still broken" trap.** After any extension update, **quit + reopen VS Code.** A running window keeps the old code.
- 📋 **Jetson ≠ git.** It's a file copy. Desktop commits don't propagate — you must `deploy.ps1` (scp).
- 🧪 **Test on the desktop, not the Jetson.** The Jetson has no `tests/` dir and no pytest.
- 🕒 **Timestamps are Eastern now.** Header shows **EDT** in summer, **EST** in winter (correct on the Jetson, which has full tz data).
- ⚡ **`top` only writes readable summaries for the top 8 items** (`ranking.summary_top_n`), each capped at a 20s call. Lower items still rank and render — they just use the short fallback line.
- 👽 **Reddit is IP-throttled (429).** Only ~2–4 of 22 subreddits land per cycle; coverage rotates across runs. Expected, not a bug.
- 🌐 **Jetson internet = desktop ICS.** If it drops, re-run `$env:TEMP\jetics.ps1` elevated.

---

## 7. Session recap (2026-06-27)

What we did, in order:

1. **Resumed** the project — confirmed v0.1.4 was already shipped (commit `3b104a5`), tree clean.
2. **Cold-load fix** (`d46040f`, deployed): a `top` click used to block ~20–60s summarizing all 20 items synchronously. Now it summarizes only the top **8** with a **20s per-call timeout**. Engine-only; defaults baked in.
3. **Eastern timestamps** (`05cc114`, deployed): the digest header was UTC → now renders **EST/EDT** (`America/New_York`). Internal age math stays UTC.
4. **Diagnosed "not updating":** the Jetson corpus was healthy (collecting every ~16 min, 2889 items) — the stale digest was the **un-reloaded extension** (Step A). Refreshed the cache as a stopgap; the permanent fix is quitting + reopening VS Code.
5. **Tests:** suite grew **55 → 60**, all green via the `.venv` (incl. live source/Ollama/e2e), each fix with a regression test.

**Still on your plate:** quit + reopen VS Code once, so the badge auto-refreshes and renders Eastern from now on.

---
*Keep this current. When you change how the system is run or shipped, update the relevant section.*
