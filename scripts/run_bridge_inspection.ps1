#requires -version 5.1
[CmdletBinding()]
param(
  [ValidateRange(0.0, 15.0)][double]$WindMps = 3.0,
  [ValidateRange(0.5, 3.0)][double]$CruiseSpeed = 1.0,
  [ValidateRange(0.5, 10.0)][double]$CameraCaptureHz = 1.0,
  [ValidateRange(320, 1920)][int]$CameraWidth = 320,
  [ValidateRange(180, 1080)][int]$CameraHeight = 180,
  [switch]$KeepRunning,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $PSCommandPath
$Args = @{
  SemanticMapFile = Join-Path $Here "bridge_semantic_map.json"
  PlanFile = Join-Path $Here "test-plans\bridge_crossing_capture.json"
  UeMap = "/Game/Maps/SZTUBridgeSemanticScene"
  Alt = 3.0
  CruiseSpeed = $CruiseSpeed
  MissionTimeoutSec = 120
  WindMps = $WindMps
  CameraCaptureHz = $CameraCaptureHz
  CameraWidth = $CameraWidth
  CameraHeight = $CameraHeight
  Vision = $true
  ArchiveFrames = $true
  KeepRunning = $KeepRunning
  DryRun = $DryRun
}

& (Join-Path $Here "run_semantic_city.ps1") @Args
exit $LASTEXITCODE
