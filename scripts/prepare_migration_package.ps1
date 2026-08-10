<#
.SYNOPSIS
  Prepare the IFSC Bridge Inspection Platform migration package.

.DESCRIPTION
  Copies UE project, WSL scripts, Windows scripts, docs, both retained ground
  stations, and validated recordings into a single migration directory with
  SHA256 manifest and verify script.

.PARAMETER TargetDir
  Target directory for the migration package. A timestamped subdirectory
  will be created inside it. Default: D:\SZTU_Migration_Package

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File prepare_migration_package.ps1 -TargetDir "D:\SZTU_Migration_Package"
#>
[CmdletBinding()]
param(
    [string]$TargetDir = "D:\SZTU_Migration_Package",
    [string]$RecordingDir = ""
)

$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$PackageDir = Join-Path $TargetDir "IFSC_BridgeInspection_Migration_$Timestamp"

Write-Host "[migration] workspace: $Workspace"
Write-Host "[migration] package dir: $PackageDir"

# Create directory structure
$Dirs = @(
    "01_UE_Project",
    "02_WSL_Scripts",
    "03_Windows_Scripts",
    "04_OneClick_CMD",
    "05_Docs",
    "06_QGC",
    "07_IFSC_Station",
    "08_Recordings",
    "09_Validation_Reports"
)
foreach ($d in $Dirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PackageDir $d) | Out-Null
}

# 1. UE Project (exclude Intermediate\Build, Binaries, Saved\Logs, .vs)
Write-Host "[1/8] Copying UE project..."
$ueSrc = "D:\SZTUWindFieldPX4Demo\Unreal"
$ueDst = Join-Path $PackageDir "01_UE_Project"
$ueArgs = @($ueSrc, $ueDst, "/E", "/COPY:DAT", "/R:1", "/W:1", "/NFL", "/NDL",
            "/XD", "Intermediate", "Binaries", ".vs", "Saved",
            "/XF", "*.pdb", "*.ilk", "*.exp")
& robocopy @ueArgs | Out-Null
Write-Host "  UE project copied."

# 2. WSL Scripts
Write-Host "[2/8] Copying WSL scripts..."
$wslDst = Join-Path $PackageDir "02_WSL_Scripts"
$wslFiles = @(
    "start_px4_ue_hil.sh",
    "mavlink_relay.py",
    "takeoff_offboard.py",
    "manual_fly.py",
    "semantic_obstacle_mission.py",
    "semantic_mission_verdict.py",
    "semantic_motion_profile.py",
    "semantic_route_planner.py",
    "semantic_explainability.py",
    "semantic_runtime_config.py",
    "semantic_runtime_config.json",
    "vision_detector.py",
    "ifsc_event_log.py",
    "urban_semantic_map.json"
)
foreach ($f in $wslFiles) {
    $src = Join-Path $Workspace "outputs\$f"
    if (Test-Path $src) {
        Copy-Item $src -Destination $wslDst -Force
    } else {
        Write-Host "  WARNING: $f not found"
    }
}
# Rover airframe
$airframeSrc = Join-Path $Workspace "outputs\px4_airframes"
if (Test-Path $airframeSrc) {
    Copy-Item $airframeSrc -Destination $wslDst -Recurse -Force
}
# QGC takeoff patch
$patchSrc = Join-Path $Workspace "outputs\px4-qgc-takeoff-p1-compat.patch"
if (Test-Path $patchSrc) {
    Copy-Item $patchSrc -Destination $wslDst -Force
}
Write-Host "  WSL scripts copied."

# 3. Windows Scripts
Write-Host "[3/8] Copying Windows scripts..."
$winDst = Join-Path $PackageDir "03_Windows_Scripts"
$winFiles = @(
    "run_qgc_control.ps1",
    "verify_px4_ue_hil.ps1",
    "run_headwind_test.ps1",
    "run_circle_flight.ps1",
    "run_semantic_city.ps1",
    "run_semantic_regression.ps1",
    "run_semantic_wind_matrix.ps1",
    "start_px4_ue_hil.sh",
    "mavlink_relay.py",
    "takeoff_offboard.py",
    "semantic_obstacle_mission.py",
    "semantic_mission_verdict.py",
    "semantic_motion_profile.py",
    "semantic_route_planner.py",
    "semantic_regression_gates.py",
    "semantic_runtime_config.py",
    "semantic_runtime_config.json",
    "vision_detector.py",
    "ifsc_event_log.py",
    "verify_vision_detector_test.py",
    "verify_video_artifact.py",
    "verify_video_artifact_test.py",
    "verify_ifsc_quality_gates.ps1",
    "capture_screen_until_stop.ps1",
    "prepare_triple_view_layout.ps1",
    "record_task_triple_view.ps1",
    "launch_triple_view_recorded_orbit.ps1",
    "urban_semantic_map.json",
    "drone_dashboard.py",
    "setup_deepseek_key.ps1",
    "verify_dashboard_test.py",
    "verify_semantic_explainability_test.py",
    "verify_semantic_motion_test.py",
    "verify_semantic_resilience_test.py",
    "verify_semantic_fault_injection.ps1",
    "prepare_wind_demo_layout.ps1",
    "start_wind_qgc_demo.ps1",
    "check_deployment_bundle.ps1",
    "check_px4_wsl_env.ps1",
    "px4_emergency_stop.ps1",
    "analyze_flight_patterns.py",
    "prepare_migration_package.ps1"
)
foreach ($f in $winFiles) {
    $src = Join-Path $Workspace "outputs\$f"
    if (Test-Path $src) {
        Copy-Item $src -Destination $winDst -Force
    } else {
        Write-Host "  WARNING: $f not found"
    }
}
$testPlanSrc = Join-Path $Workspace "outputs\test-plans"
if (Test-Path -LiteralPath $testPlanSrc) {
    Copy-Item -LiteralPath $testPlanSrc -Destination $winDst -Recurse -Force
} else {
    throw "Semantic test-plan directory missing: $testPlanSrc"
}
$contractSrc = Join-Path $Workspace "outputs\contracts"
if (Test-Path -LiteralPath $contractSrc) {
    Copy-Item -LiteralPath $contractSrc -Destination $winDst -Recurse -Force
} else {
    throw "Interface contract directory missing: $contractSrc"
}
Write-Host "  Windows scripts copied."

# 4. One-click CMD
Write-Host "[4/8] Copying one-click CMD..."
$cmdDst = Join-Path $PackageDir "04_OneClick_CMD"
$cmdFiles = @(
    "START_FACTORY_WIND_QGC.cmd",
    "START_WIND_QGC_DEMO.cmd",
    "START_SEMANTIC_CITY.cmd"
)
foreach ($f in $cmdFiles) {
    $src = Join-Path $Workspace $f
    if (Test-Path $src) {
        Copy-Item $src -Destination $cmdDst -Force
    } else {
        Write-Host "  WARNING: $f not found"
    }
}
Write-Host "  CMD files copied."

# 5. Docs
Write-Host "[5/8] Copying docs..."
$docsDst = Join-Path $PackageDir "05_Docs"
$docFiles = @(
    "DEPLOYMENT_MANUAL.md",
    "THIRD_PARTY_ASSETS.md",
    "FLIGHT_PATTERNS.md",
    "PX4_UE_QGC_DATA_FLOW.md",
    "QGC_CONTROL.md",
    "SERVER_REPRO.md",
    "UE5_SCENE_IMPORT.md",
    "VERTICAL_PID_TUNING.md",
    "SEMANTIC_CITY.md",
    "outputs\TRIPLE_VIEW_RECORDING.md",
    "outputs\VISION_YOLO_INTERFACE.md",
    "outputs\MIGRATION_PACKAGE_AUDIT_20260729.md",
    "MIGRATION_PACKAGE.md"
)
foreach ($f in $docFiles) {
    $src = Join-Path $Workspace $f
    if (Test-Path $src) {
        Copy-Item $src -Destination $docsDst -Force
    } else {
        Write-Host "  WARNING: $f not found"
    }
}
Write-Host "  Docs copied."

# 6. Retained QGroundControl baseline
Write-Host "[6/8] Copying retained QGroundControl..."
$qgcDst = Join-Path $PackageDir "06_QGC"
$qgcSrc = "C:\QGroundControl"
if (Test-Path $qgcSrc) {
    & robocopy $qgcSrc $qgcDst /E /COPY:DAT /R:1 /W:1 /NFL /NDL | Out-Null
    Write-Host "  QGroundControl copied."
} else {
    Write-Host "  WARNING: C:\QGroundControl not found; QGroundControl not included."
}

# 7. IFSC Station is included alongside QGC, not as a replacement.
Write-Host "[7/8] Copying IFSC Station..."
$ifscDst = Join-Path $PackageDir "07_IFSC_Station"
$ifscSrc = "C:\IFSC-Station"
if (Test-Path $ifscSrc) {
    & robocopy $ifscSrc $ifscDst /E /COPY:DAT /R:1 /W:1 /NFL /NDL | Out-Null
    Write-Host "  IFSC Station copied."
} else {
    Write-Host "  WARNING: C:\IFSC-Station not found; IFSC Station not included."
}

# 8. Validated recordings only. Failed attempts and temporary captures are excluded.
Write-Host "[8/8] Copying validated recordings..."
$recordingDst = Join-Path $PackageDir "08_Recordings"
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
    if ($recordingCandidates.Count -ne 1) {
        throw "Expected exactly one validated recording directory under $recordingSearchRoot; found $($recordingCandidates.Count). Pass -RecordingDir explicitly."
    }
    $RecordingDir = $recordingCandidates[0].FullName
}
$recordingPatterns = @("01_*.mp4", "02_*.mp4", "03_*.mp4", "04_*.mp4")
foreach ($pattern in $recordingPatterns) {
    $matches = @(Get-ChildItem -LiteralPath $RecordingDir -File -Filter $pattern)
    if ($matches.Count -ne 1) {
        throw "Expected one validated recording matching $pattern in $RecordingDir; found $($matches.Count)."
    }
    Copy-Item -LiteralPath $matches[0].FullName -Destination $recordingDst -Force
}
$recordingReport = Join-Path $Workspace "outputs\QGC_FOUR_SCENE_RECORDING_REPORT_20260722.md"
if (-not (Test-Path -LiteralPath $recordingReport)) {
    throw "Recording validation report missing: $recordingReport"
}
Copy-Item -LiteralPath $recordingReport `
    -Destination (Join-Path $recordingDst "RECORDING_VALIDATION_REPORT.md") -Force
Write-Host "  Validated recordings copied."

# 9. Current semantic wind validation evidence
Write-Host "[9/9] Copying semantic wind validation reports..."
$validationDst = Join-Path $PackageDir "09_Validation_Reports"
$validationFiles = @(
    "outputs\semantic-wind-validation-20260730.md",
    "outputs\semantic-wind-matrix\20260730-172405\summary.md",
    "outputs\semantic-wind-matrix\20260730-172405\summary.json"
)
foreach ($f in $validationFiles) {
    $src = Join-Path $Workspace $f
    if (-not (Test-Path -LiteralPath $src)) {
        throw "Validation report missing: $src"
    }
    $name = if ($f -like "*semantic-wind-matrix*") {
        "semantic-wind-matrix-20260730-" + (Split-Path $f -Leaf)
    } else {
        Split-Path $f -Leaf
    }
    Copy-Item -LiteralPath $src -Destination (Join-Path $validationDst $name) -Force
}
Write-Host "  Validation reports copied."

# Copy MIGRATION_PACKAGE.md as README
Copy-Item (Join-Path $Workspace "MIGRATION_PACKAGE.md") -Destination (Join-Path $PackageDir "MIGRATION_README.md") -Force

# Generate verify_migration.ps1
Write-Host "[migration] generating verify_migration.ps1..."
$verifyScript = @'
# Migration package verification script
# Run this on the 7th-floor new machine after copying the package.

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "[verify] package dir: $PackageDir"

# 1. Check SHA256 manifest
$manifestPath = Join-Path $PackageDir "SHA256_MANIFEST.txt"
if (-not (Test-Path $manifestPath)) {
    Write-Host "[FAIL] SHA256_MANIFEST.txt missing"
    exit 1
}
$entries = Get-Content $manifestPath
$mismatches = 0
$missing = 0
foreach ($entry in $entries) {
    $parts = $entry -split "  ", 2
    if ($parts.Count -ne 2) { continue }
    $expectedHash = $parts[0]
    $relativePath = $parts[1]
    $fullPath = Join-Path $PackageDir $relativePath
    if (-not (Test-Path $fullPath)) {
        Write-Host "[MISSING] $relativePath"
        $missing++
        continue
    }
    $actualHash = (Get-FileHash -Path $fullPath -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        Write-Host "[MISMATCH] $relativePath"
        $mismatches++
    }
}
Write-Host "[verify] $missing missing, $mismatches hash mismatches"
if ($missing -gt 0 -or $mismatches -gt 0) {
    Write-Host "[FAIL] integrity check"
    exit 1
}
Write-Host "[PASS] integrity check"

# 2. Check UE project structure
$ueDir = Join-Path $PackageDir "01_UE_Project"
$requiredUePaths = @(
    "SZTUWindFieldPX4Demo.uproject",
    "Source\SZTUWindFieldPX4Demo\SZTUWindFieldGameMode.cpp",
    "Source\SZTUWindFieldPX4Demo\SZTUDronePawn.cpp",
    "Content\Maps\SZTUFactoryWindScene.umap",
    "Content\Maps\SZTUUrbanSemanticScene.umap",
    "Content\Drone\Materials\M_DroneBodyColor.uasset"
)
foreach ($p in $requiredUePaths) {
    $fullPath = Join-Path $ueDir $p
    if (-not (Test-Path $fullPath)) {
        Write-Host "[FAIL] UE project missing: $p"
        exit 1
    }
}
Write-Host "[PASS] UE project structure"

# 3. Check scripts
$wslDir = Join-Path $PackageDir "02_WSL_Scripts"
$winDir = Join-Path $PackageDir "03_Windows_Scripts"
$requiredScripts = @(
    "$wslDir\start_px4_ue_hil.sh",
    "$wslDir\mavlink_relay.py",
    "$wslDir\takeoff_offboard.py",
    "$wslDir\semantic_obstacle_mission.py",
    "$wslDir\semantic_mission_verdict.py",
    "$wslDir\semantic_motion_profile.py",
    "$wslDir\semantic_route_planner.py",
    "$wslDir\semantic_runtime_config.py",
    "$wslDir\semantic_runtime_config.json",
    "$wslDir\vision_detector.py",
    "$wslDir\urban_semantic_map.json",
    "$winDir\run_qgc_control.ps1",
    "$winDir\run_semantic_city.ps1",
    "$winDir\run_semantic_regression.ps1",
    "$winDir\run_semantic_wind_matrix.ps1",
    "$winDir\start_px4_ue_hil.sh",
    "$winDir\mavlink_relay.py",
    "$winDir\takeoff_offboard.py",
    "$winDir\semantic_runtime_config.py",
    "$winDir\semantic_explainability.py",
    "$winDir\semantic_runtime_config.json",
    "$winDir\semantic_regression_gates.py",
    "$winDir\drone_dashboard.py",
    "$winDir\urban_semantic_map.json",
    "$winDir\verify_dashboard_test.py",
    "$winDir\verify_semantic_explainability_test.py",
    "$winDir\verify_semantic_motion_test.py",
    "$winDir\verify_semantic_resilience_test.py",
    "$winDir\verify_semantic_fault_injection.ps1",
    "$winDir\test-plans\clear_hover_land.json",
    "$winDir\verify_px4_ue_hil.ps1",
    "$winDir\check_deployment_bundle.ps1"
)
foreach ($s in $requiredScripts) {
    if (-not (Test-Path $s)) {
        Write-Host "[FAIL] script missing: $s"
        exit 1
    }
}
Write-Host "[PASS] scripts present"

# 4. Check both retained ground stations
$qgcDir = Join-Path $PackageDir "06_QGC\bin\QGroundControl.exe"
if (-not (Test-Path $qgcDir)) {
    Write-Host "[FAIL] QGroundControl executable missing"
    exit 1
}
$ifscDir = Join-Path $PackageDir "07_IFSC_Station\IFSC-Station.exe"
if (-not (Test-Path $ifscDir)) {
    Write-Host "[FAIL] IFSC Station executable missing"
    exit 1
}
Write-Host "[PASS] QGroundControl and IFSC Station present"

# 5. Check validated recordings
$recordingDir = Join-Path $PackageDir "08_Recordings"
$requiredRecordings = @(
    "01_*.mp4",
    "02_*.mp4",
    "03_*.mp4",
    "04_*.mp4"
)
foreach ($pattern in $requiredRecordings) {
    $matches = @(Get-ChildItem -LiteralPath $recordingDir -File -Filter $pattern)
    if ($matches.Count -ne 1) {
        Write-Host "[FAIL] expected one recording matching $pattern; found $($matches.Count)"
        exit 1
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $recordingDir "RECORDING_VALIDATION_REPORT.md"))) {
    Write-Host "[FAIL] recording validation report missing"
    exit 1
}
Write-Host "[PASS] validated recordings present"

# 6. Check current wind-validation evidence
$validationDir = Join-Path $PackageDir "09_Validation_Reports"
foreach ($name in @(
    "semantic-wind-validation-20260730.md",
    "semantic-wind-matrix-20260730-summary.md",
    "semantic-wind-matrix-20260730-summary.json"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $validationDir $name))) {
        Write-Host "[FAIL] validation report missing: $name"
        exit 1
    }
}
Write-Host "[PASS] semantic wind validation reports present"

Write-Host ""
Write-Host "=========================================="
Write-Host "Migration package verified successfully."
Write-Host "=========================================="
Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Copy 01_UE_Project to D:\SZTUWindFieldPX4Demo\Unreal\"
Write-Host "2. Copy 02_WSL_Scripts to WSL ~/szefitl/"
Write-Host "3. Copy 03_Windows_Scripts to project outputs\"
Write-Host "4. Copy 04_OneClick_CMD to project root"
Write-Host "5. Keep 06_QGC\ and 07_IFSC_Station\ as separate ground-station options"
Write-Host "6. Review the validated demonstration videos in 08_Recordings\"
Write-Host "7. Review 09_Validation_Reports\ for the 3/5/8 m/s semantic baseline"
Write-Host "8. Follow 05_Docs\DEPLOYMENT_MANUAL.md for deployment"
'@
$verifyScript | Out-File -FilePath (Join-Path $PackageDir "verify_migration.ps1") -Encoding UTF8

# Generate SHA256 manifest after README and verification script exist, so both
# are covered by the integrity check. The manifest itself is intentionally
# excluded because it cannot contain its own stable hash.
Write-Host "[migration] generating SHA256 manifest..."
$manifestPath = Join-Path $PackageDir "SHA256_MANIFEST.txt"
$files = @(Get-ChildItem -Path $PackageDir -File -Recurse |
    Where-Object { $_.FullName -ne $manifestPath })
$lines = @()
foreach ($file in $files) {
    $hash = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash
    $relativePath = $file.FullName.Substring($PackageDir.Length + 1)
    $lines += "$hash  $relativePath"
}
$lines | Out-File -FilePath $manifestPath -Encoding UTF8
Write-Host "  Manifest: $manifestPath ($($files.Count) files)"

# Summary
$totalSize = (Get-ChildItem -Path $PackageDir -Recurse | Measure-Object -Property Length -Sum).Sum
$totalSizeGB = [math]::Round($totalSize / 1GB, 2)
Write-Host ""
Write-Host "=========================================="
Write-Host "Migration package prepared."
Write-Host "=========================================="
Write-Host "Location: $PackageDir"
Write-Host "Size: $totalSizeGB GB"
Write-Host "Files: $($files.Count)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Copy $PackageDir to the 7th-floor machine"
Write-Host "2. Run verify_migration.ps1 on the new machine"
Write-Host "3. Follow MIGRATION_README.md and DEPLOYMENT_MANUAL.md"
