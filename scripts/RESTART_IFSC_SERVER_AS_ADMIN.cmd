@echo off
setlocal
title IFSC Server Restart - Administrator Required
echo Requesting administrator permission to restart the IFSC server...
echo Complete the UAC prompt. The RDP connection will then disconnect.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'shutdown.exe' -ArgumentList '/r','/t','0','/d','p:2:4','/c','Finish WSL 2.6.1 service activation' -Verb RunAs"
if errorlevel 1 (
  echo.
  echo Restart request was not elevated or was cancelled.
  pause
  exit /b 1
)
exit /b 0
