#requires -version 5.1
<#
.SYNOPSIS
Emergency stop helper for PX4 SITL-HIL test runs.

.DESCRIPTION
By default this sends a MAVLink force-disarm command to PX4 through WSL. Use
-KillSim only when you also want to stop the WSL PX4 launcher and relay.

Examples:
  powershell -NoProfile -ExecutionPolicy Bypass -File outputs\px4_emergency_stop.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File outputs\px4_emergency_stop.ps1 -KillSim
#>
[CmdletBinding()]
param(
  [switch]$KillSim
)

$ErrorActionPreference = "Continue"

Write-Host "[stop] sending MAVLink force disarm through WSL"
& wsl.exe bash -lc "cd ~/szefitl && python3 px4_force_disarm.py"
$code = $LASTEXITCODE

if ($KillSim) {
  Write-Host "[stop] killing PX4 launcher, px4 binary, and MAVLink relay"
  & wsl.exe bash -lc "pkill -TERM -f '[m]avlink_relay.py' 2>/dev/null || true; pkill -TERM -f '[p]x4_sitl_default/bin/px4' 2>/dev/null || true; pkill -TERM -f '[s]tart_px4_ue_hil.sh' 2>/dev/null || true; pkill -TERM -f '[p]x4_cmd_fifo' 2>/dev/null || true; sleep 1; pkill -KILL -f '[m]avlink_relay.py' 2>/dev/null || true; pkill -KILL -f '[p]x4_sitl_default/bin/px4' 2>/dev/null || true; pkill -KILL -f '[s]tart_px4_ue_hil.sh' 2>/dev/null || true; pkill -KILL -f '[p]x4_cmd_fifo' 2>/dev/null || true; rm -f /tmp/px4_cmd_fifo 2>/dev/null || true"
}

exit $code
