#requires -version 5.1
<#
.SYNOPSIS
End-to-end validator for the SZTU UE5 wind-field map + PX4 SITL-HIL chain.

.DESCRIPTION
Starts UE in game mode, starts PX4 inside WSL through ~/szefitl/start_px4_ue_hil.sh,
waits for the simulator connection, optionally runs the WSL offboard takeoff
script, captures logs, writes a compact run summary, and cleans up by default.

Default run:
  powershell -ExecutionPolicy Bypass -File outputs\verify_px4_ue_hil.ps1

Dry run:
  powershell -ExecutionPolicy Bypass -File outputs\verify_px4_ue_hil.ps1 -DryRun
#>
[CmdletBinding()]
param(
  [string]$UnrealEditor = "D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe",
  [string]$Project = "D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject",
  [string]$Map = "/Game/Maps/SZTUWindFieldDemoScene",
  [string]$WslLaunchScript = "~/szefitl/start_px4_ue_hil.sh",
  [string]$WslTakeoffScript = "~/szefitl/takeoff_offboard.py",
  [ValidateSet("quad", "hex", "rover")]
  [string]$Vehicle = "quad",
  [string]$PythonExe = "python",
  [ValidateSet("dx11", "dx12")]
  [string]$GraphicsRhi = "dx12",
  [int]$WindowWidth = 1280,
  [int]$WindowHeight = 720,
  [switch]$NoSound,
  [double]$Alt = 2.0,
  [switch]$HeadwindTest,
  [double]$HeadwindDistance = 2.0,
  [double]$Move = 5.0,
  [double]$Hold = 6.0,
  [double]$Climb = 4.0,
  [double]$Descend = 10.0,
  [ValidateSet("legacy", "hover", "circle")]
  [string]$FlightPattern = "legacy",
  [double]$CircleRadius = 1.5,
  [double]$CircleLaps = 1.0,
  [double]$CircleDuration = 12.0,
  [double]$CircleEntry = 3.0,
  [ValidateRange(0.1, 3.0)]
  [double]$MpcZP = 1.5,
  [ValidateRange(0.1, 12.0)]
  [double]$MpcZVelPAcc = 3.0,
  [ValidateRange(0.0, 10.0)]
  [double]$MpcZVelIAcc = 1.5,
  [ValidateRange(0.0, 3.0)]
  [double]$MpcZVelDAcc = 0.0,
  [ValidateRange(0.1, 0.9)]
  [double]$MpcThrHover = 0.62,
  [ValidateRange(0.1, 5.0)]
  [double]$Ekf2AblLim = 5.0,
  [ValidateRange(0, 120)]
  [int]$EstimatorSettleSec = 35,
  [int]$TcpTimeoutSec = 90,
  [int]$Px4TimeoutSec = 120,
  [int]$FlightTimeoutSec = 90,
  [int]$TelemetryDurationSec = 0,
  [switch]$SkipFlight,
  [bool]$ShowWindArrows = $false,
  [ValidateRange(-1.0, 30.0)]
  [double]$WindMps = -1.0,
  [switch]$KeepRunning,
  [switch]$DryRun
)

# Harden against a PowerShell 5.1 / .NET Framework bug: a redirected Start-Process
# (UseShellExecute=false) makes .NET copy the env block into a case-insensitive
# dictionary and throws "字典中的关键字:'Path'所添加的关键字:'PATH'" when a
# case-duplicate env key exists (e.g. both 'Path' and 'PATH' injected by the host).
# A plain "$env:PATH = $env:PATH" only sets one key and leaves the duplicate, so
# explicitly delete both case variants then set a single one.
if ($env:PATH -or $env:Path) {
  $cleanPath = [System.Environment]::GetEnvironmentVariable("PATH", "Process")
  [System.Environment]::SetEnvironmentVariable("Path", $null, "Process")
  [System.Environment]::SetEnvironmentVariable("PATH", $null, "Process")
  [System.Environment]::SetEnvironmentVariable("PATH", $cleanPath, "Process")
}

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$script:RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$script:RunDir = Join-Path $script:RepoRoot "outputs\verification-runs\$script:RunStamp"
$script:Status = [ordered]@{
  UEStarted = $false
  UEPortListening = $false
  PX4Started = $false
  PX4Connected = $false
  PX4Ready = $false
  TelemetryStarted = $false
  FlightScriptRan = $false
  Armed = $false
  Offboard = $false
  TakeoffDetected = $false
  Disarmed = $false
  Failsafe = $false
  TelemetryPass = $false
}

$script:UEProc = $null
$script:PX4Proc = $null
$script:TelemetryProc = $null

function Write-Step {
  param([string]$Message)
  $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
  Write-Host $line
  if (-not $DryRun -and (Test-Path $script:RunDir)) {
    Add-Content -Path (Join-Path $script:RunDir "verify.log") -Value $line -Encoding UTF8
  }
}

function Require-File {
  param([string]$Path, [string]$Name)
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "$Name not found: $Path"
  }
}

function Invoke-WslText {
  param([string]$Command)
  $oldPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & wsl.exe bash -lc $Command 2>&1
    return ($out -join "`n")
  }
  finally {
    $ErrorActionPreference = $oldPreference
  }
}

function Get-WslPath {
  param([string]$WindowsPath)
  $full = [System.IO.Path]::GetFullPath($WindowsPath)
  if ($full -match "^([A-Za-z]):\\(.*)$") {
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2] -replace "\\", "/"
    return "/mnt/$drive/$tail"
  }
  throw "cannot convert Windows path to WSL path: $WindowsPath"
}

function Test-Port4560Listening {
  try {
    $conns = Get-NetTCPConnection -LocalPort 4560 -State Listen -ErrorAction Stop
    if ($script:UEProc -ne $null) {
      return [bool]($conns | Where-Object { $_.OwningProcess -eq $script:UEProc.Id })
    }
    return [bool]$conns
  }
  catch {
    try {
      $client = New-Object System.Net.Sockets.TcpClient
      $iar = $client.BeginConnect("127.0.0.1", 4560, $null, $null)
      $ok = $iar.AsyncWaitHandle.WaitOne(250, $false)
      if ($ok) { $client.EndConnect($iar) }
      $client.Close()
      return $ok
    }
    catch {
      return $false
    }
  }
}

function Wait-Until {
  param(
    [string]$Name,
    [int]$TimeoutSec,
    [scriptblock]$Condition
  )
  Write-Step "waiting: $Name (timeout ${TimeoutSec}s)"
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (& $Condition) {
      Write-Step "ok: $Name"
      return $true
    }
    Start-Sleep -Seconds 1
  }
  Write-Step "timeout: $Name"
  return $false
}

function Stop-WslSim {
  Write-Step "stopping WSL px4/relay processes"
  $cmd = "pkill -TERM -f '[m]avlink_relay.py' 2>/dev/null || true; " +
         "pkill -TERM -f '[p]x4_sitl_default/bin/px4' 2>/dev/null || true; " +
         "pkill -TERM -f '[s]tart_px4_ue_hil.sh' 2>/dev/null || true; " +
         "pkill -TERM -f '[p]x4_cmd_fifo' 2>/dev/null || true; " +
         "sleep 1; " +
         "pkill -KILL -f '[m]avlink_relay.py' 2>/dev/null || true; " +
         "pkill -KILL -f '[p]x4_sitl_default/bin/px4' 2>/dev/null || true; " +
         "pkill -KILL -f '[s]tart_px4_ue_hil.sh' 2>/dev/null || true; " +
         "pkill -KILL -f '[p]x4_cmd_fifo' 2>/dev/null || true; " +
         "rm -f /tmp/px4_cmd_fifo /tmp/px4_sitl.log /tmp/relay.log 2>/dev/null || true"
  [void](Invoke-WslText $cmd)
}

function Stop-StaleTelemetryListeners {
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "listen_telem_long\.py" } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Copy-WslArtifacts {
  if ($DryRun -or -not (Test-Path $script:RunDir)) { return }
  $runDirWsl = Get-WslPath $script:RunDir
  $cmd = "cp /tmp/px4_sitl.log '$runDirWsl/px4_sitl.log' 2>/dev/null || true; " +
         "cp /tmp/relay.log '$runDirWsl/relay.log' 2>/dev/null || true; " +
         "cp /mnt/c/tmp/hil_debug.csv '$runDirWsl/hil_debug.csv' 2>/dev/null || true"
  [void](Invoke-WslText $cmd)
}

function Copy-UELog {
  if ($DryRun -or -not (Test-Path $script:RunDir)) { return }
  $ueLogDir = Join-Path (Split-Path -Parent $Project) "Saved\Logs"
  if (-not (Test-Path $ueLogDir)) { return }
  $latest = Get-ChildItem -Path $ueLogDir -Filter "*.log" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($latest) {
    Copy-Item -LiteralPath $latest.FullName -Destination (Join-Path $script:RunDir "Unreal-latest.log") -Force
  }
}

function Update-StatusFromArtifacts {
  if ($DryRun -or -not (Test-Path $script:RunDir)) { return }
  $px4LogPath = Join-Path $script:RunDir "px4_sitl.log"
  $takeoffLogPath = Join-Path $script:RunDir "takeoff_offboard.log"
  $telemetryLogPath = Join-Path $script:RunDir "telemetry.log"

  $px4Log = if (Test-Path $px4LogPath) { Get-Content -Raw -Encoding UTF8 $px4LogPath } else { "" }
  $takeoffLog = if (Test-Path $takeoffLogPath) { Get-Content -Raw -Encoding UTF8 $takeoffLogPath } else { "" }
  $telemetryLog = if (Test-Path $telemetryLogPath) { Get-Content -Raw -Encoding UTF8 $telemetryLogPath } else { "" }

  $script:Status.PX4Connected = $script:Status.PX4Connected -or ($px4Log -match "Simulator connected")
  $script:Status.PX4Ready = $script:Status.PX4Ready -or ($px4Log -match "Ready for takeoff")
  $script:Status.Armed = ($px4Log -match "Armed by external command|Armed by internal command") -or ($takeoffLog -match "\[\*\] ARMED|ACK result=0")
  $script:Status.Offboard = $takeoffLog -match "OFFBOARD engaged"
  $script:Status.TakeoffDetected = $px4Log -match "Takeoff detected"
  $script:Status.Disarmed = ($takeoffLog -match "disarmed \(ACK result=0\)") -or ($px4Log -match "Disarmed by external command|Disarmed by internal command")
  $script:Status.Failsafe = [bool]($px4Log -match "Failsafe activated")

  $telemetryPassBySummary = [bool]($telemetryLog -match "PASS - drone stayed level")
  $telemetryPassBySamples = $false
  $sampleMatches = [regex]::Matches($telemetryLog, "pos_z=\s*([-+]?[0-9]*\.?[0-9]+)\s+roll=\s*([-+]?[0-9]*\.?[0-9]+)\s+pitch=\s*([-+]?[0-9]*\.?[0-9]+)")
  if ($sampleMatches.Count -gt 0) {
    $maxZ = -999.0
    $maxRoll = 0.0
    $maxPitch = 0.0
    $finalZ = 0.0
    foreach ($sample in $sampleMatches) {
      $z = [double]$sample.Groups[1].Value
      $roll = [Math]::Abs([double]$sample.Groups[2].Value)
      $pitch = [Math]::Abs([double]$sample.Groups[3].Value)
      if ($z -gt $maxZ) { $maxZ = $z }
      if ($roll -gt $maxRoll) { $maxRoll = $roll }
      if ($pitch -gt $maxPitch) { $maxPitch = $pitch }
      $finalZ = $z
    }
    $requiredPeakAltitude = [Math]::Max(0.5, [Math]::Min(1.0, $Alt * 0.6))
    $telemetryPassBySamples = ($maxRoll -lt 55.0) -and ($maxPitch -lt 55.0) -and ($maxZ -gt $requiredPeakAltitude) -and ([Math]::Abs($finalZ) -lt 0.5)
  }
  $script:Status.TelemetryPass = $telemetryPassBySummary -or $telemetryPassBySamples
}

function Write-Summary {
  if ($DryRun -or -not (Test-Path $script:RunDir)) { return }
  Update-StatusFromArtifacts
  $summaryPath = Join-Path $script:RunDir "summary.md"
  $lines = @(
    "# PX4 UE HIL validation run",
    "",
    "- Time: $script:RunStamp",
    "- Map: $Map",
    "- Vehicle: $Vehicle",
    "- Project: $Project",
    "- Graphics: $GraphicsRhi ${WindowWidth}x${WindowHeight} NoSound=$NoSound",
    "- Alt/Hold/Climb/Descend: $Alt / $Hold / $Climb / $Descend",
    "- HeadwindTest: $HeadwindTest",
    "- HeadwindDistance/Move: $HeadwindDistance / $Move",
    "- FlightPattern: $FlightPattern",
    "- CircleRadius/Laps/Duration/Entry: $CircleRadius / $CircleLaps / $CircleDuration / $CircleEntry",
    "- VerticalParams ZP/ZVelP/ZVelI/ZVelD/Hover: $MpcZP / $MpcZVelPAcc / $MpcZVelIAcc / $MpcZVelDAcc / $MpcThrHover",
    "- EKF2_ABL_LIM: $Ekf2AblLim",
    "- EstimatorSettleSec: $EstimatorSettleSec",
    "- TelemetryDurationSec: $TelemetryDurationSec",
    "- ShowWindArrows: $ShowWindArrows",
    "- WindMpsOverride: $WindMps",
    "- SkipFlight: $SkipFlight",
    "",
    "## Verdict flags",
    ""
  )
  foreach ($key in $script:Status.Keys) {
    $lines += ("- {0}: {1}" -f $key, $script:Status[$key])
  }
  $lines += @(
    "",
    "## Logs",
    "",
    "- verify.log",
    "- px4_sitl.log",
    "- relay.log",
    "- telemetry.log",
    "- takeoff_offboard.log",
    "- Unreal-latest.log",
    "- hil_debug.csv"
  )
  Set-Content -Path $summaryPath -Value ($lines -join "`r`n") -Encoding UTF8
  Write-Step "summary written: $summaryPath"

  if ($SkipFlight) {
    Write-Step "flight analyzers skipped by -SkipFlight"
    return
  }

  $analyzer = Join-Path $script:RepoRoot "outputs\analyze_flight_run.py"
  if (Test-Path $analyzer) {
    Write-Step "running flight log analyzer"
    & $PythonExe $analyzer $script:RunDir `
      1> (Join-Path $script:RunDir "flight_analysis.stdout.log") `
      2> (Join-Path $script:RunDir "flight_analysis.stderr.log")
    Write-Step "flight log analyzer exit code: $LASTEXITCODE"
  }

  if ($FlightPattern -ne "legacy") {
    $patternAnalyzer = Join-Path $script:RepoRoot "outputs\analyze_flight_patterns.py"
    if (Test-Path $patternAnalyzer) {
      Write-Step "running $FlightPattern pattern analyzer"
      & $PythonExe $patternAnalyzer $script:RunDir `
        1> (Join-Path $script:RunDir "pattern_analysis.stdout.log") `
        2> (Join-Path $script:RunDir "pattern_analysis.stderr.log")
      Write-Step "pattern analyzer exit code: $LASTEXITCODE"
    }
  }
}

function Start-Validation {
  Require-File $UnrealEditor "UnrealEditor"
  Require-File $Project "UE project"

  if ($DryRun) {
    Write-Step "dry run only"
    Write-Step "repo root: $script:RepoRoot"
    Write-Step "run dir: $script:RunDir"
    Write-Step "UE: $UnrealEditor"
    Write-Step "project: $Project"
  Write-Step "map: $Map"
  Write-Step "vehicle: $Vehicle"
  Write-Step "WSL launch: $WslLaunchScript"
  Write-Step "WSL takeoff: $WslTakeoffScript"
  Write-Step "headwind test: $HeadwindTest distance=$HeadwindDistance move=$Move"
  Write-Step "wind speed override: $WindMps m/s (-1 means map default)"
  Write-Step "flight pattern: $FlightPattern circle=$CircleRadius m x $CircleLaps lap(s) / ${CircleDuration}s"
  Write-Step "vertical params: Z_P=$MpcZP Z_VEL_PI_D=$MpcZVelPAcc/$MpcZVelIAcc/$MpcZVelDAcc THR_HOVER=$MpcThrHover ABL_LIM=$Ekf2AblLim settle=${EstimatorSettleSec}s"
    return
  }

  New-Item -ItemType Directory -Force -Path $script:RunDir | Out-Null
  Write-Step "run dir: $script:RunDir"

  Write-Step "cleaning stale UnrealEditor"
  Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force
  Stop-WslSim
  Stop-StaleTelemetryListeners
  Remove-Item -LiteralPath "C:\tmp\hil_debug.csv" -Force -ErrorAction SilentlyContinue

  Write-Step "starting UE game mode"
  $ueArgs = @($Project, $Map, "-game", "-windowed", "-ResX=$WindowWidth", "-ResY=$WindowHeight", "-$GraphicsRhi", "-DarkGround", "-log")
  if ($NoSound) {
    $ueArgs += "-nosound"
  }
  if ($ShowWindArrows) {
    $ueArgs += "-ShowWindArrows"
  }
  if ($WindMps -ge 0.0) {
    $windText = $WindMps.ToString([System.Globalization.CultureInfo]::InvariantCulture)
    $ueArgs += "-WindMps=$windText"
  }
  $ueArgs += "-Vehicle=$Vehicle"
  $script:UEProc = Start-Process -FilePath $UnrealEditor -ArgumentList $ueArgs -WindowStyle Normal -PassThru
  $script:Status.UEStarted = $true
  Write-Step "UnrealEditor pid: $($script:UEProc.Id)"

  $script:Status.UEPortListening = Wait-Until "UE listening on TCP 4560" $TcpTimeoutSec { Test-Port4560Listening }
  if (-not $script:Status.UEPortListening) { throw "UE did not listen on TCP 4560" }

  $culture = [System.Globalization.CultureInfo]::InvariantCulture
  $launchEnvironment = @(
    "SZTU_VEHICLE_TYPE=$Vehicle",
    "SZTU_MPC_Z_P=$($MpcZP.ToString($culture))",
    "SZTU_MPC_Z_VEL_P_ACC=$($MpcZVelPAcc.ToString($culture))",
    "SZTU_MPC_Z_VEL_I_ACC=$($MpcZVelIAcc.ToString($culture))",
    "SZTU_MPC_Z_VEL_D_ACC=$($MpcZVelDAcc.ToString($culture))",
    "SZTU_MPC_THR_HOVER=$($MpcThrHover.ToString($culture))",
    "SZTU_EKF2_ABL_LIM=$($Ekf2AblLim.ToString($culture))"
  ) -join " "
  $launchCommand = "$launchEnvironment $WslLaunchScript"

  Write-Step "starting PX4 in WSL"
  $script:PX4Proc = Start-Process -FilePath "wsl.exe" `
    -ArgumentList @("bash", "-lc", "`"$launchCommand`"") `
    -RedirectStandardOutput (Join-Path $script:RunDir "wsl_launch.stdout.log") `
    -RedirectStandardError (Join-Path $script:RunDir "wsl_launch.stderr.log") `
    -WindowStyle Hidden `
    -PassThru
  $script:Status.PX4Started = $true
  Write-Step "wsl px4 launcher pid: $($script:PX4Proc.Id)"

  $script:Status.PX4Connected = Wait-Until "PX4 simulator connected" $Px4TimeoutSec {
    if ($script:PX4Proc.HasExited) {
      throw "WSL PX4 launcher exited before simulator connection (exit $($script:PX4Proc.ExitCode))"
    }
    (Invoke-WslText "test -f /tmp/px4_sitl.log && grep -q 'Simulator connected' /tmp/px4_sitl.log && echo OK || true") -match "OK"
  }
  if (-not $script:Status.PX4Connected) { throw "PX4 did not connect to UE" }

  $script:Status.PX4Ready = Wait-Until "PX4 ready for takeoff" $Px4TimeoutSec {
    if ($script:PX4Proc.HasExited) {
      throw "WSL PX4 launcher exited before readiness (exit $($script:PX4Proc.ExitCode))"
    }
    (Invoke-WslText "test -f /tmp/px4_sitl.log && grep -q 'Ready for takeoff' /tmp/px4_sitl.log && echo OK || true") -match "OK"
  }
  if (-not $script:Status.PX4Ready) { throw "PX4 did not become ready for takeoff" }

  if ($EstimatorSettleSec -gt 0) {
    Write-Step "waiting ${EstimatorSettleSec}s for ground-state estimator convergence"
    Start-Sleep -Seconds $EstimatorSettleSec
  }

  Write-Step "starting UE telemetry listener"
  $patternSeconds = if ($FlightPattern -eq "circle") { $CircleDuration + 2.0 * $CircleEntry } else { 0.0 }
  $computedTelemetrySeconds = [Math]::Max(40, [int]($Climb + $Hold + $Move + $patternSeconds + $Descend + 35))
  $telemetrySeconds = if ($TelemetryDurationSec -gt 0) {
    [Math]::Max(10, $TelemetryDurationSec)
  }
  else {
    $computedTelemetrySeconds
  }
  $script:TelemetryProc = Start-Process -FilePath $PythonExe `
    -ArgumentList @((Join-Path $script:RepoRoot "outputs\listen_telem_long.py"), "$telemetrySeconds") `
    -RedirectStandardOutput (Join-Path $script:RunDir "telemetry.log") `
    -RedirectStandardError (Join-Path $script:RunDir "telemetry.err.log") `
    -WindowStyle Hidden `
    -PassThru
  Start-Sleep -Seconds 1
  if ($script:TelemetryProc.HasExited) {
    $telemetryError = Join-Path $script:RunDir "telemetry.err.log"
    $errorText = if (Test-Path -LiteralPath $telemetryError) {
      (Get-Content -LiteralPath $telemetryError -Raw -ErrorAction SilentlyContinue).Trim()
    }
    else { "no stderr output" }
    throw "UE telemetry listener exited immediately: $errorText"
  }
  $script:Status.TelemetryStarted = $true

  if (-not $SkipFlight) {
    Write-Step "running WSL offboard takeoff script"
    $extraFlightArgs = ""
    if ($HeadwindTest) {
      $extraFlightArgs = "--dx -$HeadwindDistance --move $Move"
    }
    if ($FlightPattern -eq "circle") {
      if ($HeadwindTest) { throw "-HeadwindTest cannot be combined with -FlightPattern circle" }
      $extraFlightArgs = "--circle-radius $CircleRadius --circle-laps $CircleLaps --circle-duration $CircleDuration --circle-entry $CircleEntry"
    }
    $takeoffCmd = "cd ~/szefitl && python3 $WslTakeoffScript --alt $Alt --hold $Hold --climb $Climb --descend $Descend $extraFlightArgs"
    $takeoff = Start-Process -FilePath "wsl.exe" `
      -ArgumentList @("bash", "-lc", $takeoffCmd) `
      -RedirectStandardOutput (Join-Path $script:RunDir "takeoff_offboard.log") `
      -RedirectStandardError (Join-Path $script:RunDir "takeoff_offboard.err.log") `
      -WindowStyle Hidden `
      -PassThru
    $script:Status.FlightScriptRan = $true
    if (-not $takeoff.WaitForExit($FlightTimeoutSec * 1000)) {
      Stop-Process -Id $takeoff.Id -Force -ErrorAction SilentlyContinue
      throw "takeoff script timed out after ${FlightTimeoutSec}s"
    }
    [void]$takeoff.WaitForExit()
    $takeoff.Refresh()
    Write-Step "takeoff script exit code: $($takeoff.ExitCode)"
  }
  else {
    Write-Step "flight skipped by -SkipFlight"
  }

  if ($script:TelemetryProc -and -not $script:TelemetryProc.HasExited) {
    $script:TelemetryProc.WaitForExit(15000) | Out-Null
  }
}

try {
  Start-Validation
}
finally {
  if (-not $DryRun) {
    Copy-WslArtifacts
    Copy-UELog
    Write-Summary
    if (-not $KeepRunning) {
      if ($script:TelemetryProc -and -not $script:TelemetryProc.HasExited) {
        Stop-Process -Id $script:TelemetryProc.Id -Force -ErrorAction SilentlyContinue
      }
      Stop-WslSim
      if ($script:UEProc -and -not $script:UEProc.HasExited) {
        Write-Step "stopping UnrealEditor pid $($script:UEProc.Id)"
        Stop-Process -Id $script:UEProc.Id -Force -ErrorAction SilentlyContinue
      }
    }
    else {
      Write-Step "KeepRunning set; UE/PX4 were left running"
    }
  }
}

if (-not $DryRun -and -not $SkipFlight) {
  Update-StatusFromArtifacts
  $required = @("UEStarted", "UEPortListening", "PX4Started", "PX4Connected", "PX4Ready", "FlightScriptRan", "Armed", "Offboard", "TakeoffDetected", "Disarmed", "TelemetryPass")
  $failed = @($required | Where-Object { -not $script:Status[$_] })
  if ($script:Status.Failsafe) { $failed += "NoFailsafe" }
  $flightResultPath = Join-Path $script:RunDir "flight_analysis.json"
  if (-not (Test-Path -LiteralPath $flightResultPath)) {
    $failed += "FlightAnalysisMissing"
  }
  else {
    $flightResult = Get-Content -LiteralPath $flightResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $flightResult.pass) { $failed += "FlightAnalysis" }
    if ($HeadwindTest) {
      $hasHeadwindMotion = $flightResult.telemetry.PSObject.Properties.Name -contains "headwind_motion_confirmed"
      if (-not $hasHeadwindMotion -or -not $flightResult.telemetry.headwind_motion_confirmed) {
        $failed += "HeadwindMotion"
      }
    }
  }
  if ($FlightPattern -ne "legacy") {
    $patternResultPath = Join-Path $script:RunDir "pattern_analysis.json"
    if (-not (Test-Path -LiteralPath $patternResultPath)) {
      $failed += "PatternAnalysisMissing"
    }
    else {
      $patternResult = Get-Content -LiteralPath $patternResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
      if (-not $patternResult.pass) { $failed += "PatternAnalysis" }
    }
  }
  if ($failed.Count -gt 0) {
    throw "validation failed ($($failed -join ', ')); see $script:RunDir"
  }
}
