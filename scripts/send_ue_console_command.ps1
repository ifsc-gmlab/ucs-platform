#requires -version 5.1
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$ConsoleCommand,
  [ValidateRange(0, 10)]
  [int]$DelaySeconds = 2,
  [string]$ScreenshotPath = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class SZTUConsoleInput {
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte key, byte scan, uint flags, UIntPtr extra);
}
'@

[void][SZTUConsoleInput]::SetProcessDpiAwarenessContext([IntPtr](-4))
Start-Sleep -Seconds $DelaySeconds
Get-Process ChatGPT -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.MainWindowHandle -ne [IntPtr]::Zero) {
    # SW_MINIMIZE
    Add-Type -Name NativeShow -Namespace SZTU -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
'@ -ErrorAction SilentlyContinue
    [void][SZTU.NativeShow]::ShowWindow($_.MainWindowHandle, 6)
  }
}

$ue = Get-Process UnrealEditor -ErrorAction Stop |
  Sort-Object StartTime -Descending | Select-Object -First 1
[void][SZTUConsoleInput]::SetForegroundWindow($ue.MainWindowHandle)
Start-Sleep -Milliseconds 500

# VK_OEM_3 opens the UE console regardless of SendKeys' special handling of ~.
[SZTUConsoleInput]::keybd_event(0xC0, 0, 0, [UIntPtr]::Zero)
[SZTUConsoleInput]::keybd_event(0xC0, 0, 2, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 300
[System.Windows.Forms.SendKeys]::SendWait($ConsoleCommand)
[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
Start-Sleep -Milliseconds 500
[System.Windows.Forms.SendKeys]::SendWait("{ESC}")

if ($ScreenshotPath) {
  Start-Sleep -Seconds 2
  $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $fullPath = [System.IO.Path]::GetFullPath($ScreenshotPath)
    $bitmap.Save($fullPath, [System.Drawing.Imaging.ImageFormat]::Png)
  }
  finally {
    $graphics.Dispose()
    $bitmap.Dispose()
  }
}
