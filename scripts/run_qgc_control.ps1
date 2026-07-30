#requires -version 5.1
[CmdletBinding()]
param(
  [ValidateSet("no-wind", "factory", "factory-wind", "wind", "urban", "hex", "rover")]
  [string]$Scene = "no-wind",
  [int]$SessionSeconds = 900,
  [string]$Distro = "Ubuntu",
  [string]$QgcPath = "C:\IFSC-Station\IFSC-Station.exe",
  [ValidateRange(0, 120)]
  [int]$EstimatorSettleSec = 35,
  [ValidateRange(0.1, 5.0)]
  [double]$Ekf2AblLim = 5.0,
  [ValidateRange(-1.0, 30.0)]
  [double]$WindMps = -1.0,
  [switch]$WindUI,
  [switch]$RestartQGC,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$VerifyScript = Join-Path $Root "outputs\verify_px4_ue_hil.ps1"
$MainLauncher = Join-Path $Root "outputs\start_px4_ue_hil.sh"
$QgcLauncher = Join-Path $Root "outputs\start_px4_ue_qgc_control.sh"
$RelayScript = Join-Path $Root "outputs\mavlink_relay.py"
$RoverAirframe = Join-Path $Root "outputs\px4_airframes\51001_none_rover_ackermann"
$Map = switch ($Scene) {
  "factory" { "/Game/Maps/SZTUFactoryFlightScene" }
  "factory-wind" { "/Game/Maps/SZTUFactoryWindScene" }
  "wind" { "/Game/Maps/SZTUWindFieldDemoScene" }
  "urban" { "/Game/Maps/SZTUUrbanSemanticScene" }
  "hex" { "/Game/Maps/SZTUHexacopterScene" }
  "rover" { "/Game/Maps/SZTURoverScene" }
  default { "/Game/Maps/SZTUFlightPatternsScene" }
}
$Vehicle = switch ($Scene) {
  "hex" { "hex" }
  "rover" { "rover" }
  default { "quad" }
}
$EffectiveWindMps = $WindMps
if ($Scene -in @("wind", "factory-wind") -and -not $PSBoundParameters.ContainsKey("WindMps")) {
  $EffectiveWindMps = 8.0
}

function Get-WslPath {
  param([string]$WindowsPath)
  $full = [System.IO.Path]::GetFullPath($WindowsPath)
  if ($full -notmatch "^([A-Za-z]):\\(.*)$") {
    throw "cannot convert path to WSL: $full"
  }
  $drive = $Matches[1].ToLowerInvariant()
  $tail = $Matches[2] -replace "\\", "/"
  "/mnt/$drive/$tail"
}

function Sync-WslLauncher {
  param([string]$WindowsPath)
  $source = Get-WslPath $WindowsPath
  $leaf = Split-Path -Leaf $WindowsPath
  & wsl.exe -d $Distro -- bash -lc "mkdir -p ~/szefitl && cp '$source' ~/szefitl/$leaf && sed -i 's/\r$//' ~/szefitl/$leaf && chmod +x ~/szefitl/$leaf"
  if ($LASTEXITCODE -ne 0) {
    throw "failed to sync $leaf into WSL"
  }
}

function Get-GroundStationProcesses {
  @(
    Get-Process -Name "IFSC-Station", "QGroundControl" -ErrorAction SilentlyContinue
  )
}

if ($SessionSeconds -lt 60) {
  throw "SessionSeconds must be at least 60"
}

if (-not $DryRun) {
  Sync-WslLauncher $MainLauncher
  Sync-WslLauncher $QgcLauncher
  Sync-WslLauncher $RelayScript
  $roverAirframeSource = Get-WslPath $RoverAirframe
  & wsl.exe -d $Distro -- bash -lc "mkdir -p ~/szefitl/px4_airframes && cp '$roverAirframeSource' ~/szefitl/px4_airframes/51001_none_rover_ackermann && sed -i 's/\r$//' ~/szefitl/px4_airframes/51001_none_rover_ackermann && chmod +x ~/szefitl/px4_airframes/51001_none_rover_ackermann"
  if ($LASTEXITCODE -ne 0) {
    throw "failed to sync rover airframe into WSL"
  }
}

Write-Host "[qgc-control] scene=$Scene map=$Map session=${SessionSeconds}s"
Write-Host "[qgc-control] vehicle=$Vehicle"
Write-Host "[qgc-control] OFFBOARD flight script is disabled; IFSC Station owns flight commands"
Write-Host "[qgc-control] estimator settle=${EstimatorSettleSec}s ABL_LIM=$Ekf2AblLim wind=${EffectiveWindMps}m/s before IFSC Station opens"
Write-Host "[qgc-control] wind UI=$WindUI"

$verifyParams = @{
  Map = $Map
  Vehicle = $Vehicle
  WslLaunchScript = "~/szefitl/start_px4_ue_qgc_control.sh"
  GraphicsRhi = "dx11"
  WindowWidth = 960
  WindowHeight = 540
  NoSound = $true
  SkipFlight = $true
  KeepRunning = $true
  TelemetryDurationSec = $SessionSeconds
  EstimatorSettleSec = $EstimatorSettleSec
  Ekf2AblLim = $Ekf2AblLim
  MpcThrHover = if ($Vehicle -eq "hex") { 0.50 } else { 0.62 }
  WindMps = $EffectiveWindMps
  WindUI = $WindUI
  DryRun = $DryRun
}

& $VerifyScript @verifyParams
$verifySucceeded = $?
if (-not $verifySucceeded) {
  exit 1
}

if ($DryRun) {
  exit 0
}

if ($RestartQGC) {
  $existing = @(Get-GroundStationProcesses)
  foreach ($process in $existing) {
    [void]$process.CloseMainWindow()
  }
  Start-Sleep -Seconds 3
  $remaining = @(Get-GroundStationProcesses)
  foreach ($process in $remaining) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    try {
      $process.WaitForExit(5000)
    }
    catch {
      # A process that exited between discovery and WaitForExit is already clean.
    }
  }
  Start-Sleep -Seconds 1
}

if (@(Get-GroundStationProcesses).Count -eq 0) {
  if (-not (Test-Path -LiteralPath $QgcPath)) {
    throw "IFSC Station not found: $QgcPath"
  }
  Start-Process -FilePath $QgcPath
}

$listener = $null
$groundStationDeadline = (Get-Date).AddSeconds(30)
do {
  $groundStationPids = @(Get-GroundStationProcesses | Select-Object -ExpandProperty Id)
  $listener = Get-NetUDPEndpoint -LocalPort 14550 -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -in $groundStationPids } |
    Select-Object -First 1
  if ($listener) {
    break
  }
  Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $groundStationDeadline)

if ($listener) {
  Write-Host "[qgc-control] IFSC Station UDP 14550 listener detected"
}
else {
  throw "IFSC Station did not acquire UDP 14550 within 30 seconds"
}

if ($Vehicle -eq "rover") {
  Write-Host "[qgc-control] ready: arm in IFSC Station, then use the virtual joystick (throttle + steering)"
}
else {
  Write-Host "[qgc-control] ready: wait for IFSC Station 'Ready to Fly', then use Fly > Takeoff"
}
Write-Host "[qgc-control] PID trials: one takeoff per fresh UE/PX4 session; restart after landing"
Write-Host "[qgc-control] normal stop: Land in IFSC Station before running outputs\px4_emergency_stop.ps1 -KillSim"
