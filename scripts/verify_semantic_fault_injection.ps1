#requires -version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceDir = Split-Path -Parent $PSCommandPath
$Runner = Join-Path $SourceDir "run_semantic_city.ps1"
$PythonTest = Join-Path $SourceDir "verify_semantic_resilience_test.py"
$Python = (Get-Command python -ErrorAction Stop).Source
$PowerShell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("semantic-faults-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
$Failures = @()

function Expect-Failure {
  param([string]$Name, [string[]]$Arguments)
  $old = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $Runner @Arguments *> $null
    $exit = $LASTEXITCODE
  } finally { $ErrorActionPreference = $old }
  if ($exit -eq 0) {
    $script:Failures += "$Name unexpectedly succeeded"
    Write-Host "FAIL $Name"
  } else {
    Write-Host "PASS $Name"
  }
}

try {
  & $Python $PythonTest
  if ($LASTEXITCODE -ne 0) { $Failures += "pure resilience tests failed" }

  $BadConfig = Join-Path $TempDir "bad-config.json"
  '{"schema_version":99}' |
    Set-Content -LiteralPath $BadConfig -Encoding UTF8
  Expect-Failure "invalid config is rejected" @("-ConfigFile", $BadConfig, "-DryRun")

  $BadPlan = Join-Path $TempDir "bad-plan.json"
  '{"name":"bad","steps":[]}' |
    Set-Content -LiteralPath $BadPlan -Encoding UTF8
  Expect-Failure "empty mission plan is rejected" @("-PlanFile", $BadPlan, "-DryRun")

  $listeners = @(Get-NetTCPConnection -LocalPort 4560 -State Listen -ErrorAction SilentlyContinue)
  if ($listeners.Count -gt 0) {
    Write-Host "PASS occupied 4560 is observable before startup (owners: $($listeners.OwningProcess -join ','))"
  } else {
    Write-Host "PASS TCP 4560 is clear before startup"
  }
}
finally {
  $resolved = [IO.Path]::GetFullPath($TempDir)
  $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
  if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
  }
}

if ($Failures.Count -gt 0) {
  $Failures | ForEach-Object { Write-Error $_ }
  exit 1
}
Write-Host "ALL_OK"
