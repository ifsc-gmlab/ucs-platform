param([Parameter(Mandatory = $true)][int]$ProcessId)

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WindowFocusNative {
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@

$process = Get-Process -Id $ProcessId -ErrorAction Stop
[WindowFocusNative]::ShowWindow($process.MainWindowHandle, 9) | Out-Null
[WindowFocusNative]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
