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
$SourceDir = Split-Path -Parent $PSCommandPath
$OutputsDir = if ((Split-Path -Leaf $SourceDir) -ieq "scripts") {
  Join-Path $Root "outputs"
}
else {
  $SourceDir
}
$Runner = Join-Path $SourceDir "run_semantic_city.ps1"
$PlanDir = if (Test-Path -LiteralPath (Join-Path $Root "test-plans")) {
  Join-Path $Root "test-plans"
}
else {
  Join-Path $SourceDir "test-plans"
}
$PowerShellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$PythonExe = Join-Path $env:USERPROFILE "anaconda3\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if (-not $PythonCommand) { throw "Python interpreter not found" }
  $PythonExe = $PythonCommand.Source
}

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
$PreflightLog = Join-Path $ReportDir "preflight.log"
$PreflightTests = @(
  (Join-Path $SourceDir "verify_semantic_motion_test.py"),
  (Join-Path $SourceDir "verify_dashboard_test.py")
)
foreach ($TestScript in $PreflightTests) {
  if (-not (Test-Path -LiteralPath $TestScript)) {
    throw "Regression preflight test not found: $TestScript"
  }
  @(& $PythonExe $TestScript 2>&1) | Tee-Object -FilePath $PreflightLog -Append
  if ($LASTEXITCODE -ne 0) {
    throw "Regression preflight failed: $TestScript"
  }
}
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
  $MissionResultPath = $null
  $MissionResult = $null
  if (-not $DryRun) {
    $MissionRun = Get-ChildItem -LiteralPath (Join-Path $OutputsDir "semantic-runs") -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTime -ge $StartedAt.AddSeconds(-2) } |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
    if ($MissionRun) {
      $MissionResultPath = Join-Path $MissionRun.FullName "mission_result.json"
      if (Test-Path -LiteralPath $MissionResultPath) {
        $MissionResult = Get-Content -LiteralPath $MissionResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
      }
    }
  }

  $ArrivalSpeeds = @()
  if ($MissionResult -and $MissionResult.legs) {
    $ArrivalSpeeds = @($MissionResult.legs | ForEach-Object {
      if ($null -ne $_.arrival_speed_mps) { [double]$_.arrival_speed_mps }
    })
  }

  $Results += [pscustomobject]@{
    plan = $Plan.Name
    mode = $(if ($DryRun) { "dry-run" } else { "live" })
    passed = ($ExitCode -eq 0)
    exit_code = $ExitCode
    duration_sec = [Math]::Round(($FinishedAt - $StartedAt).TotalSeconds, 2)
    collision_count = if ($MissionResult) { $MissionResult.collision_count } else { $null }
    final_error_m = if ($MissionResult) { $MissionResult.final_position_error_m } else { $null }
    max_arrival_speed_mps = if ($ArrivalSpeeds.Count) {
      [Math]::Round(($ArrivalSpeeds | Measure-Object -Maximum).Maximum, 4)
    } else { $null }
    sensor_samples = if ($MissionResult) { $MissionResult.sensor_samples } else { $null }
    detour_waypoints = if ($MissionResult) { $MissionResult.planned_detour_waypoints } else { $null }
    mission_result = $MissionResultPath
    log = $LogPath
  }

  if (($ExitCode -ne 0) -and (-not $ContinueOnFailure)) {
    Write-Warning "Stopping after failed plan: $($Plan.Name)"
    break
  }
}

$PassedCount = @($Results | Where-Object passed).Count
$FailedCount = @($Results | Where-Object { -not $_.passed }).Count
$MetricRows = @($Results | Where-Object { $null -ne $_.final_error_m })
$Aggregate = [ordered]@{
  average_duration_sec = if ($Results.Count) {
    [Math]::Round(($Results.duration_sec | Measure-Object -Average).Average, 2)
  } else { $null }
  average_final_error_m = if ($MetricRows.Count) {
    [Math]::Round(($MetricRows.final_error_m | Measure-Object -Average).Average, 4)
  } else { $null }
  max_final_error_m = if ($MetricRows.Count) {
    [Math]::Round(($MetricRows.final_error_m | Measure-Object -Maximum).Maximum, 4)
  } else { $null }
  total_collisions = if ($MetricRows.Count) {
    [int](($MetricRows.collision_count | Measure-Object -Sum).Sum)
  } else { $null }
}
$PreviousSummary = Get-ChildItem -LiteralPath (Join-Path $OutputsDir "semantic-regression") -Filter "summary.json" -File -Recurse -ErrorAction SilentlyContinue |
  Where-Object { $_.DirectoryName -ne $ReportDir } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$Trend = $null
if ($PreviousSummary) {
  try {
    $Previous = Get-Content -LiteralPath $PreviousSummary.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
      $Previous.PSObject.Properties.Name -contains "aggregate" -and
      $Previous.mode -eq $(if ($DryRun) { "dry-run" } else { "live" })
    ) {
      $Trend = [ordered]@{
        previous_run_id = $Previous.run_id
        average_duration_delta_sec = if ($null -ne $Previous.aggregate.average_duration_sec) {
          [Math]::Round($Aggregate.average_duration_sec - [double]$Previous.aggregate.average_duration_sec, 2)
        } else { $null }
        average_final_error_delta_m = if (
          $null -ne $Aggregate.average_final_error_m -and
          $null -ne $Previous.aggregate.average_final_error_m) {
          [Math]::Round($Aggregate.average_final_error_m - [double]$Previous.aggregate.average_final_error_m, 4)
        } else { $null }
      }
    }
  }
  catch {
    Write-Warning "Could not read previous regression summary: $($_.Exception.Message)"
  }
}
$Summary = [ordered]@{
  run_id = $RunStamp
  mode = $(if ($DryRun) { "dry-run" } else { "live" })
  started_at = $SuiteStartedAt.ToString("o")
  plan_count = $Results.Count
  passed_count = $PassedCount
  failed_count = $FailedCount
  all_passed = ($FailedCount -eq 0 -and $Results.Count -eq $PlanFiles.Count)
  aggregate = $Aggregate
  trend = $Trend
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
  "- Average duration: $($Aggregate.average_duration_sec) s",
  "- Average final error: $($Aggregate.average_final_error_m) m",
  "- Maximum final error: $($Aggregate.max_final_error_m) m",
  "- Total collisions: $($Aggregate.total_collisions)",
  "",
  "| Plan | Result | Exit | Duration (s) | Collision | Final error (m) | Arrival speed (m/s) |",
  "|---|---:|---:|---:|---:|---:|---:|"
)
foreach ($Result in $Results) {
  $Label = if ($Result.passed) { "PASS" } else { "FAIL" }
  $Markdown += "| $($Result.plan) | $Label | $($Result.exit_code) | $($Result.duration_sec) | $($Result.collision_count) | $($Result.final_error_m) | $($Result.max_arrival_speed_mps) |"
}
if ($Trend) {
  $Markdown += @(
    "",
    "## Trend versus $($Trend.previous_run_id)",
    "",
    "- Average duration delta: $($Trend.average_duration_delta_sec) s",
    "- Average final error delta: $($Trend.average_final_error_delta_m) m"
  )
}
$Markdown | Set-Content -LiteralPath $MarkdownPath -Encoding UTF8

Write-Host ""
Write-Host "Regression summary: $MarkdownPath"
Write-Host ("Result: {0} passed, {1} failed" -f $PassedCount, $FailedCount)
if (-not $Summary.all_passed) {
  exit 1
}
