#requires -version 5.1
[CmdletBinding()]
param(
  [string]$TargetRoot = "",
  [string]$UeProjectRoot = "D:\SZTUWindFieldPX4Demo\Unreal",
  [switch]$NoZip
)

$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
if (-not $TargetRoot) { $TargetRoot = Join-Path $Workspace "dist" }
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$PackageName = "IFSC_BridgeInspection_Platform_${Stamp}_CORE"
$PackageRoot = Join-Path $TargetRoot $PackageName

$Layers = @(
  "00_START_HERE",
  "01_BRIDGE_DECK\branding", "01_BRIDGE_DECK\maps", "01_BRIDGE_DECK\ue_source_overlay",
  "02_SEMANTIC_MAP\config", "02_SEMANTIC_MAP\runtime",
  "03_WIND_FIELD\runtime",
  "04_PX4_UE5_QGC_LLM\px4_wsl", "04_PX4_UE5_QGC_LLM\ue5_hil",
  "04_PX4_UE5_QGC_LLM\qgc", "04_PX4_UE5_QGC_LLM\llm",
  "05_RUNTIME_ORCHESTRATION", "06_SERVER_DEPLOYMENT",
  "07_ACCEPTANCE_EVIDENCE", "08_MANIFESTS"
)
foreach ($Layer in $Layers) {
  New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot $Layer) | Out-Null
}

function Copy-IfPresent {
  param([string]$Source, [string]$Destination)
  if (Test-Path -LiteralPath $Source) {
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
  } else {
    Write-Warning "Missing package input: $Source"
  }
}

$copyPlan = @(
  @("IFSC_PROJECT_STRUCTURE.md", "00_START_HERE"),
  @("START_IFSC_PLATFORM.cmd", "00_START_HERE"),
  @("START_IFSC_50M_BRIDGE_DASHBOARD.cmd", "00_START_HERE"),
  @("outputs\verify_ifsc_package.ps1", "00_START_HERE"),
  @("assets\branding\IFSC-StationBrandImage.png", "01_BRIDGE_DECK\branding"),
  @("outputs\bridge_semantic_map.json", "01_BRIDGE_DECK\maps"),
  @("outputs\large_bridge_semantic_map.json", "01_BRIDGE_DECK\maps"),
  @("outputs\build_bridge_map.ps1", "01_BRIDGE_DECK"),
  @("outputs\build_large_bridge_map.ps1", "01_BRIDGE_DECK"),
  @("BRIDGE_INSPECTION.md", "01_BRIDGE_DECK"),
  @("outputs\drone_dashboard.py", "02_SEMANTIC_MAP\runtime"),
  @("outputs\semantic_obstacle_mission.py", "02_SEMANTIC_MAP\runtime"),
  @("outputs\semantic_route_planner.py", "02_SEMANTIC_MAP\runtime"),
  @("outputs\semantic_explainability.py", "02_SEMANTIC_MAP\runtime"),
  @("outputs\semantic_motion_profile.py", "02_SEMANTIC_MAP\runtime"),
  @("outputs\semantic_mission_verdict.py", "02_SEMANTIC_MAP\runtime"),
  @("outputs\vision_detector.py", "02_SEMANTIC_MAP\runtime"),
  @("outputs\ifsc_event_log.py", "02_SEMANTIC_MAP\runtime"),
  @("outputs\contracts\vision_detection.schema.json", "02_SEMANTIC_MAP\contracts"),
  @("outputs\VISION_YOLO_INTERFACE.md", "02_SEMANTIC_MAP"),
  @("outputs\semantic_runtime_config.json", "02_SEMANTIC_MAP\config"),
  @("outputs\urban_semantic_map.json", "02_SEMANTIC_MAP\config"),
  @("SEMANTIC_CITY.md", "02_SEMANTIC_MAP"),
  @("outputs\run_headwind_test.ps1", "03_WIND_FIELD\runtime"),
  @("outputs\run_wind_hover.ps1", "03_WIND_FIELD\runtime"),
  @("outputs\run_wind_speed_sweep.ps1", "03_WIND_FIELD\runtime"),
  @("outputs\run_semantic_wind_matrix.ps1", "03_WIND_FIELD\runtime"),
  @("outputs\start_px4_ue_hil.sh", "04_PX4_UE5_QGC_LLM\px4_wsl"),
  @("outputs\mavlink_relay.py", "04_PX4_UE5_QGC_LLM\px4_wsl"),
  @("outputs\takeoff_offboard.py", "04_PX4_UE5_QGC_LLM\px4_wsl"),
  @("outputs\px4-qgc-takeoff-p1-compat.patch", "04_PX4_UE5_QGC_LLM\px4_wsl"),
  @("outputs\verify_px4_ue_hil.ps1", "04_PX4_UE5_QGC_LLM\ue5_hil"),
  @("PX4_UE_QGC_DATA_FLOW.md", "04_PX4_UE5_QGC_LLM\ue5_hil"),
  @("outputs\run_qgc_control.ps1", "04_PX4_UE5_QGC_LLM\qgc"),
  @("QGC_CONTROL.md", "04_PX4_UE5_QGC_LLM\qgc"),
  @("outputs\setup_deepseek_key.ps1", "04_PX4_UE5_QGC_LLM\llm"),
  @("outputs\run_semantic_city.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\start_large_bridge_dashboard.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\start_ifsc_platform.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\preview_semantic_trajectory.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\build_large_bridge_map.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\drone_dashboard.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\large_bridge_semantic_map.json", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\semantic_obstacle_mission.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\semantic_route_planner.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\semantic_explainability.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\verify_semantic_explainability_test.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\semantic_motion_profile.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\semantic_mission_verdict.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\semantic_runtime_config.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\semantic_runtime_config.json", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\vision_detector.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\ifsc_event_log.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\contracts\vision_detection.schema.json", "05_RUNTIME_ORCHESTRATION\contracts"),
  @("outputs\VISION_YOLO_INTERFACE.md", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\verify_vision_detector_test.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\verify_video_artifact.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\verify_video_artifact_test.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\verify_ifsc_quality_gates.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\capture_screen_until_stop.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\prepare_triple_view_layout.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\record_task_triple_view.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\TRIPLE_VIEW_RECORDING.md", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\launch_triple_view_recorded_orbit.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\verify_px4_ue_hil.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\check_px4_wsl_env.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\start_px4_ue_hil.sh", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\mavlink_relay.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\takeoff_offboard.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\listen_telem_long.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\analyze_flight_run.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\analyze_flight_patterns.py", "05_RUNTIME_ORCHESTRATION"),
  @("outputs\run_qgc_control.ps1", "05_RUNTIME_ORCHESTRATION"),
  @("config\ifsc.runtime.example.json", "05_RUNTIME_ORCHESTRATION"),
  @("DEPLOYMENT_MANUAL.md", "06_SERVER_DEPLOYMENT"),
  @("SERVER_REPRO.md", "06_SERVER_DEPLOYMENT"),
  @("MIGRATION_PACKAGE.md", "06_SERVER_DEPLOYMENT"),
  @("outputs\check_deployment_bundle.ps1", "06_SERVER_DEPLOYMENT"),
  @("outputs\check_px4_wsl_env.ps1", "06_SERVER_DEPLOYMENT"),
  @("outputs\install_workspace_layout.ps1", "06_SERVER_DEPLOYMENT")
)
foreach ($Pair in $copyPlan) {
  Copy-IfPresent (Join-Path $Workspace $Pair[0]) (Join-Path $PackageRoot $Pair[1])
}

$ueOverlay = Join-Path $PackageRoot "01_BRIDGE_DECK\ue_source_overlay"
Copy-IfPresent (Join-Path $UeProjectRoot "Source\SZTUWindFieldPX4Demo\SZTUWindFieldBuildMapsCommandlet.cpp") $ueOverlay
Copy-IfPresent (Join-Path $UeProjectRoot "Source\SZTUWindFieldPX4Demo\SZTUWindFieldBuildMapsCommandlet.h") $ueOverlay
Copy-IfPresent (Join-Path $UeProjectRoot "Source\SZTUWindFieldPX4Demo\SZTUWindFieldGameMode.cpp") $ueOverlay
Copy-IfPresent (Join-Path $UeProjectRoot "Source\SZTUWindFieldPX4Demo\SZTUWindFieldGameMode.h") $ueOverlay
Copy-IfPresent (Join-Path $UeProjectRoot "Source\SZTUWindFieldPX4Demo\SZTUDronePawn.cpp") (Join-Path $PackageRoot "04_PX4_UE5_QGC_LLM\ue5_hil")
Copy-IfPresent (Join-Path $UeProjectRoot "Source\SZTUWindFieldPX4Demo\SZTUMavlinkSITLComponent.cpp") (Join-Path $PackageRoot "04_PX4_UE5_QGC_LLM\ue5_hil")

$latestRun = Get-ChildItem (Join-Path $Workspace "outputs\semantic-runs") -Directory -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | Select-Object -First 1
if ($latestRun) {
  foreach ($name in @("summary.md", "mission_result.json", "flight_result.json")) {
    $evidence = Join-Path $latestRun.FullName $name
    if (Test-Path -LiteralPath $evidence) {
      Copy-Item -LiteralPath $evidence -Destination (Join-Path $PackageRoot "07_ACCEPTANCE_EVIDENCE") -Force
    }
  }
}

$largeAssets = @(
  "# IFSC large assets (not embedded in CORE ZIP)",
  "",
  "Transfer and validate these separately on the server:",
  "- UE project: $UeProjectRoot",
  "- Required UE folders: Config, Content, Source, Scripts, SourceArt.",
  "- Regenerate UE Binaries, Intermediate, Saved and DerivedDataCache.",
  "- Install or restore Unreal Engine 5.8 separately.",
  "- Keep PX4-Autopilot and its build inside the WSL ext4 filesystem.",
  "- Install QGroundControl and validate it after migration.",
  "",
  "SZTU-prefixed module/map names are internal compatibility identifiers; public branding is IFSC."
)
Set-Content -LiteralPath (Join-Path $PackageRoot "08_MANIFESTS\LARGE_ASSETS.md") -Value $largeAssets -Encoding UTF8

$manifest = [ordered]@{
  schema = "ifsc.bridge_inspection.package.v1"
  product = "IFSC Bridge Inspection Platform"
  created_at = (Get-Date).ToString("o")
  package_name = $PackageName
  logical_order = @("bridge_deck", "semantic_map", "wind_field", "px4_ue5_qgc_llm")
  ue_legacy_project_root = $UeProjectRoot
  compatibility_policy = "Public IFSC branding; legacy SZTU UE/PX4 identifiers retained internally."
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PackageRoot "08_MANIFESTS\package.json") -Encoding UTF8

$hashLines = Get-ChildItem -LiteralPath $PackageRoot -Recurse -File |
  Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
  Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring($PackageRoot.Length + 1).Replace("\", "/")
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
  }
Set-Content -LiteralPath (Join-Path $PackageRoot "08_MANIFESTS\SHA256SUMS.txt") -Value $hashLines -Encoding ASCII

$zipPath = "$PackageRoot.zip"
if (-not $NoZip) {
  Compress-Archive -LiteralPath $PackageRoot -DestinationPath $zipPath -CompressionLevel Optimal -Force
  # Keep the stable hand-off name synchronized with the timestamped artifact.
  # Consumers can pin the timestamped file while operators use CURRENT_CORE.
  Copy-Item -LiteralPath $zipPath -Destination (Join-Path $TargetRoot "IFSC_BridgeInspection_Platform_CURRENT_CORE.zip") -Force
}

Write-Host "[PASS] IFSC layered package: $PackageRoot"
if (-not $NoZip) { Write-Host "[PASS] IFSC CORE ZIP: $zipPath" }
