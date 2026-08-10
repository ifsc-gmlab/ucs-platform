#requires -version 5.1
[CmdletBinding()]
param([int]$WaitSec = 60)

$ErrorActionPreference = "Stop"
$Project = "D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject"
$Engine = "D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"
$ShotSrc = "D:\SZTUWindFieldPX4Demo\Unreal\Saved\Screenshots\Windows\BridgeArchReview.png"
$ShotDst = "C:\Users\49337\OneDrive\Desktop\Desktop\实习\光明\outputs\bridge_no_protrusion_$(Get-Date -Format 'yyyyMMdd-HHmmss').png"

if (Get-Process -Name UnrealEditor -ErrorAction SilentlyContinue) {
  Stop-Process -Name UnrealEditor -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
}
if (Test-Path -LiteralPath $ShotSrc) { Remove-Item -LiteralPath $ShotSrc -Force }

Start-Process -FilePath $Engine -ArgumentList @(
  $Project, "/Game/Maps/SZTUBridgeSemanticScene", "-game", "-windowed",
  "-resx=1280", "-resy=720", "-BridgeReviewShot", "-NoSound"
) -WorkingDirectory (Split-Path -Parent $Engine) -PassThru | Out-Null

Write-Host "[bridge-review] UE launching, waiting $WaitSec s for BridgeArchReview.png..."
Start-Sleep -Seconds $WaitSec

if (-not (Test-Path -LiteralPath $ShotSrc)) {
  Write-Host "[bridge-review] screenshot not produced yet, waiting extra 30 s..."
  Start-Sleep -Seconds 30
}

if (Test-Path -LiteralPath $ShotSrc) {
  Copy-Item -LiteralPath $ShotSrc -Destination $ShotDst -Force
  $info = Get-Item -LiteralPath $ShotDst
  Write-Host "[bridge-review] PASS $ShotDst ($($info.Length) bytes)"
} else {
  Write-Host "[bridge-review] FAIL no screenshot at $ShotSrc"
  Stop-Process -Name UnrealEditor -Force -ErrorAction SilentlyContinue
  throw "BridgeArchReview.png was not produced."
}

Stop-Process -Name UnrealEditor -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$left = Get-Process -Name UnrealEditor -ErrorAction SilentlyContinue
if ($left) { Write-Host "[bridge-review] WARN UnrealEditor still running: $($left.Id)" }
else { Write-Host "[bridge-review] UE stopped." }