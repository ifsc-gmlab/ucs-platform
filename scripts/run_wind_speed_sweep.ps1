#requires -version 5.1
[CmdletBinding()]
param(
  [string]$WindSpeeds = "3,5,8,10",
  [double]$Alt = 3.0,
  [double]$Hover = 30.0,
  [double]$Climb = 6.0,
  [double]$Descend = 12.0,
  [ValidateRange(0.1, 5.0)]
  [double]$Ekf2AblLim = 5.0,
  [ValidateRange(0, 120)]
  [int]$EstimatorSettleSec = 35,
  [switch]$DryRun
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$RunRoot = Join-Path $Root "outputs\verification-runs"
$SingleRun = Join-Path $Root "outputs\run_wind_hover.ps1"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$CsvPath = Join-Path $Root "outputs\wind_speed_sweep_$Stamp.csv"
$ReportPath = Join-Path $Root "outputs\WIND_SPEED_SWEEP_$Stamp.md"
$Rows = @()
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$SpeedValues = @(
  $WindSpeeds.Split(',') | ForEach-Object {
    [double]::Parse($_.Trim(), $culture)
  }
)

foreach ($speed in $SpeedValues) {
  if ($speed -lt 0.5 -or $speed -gt 30.0) {
    Write-Warning "skipping invalid wind speed: $speed"
    continue
  }

  Write-Host "`n===== WIND SWEEP: ${speed}m/s =====" -ForegroundColor Cyan
  if ($DryRun) {
    & $SingleRun -WindMps $speed -Alt $Alt -Hover $Hover -Climb $Climb -Descend $Descend `
      -Ekf2AblLim $Ekf2AblLim -EstimatorSettleSec $EstimatorSettleSec -DryRun
    continue
  }

  $before = @(Get-ChildItem $RunRoot -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
  $scriptError = ""
  try {
    & $SingleRun -WindMps $speed -Alt $Alt -Hover $Hover -Climb $Climb -Descend $Descend `
      -Ekf2AblLim $Ekf2AblLim -EstimatorSettleSec $EstimatorSettleSec
  }
  catch {
    $scriptError = $_.Exception.Message
    Write-Warning $scriptError
  }

  $after = @(Get-ChildItem $RunRoot -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
  $newName = $after | Where-Object { $before -notcontains $_ } | Sort-Object | Select-Object -Last 1
  if (-not $newName) {
    $Rows += [pscustomobject]@{ WindMps=$speed; Run="missing"; Verdict="ERROR"; MeanWind=""; Drift=""; AltRange=""; Pitch=""; MaxMotor=""; MotorMargin=""; Error=$scriptError }
    continue
  }

  $runDir = Join-Path $RunRoot $newName
  $analysisPath = Join-Path $runDir "pattern_analysis.json"
  if (-not (Test-Path $analysisPath)) {
    $Rows += [pscustomobject]@{ WindMps=$speed; Run=$newName; Verdict="ERROR"; MeanWind=""; Drift=""; AltRange=""; Pitch=""; MaxMotor=""; MotorMargin=""; Error="pattern analysis missing" }
    continue
  }

  $analysis = Get-Content -Raw -Encoding UTF8 $analysisPath | ConvertFrom-Json
  $m = $analysis.metrics
  $Rows += [pscustomobject]@{
    WindMps = $speed
    Run = $newName
    Verdict = $(if ($analysis.pass) { "PASS" } else { "CHECK" })
    MeanWind = $m.mean_wind_x_mps
    Drift = $m.max_hover_drift_m
    AltRange = $m.pattern_altitude_5_95_range_m
    Pitch = $m.max_abs_pitch_deg
    MaxMotor = $m.max_motor_avg
    MotorMargin = $m.motor_margin
    Error = $scriptError
  }
}

if ($DryRun) { exit 0 }

$Rows | Export-Csv -NoTypeInformation -Encoding UTF8 $CsvPath
$passSpeeds = @($Rows | Where-Object { $_.Verdict -eq "PASS" } | ForEach-Object { [double]$_.WindMps })
$boundary = if ($passSpeeds.Count) { ($passSpeeds | Measure-Object -Maximum).Maximum } else { "none" }
$lines = @(
  "# Wind Speed Sweep",
  "",
  "- Time: $Stamp",
  "- Altitude/Hover: $Alt m / $Hover s",
  "- EKF2_ABL_LIM: $Ekf2AblLim",
  "- Highest tested PASS wind speed: $boundary m/s",
  "",
  "| Requested wind | Run | Verdict | Mean wind | Max drift | Altitude 5-95% | Max pitch | Max motor | Motor margin |",
  "|---:|---|---|---:|---:|---:|---:|---:|---:|"
)
foreach ($row in $Rows) {
  $lines += "| $($row.WindMps) | ``$($row.Run)`` | $($row.Verdict) | $($row.MeanWind) | $($row.Drift) | $($row.AltRange) | $($row.Pitch) | $($row.MaxMotor) | $($row.MotorMargin) |"
}
$lines += @("", "CSV: ``$CsvPath``")
Set-Content -Encoding UTF8 -Path $ReportPath -Value ($lines -join "`r`n")
Write-Host "[wind-sweep] report: $ReportPath"
Write-Host "[wind-sweep] highest tested PASS: ${boundary}m/s"
