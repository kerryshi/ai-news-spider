<#
.SYNOPSIS
  One-command CI/CD for the AI Signal scraper: test -> ship engine to the Jetson
  -> rebuild+reinstall the VS Code extension -> remote smoke test -> commit.

.EXAMPLE
  ./scripts/deploy.ps1 -Message "tune ranking weights"
  ./scripts/deploy.ps1 -SkipExtension      # engine-only change
  ./scripts/deploy.ps1 -SkipTests -NoCommit
#>
param(
  [string]$Message = "deploy",
  [switch]$SkipTests,
  [switch]$SkipExtension,
  [switch]$NoCommit
)
$ErrorActionPreference = 'Stop'

$root      = Split-Path $PSScriptRoot -Parent
$py        = Join-Path $root ".venv\Scripts\python.exe"
$jet       = "kershy@192.168.55.1"
$remoteDir = "/home/kershy/ai-signal"
$remotePy  = "/home/kershy/miniforge3/bin/python"
$code      = "C:\Users\PC\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd"
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "FAILED: $m" -ForegroundColor Red; exit 1 }

Set-Location $root

# 1. TEST (the gate) --------------------------------------------------------
if (-not $SkipTests) {
  Step "Running tests"
  & $py -m pytest
  if ($LASTEXITCODE -ne 0) { Die "tests failed - not deploying" }
} else { Write-Host "(skipping tests)" -ForegroundColor DarkGray }

# 2. SHIP ENGINE to the Jetson ---------------------------------------------
Step "Deploying engine to the Jetson"
$enginePy  = (Get-ChildItem "$root\engine\*.py").FullName
$sourcePy  = (Get-ChildItem "$root\engine\sources\*.py").FullName
scp -q $enginePy "${jet}:${remoteDir}/engine/"
scp -q $sourcePy "${jet}:${remoteDir}/engine/sources/"
scp -q "$root\config.toml" "${jet}:${remoteDir}/config.toml"
# the Jetson talks to THIS PC's Ollama, not localhost
ssh -o BatchMode=yes $jet "cd $remoteDir && sed -i 's|http://localhost:11434|http://192.168.55.100:11434|' config.toml"
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
