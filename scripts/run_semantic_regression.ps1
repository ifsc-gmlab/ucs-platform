#requires -version 5.1
[CmdletBinding()]
param(
  [string[]]$PlanNames = @(),
  [string]$ConfigFile = "",
  [string]$SemanticMapFile = "",
  [string]$UeMap = "",
  [ValidateRange(1.0, 5.0)]
  [double]$Alt = 2.0,
  [ValidateRange(0.3, 3.0)]
  [double]$CruiseSpeed = 1.25,
  [ValidateRange(30, 180)]
  [int]$MissionTimeoutSec = 180,
  [ValidateRange(0.0, 15.0)]
  [double]$WindMps = 0.0,
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
$RuntimeConfig = if ($ConfigFile) {
  [System.IO.Path]::GetFullPath($ConfigFile)
}
else {
  Join-Path $SourceDir "semantic_runtime_config.json"
}
$SemanticMap = if ($SemanticMapFile) {
  [IO.Path]::GetFullPath($SemanticMapFile)
}
else {
  Join-Path $SourceDir "urban_semantic_map.json"
}
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
if (-not (Test-Path -LiteralPath $RuntimeConfig)) {
  throw "Semantic runtime config not found: $RuntimeConfig"
}
if (-not (Test-Path -LiteralPath $SemanticMap)) {
  throw "Semantic map not found: $SemanticMap"
}
$SemanticMapSha256 = (
  Get-FileHash -LiteralPath $SemanticMap -Algorithm SHA256
).Hash.ToLowerInvariant()
$SemanticMapObject = Get-Content -LiteralPath $SemanticMap -Raw -Encoding UTF8 |
  ConvertFrom-Json
if (-not $UeMap) {
  $UeMap = [string]$SemanticMapObject.ue_map
}
if ($UeMap -notmatch "^/Game/[A-Za-z0-9_./-]+$" -or $UeMap.Contains("..")) {
  throw "invalid UE map asset path: $UeMap"
}
$ConfigObject = Get-Content -LiteralPath $RuntimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
if ($ConfigObject.schema_version -ne 1) {
  throw "Unsupported semantic runtime config schema: $($ConfigObject.schema_version)"
}
if (-not $PSBoundParameters.ContainsKey("Alt")) {
  $Alt = [double]$ConfigObject.mission.altitude_m
}
if (-not $PSBoundParameters.ContainsKey("CruiseSpeed")) {
  $CruiseSpeed = [double]$ConfigObject.mission.cruise_speed_mps
}
if (-not $PSBoundParameters.ContainsKey("MissionTimeoutSec")) {
  $MissionTimeoutSec = [int]$ConfigObject.mission.mission_timeout_s
}
$ConfigSha256 = (Get-FileHash -LiteralPath $RuntimeConfig -Algorithm SHA256).Hash.ToLowerInvariant()
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
$PlanSignature = (@($PlanFiles.Name | Sort-Object) -join ",")

$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$SuiteStartedAt = Get-Date
$ReportDir = Join-Path $OutputsDir "semantic-regression\$RunStamp"
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$PreflightLog = Join-Path $ReportDir "preflight.log"
$PreflightTests = @(
  (Join-Path $SourceDir "verify_semantic_motion_test.py"),
  (Join-Path $SourceDir "verify_dashboard_test.py"),
  (Join-Path $SourceDir "verify_semantic_resilience_test.py")
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
    "-ConfigFile", $RuntimeConfig,
    "-UeMap", $UeMap,
    "-Alt", $Alt.ToString([Globalization.CultureInfo]::InvariantCulture),
    "-CruiseSpeed", $CruiseSpeed.ToString([Globalization.CultureInfo]::InvariantCulture),
    "-MissionTimeoutSec", $MissionTimeoutSec.ToString(),
    "-WindMps", $WindMps.ToString([Globalization.CultureInfo]::InvariantCulture)
  )
  if ($SemanticMapFile) {
    $Arguments += @("-SemanticMapFile", [IO.Path]::GetFullPath($SemanticMapFile))
  }
  if ($DryRun) {
    $Arguments += "-DryRun"
  }

  Write-Host ""
  Write-Host ("=== {0}: {1} ===" -f $(if ($DryRun) { "DRY RUN" } else { "RUN" }), $Plan.Name)
  $ChildStderrPath = Join-Path $ReportDir ("{0}.stderr.log" -f $Plan.BaseName)
  $PreviousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $Output = @(& $PowerShellExe @Arguments 2> $ChildStderrPath)
    $ExitCode = $LASTEXITCODE
  }
  finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
  }
  $Output | Tee-Object -FilePath $LogPath
  if (Test-Path -LiteralPath $ChildStderrPath) {
    $ChildStderr = @(Get-Content -LiteralPath $ChildStderrPath -ErrorAction SilentlyContinue)
    if ($ChildStderr.Count -gt 0) {
      $ChildStderr | Tee-Object -FilePath $LogPath -Append | Out-Null
      if ($ExitCode -ne 0) {
        Write-Warning ($ChildStderr -join [Environment]::NewLine)
      }
      else {
        Write-Host "[regression] non-fatal child stderr retained in $ChildStderrPath"
      }
    }
  }
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
    mean_wind_mps = if ($MissionResult) { $MissionResult.wind.mean_speed_mps } else { $null }
    max_wind_mps = if ($MissionResult) { $MissionResult.wind.max_speed_mps } else { $null }
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
      $Previous.mode -eq $(if ($DryRun) { "dry-run" } else { "live" }) -and
      $Previous.PSObject.Properties.Name -contains "wind_mps" -and
      [double]$Previous.wind_mps -eq $WindMps -and
      $Previous.PSObject.Properties.Name -contains "plan_signature" -and
      [string]$Previous.plan_signature -eq $PlanSignature -and
      $Previous.PSObject.Properties.Name -contains "ue_map" -and
      [string]$Previous.ue_map -eq $UeMap -and
      $Previous.PSObject.Properties.Name -contains "semantic_map_sha256" -and
      [string]$Previous.semantic_map_sha256 -eq $SemanticMapSha256
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
$GateFailures = @()
if (-not $DryRun) {
  $Gates = $ConfigObject.regression_gates
  foreach ($Row in $MetricRows) {
    if ($WindMps -gt 0 -and [double]$Row.mean_wind_mps -lt ($WindMps * 0.80)) {
      $GateFailures += "$($Row.plan): requested wind was not observed"
    }
    if ([int]$Row.collision_count -gt [int]$Gates.max_collisions) {
      $GateFailures += "$($Row.plan): collision limit exceeded"
    }
    if ([double]$Row.max_arrival_speed_mps -gt [double]$Gates.max_arrival_speed_mps) {
      $GateFailures += "$($Row.plan): arrival speed $($Row.max_arrival_speed_mps)m/s exceeds $($Gates.max_arrival_speed_mps)m/s"
    }
    if ([double]$Row.final_error_m -gt [double]$Gates.max_final_error_m) {
      $GateFailures += "$($Row.plan): final error $($Row.final_error_m)m exceeds $($Gates.max_final_error_m)m"
    }
  }
  if ($Trend -and $Previous) {
    if (
      $null -ne $Previous.aggregate.average_final_error_m -and
      [double]$Previous.aggregate.average_final_error_m -gt 0
    ) {
      $ratio = (
        [double]$Aggregate.average_final_error_m -
        [double]$Previous.aggregate.average_final_error_m
      ) / [double]$Previous.aggregate.average_final_error_m
      if ($ratio -gt [double]$Gates.max_average_error_regression_ratio) {
        $GateFailures += "average final error regressed $([Math]::Round($ratio * 100, 1))%"
      }
    }
    if (
      $null -ne $Previous.aggregate.average_duration_sec -and
      [double]$Previous.aggregate.average_duration_sec -gt 0
    ) {
      $ratio = (
        [double]$Aggregate.average_duration_sec -
        [double]$Previous.aggregate.average_duration_sec
      ) / [double]$Previous.aggregate.average_duration_sec
      if ($ratio -gt [double]$Gates.max_average_duration_regression_ratio) {
        $GateFailures += "average duration regressed $([Math]::Round($ratio * 100, 1))%"
      }
    }
  }
}
$Summary = [ordered]@{
  run_id = $RunStamp
  mode = $(if ($DryRun) { "dry-run" } else { "live" })
  wind_mps = $WindMps
  ue_map = $UeMap
  semantic_map_path = $SemanticMap
  semantic_map_sha256 = $SemanticMapSha256
  plan_signature = $PlanSignature
  config_sha256 = $ConfigSha256
  started_at = $SuiteStartedAt.ToString("o")
  plan_count = $Results.Count
  passed_count = $PassedCount
  failed_count = $FailedCount
  gates_passed = ($GateFailures.Count -eq 0)
  gate_failures = $GateFailures
  all_passed = (
    $FailedCount -eq 0 -and
    $Results.Count -eq $PlanFiles.Count -and
    $GateFailures.Count -eq 0)
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
  "- Requested wind: $WindMps m/s",
  "- Runtime config SHA-256: $ConfigSha256",
  "- Plans executed: $($Summary.plan_count) / $($PlanFiles.Count)",
  "- Passed: $PassedCount",
  "- Failed: $FailedCount",
  "- Average duration: $($Aggregate.average_duration_sec) s",
  "- Average final error: $($Aggregate.average_final_error_m) m",
  "- Maximum final error: $($Aggregate.max_final_error_m) m",
  "- Total collisions: $($Aggregate.total_collisions)",
  "",
  "- Regression gates: $(if ($Summary.gates_passed) { 'PASS' } else { 'CHECK' })",
  "",
  "| Plan | Result | Exit | Duration (s) | Collision | Final error (m) | Arrival speed (m/s) | Mean wind (m/s) |",
  "|---|---:|---:|---:|---:|---:|---:|---:|"
)
foreach ($Result in $Results) {
  $Label = if ($Result.passed) { "PASS" } else { "FAIL" }
  $Markdown += "| $($Result.plan) | $Label | $($Result.exit_code) | $($Result.duration_sec) | $($Result.collision_count) | $($Result.final_error_m) | $($Result.max_arrival_speed_mps) | $($Result.mean_wind_mps) |"
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
if ($GateFailures.Count -gt 0) {
  $Markdown += @("", "## Regression gate failures", "")
  foreach ($Failure in $GateFailures) {
    $Markdown += "- $Failure"
  }
}
$Markdown | Set-Content -LiteralPath $MarkdownPath -Encoding UTF8

Write-Host ""
Write-Host "Regression summary: $MarkdownPath"
Write-Host ("Result: {0} passed, {1} failed" -f $PassedCount, $FailedCount)
if (-not $Summary.all_passed) {
  exit 1
}
