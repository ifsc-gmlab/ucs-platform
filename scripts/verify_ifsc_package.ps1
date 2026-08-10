#requires -version 5.1
[CmdletBinding()]
param([string]$PackageRoot = "")

$ErrorActionPreference = "Stop"
if (-not $PackageRoot) { $PackageRoot = Split-Path -Parent $PSScriptRoot }
$required = @(
  "00_START_HERE", "01_BRIDGE_DECK", "02_SEMANTIC_MAP", "03_WIND_FIELD",
  "04_PX4_UE5_QGC_LLM", "05_RUNTIME_ORCHESTRATION",
  "06_SERVER_DEPLOYMENT", "07_ACCEPTANCE_EVIDENCE", "08_MANIFESTS"
)
$errors = New-Object System.Collections.Generic.List[string]
foreach ($item in $required) {
  if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot $item))) {
    $errors.Add("missing layer: $item")
  }
}
$hashFile = Join-Path $PackageRoot "08_MANIFESTS\SHA256SUMS.txt"
if (-not (Test-Path -LiteralPath $hashFile)) {
  $errors.Add("missing SHA256 manifest")
} else {
  foreach ($line in Get-Content -LiteralPath $hashFile) {
    if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { continue }
    $expected = $Matches[1]
    $relative = $Matches[2].Replace('/', '\')
    $path = Join-Path $PackageRoot $relative
    if (-not (Test-Path -LiteralPath $path)) {
      $errors.Add("missing file: $relative")
      continue
    }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { $errors.Add("hash mismatch: $relative") }
  }
}
if ($errors.Count) {
  $errors | ForEach-Object { Write-Error $_ }
  exit 1
}
Write-Host "[PASS] IFSC package layers and SHA256 manifest verified"
