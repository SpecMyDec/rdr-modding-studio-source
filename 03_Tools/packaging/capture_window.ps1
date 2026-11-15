# Capture the app's own window to a PNG, so "it renders" is a picture and not
# a claim. Uses PrintWindow, which asks the window to draw itself -- that works
# for a WebView2 surface where a plain screen grab of an occluded window would
# come back black.
#
#   .\capture_window.ps1 -Title "RDR Modding Studio" -Out shot.png

param(
  [string]$Title = "RDR Modding Studio",
  [Parameter(Mandatory=$true)][string]$Out
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int v);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@

# This screen is 1920x1080 at 125%. A DPI-unaware process is handed virtualized
# coordinates -- GetWindowRect reports 900 for a window that is 1125 physical
# pixels wide -- so the capture bitmap came out too small and the right-hand
# side of every window looked clipped. It was the screenshot that was cropped,
# not the app. Ask for real pixels before measuring anything.
try { [Win]::SetProcessDpiAwareness(2) | Out-Null } catch {}

$p = Get-Process | Where-Object { $_.MainWindowTitle -like "*$Title*" } | Select-Object -First 1
if (-not $p) { throw "no window titled like '$Title'" }
$h = $p.MainWindowHandle

[Win]::ShowWindow($h, 9) | Out-Null      # SW_RESTORE
[Win]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 900            # let WebView2 repaint

$r = New-Object Win+RECT
[Win]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.R - $r.L; $ht = $r.B - $r.T
if ($w -le 0 -or $ht -le 0) { throw "window has no size" }

$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
# 2 = PW_RENDERFULLCONTENT, required for hardware-composited WebView2 content
[Win]::PrintWindow($h, $dc, 2) | Out-Null
$g.ReleaseHdc($dc); $g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

"{0}  ({1}x{2}, {3:N0} KB)" -f $Out, $w, $ht, ((Get-Item $Out).Length/1KB)
