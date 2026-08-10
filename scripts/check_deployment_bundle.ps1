#requires -version 5.1
[CmdletBinding()]
param(
  [string]$ProjectRoot = "D:\SZTUWindFieldPX4Demo\Unreal",
  [string]$UnrealEditor = "D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe",
  [string]$QgcPath = "C:\QGroundControl\bin\QGroundControl.exe",
  [string]$IfscPath = "C:\IFSC-Station\IFSC-Station.exe",
  [string]$RecordingDir = "",
  [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $ReportPath) {
  $ReportPath = Join-Path $WorkspaceRoot "outputs\deployment_bundle_report.md"
}

$checks = New-Object System.Collections.Generic.List[object]
function Add-PathCheck {
  param([string]$Name, [string]$Path, [bool]$Required = $true)
  $exists = Test-Path -LiteralPath $Path
  $checks.Add([pscustomobject]@{
      Name = $Name
      Path = $Path
      Required = $Required
      Exists = $exists
    })
}

Add-PathCheck "Deployment manual" (Join-Path $WorkspaceRoot "DEPLOYMENT_MANUAL.md")
Add-PathCheck "Environment checker" (Join-Path $WorkspaceRoot "outputs\check_px4_wsl_env.ps1")
Add-PathCheck "HIL verifier" (Join-Path $WorkspaceRoot "outputs\verify_px4_ue_hil.ps1")
Add-PathCheck "QGC launcher" (Join-Path $WorkspaceRoot "outputs\run_qgc_control.ps1")
Add-PathCheck "Wind QGC one-click entry" (Join-Path $WorkspaceRoot "START_WIND_QGC_DEMO.cmd")
Add-PathCheck "Wind QGC demo launcher" (Join-Path $WorkspaceRoot "outputs\start_wind_qgc_demo.ps1")
Add-PathCheck "UE/QGC window layout" (Join-Path $WorkspaceRoot "outputs\prepare_wind_demo_layout.ps1")
Add-PathCheck "Circle-flight launcher" (Join-Path $WorkspaceRoot "outputs\run_circle_flight.ps1")
Add-PathCheck "Headwind launcher" (Join-Path $WorkspaceRoot "outputs\run_headwind_test.ps1")
Add-PathCheck "Semantic city guide" (Join-Path $WorkspaceRoot "SEMANTIC_CITY.md")
Add-PathCheck "Semantic city one-click entry" (Join-Path $WorkspaceRoot "START_SEMANTIC_CITY.cmd")
Add-PathCheck "Semantic city launcher" (Join-Path $WorkspaceRoot "outputs\run_semantic_city.ps1")
Add-PathCheck "Semantic avoidance controller" (Join-Path $WorkspaceRoot "outputs\semantic_obstacle_mission.py")
Add-PathCheck "Semantic verdict rules" (Join-Path $WorkspaceRoot "outputs\semantic_mission_verdict.py")
Add-PathCheck "Semantic motion profile" (Join-Path $WorkspaceRoot "outputs\semantic_motion_profile.py")
Add-PathCheck "Semantic route planner" (Join-Path $WorkspaceRoot "outputs\semantic_route_planner.py")
Add-PathCheck "Semantic explainability store" (Join-Path $WorkspaceRoot "outputs\semantic_explainability.py")
Add-PathCheck "Semantic runtime config module" (Join-Path $WorkspaceRoot "outputs\semantic_runtime_config.py")
Add-PathCheck "Semantic runtime config" (Join-Path $WorkspaceRoot "outputs\semantic_runtime_config.json")
Add-PathCheck "Semantic vision detector" (Join-Path $WorkspaceRoot "outputs\vision_detector.py")
Add-PathCheck "IFSC structured event logger" (Join-Path $WorkspaceRoot "outputs\ifsc_event_log.py")
Add-PathCheck "Vision detection contract" (Join-Path $WorkspaceRoot "outputs\contracts\vision_detection.schema.json")
Add-PathCheck "Vision/YOLO interface guide" (Join-Path $WorkspaceRoot "outputs\VISION_YOLO_INTERFACE.md")
Add-PathCheck "IFSC quality gates" (Join-Path $WorkspaceRoot "outputs\verify_ifsc_quality_gates.ps1")
Add-PathCheck "Triple-view recorder" (Join-Path $WorkspaceRoot "outputs\record_task_triple_view.ps1")
Add-PathCheck "Triple-view recording guide" (Join-Path $WorkspaceRoot "outputs\TRIPLE_VIEW_RECORDING.md")
Add-PathCheck "Video artifact validator" (Join-Path $WorkspaceRoot "outputs\verify_video_artifact.py")
Add-PathCheck "Urban semantic map" (Join-Path $WorkspaceRoot "outputs\urban_semantic_map.json")
Add-PathCheck "Semantic regression launcher" (Join-Path $WorkspaceRoot "outputs\run_semantic_regression.ps1")
Add-PathCheck "Semantic wind matrix launcher" (Join-Path $WorkspaceRoot "outputs\run_semantic_wind_matrix.ps1")
Add-PathCheck "Semantic regression gates" (Join-Path $WorkspaceRoot "outputs\semantic_regression_gates.py")
Add-PathCheck "Semantic dashboard" (Join-Path $WorkspaceRoot "outputs\drone_dashboard.py")
Add-PathCheck "Semantic test plans" (Join-Path $WorkspaceRoot "outputs\test-plans")
Add-PathCheck "Semantic wind validation report" (Join-Path $WorkspaceRoot "outputs\semantic-wind-validation-20260730.md")
Add-PathCheck "Migration package builder" (Join-Path $WorkspaceRoot "outputs\prepare_migration_package.ps1")
Add-PathCheck "PX4 launcher" (Join-Path $WorkspaceRoot "outputs\start_px4_ue_hil.sh")
Add-PathCheck "MAVLink relay" (Join-Path $WorkspaceRoot "outputs\mavlink_relay.py")
Add-PathCheck "QGC takeoff patch" (Join-Path $WorkspaceRoot "outputs\px4-qgc-takeoff-p1-compat.patch")
Add-PathCheck "Rover external airframe" (Join-Path $WorkspaceRoot "outputs\px4_airframes\51001_none_rover_ackermann")

Add-PathCheck "UE project" (Join-Path $ProjectRoot "SZTUWindFieldPX4Demo.uproject")
Add-PathCheck "UE VS config" (Join-Path $ProjectRoot ".vsconfig")
Add-PathCheck "UE Config" (Join-Path $ProjectRoot "Config")
Add-PathCheck "UE Content" (Join-Path $ProjectRoot "Content")
Add-PathCheck "UE Source" (Join-Path $ProjectRoot "Source")
Add-PathCheck "UE source assets" (Join-Path $ProjectRoot "SourceArt")
Add-PathCheck "Wind map" (Join-Path $ProjectRoot "Content\Maps\SZTUWindFieldDemoScene.umap")
Add-PathCheck "No-wind map" (Join-Path $ProjectRoot "Content\Maps\SZTUFlightPatternsScene.umap")
Add-PathCheck "Hexacopter map" (Join-Path $ProjectRoot "Content\Maps\SZTUHexacopterScene.umap")
Add-PathCheck "Rover map" (Join-Path $ProjectRoot "Content\Maps\SZTURoverScene.umap")
Add-PathCheck "Open factory flight map" (Join-Path $ProjectRoot "Content\Maps\SZTUFactoryFlightScene.umap")
Add-PathCheck "Factory wind flight map" (Join-Path $ProjectRoot "Content\Maps\SZTUFactoryWindScene.umap")
Add-PathCheck "Urban semantic flight map" (Join-Path $ProjectRoot "Content\Maps\SZTUUrbanSemanticScene.umap")
Add-PathCheck "Factory environment map" (Join-Path $ProjectRoot "Content\TrainEnvironment\Map\Demo.umap")
Add-PathCheck "Factory environment material" (Join-Path $ProjectRoot "Content\TrainEnvironment\Materials\Mat_Concrete_1.uasset")
Add-PathCheck "Factory environment mesh" (Join-Path $ProjectRoot "Content\TrainEnvironment\Meshes\SM_Floor_1.uasset")
Add-PathCheck "Hexacopter mesh" (Join-Path $ProjectRoot "Content\Vehicles\Hexacopter\Meshes\typhoon_h480_complete.uasset")
Add-PathCheck "Rover mesh" (Join-Path $ProjectRoot "Content\Vehicles\Rover\Meshes\rover_ackermann_complete.uasset")

Add-PathCheck "UE 5.8 editor" $UnrealEditor
Add-PathCheck "QGroundControl" $QgcPath
Add-PathCheck "IFSC Station" $IfscPath
if (-not $RecordingDir) {
  $recordingSearchRoot = Join-Path $env:OneDrive "Desktop"
  $recordingCandidates = @(
    Get-ChildItem -LiteralPath $recordingSearchRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object {
      @(Get-ChildItem -LiteralPath $_.FullName -File -Filter "01_*.mp4" -ErrorAction SilentlyContinue).Count -eq 1 -and
      @(Get-ChildItem -LiteralPath $_.FullName -File -Filter "02_*.mp4" -ErrorAction SilentlyContinue).Count -eq 1 -and
      @(Get-ChildItem -LiteralPath $_.FullName -File -Filter "03_*.mp4" -ErrorAction SilentlyContinue).Count -eq 1 -and
      @(Get-ChildItem -LiteralPath $_.FullName -File -Filter "04_*.mp4" -ErrorAction SilentlyContinue).Count -eq 1
    }
  )
  if ($recordingCandidates.Count -eq 1) {
    $RecordingDir = $recordingCandidates[0].FullName
  }
}
foreach ($pattern in @("01_*.mp4", "02_*.mp4", "03_*.mp4", "04_*.mp4")) {
  $matches = if ($RecordingDir -and (Test-Path -LiteralPath $RecordingDir)) {
    @(Get-ChildItem -LiteralPath $RecordingDir -File -Filter $pattern -ErrorAction SilentlyContinue)
  } else {
    @()
  }
  $recordingPath = if ($matches.Count -eq 1) {
    $matches[0].FullName
  } elseif ($RecordingDir) {
    Join-Path $RecordingDir $pattern
  } else {
    $pattern
  }
  Add-PathCheck "Validated recording $pattern" $recordingPath
}
Add-PathCheck "Recording validation report" (Join-Path $WorkspaceRoot "outputs\QGC_FOUR_SCENE_RECORDING_REPORT_20260722.md")

$missing = @($checks | Where-Object { $_.Required -and -not $_.Exists })
$qgcHash = if (Test-Path -LiteralPath $QgcPath) {
  (Get-FileHash -LiteralPath $QgcPath -Algorithm SHA256).Hash
} else {
  "unavailable"
}
$ifscHash = if (Test-Path -LiteralPath $IfscPath) {
  (Get-FileHash -LiteralPath $IfscPath -Algorithm SHA256).Hash
} else {
  "unavailable"
}
$lines = @(
  "# Deployment Bundle Report",
  "",
  "- Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')",
  "- Workspace: $WorkspaceRoot",
  "- UE project root: $ProjectRoot",
  "- QGroundControl SHA256: $qgcHash",
  "- IFSC Station SHA256: $ifscHash",
  "- Overall: $(if ($missing.Count -eq 0) { 'PASS' } else { 'FAIL' })",
  "",
  "## Checks",
  ""
)
foreach ($check in $checks) {
  $mark = if ($check.Exists) { "[x]" } else { "[ ]" }
  $lines += '- {0} {1}: `{2}`' -f $mark, $check.Name, $check.Path
}
if ($missing.Count -gt 0) {
  $lines += @("", "## Missing Required Items", "")
  foreach ($check in $missing) {
    $lines += '- {0}: `{1}`' -f $check.Name, $check.Path
  }
}

Set-Content -LiteralPath $ReportPath -Value ($lines -join "`r`n") -Encoding UTF8
Write-Host "Deployment bundle: $(if ($missing.Count -eq 0) { 'PASS' } else { 'FAIL' })"
Write-Host "Report: $ReportPath"
if ($missing.Count -gt 0) { exit 1 }
