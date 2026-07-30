#requires -version 5.1
[CmdletBinding()]
param(
  [string[]]$PlanNames = @(),
  [ValidateRange(1.0, 5.0)]
  [double]$Alt = 2.0,
  [ValidateRange(0.3, 3.0)]
  [double]$CruiseSpeed = 1.25,
  [ValidateRange(30, 180)]
  [int]$MissionTimeoutSec = 180,
  [switch]$DryRun,
  [switch]$ContinueOnFailure
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$OutputsDir = Join-Path $Root "outputs"
$Runner = Join-Path $OutputsDir "run_semantic_city.ps1"
$PlanDir = Join-Path $OutputsDir "test-plans"
$PowerShellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath $Runner)) {
  throw "Semantic mission runner not found: $Runner"
}
if (-not (Test-Path -LiteralPath $PlanDir)) {
  throw "Semantic test-plan directory not found: $PlanDir"
}
if (-not (Test-Path -LiteralPath $PowerShellExe)) {
  throw "Windows PowerShell not found: $PowerShellExe"
}

$PlanFiles = @()
if ($PlanNames.Count -gt 0) {
  foreach ($Name in $PlanNames) {
    $Leaf = if ([IO.Path]::GetExtension($Name)) { $Name } else { "$Name.json" }
    $Candidate = Join-Path $PlanDir $Leaf
    if (-not (Test-Path -LiteralPath $Candidate)) {
      throw "Semantic test plan not found: $Candidate"
    }
    $PlanFiles += Get-Item -LiteralPath $Candidate
  }
}
else {
  $PlanFiles = @(Get-ChildItem -LiteralPath $PlanDir -Filter "*.json" -File | Sort-Object Name)
}

if ($PlanFiles.Count -eq 0) {
  throw "No semantic test plans found in: $PlanDir"
}

$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$SuiteStartedAt = Get-Date
$ReportDir = Join-Path $OutputsDir "semantic-regression\$RunStamp"
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$Results = @()

foreach ($Plan in $PlanFiles) {
  $StartedAt = Get-Date
  $LogPath = Join-Path $ReportDir ($Plan.BaseName + ".log")
  $Arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $Runner,
    "-PlanFile", $Plan.FullName,
    "-Alt", $Alt.ToString([Globalization.CultureInfo]::InvariantCulture),
    "-CruiseSpeed", $CruiseSpeed.ToString([Globalization.CultureInfo]::InvariantCulture),
    "-MissionTimeoutSec", $MissionTimeoutSec.ToString()
  )
  if ($DryRun) {
    $Arguments += "-DryRun"
  }

  Write-Host ""
  Write-Host ("=== {0}: {1} ===" -f $(if ($DryRun) { "DRY RUN" } else { "RUN" }), $Plan.Name)
  $Output = @(& $PowerShellExe @Arguments 2>&1)
  $ExitCode = $LASTEXITCODE
  $Output | Tee-Object -FilePath $LogPath
  $FinishedAt = Get-Date

  $Results += [pscustomobject]@{
    plan = $Plan.Name
    mode = $(if ($DryRun) { "dry-run" } else { "live" })
    passed = ($ExitCode -eq 0)
    exit_code = $ExitCode
    duration_sec = [Math]::Round(($FinishedAt - $StartedAt).TotalSeconds, 2)
    log = $LogPath
  }

  if (($ExitCode -ne 0) -and (-not $ContinueOnFailure)) {
    Write-Warning "Stopping after failed plan: $($Plan.Name)"
    break
  }
}

$PassedCount = @($Results | Where-Object passed).Count
$FailedCount = @($Results | Where-Object { -not $_.passed }).Count
$Summary = [ordered]@{
  run_id = $RunStamp
  mode = $(if ($DryRun) { "dry-run" } else { "live" })
  started_at = $SuiteStartedAt.ToString("o")
  plan_count = $Results.Count
  passed_count = $PassedCount
  failed_count = $FailedCount
  all_passed = ($FailedCount -eq 0 -and $Results.Count -eq $PlanFiles.Count)
  results = $Results
}

$JsonPath = Join-Path $ReportDir "summary.json"
$MarkdownPath = Join-Path $ReportDir "summary.md"
$Summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $JsonPath -Encoding UTF8

$Markdown = @(
  "# Semantic regression $RunStamp",
  "",
  "- Mode: $($Summary.mode)",
  "- Plans executed: $($Summary.plan_count) / $($PlanFiles.Count)",
  "- Passed: $PassedCount",
  "- Failed: $FailedCount",
  "",
  "| Plan | Result | Exit | Duration (s) |",
  "|---|---:|---:|---:|"
)
foreach ($Result in $Results) {
  $Label = if ($Result.passed) { "PASS" } else { "FAIL" }
  $Markdown += "| $($Result.plan) | $Label | $($Result.exit_code) | $($Result.duration_sec) |"
}
$Markdown | Set-Content -LiteralPath $MarkdownPath -Encoding UTF8

Write-Host ""
Write-Host "Regression summary: $MarkdownPath"
Write-Host ("Result: {0} passed, {1} failed" -f $PassedCount, $FailedCount)
if (-not $Summary.all_passed) {
  exit 1
}
