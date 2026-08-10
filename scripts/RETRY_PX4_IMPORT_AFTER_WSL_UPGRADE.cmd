@echo off
setlocal
title IFSC Server - Resume PX4 Import after WSL Upgrade

echo ============================================================
echo IFSC server: WSL 2.6.1 is installed; resume PX4 import
echo ============================================================
echo.
echo No installer or UAC is required in this resume pass.
echo Keep this window open. Import can take several minutes.
echo UE, PX4, and QGC will NOT be started.
echo.

set "SCRIPT=\\tsclient\D\Installers\server_install_wsl261_retry_px4.ps1"
if not exist "%SCRIPT%" (
  echo ERROR: Cannot find %SCRIPT%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -SkipMsi
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
echo Reports are copied to local D:\Installers\server-results
pause
exit /b %RC%
