param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WindowCaptureNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")]
    public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
}
"@

# Use physical desktop pixels.  UE and QGC can otherwise report virtualized
# coordinates at 150% display scaling while CopyFromScreen uses physical ones.
if (-not [WindowCaptureNative]::SetProcessDpiAwarenessContext([IntPtr](-4))) {
    [void][WindowCaptureNative]::SetProcessDPIAware()
}

$process = Get-Process -Id $ProcessId -ErrorAction Stop
$rect = New-Object WindowCaptureNative+RECT
if (-not [WindowCaptureNative]::GetWindowRect($process.MainWindowHandle, [ref]$rect)) {
    throw "Could not read the UE window rectangle."
}

$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) {
    throw "UE window has invalid dimensions: ${width}x${height}."
}

$bitmap = New-Object System.Drawing.Bitmap $width, $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $fullOutputPath = [System.IO.Path]::GetFullPath($OutputPath)
    $parent = [System.IO.Path]::GetDirectoryName($fullOutputPath)
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    $bitmap.Save($fullOutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output $fullOutputPath
}
finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
