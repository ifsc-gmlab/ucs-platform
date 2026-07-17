#requires -version 5.1
[CmdletBinding()]
param(
  [ValidateRange(0.5, 30.0)]
  [double]$WindMps = 5.0,
  [double]$Alt = 3.0,
  [double]$Hover = 30.0,
  [double]$Climb = 6.0,
  [double]$Descend = 12.0,
  [ValidateRange(0.1, 5.0)]
  [double]$Ekf2AblLim = 5.0,
  [ValidateRange(0, 120)]
  [int]$EstimatorSettleSec = 35,
  [int]$FlightTimeoutSec = 160,
  [switch]$KeepRunning,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$VerifyScript = Join-Path $Root "outputs\verify_px4_ue_hil.ps1"
$RuntimeFiles = @(
  (Join-Path $Root "outputs\takeoff_offboard.py"),
  (Join-Path $Root "outputs\start_px4_ue_hil.sh"),
  (Join-Path $Root "outputs\mavlink_relay.py")
)

if (-not $DryRun) {
  foreach ($runtimeFile in $RuntimeFiles) {
    $full = [System.IO.Path]::GetFullPath($runtimeFile)
    if ($full -notmatch "^([A-Za-z]):\\(.*)$") {
      throw "cannot convert runtime file path to WSL: $full"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2] -replace "\\", "/"
    $wslSource = "/mnt/$drive/$tail"
    $leaf = Split-Path -Leaf $runtimeFile
    & wsl.exe bash -lc "mkdir -p ~/szefitl && cp '$wslSource' ~/szefitl/$leaf && sed -i 's/\r$//' ~/szefitl/$leaf && chmod +x ~/szefitl/$leaf"
    if ($LASTEXITCODE -ne 0) { throw "failed to sync $leaf into WSL" }
  }
}

Write-Host "[wind-hover] wind=${WindMps}m/s alt=${Alt}m hover=${Hover}s settle=${EstimatorSettleSec}s"

$verifyParams = @{
  Map = "/Game/Maps/SZTUWindFieldDemoScene"
  FlightPattern = "hover"
  WindMps = $WindMps
  Alt = $Alt
  Hold = $Hover
  Climb = $Climb
  Descend = $Descend
  Ekf2AblLim = $Ekf2AblLim
  EstimatorSettleSec = $EstimatorSettleSec
  FlightTimeoutSec = $FlightTimeoutSec
  KeepRunning = $KeepRunning
  DryRun = $DryRun
}

& $VerifyScript @verifyParams
