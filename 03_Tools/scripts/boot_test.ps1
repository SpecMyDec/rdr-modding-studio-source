<#
.SYNOPSIS
  Boot-test a modded RDR ISO in PCSX2 and read the verdict from the emulator log.

.DESCRIPTION
  This script reads its verdict from the emulator log:

     "is executing" / "Elf entry"  -> the ELF booted
     "TLB Miss"                    -> the bad-mod crash signature

  CORRECTED 2026-09-18. This block used to state, as settled fact, that
  "PCSX2 renders through Vulkan, so screenshots of the game window come back
  black -- do not try to verify visually". That is CONDITIONAL and the
  condition is not in force here:

     inis/PCSX2.ini   Renderer = -1   (automatic -> Direct3D on Windows)

  Under Direct3D an ordinary OS window grab captures the game fine.
  `scripts/play_test.py` has been doing exactly that all along -- a PowerShell
  window grab, deliberately NOT PCSX2's own F8 -- and on 2026-09-18 it returned
  12 of 12 legible frames, rendered 3D included, which is how the M20 spawn
  boot was diagnosed as having stalled at the title screen.

  Note this file contains no capture code at all (no Bitmap, CopyFromScreen or
  Graphics), so the old claim was never a measurement made here. It read as
  fact because nothing in the file showed it had gone untested, and it cost
  rdr-3d a wrong conclusion reported to the reviewer -- that an in-game level load
  could not be verified visually and so could not be automated. It can.

  If anyone sets Renderer to Vulkan the old warning becomes true again, so a
  visual check must ASSERT the frame is not uniformly black rather than assume
  it rendered. A check that has never been watched to succeed is not evidence
  when it fails -- which is the same trap as the claim it replaces.

  A boot test only reaches the main menu. Menu edits and level scripting still
  need an in-game LEVEL LOAD test, which this script does not do -- but that is
  a limit of this script, not of automation.

.EXAMPLE
  .\boot_test.ps1
  .\boot_test.ps1 -Seconds 90 -Iso "02_Modded\RDR_MODDED.iso"
#>
[CmdletBinding()]
param(
  [string]$Iso     = "02_Modded\RDR_MODDED.iso",
  [string]$Pcsx2   = "<PCSX2 folder>\pcsx2-qt.exe",
  [int]   $Seconds = 45,
  [switch]$KeepOpen
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Iso))   { Write-Error "ISO not found: $Iso" }
if (-not (Test-Path $Pcsx2)) { Write-Error "PCSX2 not found: $Pcsx2" }

# Portable installs log beside the exe; installed ones use Documents.
$logCandidates = @(
  (Join-Path (Split-Path $Pcsx2) "logs\emulog.txt"),
  (Join-Path $env:USERPROFILE "Documents\PCSX2\logs\emulog.txt")
)
$log = $logCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $log) { $log = $logCandidates[0] }

$launchedAt = Get-Date

Write-Host "ISO   : $Iso"
Write-Host "PCSX2 : $Pcsx2"
Write-Host "Log   : $log"
Write-Host "Running for $Seconds seconds..." -ForegroundColor Cyan

$proc = Start-Process -FilePath $Pcsx2 -ArgumentList @('-fastboot', $Iso) -PassThru
Start-Sleep -Seconds $Seconds

if (-not $KeepOpen) {
  try { if (-not $proc.HasExited) { $proc.CloseMainWindow() | Out-Null; Start-Sleep 3 } } catch {}
  try { if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force } } catch {}
}

if (-not (Test-Path $log)) {
  Write-Host "RESULT: UNKNOWN - no emulator log at $log" -ForegroundColor Yellow
  exit 2
}

# PCSX2 REWRITES the log from scratch each launch, so the whole file IS this
# run. Diffing against the previous size read an empty tail and reported
# healthy boots as failures. Guard staleness by timestamp instead.
if ((Get-Item $log).LastWriteTime -lt $launchedAt.AddSeconds(-5)) {
  Write-Host "RESULT: UNKNOWN - emulator log was not updated; did PCSX2 start?" -ForegroundColor Yellow
  exit 2
}
$fs = [System.IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
$text = (New-Object System.IO.StreamReader($fs)).ReadToEnd()
$fs.Close()

$tlb    = ([regex]::Matches($text, 'TLB Miss')).Count
$booted = $text -match 'is executing' -or $text -match 'Elf entry'

Write-Host ""
Write-Host ("booted     : {0}" -f $booted)
Write-Host ("TLB misses : {0}" -f $tlb)
Write-Host ""

if ($booted -and $tlb -eq 0) {
  Write-Host "RESULT: PASS" -ForegroundColor Green
  exit 0
}

Write-Host "RESULT: FAIL" -ForegroundColor Red
if ($tlb -gt 0) {
  Write-Host "TLB misses are the bad-mod signature: a resource lookup returned garbage."
  Write-Host "Restore vanilla, then re-apply ONE change at a time to find the culprit."
}
$text -split "`n" | Select-Object -Last 25 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
exit 1
