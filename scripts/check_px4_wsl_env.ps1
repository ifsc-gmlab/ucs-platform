#requires -version 5.1
[CmdletBinding()]
param(
  [string]$Distro = "Ubuntu",
  [string]$Px4Root = "~/PX4-Autopilot",
  [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $ReportPath) {
  $ReportPath = Join-Path $Root "outputs\px4_wsl_env_report.md"
}

function Invoke-WslCheck {
  param([string]$Command)
  $oldPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $output = & wsl.exe -d $Distro -- bash -lc $Command 2>$null
    $exitCode = $LASTEXITCODE
    [pscustomobject]@{
      Ok = $exitCode -eq 0
      Text = (($output | ForEach-Object { "$_" }) -join "`n").Trim()
    }
  }
  finally {
    $ErrorActionPreference = $oldPreference
  }
}

$ubuntu = Invoke-WslCheck "lsb_release -ds"
$repo = Invoke-WslCheck "test -d $Px4Root"
$revision = Invoke-WslCheck "git -C $Px4Root rev-parse --short HEAD"
$branch = Invoke-WslCheck "git -C $Px4Root status --short --branch | head -1"
$submodules = Invoke-WslCheck "if git -C $Px4Root submodule status --recursive | grep -q '^-'; then exit 1; fi"
$binary = Invoke-WslCheck "test -x $Px4Root/build/px4_sitl_default/bin/px4"
$pymavlink = Invoke-WslCheck "python3 -c 'import pymavlink; print(pymavlink.__file__)'"
$hostIp = Invoke-WslCheck "ip route show default | cut -d ' ' -f3 | head -1"

$checks = [ordered]@{
  "Ubuntu available" = $ubuntu.Ok
  "PX4 repository in WSL filesystem" = $repo.Ok
  "PX4 git revision readable" = $revision.Ok
  "PX4 recursive submodules initialized" = $submodules.Ok
  "px4_sitl binary built" = $binary.Ok
  "pymavlink importable" = $pymavlink.Ok
  "Windows host gateway detected" = $hostIp.Ok -and [bool]$hostIp.Text
}
$allOk = -not ($checks.Values -contains $false)

$lines = @(
  "# PX4 WSL Environment Report",
  "",
  "- Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')",
  "- Distro: $Distro",
  "- Ubuntu: $($ubuntu.Text)",
  "- PX4 root: $Px4Root",
  "- PX4 revision: $($revision.Text)",
  "- PX4 branch: $($branch.Text)",
  "- Windows host gateway: $($hostIp.Text)",
  "- Overall: $(if ($allOk) { 'PASS' } else { 'CHECK' })",
  "",
  "## Checks",
  ""
)
foreach ($item in $checks.GetEnumerator()) {
  $lines += "- $($item.Key): $($item.Value)"
}
$lines += @(
  "",
  "## Reference",
  "",
  "- https://docs.px4.io/main/en/dev_setup/dev_env_windows_wsl"
)
Set-Content -LiteralPath $ReportPath -Value ($lines -join "`r`n") -Encoding UTF8
Write-Host "PX4 WSL environment: $(if ($allOk) { 'PASS' } else { 'CHECK' })"
Write-Host "Report: $ReportPath"
if (-not $allOk) { exit 1 }
