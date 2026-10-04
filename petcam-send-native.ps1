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

# ---- Score channel: apply click/type events from HQ via SendInput ----
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class PetcamInput {
  [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int dx, dy, mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Sequential)] struct INPUTUNION { public MOUSEINPUT mi; public KEYBDINPUT ki; }
  [StructLayout(LayoutKind.Explicit)] public struct INPUT { [FieldOffset(0)] public uint type; [FieldOffset(8)] public INPUTUNION u; }
  [DllImport("user32.dll")] static extern uint SendInput(uint n, INPUT[] p, int cb);
  const uint MI_MOVE=0x1, MI_DOWN=0x2, MI_UP=0x4, MK_LBUTTON=0x0001, MK_RBUTTON=0x0002;
  const ushort KEYEVENTF_KEYUP=0x0002;
  static INPUT Mouse(uint flags) { INPUT i=new INPUT(); i.type=0; i.u.mi.dwFlags=(int)flags; i.u.mi.mouseData=(int)(flags&0x108000L); return i; }
  public static void Click(int x, int y, bool right) {
    INPUT[] m = { Mouse(MI_MOVE) }; m[0].u.mi.dx=x; m[0].u.mi.dy=y; SendInput(1,m,Marshal.SizeOf(typeof(INPUT)));
    uint btn = right?MK_RBUTTON:MK_LBUTTON;
    INPUT[] d = { Mouse(MI_DOWN) }; d[0].u.mi.mouseData=(int)btn; SendInput(1,d,Marshal.SizeOf(typeof(INPUT)));
    INPUT[] u = { Mouse(MI_UP) }; u[0].u.mi.mouseData=(int)btn; SendInput(1,u,Marshal.SizeOf(typeof(INPUT)));
  }
  public static void Move(int x, int y) {
    INPUT[] m = { Mouse(MI_MOVE) }; m[0].u.mi.dx=x; m[0].u.mi.dy=y; SendInput(1,m,Marshal.SizeOf(typeof(INPUT)));
  }
  public static void Type(string s) {
    INPUT[] k = new INPUT[s.Length*2]; int n=0;
    foreach (char ch in s) {
      k[n++]=new INPUT(){type=1, u=new INPUTUNION(){ki=new KEYBDINPUT(){wVk=(ushort)ch, dwFlags=0}}};
      k[n++]=new INPUT(){type=1, u=new INPUTUNION(){ki=new KEYBDINPUT(){wVk=(ushort)ch, dwFlags=KEYEVENTF_KEYUP}}};
    }
    SendInput((uint)n,k,Marshal.SizeOf(typeof(INPUT)));
  }
}
"@
$ctrlBase = ($target -replace '/frame$', '')
$lastSeq = 0
function Poll-Control {
  param($Web)
  try {
    $r = $Web.DownloadString("$ctrlBase/control?after=$lastSeq")
    $j = $r | ConvertFrom-Json
    foreach ($e in $j.events) {
      if ($e.type -eq 'click') {
        $px = [int]([Math]::Round($e.x * $screen.Width / 1280.0))
        $py = [int]([Math]::Round($e.y * $screen.Height / 720.0))
        [PetcamInput]::Click($px, $py, ($e.button -eq 2))
        Write-Host ("score: click feed {0},{1} -> screen {2},{3} (btn {4})" -f $e.x, $e.y, $px, $py, $e.button)
      } elseif ($e.type -eq 'type') {
        [PetcamInput]::Type($e.text)
        Write-Host ("score: typed {0} chars" -f $e.text.Length)
      }
      if ($e.seq -gt $lastSeq) { $script:lastSeq = $e.seq }
    }
  } catch {}
}

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
    Poll-Control $web
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
