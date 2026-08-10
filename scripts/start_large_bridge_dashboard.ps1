#requires -version 5.1
[CmdletBinding()]
param(
  [ValidateRange(1024, 65535)][int]$Port = 8082,
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$inheritedPath = $env:Path
[Environment]::SetEnvironmentVariable("Path", $null, "Process")
[Environment]::SetEnvironmentVariable("PATH", $inheritedPath, "Process")
$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboard = Join-Path $PSScriptRoot "drone_dashboard.py"
$map = Join-Path $PSScriptRoot "large_bridge_semantic_map.json"
$stdout = Join-Path $PSScriptRoot "large-bridge-dashboard.stdout.log"
$stderr = Join-Path $PSScriptRoot "large-bridge-dashboard.stderr.log"
$pidFile = Join-Path $PSScriptRoot "large-bridge-dashboard.pid"
$url = "http://[::1]:$Port"

try {
  $existing = Invoke-RestMethod -Uri "$url/api/map" -TimeoutSec 2
  if ($existing.map_id -ne "sztu_large_suspension_bridge_semantic_v2") {
    throw "Port $Port is already used by a different dashboard/service."
  }
  if (-not $NoBrowser) { Start-Process $url }
  Write-Host "[large-bridge-dashboard] already running: $url"
  exit 0
} catch {
  if ($_.Exception.Message -like "Port * already used*") { throw }
}

$python = Get-Command py -ErrorAction SilentlyContinue
$pythonArgs = @()
if ($python) { $pythonArgs += "-3" } else { $python = Get-Command python -ErrorAction Stop }
$pythonArgs += @($dashboard, "--map", $map, "--host", "::1", "--port", $Port)
$proc = Start-Process -FilePath $python.Source -ArgumentList $pythonArgs `
  -WorkingDirectory $repoRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $pidFile -Value $proc.Id -Encoding ascii

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 250
  try {
    $loaded = Invoke-RestMethod -Uri "$url/api/map" -TimeoutSec 2
    if ($loaded.map_id -eq "sztu_large_suspension_bridge_semantic_v2") { $ready = $true; break }
  } catch {}
}
if (-not $ready) {
  if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
  throw "Large bridge dashboard did not become ready. See $stderr"
}

if (-not $NoBrowser) { Start-Process $url }
Write-Host "[large-bridge-dashboard] ready: $url"
Write-Host "[large-bridge-dashboard] PID: $($proc.Id)"
