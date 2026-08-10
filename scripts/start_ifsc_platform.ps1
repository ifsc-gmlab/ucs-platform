#requires -version 5.1
[CmdletBinding()]
param(
  [ValidateSet("dashboard", "preview", "build-bridge", "verify", "no-wind", "wind")]
  [string]$Mode = "dashboard",
  [string]$ConfigFile = ""
)

$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
if (-not $ConfigFile) {
  $ConfigFile = Join-Path $Workspace "config\ifsc.runtime.json"
  if (-not (Test-Path -LiteralPath $ConfigFile)) {
    $ConfigFile = Join-Path $Workspace "config\ifsc.runtime.example.json"
  }
  if (-not (Test-Path -LiteralPath $ConfigFile)) {
    $ConfigFile = Join-Path $PSScriptRoot "ifsc.runtime.example.json"
  }
}
$Config = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
$ProjectRoot = [string]$Config.ue_project_root
$EngineRoot = [string]$Config.ue_engine_root
$env:IFSC_UE_PROJECT_ROOT = $ProjectRoot

Write-Host "[IFSC] mode=$Mode"
Write-Host "[IFSC] UE project root=$ProjectRoot"

switch ($Mode) {
  "dashboard" {
    & (Join-Path $PSScriptRoot "start_large_bridge_dashboard.ps1") `
      -Port ([int]$Config.dashboard.large_bridge_port)
  }
  "preview" {
    & (Join-Path $PSScriptRoot "preview_semantic_trajectory.ps1") `
      -ProjectRoot $ProjectRoot -EngineRoot $EngineRoot
  }
  "build-bridge" {
    & (Join-Path $PSScriptRoot "build_large_bridge_map.ps1") `
      -ProjectRoot $ProjectRoot -EngineRoot $EngineRoot
  }
  "verify" {
    & (Join-Path $PSScriptRoot "check_px4_wsl_env.ps1") -Distro ([string]$Config.wsl_distro)
    if ($LASTEXITCODE -ne 0) { throw "IFSC WSL/PX4 environment validation failed" }
    & (Join-Path $PSScriptRoot "verify_px4_ue_hil.ps1") -DryRun `
      -Project (Join-Path $ProjectRoot "SZTUWindFieldPX4Demo.uproject")
  }
  "no-wind" {
    & (Join-Path $PSScriptRoot "run_qgc_control.ps1") -Scene factory `
      -Project (Join-Path $ProjectRoot "SZTUWindFieldPX4Demo.uproject") `
      -Distro ([string]$Config.wsl_distro) -RestartQGC
  }
  "wind" {
    & (Join-Path $PSScriptRoot "run_qgc_control.ps1") -Scene factory-wind `
      -Project (Join-Path $ProjectRoot "SZTUWindFieldPX4Demo.uproject") `
      -Distro ([string]$Config.wsl_distro) -WindUI -WindMps 8 -RestartQGC
  }
}

if ($LASTEXITCODE -ne 0) { throw "IFSC mode '$Mode' failed with exit code $LASTEXITCODE" }
