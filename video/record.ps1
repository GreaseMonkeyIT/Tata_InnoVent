# record.ps1: capture the full screen for the demo video (POC_SCRIPT.md section 4, LOG-096).
#   powershell -ExecutionPolicy Bypass -File video\record.ps1 start take1    # starts ffmpeg in its own window
#   powershell -ExecutionPolicy Bypass -File video\record.ps1 stop           # stops it
#   ... record.ps1 start take3 -NoCursor    # a take that Claude drives: no still mouse pointer in the frame
# ffmpeg records the desktop at 30 frames per second into a Matroska file (video\takes\<name>.mkv).
# A Matroska file stays playable when the capture stops abruptly, so stop ends ffmpeg directly.
# No audio: the narrator records the voice after the edit (POC_SCRIPT.md section 6).
param([Parameter(Mandatory = $true)][ValidateSet("start", "stop")][string]$Action, [string]$Name = "take1",
      [switch]$NoCursor)

$dir = Join-Path $PSScriptRoot "takes"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
if ($Action -eq "start") {
  $out = Join-Path $dir "$Name.mkv"
  if (Test-Path $out) { Write-Output "STOP $out exists. Pick another name."; exit 1 }
  $mouse = if ($NoCursor) { 0 } else { 1 }
  $ffargs = "-hide_banner -f gdigrab -framerate 30 -draw_mouse $mouse -i desktop -c:v libx264 -preset ultrafast -crf 18 `"$out`""
  Start-Process -FilePath "ffmpeg" -ArgumentList $ffargs -WindowStyle Minimized
  Write-Output ("recording to {0} since {1}" -f $out, (Get-Date -Format "HH:mm:ss"))
} else {
  $p = Get-Process ffmpeg -ErrorAction SilentlyContinue
  if (-not $p) { Write-Output "no ffmpeg is running"; exit 0 }
  $p | Stop-Process
  Write-Output ("stopped at {0}" -f (Get-Date -Format "HH:mm:ss"))
}
