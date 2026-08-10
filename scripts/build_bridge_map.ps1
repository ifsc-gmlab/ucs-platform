#requires -version 5.1
[CmdletBinding()]
param([switch]$SkipBuild)

$ErrorActionPreference = "Stop"
$Project = "D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject"
$EngineRoot = "D:\Epic Games\UE_5.8"
$Build = Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat"
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"

foreach ($Path in @($Project, $Build, $EditorCmd)) {
  if (-not (Test-Path -LiteralPath $Path)) { throw "required path missing: $Path" }
}

if (-not $SkipBuild) {
  & $Build SZTUWindFieldPX4DemoEditor Win64 Development `
    "-Project=$Project" -WaitMutex -NoHotReloadFromIDE -NoUBA -MaxParallelActions=1
  if ($LASTEXITCODE -ne 0) { throw "UE Editor target build failed: $LASTEXITCODE" }
}

& $EditorCmd $Project -run=SZTUWindFieldBuildMaps -BridgeMapOnly `
  -unattended -nop4 -nosplash -NullRHI -NoSound
if ($LASTEXITCODE -ne 0) { throw "bridge map commandlet failed: $LASTEXITCODE" }

$Map = "D:\SZTUWindFieldPX4Demo\Unreal\Content\Maps\SZTUBridgeSemanticScene.umap"
if (-not (Test-Path -LiteralPath $Map)) { throw "bridge map was not generated: $Map" }
Write-Host "[PASS] bridge map generated: $Map"
