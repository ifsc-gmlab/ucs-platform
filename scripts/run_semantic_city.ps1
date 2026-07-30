#requires -version 5.1
[CmdletBinding()]
param(
  [string]$Goal = "delivery_zone",
  [string]$PlanFile = "",
  [ValidateRange(1.0, 5.0)]
  [double]$Alt = 2.0,
  [ValidateRange(0.3, 3.0)]
  [double]$CruiseSpeed = 1.25,
  [ValidateRange(30, 180)]
  [int]$MissionTimeoutSec = 70,
  [ValidateRange(0, 120)]
  [int]$EstimatorSettleSec = 35,
  [ValidateRange(30, 300)]
  [int]$UeStartupTimeoutSec = 180,
  [string]$Distro = "Ubuntu",
  [switch]$OnboardCamera,
  [switch]$ShowSensorRays,
  [switch]$Vision,
  [switch]$KeepRunning,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Host PowerShell sometimes carries both "Path" and "PATH" env keys; Start-Process
# with -RedirectStandard* forces UseShellExecute=false and .NET then throws
# "字典中的关键字:Path 所添加的关键字:PATH". Collapse to a single key up front.
$inheritedPath = $env:Path
try {
  [Environment]::SetEnvironmentVariable("Path", $null, "Process")
  [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
  $requiredPath = @(
    (Join-Path $env:WINDIR "System32"),
    (Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0"),
    (Join-Path $env:USERPROFILE "anaconda3")
  ) -join ";"
  $cleanPath = "$requiredPath;$inheritedPath"
  [Environment]::SetEnvironmentVariable("PATH", $cleanPath, "Process")
}
catch { }

$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$VerifyScript = Join-Path $Root "outputs\verify_px4_ue_hil.ps1"
$ControllerScript = Join-Path $Root "outputs\semantic_obstacle_mission.py"
$VerdictRules = Join-Path $Root "outputs\semantic_mission_verdict.py"
$RoutePlanner = Join-Path $Root "outputs\semantic_route_planner.py"
$TakeoffScript = Join-Path $Root "outputs\takeoff_offboard.py"
$LaunchScript = Join-Path $Root "outputs\start_px4_ue_hil.sh"
$RelayScript = Join-Path $Root "outputs\mavlink_relay.py"
$VisionDetector = Join-Path $Root "outputs\vision_detector.py"
$SemanticMap = Join-Path $Root "outputs\urban_semantic_map.json"
$UrbanMapAsset = "D:\SZTUWindFieldPX4Demo\Unreal\Content\Maps\SZTUUrbanSemanticScene.umap"
$WslExe = Join-Path $env:WINDIR "System32\wsl.exe"
if (-not (Test-Path -LiteralPath $WslExe)) { throw "WSL executable not found: $WslExe" }
$PowerShellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShellExe)) { throw "PowerShell executable not found: $PowerShellExe" }
$PythonExe = Join-Path $env:USERPROFILE "anaconda3\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if (-not $PythonCommand) { throw "Python interpreter not found" }
  $PythonExe = $PythonCommand.Source
}
$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = Join-Path $Root "outputs\semantic-runs\$RunStamp"

function Require-File {
  param([string]$Path, [string]$Name)
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "$Name not found: $Path"
  }
}

function Get-WslPath {
  param([string]$WindowsPath)
  $full = [System.IO.Path]::GetFullPath($WindowsPath)
  if ($full -notmatch "^([A-Za-z]):\\(.*)$") {
    throw "cannot convert path to WSL: $full"
  }
  $drive = $Matches[1].ToLowerInvariant()
  $tail = $Matches[2] -replace "\\", "/"
  "/mnt/$drive/$tail"
}

function Sync-WslFile {
  param([string]$WindowsPath)
  $source = Get-WslPath $WindowsPath
  $leaf = Split-Path -Leaf $WindowsPath
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $WslExe -d $Distro -- bash -lc "mkdir -p ~/szefitl && cp '$source' ~/szefitl/$leaf && sed -i 's/\r$//' ~/szefitl/$leaf && chmod +x ~/szefitl/$leaf" 2>$null
  }
  finally {
    $ErrorActionPreference = $prevEap
  }
  if ($LASTEXITCODE -ne 0) {
    throw "failed to sync $leaf into WSL (exit $LASTEXITCODE)"
  }
}

foreach ($required in @(
  @($VerifyScript, "HIL verifier"),
  @($ControllerScript, "semantic controller"),
  @($VerdictRules, "semantic mission verdict rules"),
  @($RoutePlanner, "semantic route planner"),
  @($TakeoffScript, "OFFBOARD helper"),
  @($LaunchScript, "PX4 launcher"),
  @($RelayScript, "MAVLink relay"),
  @($SemanticMap, "semantic map"),
  @($UrbanMapAsset, "urban UE map")
)) {
  Require-File $required[0] $required[1]
}

$PlanObject = $null
if ($PlanFile) {
  Require-File $PlanFile "mission plan"
  $PlanObject = Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8 | ConvertFrom-Json
  if (-not $PlanObject.steps -or $PlanObject.steps.Count -lt 1) {
    throw "mission plan must contain a non-empty steps array"
  }
  if (@($PlanObject.steps | Where-Object {
        $_.PSObject.Properties.Name -contains "vision" -and $_.vision -eq $true
      }).Count -gt 0) {
    $Vision = $true
  }
}

$extraUEArgs = @()
if ($OnboardCamera) { $extraUEArgs += "-OnboardCamera" }
if ($ShowSensorRays) { $extraUEArgs += "-ShowSensorRays" }
if ($Vision) { $extraUEArgs += "-VisionCapture" }

if ($DryRun) {
  if ($PlanFile) {
    Write-Host "[semantic-city] dry run plan=$PlanFile steps=$($PlanObject.steps.Count) alt=${Alt}m speed=${CruiseSpeed}m/s"
  } else {
    Write-Host "[semantic-city] dry run goal=$Goal alt=${Alt}m speed=${CruiseSpeed}m/s"
  }
  Write-Host "[semantic-city] map=/Game/Maps/SZTUUrbanSemanticScene"
  Write-Host "[semantic-city] semantic map=$SemanticMap"
  if ($Vision) { Write-Host "[semantic-city] VISION mode: UE -VisionCapture + controller --vision" }
  & $PythonExe -m py_compile $ControllerScript $RoutePlanner $TakeoffScript $VisionDetector
  if ($LASTEXITCODE -ne 0) { throw "Python syntax validation failed" }
  & $VerifyScript `
    -Map "/Game/Maps/SZTUUrbanSemanticScene" `
    -SkipFlight -SkipTelemetryListener -KeepRunning `
    -TelemetryHost "127.0.0.1" -TelemetryPort 14660 `
    -ExtraUEArgs $extraUEArgs -DryRun
  exit $LASTEXITCODE
}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$EffectivePlanFile = ""
if ($PlanFile) {
  $EffectivePlanFile = Join-Path $RunDir "mission_plan.json"
  Copy-Item -LiteralPath $PlanFile -Destination $EffectivePlanFile -Force
}
foreach ($runtimeFile in @($ControllerScript, $VerdictRules, $RoutePlanner, $TakeoffScript, $LaunchScript, $RelayScript, $VisionDetector)) {
  Sync-WslFile $runtimeFile
}

# Restart WSL so the D: drive re-mounts cleanly. The 9p mount of the D:
# drive goes stale in long-lived WSL sessions, which makes /mnt/d unreadable
# and silently breaks the onboard-camera vision loop (frames live on D:). A
# fresh instance re-mounts D: correctly, so the controller can read frames.
Write-Host "[semantic-city] restarting WSL to refresh D: mount (vision frames live on D:)"
& $WslExe --shutdown
Start-Sleep -Seconds 2

$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
  $rawWslAddresses = (& $WslExe -d $Distro -- bash -lc "hostname -I" 2>$null) -join " "
  $wslAddressExit = $LASTEXITCODE
}
finally {
  $ErrorActionPreference = $oldPreference
}
if ($wslAddressExit -ne 0) {
  throw "failed to query WSL IPv4 address (exit $wslAddressExit)"
}
$wslIpMatches = [regex]::Matches($rawWslAddresses, "\b(?:\d{1,3}\.){3}\d{1,3}\b")
if ($wslIpMatches.Count -lt 1) {
  throw "cannot determine WSL IPv4 address from: $rawWslAddresses"
}
$WslIp = $wslIpMatches[0].Value
Write-Host "[semantic-city] run dir: $RunDir"
Write-Host "[semantic-city] UE telemetry -> WSL ${WslIp}:14660"

$controllerExit = -1
try {
  if ($Vision) {
    # Copy the semantic map next to the uproject so the C++ landmark spawner
    # can read goal coordinates (it also has hardcoded fallbacks).
    $projectJson = "D:\SZTUWindFieldPX4Demo\Unreal\urban_semantic_map.json"
    Copy-Item -LiteralPath $SemanticMap -Destination $projectJson -Force
    Write-Host "[semantic-city] copied semantic map -> $projectJson"
  }

  # UE occasionally stalls during the editor's cold-start platform/shader
  # initialization and never opens TCP 4560.  A fresh launch normally recovers,
  # so retry the startup preflight once before failing the user mission.
  $startupReady = $false
  $startupError = $null
  for ($startupAttempt = 1; $startupAttempt -le 2; $startupAttempt++) {
    try {
      Write-Host "[semantic-city] UE/PX4 startup attempt $startupAttempt/2"
      & $VerifyScript `
        -Map "/Game/Maps/SZTUUrbanSemanticScene" `
        -Vehicle quad `
        -GraphicsRhi dx11 `
        -WindowWidth 1280 -WindowHeight 720 -NoSound `
        -SkipFlight -SkipTelemetryListener -KeepRunning `
        -TelemetryHost $WslIp -TelemetryPort 14660 `
        -ExtraUEArgs $extraUEArgs `
        -TcpTimeoutSec $UeStartupTimeoutSec `
        -EstimatorSettleSec $EstimatorSettleSec
      $startupReady = $true
      break
    }
    catch {
      $startupError = $_
      if ($startupAttempt -lt 2) {
        Write-Warning "[semantic-city] UE/PX4 startup failed; cleaning up and retrying once: $($_.Exception.Message)"
        Get-Process UnrealEditor -ErrorAction SilentlyContinue |
          Stop-Process -Force -ErrorAction SilentlyContinue
        $oldRetryPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
          & $PowerShellExe -NoProfile -ExecutionPolicy Bypass `
            -File (Join-Path $Root "outputs\px4_emergency_stop.ps1") -KillSim |
            Out-Host
        }
        finally {
          $ErrorActionPreference = $oldRetryPreference
        }
        Start-Sleep -Seconds 3
      }
    }
  }
  if (-not $startupReady) {
    $startupMessage = if ($startupError) { $startupError.Exception.Message } else { "unknown startup error" }
    throw "UE/PX4 startup failed after 2 attempts: $startupMessage"
  }

  $mapWsl = Get-WslPath $SemanticMap
  $resultPath = Join-Path $RunDir "mission_result.json"
  $tracePath = Join-Path $RunDir "perception_trace.jsonl"
  $resultWsl = Get-WslPath $resultPath
  $traceWsl = Get-WslPath $tracePath
  $culture = [System.Globalization.CultureInfo]::InvariantCulture
  $altText = $Alt.ToString($culture)
  $speedText = $CruiseSpeed.ToString($culture)
  $cameraDirWsl = "/mnt/d/SZTUWindFieldPX4Demo/Unreal/Saved/CameraFrames"
  $visionArgs = if ($Vision) { " --vision --camera-dir '$cameraDirWsl'" } else { "" }
  $planArgs = ""
  if ($EffectivePlanFile) {
    $planWsl = Get-WslPath $EffectivePlanFile
    $planArgs = " --plan '$planWsl'"
  }
  $controllerCommand = "cd ~/szefitl && PYTHONUNBUFFERED=1 python3 -u semantic_obstacle_mission.py --map '$mapWsl' --goal '$Goal'$planArgs --alt $altText --speed $speedText --mission-timeout $MissionTimeoutSec --result '$resultWsl' --trace '$traceWsl'$visionArgs"

  Write-Host "[semantic-city] starting semantic obstacle-avoidance controller"
  $controllerStdout = Join-Path $RunDir "controller.stdout.log"
  $controllerStderr = Join-Path $RunDir "controller.stderr.log"
  $controllerLog = Join-Path $RunDir "controller.log"
  # Stream each flushed controller line to the parent process while retaining
  # the existing run artifacts. Start-Process -Wait used to hide all semantic
  # navigation output from the dashboard until the controller exited.
  # Windows PowerShell 5.1 wraps native stderr (including WSL's harmless proxy
  # warning) as NativeCommandError when the global preference is Stop. Keep
  # streaming that warning, but let the native exit code decide success.
  $controllerPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $WslExe -d $Distro -- bash -lc $controllerCommand 2>&1 |
      Tee-Object -FilePath $controllerStdout |
      Tee-Object -FilePath $controllerLog |
      Out-Host
    $controllerExit = $LASTEXITCODE
  }
  finally {
    $ErrorActionPreference = $controllerPreference
  }
  if (-not (Test-Path -LiteralPath $controllerStderr)) {
    New-Item -ItemType File -Path $controllerStderr -Force | Out-Null
  }

  $px4Stdout = Join-Path $RunDir "px4_sitl.log"
  $px4Stderr = Join-Path $RunDir "px4_sitl.stderr.log"
  $px4CopyProcess = Start-Process -FilePath $WslExe `
    -ArgumentList @("-d", $Distro, "--", "bash", "-lc", "`"test -f /tmp/px4_sitl_1.log && cat /tmp/px4_sitl_1.log || true`"") `
    -RedirectStandardOutput $px4Stdout `
    -RedirectStandardError $px4Stderr `
    -WindowStyle Hidden -Wait -PassThru

  Require-File $resultPath "mission result"
  $result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $px4Failsafe = $false
  if (Test-Path -LiteralPath $px4Stdout) {
    $px4Failsafe = [bool](Select-String -LiteralPath $px4Stdout `
      -Pattern "Failsafe activated|RTL:" -Quiet)
  }
  $verdict = if ($result.pass -and $controllerExit -eq 0 -and -not $px4Failsafe) { "PASS" } else { "CHECK" }
  $summary = @(
    "# Semantic City Mission Summary",
    "",
    "- Run: $RunStamp",
    "- Verdict: $verdict",
    "- Goal: $($result.goal_id) ($($result.goal_label))",
    "- Plan steps: $(@($result.plan_steps).Count)",
    "- Completed legs: $(@($result.legs).Count)",
    "- Reached: $($result.reached)",
    "- Vision enabled: $($result.vision_enabled)",
    "- Vision confirmed: $($result.vision_confirmed)",
    "- Avoidance events: $($result.avoidance_events)",
    "- Planned detour waypoints: $($result.planned_detour_waypoints)",
    "- Avoidance-required legs: $($result.avoidance_required_legs)",
    "- Avoidance demonstrated where required: $($result.avoidance_demonstrated)",
    "- Obstacle collisions: $($result.collision_count)",
    "- Sensor samples: $($result.sensor_samples)",
    "- Landed: $($result.landed)",
    "- Disarmed: $($result.disarmed)",
    "- Final position: $($result.final_position_m -join ', ')",
    "- Final position error: $($result.final_position_error_m) m",
    "- PX4 failsafe: $px4Failsafe",
    "- Controller exit: $controllerExit",
    "- Failure: $($result.failure)",
    "",
    "Required PASS criteria: semantic goal reached; every leg whose direct path crosses an expanded obstacle demonstrates a map-planned detour or sensor-triggered avoidance; clear direct legs do not require an artificial detour; zero obstacle collisions; at least 20 sensor samples; landed and disarmed; final horizontal error <= 0.50 m; and no PX4 failsafe/RTL."
  )
  $summary | Set-Content -LiteralPath (Join-Path $RunDir "summary.md") -Encoding UTF8
  Write-Host "[semantic-city] verdict=$verdict summary=$(Join-Path $RunDir 'summary.md')"

  if ($verdict -ne "PASS") {
    throw "semantic mission validation failed; see $RunDir"
  }
}
finally {
  if (-not $KeepRunning) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "outputs\px4_emergency_stop.ps1") -KillSim | Out-Host
      Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    finally {
      $ErrorActionPreference = $oldPreference
    }
  }
  else {
    Write-Host "[semantic-city] KeepRunning set; UE/PX4 left running"
  }
}
