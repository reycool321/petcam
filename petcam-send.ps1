# petcam-send.ps1 — Windows send side, zero installs (PowerShell 5.1+ + curl.exe,
# which ships with Windows 10 1803+). Watches a folder of JPEGs written by
# ffmpeg (image2) and POSTs each finished frame to the receiver.
#
# Usage (two commands in one window):
#   ffmpeg -f gdigrab -framerate 15 -i desktop -vf scale=1280:-2 -q:v 5 -start_number 1 C:\petcam\frames\%08d.jpg
#   powershell -ExecutionPolicy Bypass -File petcam-send.ps1
# (or let petcam-feed.ps1 do both)
$target = if ($env:PETCAM_TARGET) { $env:PETCAM_TARGET } else { "http://100.86.251.100:9101/frame" }
$dir = if ($env:PETCAM_DIR) { $env:PETCAM_DIR } else { "$env:TEMP\petcam-frames" }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Write-Host "petcam-send: watching $dir -> $target"
$seen = @{}
while ($true) {
    Start-Sleep -Milliseconds 150
    Get-ChildItem -Path $dir -Filter *.jpg -File | ForEach-Object {
        $name = $_.Name
        if ($seen.ContainsKey($name)) { return }
        # consider the file "finished" when its size is stable across two polls
        $sz = $_.Length
        if ($seen[$name] -ne $null) {
            if ($seen[$name] -eq $sz -and $sz -gt 128) {
                try {
                    & curl.exe -s -m 3 -X POST -H "Content-Type: image/jpeg" --data-binary "@$_" $target | Out-Null
                    $seen[$name] = $sz
                    Remove-Item $_ -Force -ErrorAction SilentlyContinue
                } catch { $seen[$name] = $sz }
            } else { $seen[$name] = $sz }
        } else { $seen[$name] = $sz }
    }
    # prune stale entries (files we already deleted)
    $existing = @(Get-ChildItem -Path $dir -Filter *.jpg -File).Name
    foreach ($k in @($seen.Keys)) { if ($existing -notcontains $k) { $seen.Remove($k) } }
}
