<#
.SYNOPSIS
  Restore the Jetson's internet: re-enable Internet Connection Sharing,
  Wi-Fi (public/shared) -> Ethernet (private, the Jetson's RJ45 uplink).

.DESCRIPTION
  The Jetson gets internet via Windows ICS on this desktop (New-NetNat is
  unavailable on Windows Home). When ICS silently drops (observed after some
  updates/reboots), the Jetson's eth0 loses its 192.168.137.x lease and every
  collect fails with "Name or service not known" — see RUNBOOK troubleshooting.

  MUST RUN ELEVATED:
    powershell -ExecutionPolicy Bypass -File scripts\jetson-ics.ps1
  (right-click -> Run as administrator, or let the caller trigger UAC)

  Replaces the ad-hoc %TEMP%\jetics.ps1, which Windows temp cleanup deleted.
#>
param(
  [string]$PublicAdapter  = "Wi-Fi",      # the internet-facing side
  [string]$PrivateAdapter = "Ethernet"    # the Jetson-facing RJ45
)
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "must run elevated (ICS COM writes need admin)"
}

$share = New-Object -ComObject HNetCfg.HNetShare

function Get-Conn([string]$name) {
  foreach ($c in $share.EnumEveryConnection) {
    if ($share.NetConnectionProps.Invoke($c).Name -eq $name) { return $c }
  }
  return $null
}

$pub = Get-Conn $PublicAdapter
$prv = Get-Conn $PrivateAdapter
if (-not $pub) { throw "adapter '$PublicAdapter' not found" }
if (-not $prv) { throw "adapter '$PrivateAdapter' not found" }

# Clean slate: ICS allows exactly one shared pair; stale half-enabled state is
# the usual reason re-enabling silently no-ops.
foreach ($c in $share.EnumEveryConnection) {
  $cfg = $share.INetSharingConfigurationForINetConnection.Invoke($c)
  if ($cfg.SharingEnabled) { $cfg.DisableSharing() }
}

($share.INetSharingConfigurationForINetConnection.Invoke($pub)).EnableSharing(0)  # ICSSHARINGTYPE_PUBLIC
($share.INetSharingConfigurationForINetConnection.Invoke($prv)).EnableSharing(1)  # ICSSHARINGTYPE_PRIVATE

# Keep it surviving reboots (SharedAccess service + persistence flag).
Set-Service SharedAccess -StartupType Automatic
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Network\SharedAccess" `
  -Name EnableRebootPersistConnection -Value 1 -Type DWord -Force

Write-Host "ICS re-enabled: '$PublicAdapter' (shared) -> '$PrivateAdapter' (private)." -ForegroundColor Green
Write-Host "The Jetson's eth0 should pick up a 192.168.137.x lease within ~30s (or bounce its eth0)."
