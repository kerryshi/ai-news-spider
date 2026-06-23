# AI Signal Scraper — VS Code extension

A thin shell over the Python engine in the parent `spiders/` folder. Adds a status-bar button and
commands to run the scrape and read the digest without leaving VS Code. All the
scraping/ranking logic lives in the Python engine; this just drives it.

## Features

- **Status bar button** `$(radar) AI Signal` → runs a scrape, shows live progress,
  opens the digest in a Markdown preview when done.
- **Commands** (Ctrl+Shift+P):
  - `AI Signal: Run scrape now`
  - `AI Signal: Open latest digest`
  - `AI Signal: Open config.toml`
- **Optional scheduling** — `aiSignal.scheduleMinutes` auto-runs every N minutes;
  `aiSignal.runOnStartup` runs once when VS Code launches.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `aiSignal.projectRoot` | _(auto)_ | Folder with `config.toml` + `engine/`. Auto-detected in the workspace. |
| `aiSignal.pythonPath` | _(auto)_ | Interpreter. Defaults to the project's `.venv`, then bare `python`. |
| `aiSignal.scheduleMinutes` | `0` | Auto-run interval in minutes (0 = manual). |
| `aiSignal.runOnStartup` | `false` | Run once on VS Code startup. |
| `aiSignal.openDigestAfterRun` | `true` | Open the digest preview after each run. |

## Develop / run

```bash
cd extension
npm install
npm run compile      # or: npm run watch
```

Then press **F5** (uses `.vscode/launch.json`) to open an
Extension Development Host with the parent `spiders/` folder loaded.

## Install permanently

```bash
npm run package                              # builds ai-signal-scraper.vsix
code --install-extension ai-signal-scraper.vsix
```
