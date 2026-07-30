#requires -version 5.1
[CmdletBinding()]
param(
  [string]$WindSpeeds = "",
  [string[]]$PlanNames = @(),
  [string]$ConfigFile = "",
  [string]$SemanticMapFile = "",
  [string]$UeMap = "",
  [switch]$DryRun,
  [switch]$ContinueOnFailure
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceDir = Split-Path -Parent $PSCommandPath
$Root = Split-Path -Parent $SourceDir
$OutputsDir = if ((Split-Path -Leaf $SourceDir) -ieq "scripts") {
  Join-Path $Root "outputs"
} else { $SourceDir }
$Runner = Join-Path $SourceDir "run_semantic_regression.ps1"
$ConfigPath = if ($ConfigFile) {
  [IO.Path]::GetFullPath($ConfigFile)
} else {
  Join-Path $SourceDir "semantic_runtime_config.json"
}
if (-not (Test-Path -LiteralPath $Runner)) { throw "Regression runner not found: $Runner" }
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Runtime config not found: $ConfigPath" }
$Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$SemanticMapPath = if ($SemanticMapFile) {
  [IO.Path]::GetFullPath($SemanticMapFile)
} else {
  Join-Path $SourceDir "urban_semantic_map.json"
}
if (-not (Test-Path -LiteralPath $SemanticMapPath)) {
  throw "Semantic map not found: $SemanticMapPath"
}
if (-not $UeMap) {
  $SemanticMapObject = Get-Content -LiteralPath $SemanticMapPath -Raw -Encoding UTF8 |
    ConvertFrom-Json
  $UeMap = [string]$SemanticMapObject.ue_map
}
$culture = [Globalization.CultureInfo]::InvariantCulture
$SpeedValues = if ($WindSpeeds) {
  @($WindSpeeds.Split(",") | ForEach-Object { [double]::Parse($_.Trim(), $culture) })
} else {
  @($Config.wind_matrix.stable_wind_speeds_mps | ForEach-Object { [double]$_ })
}
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$MatrixDir = Join-Path $OutputsDir "semantic-wind-matrix\$Stamp"
New-Item -ItemType Directory -Path $MatrixDir -Force | Out-Null
$Rows = @()

foreach ($Speed in $SpeedValues) {
  Write-Host ""
  Write-Host "===== SEMANTIC WIND MATRIX: $Speed m/s =====" -ForegroundColor Cyan
  $started = Get-Date
  $params = @{
    ConfigFile = $ConfigPath
    WindMps = $Speed
    UeMap = $UeMap
    ContinueOnFailure = $true
    DryRun = $DryRun
  }
  if ($SemanticMapFile) { $params.SemanticMapFile = $SemanticMapFile }
  if ($PlanNames.Count -gt 0) { $params.PlanNames = $PlanNames }
  $exitCode = 0
  try {
    & $Runner @params
    $exitCode = $LASTEXITCODE
  }
  catch {
    $exitCode = 1
    Write-Warning $_.Exception.Message
  }
  $summaryFile = Get-ChildItem -LiteralPath (Join-Path $OutputsDir "semantic-regression") `
      -Filter "summary.json" -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -ge $started.AddSeconds(-2) } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  $summary = if ($summaryFile) {
    Get-Content -LiteralPath $summaryFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
  } else { $null }
  $Rows += [pscustomobject]@{
    wind_mps = $Speed
    passed = [bool]($exitCode -eq 0 -and $summary -and $summary.all_passed)
    plans = if ($summary) { $summary.plan_count } else { 0 }
    passed_plans = if ($summary) { $summary.passed_count } else { 0 }
    average_final_error_m = if ($summary) { $summary.aggregate.average_final_error_m } else { $null }
    max_final_error_m = if ($summary) { $summary.aggregate.max_final_error_m } else { $null }
    total_collisions = if ($summary) { $summary.aggregate.total_collisions } else { $null }
    report = if ($summaryFile) { $summaryFile.FullName } else { $null }
  }
  if ($exitCode -ne 0 -and -not $ContinueOnFailure) { break }
}

$allPassed = (
  $Rows.Count -eq $SpeedValues.Count -and
  @($Rows | Where-Object { -not $_.passed }).Count -eq 0)
$result = [ordered]@{
  run_id = $Stamp
  mode = $(if ($DryRun) { "dry-run" } else { "live" })
  config_sha256 = (Get-FileHash -LiteralPath $ConfigPath -Algorithm SHA256).Hash.ToLowerInvariant()
  ue_map = $UeMap
  semantic_map_path = $SemanticMapPath
  all_passed = $allPassed
  rows = $Rows
}
$result | ConvertTo-Json -Depth 5 |
  Set-Content -LiteralPath (Join-Path $MatrixDir "summary.json") -Encoding UTF8
$markdown = @(
  "# Semantic wind matrix $Stamp",
  "",
  "- Mode: $($result.mode)",
  "- Config SHA-256: $($result.config_sha256)",
  "- Overall: $(if ($allPassed) { 'PASS' } else { 'CHECK' })",
  "",
  "| Wind | Result | Plans | Average final error | Max final error | Collisions |",
  "|---:|---:|---:|---:|---:|---:|"
)
foreach ($row in $Rows) {
  $markdown += "| $($row.wind_mps) m/s | $(if ($row.passed) { 'PASS' } else { 'CHECK' }) | $($row.passed_plans)/$($row.plans) | $($row.average_final_error_m) | $($row.max_final_error_m) | $($row.total_collisions) |"
}
$markdown | Set-Content -LiteralPath (Join-Path $MatrixDir "summary.md") -Encoding UTF8
Write-Host "[semantic-wind-matrix] report: $(Join-Path $MatrixDir 'summary.md')"
if (-not $allPassed) { exit 1 }
