# Cold-start measurement, from the outside.
#
# Nothing inside the process can see the PyInstaller bootloader unpacking or
# the interpreter starting, so this records the clock before Start-Process and
# the app records its own first line. The difference is the launch overhead
# the user actually feels before anything of ours runs.
#
#   .\measure_app.ps1 -Exe "dist\onedir\RDR Modding Studio\RDR Modding Studio.exe" -Runs 3
#
# The app quits itself after probing its own front end (--timings-file).

param(
  [Parameter(Mandatory=$true)][string]$Exe,
  [int]$Runs = 3,
  [string]$Label = ""
)

if (-not $Label) { $Label = Split-Path $Exe -Leaf }
$out = Join-Path $env:TEMP "rdr_timings.json"
$rows = @()

for ($i = 1; $i -le $Runs; $i++) {
  if (Test-Path $out) { Remove-Item $out -Force }
  $t0 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
  $p = Start-Process -FilePath $Exe -ArgumentList @("--timings-file", $out) -PassThru -Wait
  $wall = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 - $t0

  if (-not (Test-Path $out)) { "run ${i}: no timings written (exit $($p.ExitCode))"; continue }
  $j = Get-Content $out -Raw | ConvertFrom-Json

  $rows += [pscustomobject]@{
    run          = $i
    launch_s     = [math]::Round($j.entry_epoch - $t0, 3)   # bootloader + interpreter
    imports_s    = $j.own_imports
    window_s     = $j.window_shown
    server_s     = $j.server_start
    to_studio_s  = [math]::Round(($j.entry_epoch - $t0) + $j.total_to_studio, 3)
    dom_ms       = $j.frontend.domContentLoaded
    webgl2       = $j.frontend.webgl2
    nav_buttons  = $j.frontend.navButtons
    wall_s       = [math]::Round($wall, 2)
  }
}

"=== $Label ==="
$rows | Format-Table -AutoSize
if ($rows.Count -gt 1) {
  $c = ($rows | Measure-Object to_studio_s -Average -Minimum).Minimum
  "cold start to a usable Studio, best of ${Runs}: {0:N2} s" -f $c
  "                            average       : {0:N2} s" -f (($rows | Measure-Object to_studio_s -Average).Average)
}
