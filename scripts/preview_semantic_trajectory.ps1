#requires -version 5.1
[CmdletBinding()]
param(
    [string]$Map = "/Game/Maps/SZTULargeBridgeSemanticScene",
    [string]$ProjectRoot = "D:\SZTUWindFieldPX4Demo\Unreal",
    [string]$EngineRoot = "D:\Epic Games\UE_5.8",
    [int]$Width = 1536,
    [int]$Height = 900,
    [switch]$LogoReviewCamera,
    [switch]$ReviewShot
)

$ErrorActionPreference = "Stop"
$project = Join-Path $ProjectRoot "SZTUWindFieldPX4Demo.uproject"
$editor = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
Get-Process UnrealEditor -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$arguments = @(
    $project, $Map, "-game", "-windowed", '-windowtitle="IFSC Bridge Inspection Platform"',
    "-resx=$Width", "-resy=$Height", "-dx11", "-NoSound",
    "-NoSplash", "-NoLoadingScreen"
)
if ($LogoReviewCamera) { $arguments += "-LogoReviewCamera" }
if ($ReviewShot) { $arguments += "-LargeBridgeReviewShot" }
$process = Start-Process -FilePath $editor -ArgumentList $arguments `
    -WorkingDirectory (Split-Path -Parent $editor) -PassThru
Write-Output "UE_PID=$($process.Id)"
Write-Output "MODE=trajectory_preview_only_no_px4"
