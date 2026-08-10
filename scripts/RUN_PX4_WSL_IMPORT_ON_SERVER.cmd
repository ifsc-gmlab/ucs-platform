@echo off
setlocal
title PX4 Offline WSL Import
set "SRC=\\tsclient\D\Installers"
set "DST=D:\Down"

echo Source: %SRC%
echo Target: %DST%
echo.

if not exist "%SRC%\PX4_Ubuntu_20260731.tar.gz" (
  echo ERROR: PX4 archive is not visible through the redirected local D drive.
  goto :fail
)
if not exist "%SRC%\server_import_px4_offline.ps1" (
  echo ERROR: Import script is missing from the redirected local D drive.
  goto :fail
)

if not exist "%DST%" mkdir "%DST%"

echo [1/3] Copying the import script...
robocopy "%SRC%" "%DST%" "server_import_px4_offline.ps1" /R:3 /W:3 /NP
if errorlevel 8 goto :fail

echo.
echo [2/3] Copying the 8.28 GB PX4 WSL image with restart support...
robocopy "%SRC%" "%DST%" "PX4_Ubuntu_20260731.tar.gz" /J /Z /R:3 /W:5 /NP
if errorlevel 8 goto :fail

echo.
echo [3/3] Verifying SHA-256, importing PX4-Ubuntu, and validating PX4...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DST%\server_import_px4_offline.ps1"
set "PX4_IMPORT_EXIT=%ERRORLEVEL%"
echo.
if "%PX4_IMPORT_EXIT%"=="0" (
  echo PX4 WSL import PASS.
) else (
  echo PX4 WSL import FAILED with exit code %PX4_IMPORT_EXIT%.
  echo See D:\Down\PX4_WSL_IMPORT_REPORT.txt
)
echo.
pause
exit /b %PX4_IMPORT_EXIT%

:fail
echo.
echo PX4 WSL migration failed before import.
echo Review the messages above. Existing WSL distributions were not overwritten.
echo.
pause
exit /b 1
