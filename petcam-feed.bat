@echo off
:: petcam-feed.bat — run this ON THE LAPTOP (Windows), next to petcam-send.py.
:: Streams the Windows desktop to the 5090 over Tailscale (~150ms latency).
:: Requirements: Tailscale connected + python + ffmpeg on PATH
::   (winget install Gyan.FFmpeg  and/or  winget install Python.Python.3.12)
set RATIO=15
echo petcam: streaming desktop at ~%RATIO% fps to 100.86.251.100:9101
echo Keep this window open while the pet should watch. Ctrl+C to stop.
ffmpeg -hide_banner -loglevel error -f gdigrab -framerate %RATIO% -i desktop ^
  -vf scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,format=mjpeg -q:v 5 ^
  -f mpjpeg pipe:1 ^
  | python "%~dp0petcam-send.py"
pause
