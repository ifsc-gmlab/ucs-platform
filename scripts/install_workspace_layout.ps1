#requires -version 5.1
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$WorkspaceRoot,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceDir = Split-Path -Parent $PSCommandPath
if ((Split-Path -Leaf $SourceDir) -ine "scripts") {
  throw "Run this installer from the repository scripts directory"
}
$RepoRoot = Split-Path -Parent $SourceDir
$PlanSource = if (Test-Path -LiteralPath (Join-Path $RepoRoot "test-plans")) {
  Join-Path $RepoRoot "test-plans"
}
else {
  Join-Path $SourceDir "test-plans"
}
$DocsSource = Join-Path $RepoRoot "docs"
if (-not (Test-Path -LiteralPath $PlanSource)) {
  throw "Repository test-plans directory not found: $PlanSource"
}

$TargetRoot = [IO.Path]::GetFullPath($WorkspaceRoot)
$TargetOutputs = Join-Path $TargetRoot "outputs"
$TargetPlans = Join-Path $TargetOutputs "test-plans"
$TargetDocs = Join-Path $TargetRoot "docs"

Write-Host "Repository: $RepoRoot"
Write-Host "Workspace: $TargetRoot"
Write-Host "Map scripts -> $TargetOutputs"
Write-Host "Map test plans -> $TargetPlans"
if (Test-Path -LiteralPath $DocsSource) {
  Write-Host "Map docs -> $TargetDocs"
}
if ($DryRun) {
  Write-Host "DRY RUN: no files copied"
  exit 0
}

New-Item -ItemType Directory -Path $TargetOutputs, $TargetPlans, $TargetDocs -Force | Out-Null
Get-ChildItem -LiteralPath $SourceDir -File | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $TargetOutputs -Force
}
Get-ChildItem -LiteralPath $PlanSource -File | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $TargetPlans -Force
}
if (Test-Path -LiteralPath $DocsSource) {
  Get-ChildItem -LiteralPath $DocsSource -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $TargetDocs -Force
  }
}

Write-Host "Workspace mapping complete"
Write-Host "Next: powershell -NoProfile -ExecutionPolicy Bypass -File `"$TargetOutputs\run_semantic_regression.ps1`" -DryRun"
