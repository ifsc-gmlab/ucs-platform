#requires -version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$secureKey = Read-Host "Paste the DeepSeek API key (input is hidden)" -AsSecureString
$keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
  $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
  if ($plainKey -notmatch "^sk-[A-Za-z0-9_-]{16,}$") {
    throw "Invalid key format: expected sk- followed by at least 16 safe characters."
  }

  [Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", $plainKey, "User")
  [Environment]::SetEnvironmentVariable("DEEPSEEK_MODEL", "deepseek-v4-flash", "User")
  $env:DEEPSEEK_API_KEY = $plainKey
  $env:DEEPSEEK_MODEL = "deepseek-v4-flash"

  $sourceDir = Split-Path -Parent $PSCommandPath
  $root = Split-Path -Parent $sourceDir
  $dashboard = Join-Path $sourceDir "drone_dashboard.py"
  $outDir = if ((Split-Path -Leaf $sourceDir) -ieq "scripts") {
    Join-Path $root "outputs"
  }
  else {
    $sourceDir
  }
  New-Item -ItemType Directory -Path $outDir -Force | Out-Null
  $pythonExe = (Get-Command python -ErrorAction Stop).Source

  $listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
  if ($listener) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($owner.CommandLine -match "drone_dashboard\.py") {
      Stop-Process -Id $listener.OwningProcess -Force
      Wait-Process -Id $listener.OwningProcess -Timeout 5 -ErrorAction SilentlyContinue
    }
    else {
      throw "Port 8080 is owned by another program; it was not stopped."
    }
  }

  Start-Process -FilePath $pythonExe `
    -ArgumentList @($dashboard, "--port", "8080", "--telemetry-port", "14660") `
    -WorkingDirectory $outDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $outDir "dashboard.stdout.log") `
    -RedirectStandardError (Join-Path $outDir "dashboard.stderr.log") | Out-Null

  Start-Sleep -Seconds 2
  $status = Invoke-RestMethod "http://127.0.0.1:8080/api/ai-status" -TimeoutSec 5
  if (-not $status.enabled) {
    throw "Dashboard started, but AI status is still disabled."
  }
  Write-Host "Configured successfully: $($status.model). Refresh http://127.0.0.1:8080/"
}
finally {
  if ($keyPtr -ne [IntPtr]::Zero) {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
  }
  Remove-Variable plainKey -ErrorAction SilentlyContinue
}
