#requires -version 5.1
<#
 .SYNOPSIS
  Vertical hover-oscillation PID sweep. Each variant runs one fresh UE/PX4 session
  (45 s hover), auto-captures the WSL-side ULog, runs analyze_vertical_oscillation.py,
  and summarizes the key metrics per variant into a single CSV.

 .DESCRIPTION
  - One vertical-speed PI parameter is changed per variant (MPC_Z_VEL_I_ACC or
    MPC_Z_VEL_P_ACC). Baseline is fixed at
    ZP=1.5 / ZVelP=3.0 / ZVelI=1.5 / ZVelD=0 / Hover=0.62 (the validated
    AirSim-style plant profile from run 20260716-110443).
  - run_flight_patterns.ps1 re-syncs scripts into WSL ~/szefitl and restarts PX4 on every
    call, so the loop inherently satisfies the "fresh session per param, one takeoff only"
    A/B condition (do not fly twice in one session).
  - Requires a machine where WSL/PX4 can run, and WSL python3 with pyulog + numpy installed.

 .NOTES
  Long-running: each variant is ~3-5 min (startup + 35 s estimator settle + 45 s hover +
  descent + cleanup). Keep the machine awake and leave enough RAM before running.
  If the ULog is not found in the default directory, the script warns and skips analysis, but
  the flight result is still recorded under outputs/verification-runs/<ts>/; you can re-run
  analyze_vertical_oscillation.py manually with the correct ulog path.
#>
[CmdletBinding()]
param(
  [string]$WslDistro = "Ubuntu",
  # Directory where PX4 SITL writes .ulg files (WSL-side path). Change if different.
  [string]$UlogDirWsl = "/home/reisei0824/PX4-Autopilot/build/px4_sitl_default/rootfs/log",
  [int]$EstimatorSettleSec = 35,
  [double]$MpcZP = 1.5,
  [double]$MpcThrHover = 0.62,
  [double]$Alt = 3.0,
  [double]$Hover = 45.0,
  [double]$Climb = 6.0,
  [double]$Descend = 12.0,
  [switch]$DryRun,
  [switch]$FirstOnly,
  # Comma-separated list of variant labels to run (e.g. "I=1.0,I=2.0").
  # If set, only those variants run. Used to batch a long sweep across calls.
  [string]$OnlyLabels = ""
)

$ErrorActionPreference = "Continue"   # a single variant failure must not abort the whole sweep

# Workaround for a PowerShell 5.1 / .NET Framework bug: a redirected
# Start-Process (UseShellExecute=false) makes .NET copy the env block into a
# case-insensitive dictionary, throwing "字典中的关键字:'Path'所添加的关键字:'PATH'"
# if a case-duplicate env key exists (e.g. both 'Path' and 'PATH' injected by the
# host). A plain "$env:PATH = $env:PATH" only SETS one key and leaves the other,
# so the duplicate survives. Explicitly delete both case variants, then set a single
# one; the kernel keeps only one entry and the collision disappears.
if ($env:PATH -or $env:Path) {
  $cleanPath = [System.Environment]::GetEnvironmentVariable("PATH", "Process")
  [System.Environment]::SetEnvironmentVariable("Path", $null, "Process")
  [System.Environment]::SetEnvironmentVariable("PATH", $null, "Process")
  [System.Environment]::SetEnvironmentVariable("PATH", $cleanPath, "Process")
}

$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$RunRoot = Join-Path $Root "outputs\verification-runs"
$FlightPatterns = Join-Path $Root "outputs\run_flight_patterns.ps1"
$AnalyzerRel = "outputs\analyze_vertical_oscillation.py"

# Convert a Windows path to the WSL form /mnt/<drive>/...
function WinToWsl($p) {
  $full = [System.IO.Path]::GetFullPath($p)
  if ($full -notmatch '^([A-Za-z]):\\(.*)$') { throw "cannot convert to wsl path: $full" }
  $drive = $Matches[1].ToLowerInvariant()
  $tail = $Matches[2] -replace '\\', '/'
  return "/mnt/$drive/$tail"
}

# Each variant changes exactly one parameter.
# Baseline: ZP=1.5 / ZVelP=3.0 / ZVelI=1.5 / ZVelD=0 / Hover=0.62
# To narrow the sweep, just delete lines below (e.g. keep only the I scan or only the P scan).
$Sweep = @(
  @{ label = "baseline"; MpcZVelPAcc = 3.0; MpcZVelIAcc = 1.5; MpcZVelDAcc = 0.0 }
  @{ label = "I=1.0";   MpcZVelPAcc = 3.0; MpcZVelIAcc = 1.0; MpcZVelDAcc = 0.0 }
  @{ label = "I=2.0";   MpcZVelPAcc = 3.0; MpcZVelIAcc = 2.0; MpcZVelDAcc = 0.0 }
  @{ label = "I=2.5";   MpcZVelPAcc = 3.0; MpcZVelIAcc = 2.5; MpcZVelDAcc = 0.0 }
  @{ label = "P=2.5";   MpcZVelPAcc = 2.5; MpcZVelIAcc = 1.5; MpcZVelDAcc = 0.0 }
  @{ label = "P=3.5";   MpcZVelPAcc = 3.5; MpcZVelIAcc = 1.5; MpcZVelDAcc = 0.0 }
  @{ label = "D=0.1";   MpcZVelPAcc = 3.0; MpcZVelIAcc = 1.5; MpcZVelDAcc = 0.1 }
  @{ label = "D=0.2";   MpcZVelPAcc = 3.0; MpcZVelIAcc = 1.5; MpcZVelDAcc = 0.2 }
  @{ label = "D=0.3";   MpcZVelPAcc = 3.0; MpcZVelIAcc = 1.5; MpcZVelDAcc = 0.3 }
)

if ($FirstOnly) { $Sweep = @($Sweep[0]) }
if ($OnlyLabels) {
  $wanted = $OnlyLabels.Split(',') | ForEach-Object { $_.Trim() }
  $Sweep = @($Sweep | Where-Object { $wanted -contains $_.label })
  if (-not $Sweep) { Write-Error "No variants match -OnlyLabels '$OnlyLabels'"; exit 1 }
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$SummaryCsv = Join-Path $Root "outputs\vertical_oscillation_sweep_$timestamp.csv"
$LogPath = Join-Path $Root "outputs\vertical_oscillation_sweep_$timestamp.log"
Start-Transcript -Path $LogPath -Append | Out-Null

$CsvRows = @()
$CsvRows += "label,MpcZVelPAcc,MpcZVelIAcc,run_dir,verdict,height_5_95_range_m,vertical_speed_rms_mps,jitter_freq_hz,flight_pass"

function Get-NewestUlog($before) {
  # PX4 SITL writes ulogs under date subfolders (log/YYYY-MM-DD/HH_MM_SS.ulg),
  # so recurse with find instead of a top-level glob.
  $cur = & wsl.exe -d $WslDistro -- bash -lc "find '$UlogDirWsl' -name '*.ulg' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-"
  $cur = ($cur -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
  if ($cur -and $cur.Trim() -ne $before) { return $cur.Trim() }
  return $null
}

foreach ($v in $Sweep) {
  Write-Host "`n===== SWEEP VARIANT: $($v.label)  (ZVelP=$($v.MpcZVelPAcc) ZVelI=$($v.MpcZVelIAcc)) =====" -ForegroundColor Cyan

  if ($DryRun) {
    Write-Host "[dryrun] would run: run_flight_patterns.ps1 -Pattern hover -Alt $Alt -Hover $Hover -Climb $Climb -Descend $Descend -EstimatorSettleSec $EstimatorSettleSec -MpcZP $MpcZP -MpcZVelPAcc $($v.MpcZVelPAcc) -MpcZVelIAcc $($v.MpcZVelIAcc) -MpcZVelDAcc $($v.MpcZVelDAcc) -MpcThrHover $MpcThrHover"
    $CsvRows += "$($v.label),$($v.MpcZVelPAcc),$($v.MpcZVelIAcc),DRYRUN,,,,,"
    continue
  }

  # Snapshot run dirs and ulog before this variant, to detect the newly produced artifacts.
  $beforeDirs = @(Get-ChildItem $RunRoot -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
  $ulogBefore = & wsl.exe -d $WslDistro -- bash -lc "find '$UlogDirWsl' -name '*.ulg' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-"
  $ulogBefore = ($ulogBefore -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
  if ($ulogBefore) { $ulogBefore = $ulogBefore.Trim() } else { $ulogBefore = "" }

  $flightPass = "no"
  try {
    & $FlightPatterns `
      -Pattern hover -Alt $Alt -Hover $Hover -Climb $Climb -Descend $Descend `
      -EstimatorSettleSec $EstimatorSettleSec `
      -MpcZP $MpcZP -MpcZVelPAcc $v.MpcZVelPAcc -MpcZVelIAcc $v.MpcZVelIAcc -MpcZVelDAcc $v.MpcZVelDAcc -MpcThrHover $MpcThrHover
    $flightPass = "yes"
  } catch {
    Write-Warning "flight failed for $($v.label): $_"
  }

  # Identify the newly created run dir.
  $afterDirs = @(Get-ChildItem $RunRoot -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
  $newDir = $afterDirs | Where-Object { $beforeDirs -notcontains $_ } | Sort-Object | Select-Object -Last 1
  $runDir = if ($newDir) { Join-Path $RunRoot $newDir } else { $null }

  # Identify the newly created ulog (WSL side).
  $ulog = Get-NewestUlog $ulogBefore

  $verdict = ""; $hRange = ""; $vzRms = ""; $jitter = ""
  if ($ulog -and $runDir) {
    $wslUlog = $ulog
    $wslPx4Log = WinToWsl (Join-Path $runDir "px4_sitl.log")
    $wslOut = WinToWsl (Join-Path $runDir "vertical_oscillation_analysis.md")
    $wslAnalyzer = WinToWsl (Join-Path $Root $AnalyzerRel)
    Write-Host "[analyze] $wslUlog -> $wslOut"
    try {
      & wsl.exe -d $WslDistro -- bash -lc "python3 '$wslAnalyzer' '$wslUlog' --px4-log '$wslPx4Log' --output '$wslOut'"
    } catch {
      Write-Warning "analyzer failed for $($v.label): $_"
    }
    $md = Join-Path $runDir "vertical_oscillation_analysis.md"
    if (Test-Path $md) {
      $txt = Get-Content $md -Raw -Encoding UTF8
      if ($txt -match 'Verdict:\s*\*\*([^*]+)\*\*') { $verdict = $Matches[1].Trim() }
      if ($txt -match 'Estimated height 5-95% range\s*\|\s*([0-9.]+)\s*m') { $hRange = $Matches[1] }
      if ($txt -match 'Vertical speed RMS\s*\|\s*([0-9.]+)\s*m/s') { $vzRms = $Matches[1] }
      if ($txt -match 'Jitter-band frequency \(0.4-5 Hz\)\s*\|\s*([0-9.]+)\s*Hz') { $jitter = $Matches[1] }
    }
  } else {
    Write-Warning "could not locate ulog ($ulog) or run dir ($runDir) for $($v.label); skipping analyzer. You can re-run analyze_vertical_oscillation.py manually with the correct ulog."
  }

  $CsvRows += "$($v.label),$($v.MpcZVelPAcc),$($v.MpcZVelIAcc),$($runDir),$($verdict),$($hRange),$($vzRms),$($jitter),$($flightPass)"
  Write-Host "[$($v.label)] verdict=$verdict hRange=$hRange vzRms=$vzRms jitter=$jitter flight=$flightPass"
}

$CsvRows | Out-File -Encoding UTF8 $SummaryCsv
Write-Host "`n===== SWEEP DONE =====" -ForegroundColor Green
Write-Host "Summary CSV: $SummaryCsv"
Write-Host "Per-run reports: outputs\verification-runs\<ts>\vertical_oscillation_analysis.md"
Stop-Transcript | Out-Null
