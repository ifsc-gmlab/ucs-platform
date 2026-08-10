@echo off
setlocal
title IFSC Server - Install WSL 2.6.1 and Retry PX4 Import

echo ============================================================
echo IFSC server: install Microsoft-signed WSL 2.6.1, then retry PX4 import
echo ============================================================
echo.
echo This launcher copies only from the redirected local D drive.
echo The PX4 tar already on server D:\Down is reused.
echo UE, PX4, and QGC will NOT be started.
echo.

set "SCRIPT=\\tsclient\D\Installers\server_install_wsl261_retry_px4.ps1"
if not exist "%SCRIPT%" (
  echo ERROR: Cannot find %SCRIPT%
  echo Confirm local drive D is redirected into this RDP session.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
echo Report: D:\Down\PX4_WSL_261_INSTALL_AND_IMPORT_REPORT.txt
echo Transcript: D:\Down\PX4_WSL_261_INSTALL_AND_IMPORT_TRANSCRIPT.txt
echo.
if not "%RC%"=="0" echo If a reboot was requested, reboot once and run this launcher again.
pause
exit /b %RC%
