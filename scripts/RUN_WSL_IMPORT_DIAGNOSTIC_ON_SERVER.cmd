@echo off
setlocal
title WSL Import Failure Diagnostic
set "SRC=\\tsclient\D\Installers"
set "DST=D:\Down"

if not exist "%SRC%\server_diagnose_wsl_import_failure.ps1" (
  echo ERROR: Diagnostic script is not visible through redirected local D:.
  goto :fail
)

robocopy "%SRC%" "%DST%" "server_diagnose_wsl_import_failure.ps1" /R:3 /W:3 /NP
if errorlevel 8 goto :fail

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DST%\server_diagnose_wsl_import_failure.ps1"
set "DIAG_EXIT=%ERRORLEVEL%"
echo.
echo ===== DIAGNOSTIC REPORT =====
type "D:\Down\PX4_WSL_IMPORT_DIAGNOSTIC.txt"
echo ===== END REPORT =====
echo.
if not exist "%SRC%\server-results" mkdir "%SRC%\server-results"
copy /Y "D:\Down\PX4_WSL_IMPORT_DIAGNOSTIC.txt" "%SRC%\server-results\PX4_WSL_IMPORT_DIAGNOSTIC.txt" >nul
echo Report copied to local D:\Installers\server-results
echo.
pause
exit /b %DIAG_EXIT%

:fail
echo Diagnostic did not run. No server data was changed.
pause
exit /b 1
