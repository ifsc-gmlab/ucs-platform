#requires -version 5.1
[CmdletBinding()]
param([switch]$SkipVideoFixture)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspace = Split-Path -Parent $root

function Invoke-Checked {
    param([string]$Label, [scriptblock]$Command)
    Write-Host "[quality] $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

$pythonFiles = @(
    "ifsc_event_log.py", "vision_detector.py", "verify_video_artifact.py",
    "semantic_explainability.py", "drone_dashboard.py"
) | ForEach-Object { Join-Path $root $_ }
Invoke-Checked "Python syntax" { py -3 -m py_compile @pythonFiles }

$tests = @(
    "verify_vision_detector_test.py",
    "verify_semantic_explainability_test.py",
    "verify_dashboard_test.py",
    "verify_bridge_dashboard.py",
    "verify_semantic_motion_test.py",
    "verify_semantic_resilience_test.py"
)
if (-not $SkipVideoFixture) { $tests += "verify_video_artifact_test.py" }
foreach ($test in $tests) {
    Invoke-Checked $test { py -3 (Join-Path $root $test) }
}

$powerShellScripts = @(
    "capture_screen_until_stop.ps1",
    "prepare_triple_view_layout.ps1",
    "record_task_triple_view.ps1",
    "launch_triple_view_recorded_orbit.ps1",
    "preview_semantic_trajectory.ps1"
)
foreach ($name in $powerShellScripts) {
    $path = Join-Path $root $name
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) {
        throw "PowerShell syntax failed: $name - $($errors[0].Message)"
    }
    Write-Host "PASS PowerShell syntax $name"
}

$schemaPath = Join-Path $root "contracts\vision_detection.schema.json"
$schema = Get-Content -LiteralPath $schemaPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($schema.properties.schema_version.const -ne "ifsc.vision.detection.v1") {
    throw "vision contract version mismatch"
}
Write-Host "PASS vision detection contract"

$ueSource = "D:\SZTUWindFieldPX4Demo\Unreal\Source\SZTUWindFieldPX4Demo\SZTUWindFieldBuildMapsCommandlet.cpp"
$gameMode = "D:\SZTUWindFieldPX4Demo\Unreal\Source\SZTUWindFieldPX4Demo\SZTUWindFieldGameMode.cpp"
if ((Get-Content -LiteralPath $ueSource -Raw) -notmatch 'Material->TwoSided = false') {
    throw "single-face Logo material gate failed"
}
if ((Get-Content -LiteralPath $ueSource -Raw) -notmatch 'SetAtmosphereSunLight\(true\)') {
    throw "bridge atmosphere sun gate failed"
}
if ((Get-Content -LiteralPath $ueSource -Raw) -notmatch 'M_BridgeSky') {
    throw "bridge deterministic sky backdrop gate failed"
}
if ((Get-Content -LiteralPath $gameMode -Raw) -notmatch 'LogoReviewCamera') {
    throw "Logo review camera gate failed"
}
Write-Host "PASS UE visual source gates"
Write-Host "ALL_OK"
