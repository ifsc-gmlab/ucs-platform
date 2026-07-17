#requires -version 5.1
[CmdletBinding()]
param(
  [double]$Alt = 1.0,
  [double]$Hold = 2.0,
  [double]$Climb = 3.0,
  [double]$Descend = 10.0,
  [double]$HeadwindDistance = 2.0,
  [double]$Move = 5.0,
  [ValidateRange(0.5, 30.0)]
  [double]$WindMps = 8.0,
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
  [int]$TcpTimeoutSec = 90,
  [int]$Px4TimeoutSec = 120,
  [int]$FlightTimeoutSec = 120,
  [switch]$KeepRunning,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
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

Write-Host "[headwind] wind is +X at ${WindMps}m/s; commanding drone toward -X for $HeadwindDistance m over $Move s"
Write-Host "[headwind] vertical params ZP=$MpcZP PI_D=$MpcZVelPAcc/$MpcZVelIAcc/$MpcZVelDAcc hover=$MpcThrHover ABL_LIM=$Ekf2AblLim settle=${EstimatorSettleSec}s"

$verifyParams = @{
  Map = "/Game/Maps/SZTUWindFieldDemoScene"
  Alt = $Alt
  Hold = $Hold
  Climb = $Climb
  Descend = $Descend
  HeadwindTest = $true
  HeadwindDistance = $HeadwindDistance
  Move = $Move
  WindMps = $WindMps
  MpcZP = $MpcZP
  MpcZVelPAcc = $MpcZVelPAcc
  MpcZVelIAcc = $MpcZVelIAcc
  MpcZVelDAcc = $MpcZVelDAcc
  MpcThrHover = $MpcThrHover
  Ekf2AblLim = $Ekf2AblLim
  EstimatorSettleSec = $EstimatorSettleSec
  TcpTimeoutSec = $TcpTimeoutSec
  Px4TimeoutSec = $Px4TimeoutSec
  FlightTimeoutSec = $FlightTimeoutSec
  KeepRunning = $KeepRunning
  DryRun = $DryRun
}

& $VerifyScript @verifyParams
