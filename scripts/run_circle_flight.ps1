#requires -version 5.1
<#
.SYNOPSIS
Runs one validated no-wind OFFBOARD circle flight and lands automatically.

.DESCRIPTION
This is the operator-friendly entry point for circle demonstrations. The
trajectory generator remains in takeoff_offboard.py and the full launch,
logging, analysis, touchdown confirmation, and cleanup path remains in
run_flight_patterns.ps1.
#>
[CmdletBinding()]
param(
  [ValidateRange(0.5, 10.0)]
  [double]$Alt = 1.0,
  [ValidateRange(0.5, 20.0)]
  [double]$Radius = 1.5,
  [ValidateRange(0.25, 10.0)]
  [double]$Laps = 1.0,
  [ValidateRange(6.0, 300.0)]
  [double]$Duration = 14.0,
  [ValidateRange(1.0, 30.0)]
  [double]$Entry = 3.0,
  [ValidateRange(0.0, 120.0)]
  [double]$Hover = 5.0,
  [ValidateRange(2.0, 30.0)]
  [double]$Climb = 4.0,
  [ValidateRange(6.0, 60.0)]
  [double]$Descend = 10.0,
  [ValidateRange(60, 600)]
  [int]$FlightTimeoutSec = 160,
  [switch]$KeepRunning,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$PatternRunner = Join-Path $Root "outputs\run_flight_patterns.ps1"

if (-not (Test-Path -LiteralPath $PatternRunner)) {
  throw "circle flight dependency not found: $PatternRunner"
}

Write-Host "[circle-flight] no-wind OFFBOARD circle"
Write-Host "[circle-flight] alt=${Alt}m radius=${Radius}m laps=$Laps duration=${Duration}s entry=${Entry}s"

$circleParams = @{
  Pattern = "circle"
  Alt = $Alt
  Hover = $Hover
  Climb = $Climb
  Descend = $Descend
  CircleRadius = $Radius
  CircleLaps = $Laps
  CircleDuration = $Duration
  CircleEntry = $Entry
  FlightTimeoutSec = $FlightTimeoutSec
  KeepRunning = $KeepRunning
  DryRun = $DryRun
}

& $PatternRunner @circleParams
if (-not $?) {
  exit 1
}
