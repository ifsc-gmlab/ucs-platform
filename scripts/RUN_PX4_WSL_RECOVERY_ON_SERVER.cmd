@echo off
setlocal
title PX4 WSL Safe Recovery
set "SRC=\\tsclient\D\Installers"
set "DST=D:\Down"

echo PX4 WSL safe recovery
echo - Reuses the server-side SHA-256 PASS result
echo - Preserves D:\WSL\PX4-Ubuntu from the failed attempt
echo - Decompresses only on server D: and imports from a normal tar
echo.

if not exist "%SRC%\server_recover_px4_wsl_import.ps1" (
  echo ERROR: Recovery script is not visible through redirected local D:.
  goto :fail
)
if not exist "%DST%\PX4_Ubuntu_20260731.tar.gz" (
  echo ERROR: Server archive is missing: %DST%\PX4_Ubuntu_20260731.tar.gz
  goto :fail
)

robocopy "%SRC%" "%DST%" "server_recover_px4_wsl_import.ps1" /R:3 /W:3 /NP
if errorlevel 8 goto :fail

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DST%\server_recover_px4_wsl_import.ps1"
set "RECOVERY_EXIT=%ERRORLEVEL%"
echo.
if "%RECOVERY_EXIT%"=="0" (
  echo PX4 WSL recovery PASS.
) else (
  echo PX4 WSL recovery FAILED with exit code %RECOVERY_EXIT%.
  echo See D:\Down\PX4_WSL_RECOVERY_REPORT.txt
)
echo.
pause
exit /b %RECOVERY_EXIT%

:fail
echo.
echo Recovery did not start. Existing WSL data was not removed.
echo.
pause
exit /b 1
