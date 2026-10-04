# petcam-send-native.ps1  - zero-install screen streamer for Windows 10/11.
# No ffmpeg, no Python, nothing installed: uses built-in .NET System.Drawing
# (CopyFromScreen) + built-in WebClient to POST JPEG frames to the receiver.
# ~5 fps @ 1280x720, plenty for watching. Close this window to stop.
# Run (Tailscale connected):  powershell -ExecutionPolicy Bypass -File petcam-send-native.ps1
$ErrorActionPreference = "Continue"
$target = if ($env:PETCAM_TARGET) { $env:PETCAM_TARGET } else { "http://100.86.251.100:9101/frame" }
$ow = 1280; $oh = 720          # output resolution (laptop screen is downscaled)
$fps = 5

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

# JPEG encoder, quality 75 (built-in codec)
$enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
       Where-Object { $_.MimeType -eq "image/jpeg" } | Select-Object -First 1
$ep = New-Object System.Drawing.Imaging.EncoderParameters(1)
$ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]75)

$web = New-Object System.Net.WebClient
$web.Headers.Add("Content-Type", "image/jpeg")
$web.Timeout = 4000

$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
Write-Host "petcam-native: streaming ${screen.Width}x${screen.Height} -> ${ow}x${oh} @ ~${fps}fps to $target"
Write-Host "close this window to stop"

$i = 0
while ($true) {
    $t0 = [DateTime]::Now
    $full = $null; $shrink = $null; $ms = $null
    try {
        $full = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
        $g = [System.Drawing.Graphics]::FromImage($full)
        $g.CopyFromScreen($screen.X, $screen.Y, 0, 0, $full.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
        $g.Dispose()
        $shrink = New-Object System.Drawing.Bitmap($ow, $oh, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $gs = [System.Drawing.Graphics]::FromImage($shrink)
        $gs.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::Bilinear
        $gs.DrawImage($full, 0, 0, $ow, $oh)
        $gs.Dispose()
        $full.Dispose(); $full = $null
        $ms = New-Object System.IO.MemoryStream
        $shrink.Save($ms, $enc, $ep)
        $shrink.Dispose(); $shrink = $null
        $bytes = $ms.ToArray()
        $web.UploadData($target, $bytes)
        $i++
        if ($i % 15 -eq 1) { Write-Host ("petcam-native: {0} frames sent" -f $i) }
    } catch {
        Write-Host ("petcam-native: frame error: {0}" -f $_.Exception.Message)
    } finally {
        if ($ms)   { $ms.Dispose() }
        if ($full) { $full.Dispose() }
        if ($shrink) { $shrink.Dispose() }
    }
    $elapsed = ([DateTime]::Now - $t0).TotalMilliseconds
    $sleepMs = [Math]::Max(150, [int](1000.0 / $fps - $elapsed))
    Start-Sleep -Milliseconds $sleepMs
}
