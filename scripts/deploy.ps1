<#
.SYNOPSIS
  One-command CI/CD for the AI Signal scraper: test -> ship engine to the Jetson
  -> rebuild+reinstall the VS Code extension -> remote smoke test -> commit.

.EXAMPLE
  ./scripts/deploy.ps1 -Message "tune ranking weights"
  ./scripts/deploy.ps1 -SkipExtension      # engine-only change
  ./scripts/deploy.ps1 -DryRun             # run the test gate only; no Jetson contact
#>
[CmdletBinding()]
param(
  [string]$Message = "deploy",
  [switch]$SkipExtension,
  [switch]$NoCommit,
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

$root      = Split-Path $PSScriptRoot -Parent
$py        = Join-Path $root ".venv\Scripts\python.exe"
# Host / identity come from env so nothing personal is committed. Set these (e.g. in
# your PowerShell profile) before deploying; the fallbacks are generic placeholders.
# 192.168.55.1 is the standard Jetson USB-gadget address; OLLAMA_LAN_HOST is YOUR
# desktop's LAN address that the Jetson reaches Ollama on.
$jet       = if ($env:JETSON_HOST)     { $env:JETSON_HOST }     else { "jetson@192.168.55.1" }
$remoteDir = if ($env:JETSON_DIR)      { $env:JETSON_DIR }      else { "~/ai-signal" }
$remotePy  = if ($env:JETSON_PYTHON)   { $env:JETSON_PYTHON }   else { "~/miniforge3/bin/python" }
$ollamaLan = if ($env:OLLAMA_LAN_HOST) { $env:OLLAMA_LAN_HOST } else { "http://localhost:11434" }
$code      = if ($env:VSCODE_CMD)      { $env:VSCODE_CMD }      else { "code" }
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "FAILED: $m" -ForegroundColor Red; exit 1 }

Set-Location $root

# 1. TEST (the gate; unconditional - no skip switch by decision, PRD D4) -----
Step "Running tests"
& $py -m pytest
if ($LASTEXITCODE -ne 0) { Die "tests failed - not deploying" }

# DRY RUN boundary: stop after the test gate, before ANY Jetson contact.
# No scp, no ssh, no extension build, no commit happens past this line.
if ($DryRun) {
  Write-Host "`nDRY RUN: test gate passed. Stopping at the copy boundary (step 2: ship engine to the Jetson). No scp/ssh attempted." -ForegroundColor Yellow
  exit 0
}

# 2. SHIP ENGINE to the Jetson ---------------------------------------------
Step "Deploying engine to the Jetson"
$enginePy  = (Get-ChildItem "$root\engine\*.py").FullName
$sourcePy  = (Get-ChildItem "$root\engine\sources\*.py").FullName
scp -q $enginePy "${jet}:${remoteDir}/engine/"
scp -q $sourcePy "${jet}:${remoteDir}/engine/sources/"
scp -q "$root\config.toml" "${jet}:${remoteDir}/config.toml"
# the Jetson talks to THIS PC's Ollama over the LAN, not its own localhost.
# Set OLLAMA_LAN_HOST to your desktop's address; unset = no-op (Jetson uses localhost).
ssh -o BatchMode=yes $jet "cd $remoteDir && sed -i 's|http://localhost:11434|$ollamaLan|' config.toml"
Write-Host "    engine synced (cron picks it up next cycle)" -ForegroundColor DarkGray

# 3. REMOTE SMOKE TEST ------------------------------------------------------
Step "Remote smoke test (top --json on the Jetson)"
# PS 5.1 turns a native command's redirected stderr into a terminating error, so
# relax ErrorAction just for the ssh call and keep only stdout (the JSON).
$prevEA = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$out = ssh -o BatchMode=yes $jet "cd $remoteDir && $remotePy -m engine.cli top --json --n 3" 2>$null | Out-String
$ErrorActionPreference = $prevEA
try { $j = $out | ConvertFrom-Json; Write-Host "    ok: $($j.items.Count) items ranked" -ForegroundColor Green }
catch { Die "smoke test did not return valid JSON" }

# 4. BUILD + INSTALL the extension -----------------------------------------
if (-not $SkipExtension) {
  Step "Building + installing the VS Code extension"
  $env:PATH = "C:\Program Files\nodejs;$env:PATH"
  Push-Location "$root\extension"
  npm version patch --no-git-tag-version | Out-Null      # bump so VS Code sees an update
  npm run compile; if ($LASTEXITCODE -ne 0) { Pop-Location; Die "tsc compile failed" }
  npm run package; if ($LASTEXITCODE -ne 0) { Pop-Location; Die "vsce package failed" }
  & $code --install-extension "ai-signal-scraper.vsix" --force | Out-Null
  $v = (Get-Content package.json -Raw | ConvertFrom-Json).version
  Pop-Location
  Write-Host "    installed extension v$v (reload VS Code / open a new window)" -ForegroundColor Green
}

# 5. COMMIT -----------------------------------------------------------------
if (-not $NoCommit) {
  Step "Committing"
  git add -A
  git commit -m $Message | Out-Null
  Write-Host "    committed: $Message" -ForegroundColor Green
}

Write-Host "`nDone." -ForegroundColor Green
