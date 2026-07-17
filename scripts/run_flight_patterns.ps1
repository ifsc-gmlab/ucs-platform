#requires -version 5.1
[CmdletBinding()]
param(
  [ValidateSet("hover", "circle")]
  [string]$Pattern = "circle",
  [double]$Alt = 1.0,
  [double]$Hover = 5.0,
  [double]$Climb = 4.0,
  [double]$Descend = 10.0,
  [double]$CircleRadius = 1.5,
  [double]$CircleLaps = 1.0,
  [double]$CircleDuration = 14.0,
  [double]$CircleEntry = 3.0,
  [ValidateRange(0.1, 3.0)]
  [double]$MpcZP = 1.5,
  [ValidateRange(0.1, 12.0)]
  [double]$MpcZVelPAcc = 3.0,
  [ValidateRange(0.0, 10.0)]
  [double]$MpcZVelIAcc = 1.5,
  [ValidateRange(0.0, 3.0)]
  [double]$MpcZVelDAcc = 0.0,
  [ValidateRange(0.1, 0.9)]
  [double]$MpcThrHover = 0.62,
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
$TakeoffScript = Join-Path $Root "outputs\takeoff_offboard.py"
$MainLauncher = Join-Path $Root "outputs\start_px4_ue_hil.sh"
$RelayScript = Join-Path $Root "outputs\mavlink_relay.py"

if (-not $DryRun) {
  foreach ($runtimeFile in @($TakeoffScript, $MainLauncher, $RelayScript)) {
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

Write-Host "[flight-patterns] map=no-wind pattern=$Pattern alt=${Alt}m hover=${Hover}s"
Write-Host "[flight-patterns] vertical params ZP=$MpcZP PI_D=$MpcZVelPAcc/$MpcZVelIAcc/$MpcZVelDAcc hover=$MpcThrHover ABL_LIM=$Ekf2AblLim settle=${EstimatorSettleSec}s"
if ($Pattern -eq "circle") {
  Write-Host "[flight-patterns] circle radius=${CircleRadius}m laps=$CircleLaps duration=${CircleDuration}s"
}

$verifyParams = @{
  Map = "/Game/Maps/SZTUFlightPatternsScene"
  FlightPattern = $Pattern
  Alt = $Alt
  Hold = $Hover
  Climb = $Climb
  Descend = $Descend
  CircleRadius = $CircleRadius
  CircleLaps = $CircleLaps
  CircleDuration = $CircleDuration
  CircleEntry = $CircleEntry
  MpcZP = $MpcZP
  MpcZVelPAcc = $MpcZVelPAcc
  MpcZVelIAcc = $MpcZVelIAcc
  MpcZVelDAcc = $MpcZVelDAcc
  MpcThrHover = $MpcThrHover
  Ekf2AblLim = $Ekf2AblLim
  EstimatorSettleSec = $EstimatorSettleSec
  FlightTimeoutSec = $FlightTimeoutSec
  KeepRunning = $KeepRunning
  DryRun = $DryRun
}

& $VerifyScript @verifyParams
