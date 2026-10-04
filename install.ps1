# petcam install.ps1  - one-time bootstrap on the WINDOWS LAPTOP.
# Usage (PowerShell, Tailscale must be connected):
#   powershell -ExecutionPolicy Bypass -File "$env:TEMP\petcam-install.ps1"
# Installs ffmpeg + Python (via winget, UAC prompt may appear), downloads the
# sender, writes feed.bat, and opens a "petcam feed" window that streams your
# desktop to the 5090. Close that window to stop.
$ErrorActionPreference = "Continue"
$Host_ = "100.86.251.100:9101"
$Dir = "$env:USERPROFILE\petcam"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null

# --- ffmpeg ---
$ffmpeg = $null
try { $ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source } catch { $null }
if (-not $ffmpeg) {
    $winget = $null
    try { $winget = (Get-Command winget -ErrorAction Stop).Source } catch { $null }
    if ($winget) {
        Write-Host "[1/4] installing ffmpeg via winget (UAC prompt may appear)..."
        & $winget install -e --id Gyan.FFmpeg 2>&1 | Out-Null
    } else {
        Write-Host "winget not found - install ffmpeg (Gyan) manually and re-run." -ForegroundColor Yellow
    }
    try { $ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source } catch { $null }
    if (-not $ffmpeg) {
        foreach ($c in @("$env:LOCALAPPDATA\Microsoft\WinGet\Links", "$env:USERPROFILE\scoop\shims", "C:\ProgramData\chocolatey\bin")) {
            if (Test-Path (Join-Path $c "ffmpeg.exe")) { $ffmpeg = Join-Path $c "ffmpeg.exe"; break }
        }
    }
}
if (-not $ffmpeg) { Write-Host "ffmpeg not found - install it (winget install Gyan.FFmpeg) and re-run." -ForegroundColor Red; exit 1 }
Write-Host "ffmpeg: $ffmpeg"

# --- python ---
$py = $null
try { $py = (Get-Command python -ErrorAction Stop).Source } catch { $null }
if (-not $py) {
    $winget = $null
    try { $winget = (Get-Command winget -ErrorAction Stop).Source } catch { $null }
    if ($winget) {
        Write-Host "[2/4] installing Python via winget (UAC prompt may appear)..."
        & $winget install -e --id Python.Python.3.12 2>&1 | Out-Null
    }
    try { $py = (Get-Command python -ErrorAction Stop).Source } catch { $null }
    if (-not $py) {
        $probe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        if (Test-Path $probe) { $py = $probe }
    }
}
if (-not $py) { Write-Host "Python not found - install 3.12+ and re-run." -ForegroundColor Red; exit 1 }
Write-Host "python: $py"

# --- sender (raw bytes; no PowerShell object pipeline -> cmd.exe does the piping) ---
Write-Host "[3/4] downloading petcam-send.py from $Host_ ..."
try { Invoke-WebRequest -UseBasicParsing "http://${Host_}/petcam-send.py" -OutFile (Join-Path $Dir "petcam-send.py") -TimeoutSec 15 } catch {
    Write-Host "download failed: $_  (is Tailscale connected? is the 5090 on?)" -ForegroundColor Red
    exit 1
}
$sendPy = Join-Path $Dir "petcam-send.py"

# --- feed.bat (cmd pipes raw bytes; powershell would corrupt them) ---
$bat = Join-Path $Dir "feed.bat"
$lines = @(
    '@echo off',
    'title petcam feed',
    "echo petcam feed: streaming to ${Host_} - close this window to stop",
    "call `"$ffmpeg`" -hide_banner -loglevel error -f gdigrab -framerate 15 -i desktop -vf scale=1280:-2 -q:v 5 -an -f mpjpeg pipe:1 | `"$py`" -u `"$sendPy`"",
    'echo.',
    'echo feed stopped.',
    'pause'
)
Set-Content -Path $bat -Value $lines -Encoding ASCII

# --- start it in its own window ---
Write-Host "[4/4] starting the feed in a new window: 'petcam feed'"
Start-Process -WindowStyle Normal -FilePath $bat
Write-Host ""
Write-Host "Done - the 'petcam feed' window is now streaming your desktop."
Write-Host "On the 5090 the pet should switch to: 'watching the laptop'."
Write-Host "Stop: close the 'petcam feed' window. Re-start: run $bat"
