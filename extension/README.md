# AI Signal Scraper — VS Code extension

A thin status-bar client over the Python engine that runs on a Jetson collector. It
SSHes into the Jetson, runs `engine.cli top`, and opens the ranked digest in a Markdown
preview — without leaving VS Code. All scraping/ranking lives in the engine; this just
drives it and renders the result.

## What it does

- **Status-bar badge** `$(radar) AI Signal` (bottom-left). Click it to fetch the latest
  ranking and open the digest.
- On startup it fetches once (`topOnStartup`) and then silently refreshes every
  `refreshMinutes` (default 20, matching the Jetson's collect cron) so an open preview
  stays live and a failed startup fetch self-heals.
- The digest is written atomically to `%TEMP%\ai-signal-latest.md` (temp file + rename),
  so an open preview never reads a half-written file.

## Commands (Ctrl+Shift+P)

| Command | Keybinding | What it does |
|---|---|---|
| `AI Signal: Top now` | `Ctrl+Alt+A` | Re-rank the corpus and open the digest |
| `AI Signal: Top on topic…` | `Ctrl+Alt+T` | Rank with a semantic topic query |
| `AI Signal: Collect now (force refresh)` | — | Trigger a collect on the Jetson now |
| `AI Signal: Open last digest` | — | Re-open the cached digest |

## Settings (`aiSignal.*`)

| Setting | Default | Meaning |
|---|---|---|
| `sshHost` | `192.168.55.1` | Jetson host/IP (the standard USB-gadget address). |
| `sshUser` | _(empty — **required**)_ | Linux username on the Jetson. Key-based SSH must be set up. |
| `remoteDir` | `~/ai-signal` | Path to the engine project on the Jetson. |
| `remotePython` | `venv/bin/python` | Python interpreter on the Jetson (relative to `remoteDir`, or absolute). |
| `defaultTopN` | `20` | How many items `Top now` returns. |
| `sinceHours` | `72` | Only rank items first seen within this many hours. |
| `topOnStartup` | `true` | Fetch the latest ranking when VS Code starts. |
| `openOnStartup` | `true` | Also open the digest preview on startup (off = just update the badge). |
| `refreshMinutes` | `20` | Silent background refresh interval (0 = off). |

## Develop / build

```bash
cd extension
npm install
npm run compile        # or: npm run watch
npm run package        # builds ai-signal-scraper.vsix
```

Install the built VSIX: `code --install-extension ai-signal-scraper.vsix`.

> After updating the extension, **fully quit and reopen VS Code** — a running window
> keeps the old code until it re-activates (the #1 "it's not updating" cause; see the
> repo `RUNBOOK.md`).
