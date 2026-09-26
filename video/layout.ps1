# layout.ps1: place the demo windows for the video (POC_SCRIPT.md section 2, LOG-096).
#   powershell -ExecutionPolicy Bypass -File video\layout.ps1 A    # the console, full screen (the tour)
#   powershell -ExecutionPolicy Bypass -File video\layout.ps1 B    # console left, Grafana top right, feed bottom right
# The windows are found by their title: the console "Causal AIOps", Grafana "Grafana", the feed
# "VISR SCADA feed". Open each one as an app or kiosk window first, so there is no address bar.
# The screen is 1920x1080. SCREEN_W and SCREEN_H in the environment change that.
# CONSOLE_TITLE picks the console window when two windows show the console (default "Causal AIOps").
# CONSOLE_TOP moves that many pixels of a normal browser window (the tab strip and the address bar)
# above the screen top, so only the page shows. A take that Claude drives uses a normal Chrome window.
param([Parameter(Mandatory = $true)][ValidateSet("A", "B")][string]$Layout)

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class Win {
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hgt, bool repaint);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public static IntPtr Find(string part) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, p) => {
      if (!IsWindowVisible(h)) return true;
      var sb = new StringBuilder(512);
      GetWindowText(h, sb, 512);
      if (sb.ToString().IndexOf(part, StringComparison.OrdinalIgnoreCase) >= 0) { found = h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
"@

$W = if ($env:SCREEN_W) { [int]$env:SCREEN_W } else { 1920 }
$H = if ($env:SCREEN_H) { [int]$env:SCREEN_H } else { 1080 }
$SW_RESTORE = 9; $SW_MINIMIZE = 6

function Place($title, $x, $y, $w, $h) {
  $hwnd = [Win]::Find($title)
  if ($hwnd -eq [IntPtr]::Zero) { Write-Output "not found: a window titled '$title'"; return }
  [void][Win]::ShowWindow($hwnd, $SW_RESTORE)
  [void][Win]::MoveWindow($hwnd, $x, $y, $w, $h, $true)
  Write-Output ("placed '{0}' at {1},{2} size {3}x{4}" -f $title, $x, $y, $w, $h)
}
function Hide($title) {
  $hwnd = [Win]::Find($title)
  if ($hwnd -ne [IntPtr]::Zero) { [void][Win]::ShowWindow($hwnd, $SW_MINIMIZE) }
}

$CT = if ($env:CONSOLE_TITLE) { $env:CONSOLE_TITLE } else { "Causal AIOps" }
$TOP = if ($env:CONSOLE_TOP) { [int]$env:CONSOLE_TOP } else { 0 }
$left = [int]($W * 2 / 3)          # 1280 on a 1920 screen
if ($Layout -eq "A") {
  Hide "Grafana"
  Hide "VISR SCADA feed"
  Place $CT 0 (-$TOP) $W ($H + $TOP)
} else {
  Place $CT 0 (-$TOP) $left ($H + $TOP)
  Place "Grafana" $left 0 ($W - $left) ([int]($H / 2))
  Place "VISR SCADA feed" $left ([int]($H / 2)) ($W - $left) ($H - [int]($H / 2))
}
$c = [Win]::Find($CT)
if ($c -ne [IntPtr]::Zero) { [void][Win]::SetForegroundWindow($c) }
