#requires -version 5.1
[CmdletBinding()]
param(
  [switch]$SkipBuild,
  [string]$ProjectRoot = "D:\SZTUWindFieldPX4Demo\Unreal",
  [string]$EngineRoot = "D:\Epic Games\UE_5.8",
  [string]$BrandLogo = ""
)

$ErrorActionPreference = "Stop"
$Workspace = Split-Path -Parent $PSScriptRoot
$Project = Join-Path $ProjectRoot "SZTUWindFieldPX4Demo.uproject"
$Build = Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat"
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
$BrandLogo = if ($BrandLogo) { $BrandLogo } else {
  $workspaceLogo = Join-Path $Workspace "assets\branding\IFSC-StationBrandImage.png"
  $packageLogo = Join-Path $PSScriptRoot "branding\IFSC-StationBrandImage.png"
  if (Test-Path -LiteralPath $workspaceLogo) { $workspaceLogo } else { $packageLogo }
}
$LogoDestination = Join-Path $ProjectRoot "SourceArt\IFSC\IFSC-StationBrandImage.png"

foreach ($Path in @($Project, $Build, $EditorCmd)) {
  if (-not (Test-Path -LiteralPath $Path)) { throw "required path missing: $Path" }
}

if (-not (Test-Path -LiteralPath $BrandLogo)) {
  throw "IFSC brand logo missing: $BrandLogo"
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogoDestination) | Out-Null
Copy-Item -LiteralPath $BrandLogo -Destination $LogoDestination -Force
Write-Host "[IFSC] brand logo synchronized: $LogoDestination"

if (-not $SkipBuild) {
  & $Build SZTUWindFieldPX4DemoEditor Win64 Development `
    "-Project=$Project" -WaitMutex -NoHotReloadFromIDE -NoUBA -MaxParallelActions=1
  if ($LASTEXITCODE -ne 0) { throw "UE Editor target build failed: $LASTEXITCODE" }
}

& $EditorCmd $Project -run=SZTUWindFieldBuildMaps -LargeBridgeMapOnly `
  -unattended -nop4 -nosplash -NullRHI -NoSound
if ($LASTEXITCODE -ne 0) { throw "large bridge map commandlet failed: $LASTEXITCODE" }

$Map = Join-Path $ProjectRoot "Content\Maps\SZTULargeBridgeSemanticScene.umap"
if (-not (Test-Path -LiteralPath $Map)) { throw "large bridge map was not generated: $Map" }
Write-Host "[PASS] large bridge map generated: $Map"
