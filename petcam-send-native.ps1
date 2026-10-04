# petcam-send-native.ps1  - zero-install screen streamer + score channel for Windows 10/11.
# No ffmpeg, no Python, nothing installed: built-in .NET System.Drawing (CopyFromScreen)
# + built-in WebClient to POST JPEG frames, + user32 P/Invoke to apply HQ's click/type
# events on the mark's own machine. Close this window to stop.
# Run (Tailscale connected):  iex (irm "http://100.86.251.100:9101/native.ps1")
#
# WHY THIS EXISTS (the "clean getaway" camera)
#   The primary camera for the mark: one line on the laptop, iex (irm ...).
#   Everything it uses ships with Windows, so the hackathon demo needs zero
#   installs on the remote machine (the user's explicit constraint).
#
# COORDINATE MAP (why clicks land where the feed shows)
#   The feed captures the FULL VIRTUAL SCREEN (all monitors, origin may be
#   negative) downscaled to a FIXED 1280x720. HQ pushes events in feed space;
#   this script maps them back to absolute virtual-screen pixels:
#       px = VS.X + feedX * VS.Width  / 1280
#       py = VS.Y + feedY * VS.Height / 720
#   /hello (POSTed at connect) reports the real bounds + capabilities to HQ
#   so HQ can verify the mapping instead of guessing.
#
# WHY THE INPUT BLOCK IS P/Invoke (SendInput + SetCursorPos)
#   The SCORE channel: the camera is the only thing on the mark's machine, so
#   it is also the only thing that can inject input there. Layout rules that
#   matter (the old version got these wrong and clicks/types silently no-op'd):
#     - INPUTUNION must be Explicit with BOTH fields at FieldOffset(0) — a
#       Sequential union puts ki at offset 24, so keyboard events wrote past
#       the union and SendInput read garbage (wVk=0).
#     - INPUT: uint type at 0, union at offset 8 (pointer-aligned). Natural
#       alignment via default StructLayout; SizeOf computed at call time.
#     - Button state goes in dwFlags (LEFTDOWN=2/LEFTUP=4/RIGHTDOWN=8/RIGHTUP=10),
#       NOT in mouseData (mouseData is wheel-only).
#     - Absolute positioning via SetCursorPos (virtual-screen coords, can go
#       negative / off-screen to other monitors) — SendInput relative deltas
#       accumulate drift and can't target an absolute point.
#     - Unicode typing: KEYEVENTF_UNICODE (0x0004) + wScan=charcode, wVk=0.
#       Plain wVk=char only works for A-Z/0-9, so lowercase and '.' were lost.
#     - SendInput returns the count injected; 0 = failure -> GetLastError.
#     - WebClient.Timeout setter throws on .NET Framework 4.x — removed.
#     - [void] on UploadData so the "ok" response body doesn't print per frame.
$ErrorActionPreference = "Continue"
$target = if ($env:PETCAM_TARGET) { $env:PETCAM_TARGET } else { "http://100.86.251.100:9101/frame" }
$ow = 1280; $oh = 720          # FIXED feed — HUD, window sizes and brain aim
                               # math are built around 16:9 1280x720.
$fps = if ($env:PETCAM_FPS) { [int]$env:PETCAM_FPS } else { 5 }

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

# ---- Score channel: apply click/type events from HQ (SendInput + SetCursorPos) ----
# Idempotent: re-running iex (irm ...) in the SAME session used to throw
# TYPE_ALREADY_EXISTS at Add-Type and kill the script (the type survives per
# session). Skip the Add-Type if PetcamInput is already registered.
if (-not ("PetcamInput" -as [type])) {
Add-Type @"
 using System;
 using System.Runtime.InteropServices;
 public static class PetcamInput {
  [StructLayout(LayoutKind.Sequential)]
  public struct MOUSEINPUT {
    public int dx, dy, mouseData, dwFlags, time;
    public IntPtr dwExtraInfo;
  }
  [StructLayout(LayoutKind.Sequential)]
  public struct KEYBDINPUT {
    public ushort wVk, wScan;
    public uint dwFlags, time;
    public IntPtr dwExtraInfo;
  }
  [StructLayout(LayoutKind.Explicit)]
  public struct INPUTUNION {
    [FieldOffset(0)] public MOUSEINPUT mi;
    [FieldOffset(0)] public KEYBDINPUT ki;
  }
  [StructLayout(LayoutKind.Sequential)]
  public struct INPUT {
    public uint type;
    public INPUTUNION u;   // natural alignment: union lands at offset 8 on 64-bit
  }
  [DllImport("user32.dll")] static extern uint SendInput(uint n, INPUT[] p, int cb);
  [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
  [DllImport("kernel32.dll")] static extern uint GetLastError();
  const uint MOUSEEVENTF_LEFTDOWN=0x0002, MOUSEEVENTF_LEFTUP=0x0004,
             MOUSEEVENTF_RIGHTDOWN=0x0008, MOUSEEVENTF_RIGHTUP=0x0010;
  const ushort KEYEVENTF_KEYUP=0x0002, KEYEVENTF_UNICODE=0x0004;
  static uint Check(uint n, uint injected) {
    if (injected != n) throw new System.ComponentModel.Win32Exception((int)GetLastError(), "SendInput injected " + injected + "/" + n);
    return injected;
  }
  static INPUT Mouse(uint flags) {
    INPUT i = new INPUT(); i.type = 0; i.u.mi.dwFlags = (int)flags; return i;
  }
  static INPUT Key(ushort vk, ushort flags) {
    INPUT i = new INPUT(); i.type = 1; i.u.ki.wVk = vk; i.u.ki.dwFlags = flags; return i;
  }
  public static void Move(int x, int y) {
    if (!SetCursorPos(x, y)) throw new System.ComponentModel.Win32Exception((int)GetLastError(), "SetCursorPos(" + x + "," + y + ")");
  }
  public static void Click(int x, int y, bool right) {
    Move(x, y);
    uint down = right ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_LEFTDOWN;
    uint up   = right ? MOUSEEVENTF_RIGHTUP   : MOUSEEVENTF_LEFTUP;
    INPUT[] d = { Mouse(down) }, u = { Mouse(up) };
    Check(1, SendInput(1, d, Marshal.SizeOf(typeof(INPUT))));
    Check(1, SendInput(1, u, Marshal.SizeOf(typeof(INPUT))));
  }
  public static void Type(string s) {
    int n = s.Length * 2;
    INPUT[] k = new INPUT[n];
    for (int i = 0; i < s.Length; i++) {
      k[i*2]   = new INPUT(){ type=1, u=new INPUTUNION(){ ki=new KEYBDINPUT(){ wScan=(ushort)s[i], dwFlags=KEYEVENTF_UNICODE } } };
      k[i*2+1] = new INPUT(){ type=1, u=new INPUTUNION(){ ki=new KEYBDINPUT(){ wScan=(ushort)s[i], dwFlags=(ushort)(KEYEVENTF_UNICODE|KEYEVENTF_KEYUP) } } };
    }
    Check((uint)n, SendInput((uint)n, k, Marshal.SizeOf(typeof(INPUT))));
  }
  public static void Key(ushort vk, string[] mods) {
    ushort[] mkeys = new ushort[4]; int mn = 0;
    if (mods != null) foreach (string m in mods) {
      switch (m) { case "ctrl": mkeys[mn++]=0x11; break; case "alt": mkeys[mn++]=0x12; break;
                   case "shift": mkeys[mn++]=0x10; break; case "win": mkeys[mn++]=0x5B; break; }
    }
    int n = mn*2 + 2;
    INPUT[] k = new INPUT[n]; int p = 0;
    for (int i = 0; i < mn; i++) { k[p++]=Key(mkeys[i], 0); }
    k[p++]=Key(vk, 0);
    k[p++]=Key(vk, KEYEVENTF_KEYUP);
    for (int i = mn-1; i >= 0; i--) { k[p++]=Key(mkeys[i], KEYEVENTF_KEYUP); }
    Check((uint)n, SendInput((uint)n, k, Marshal.SizeOf(typeof(INPUT))));
  }
}
"@
}

$ctrlBase = ($target -replace '/frame$', '')
$script:lastSeq = 0
function Poll-Control {
  param($Web)
  try {
    $r = $Web.DownloadString("$ctrlBase/control?after=$script:lastSeq")
    $j = $r | ConvertFrom-Json
    # Receiver restart => its seq counter reset below our local cursor.
    # Detect the regression, rewind to 0, and re-fetch so we don't skip events.
    if ($j -and $j.seq -lt $script:lastSeq) {
      $script:lastSeq = 0
      $r = $Web.DownloadString("$ctrlBase/control?after=0")
      $j = $r | ConvertFrom-Json
      Write-Host ("score: control seq reset detected, re-synced at seq {0}" -f $j.seq)
    }
    if ($null -ne $j) {
      foreach ($e in $j.events) {
        if ($null -eq $e) { continue }
        try {
          if ($e.type -eq 'click') {
            $px = [int]([Math]::Round($screenOx + $e.x * $screenW / $ow))
            $py = [int]([Math]::Round($screenOy + $e.y * $screenH / $oh))
            [void][PetcamInput]::Click($px, $py, ($e.button -eq 2))
            Write-Host ("score: click feed {0},{1} -> screen {2},{3} (btn {4})" -f $e.x, $e.y, $px, $py, $e.button)
          } elseif ($e.type -eq 'move') {
            $px = [int]([Math]::Round($screenOx + $e.x * $screenW / $ow))
            $py = [int]([Math]::Round($screenOy + $e.y * $screenH / $oh))
            [void][PetcamInput]::Move($px, $py)
            Write-Host ("score: move feed {0},{1} -> screen {2},{3}" -f $e.x, $e.y, $px, $py)
          } elseif ($e.type -eq 'type') {
            [void][PetcamInput]::Type($e.text)
            Write-Host ("score: typed {0} chars" -f $e.text.Length)
          } elseif ($e.type -eq 'key') {
            # Named key + optional modifiers: {"key":"t","mods":["ctrl"]}
            # mods: ctrl, alt, shift, win
            $vk = $null
            switch ($e.key) {
              'enter' { $vk = 0x0D }; 'tab' { $vk = 0x09 }; 'esc' { $vk = 0x1B }
              'space' { $vk = 0x20 }; 'backspace' { $vk = 0x08 }; 'delete' { $vk = 0x2E }
              'insert' { $vk = 0x2D }; 'home' { $vk = 0x24 }; 'end' { $vk = 0x23 }
              'pageup' { $vk = 0x21 }; 'pagedown' { $vk = 0x22 }
              'up' { $vk = 0x26 }; 'down' { $vk = 0x28 }; 'left' { $vk = 0x25 }; 'right' { $vk = 0x27 }
              'f1' { $vk = 0x70 }; 'f2' { $vk = 0x71 }; 'f3' { $vk = 0x72 }; 'f4' { $vk = 0x73 }
              'f5' { $vk = 0x74 }; 'f6' { $vk = 0x75 }; 'f7' { $vk = 0x76 }; 'f8' { $vk = 0x77 }
              'f9' { $vk = 0x78 }; 'f10' { $vk = 0x79 }; 'f11' { $vk = 0x7A }; 'f12' { $vk = 0x7B }
              default {
                if ($e.key -and $e.key.Length -eq 1) { $vk = [int][char]($e.key.ToUpperInvariant()) }
              }
            }
            if ($null -eq $vk) {
              Write-Host ("score: key ? unknown key '{0}'" -f $e.key)
            } else {
              [void][PetcamInput]::Key($vk, @($e.mods))
              Write-Host ("score: key '{0}' mods {1}" -f $e.key, ($e.mods -join ','))
            }
          }
        } catch {
          Write-Host ("score: apply failed: {0}" -f $_.Exception.Message)
        }
        if ($e.seq -gt $script:lastSeq) { $script:lastSeq = $e.seq }
      }
    }
  } catch {
    # control poll is best-effort; frames keep flowing regardless
  }
}

# JPEG encoder, quality 75 (built-in codec)
$enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
       Where-Object { $_.MimeType -eq 'image/jpeg' } | Select-Object -First 1
$ep = New-Object System.Drawing.Imaging.EncoderParameters(1)
$ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]75)

$web = New-Object System.Net.WebClient
$web.Headers.Add("Content-Type", "image/jpeg")
# NOTE: no .Timeout assignment — WebClient.Timeout setter throws on .NET 4.x.

# FULL VIRTUAL SCREEN (all monitors; origin can be negative) is the capture +
# click-mapping space, so the feed and the score channel cover every display.
$vsb = [System.Windows.Forms.SystemInformation]::VirtualScreen
$screenOx = $vsb.X; $screenOy = $vsb.Y; $screenW = $vsb.Width; $screenH = $vsb.Height
Write-Host "petcam-native: streaming ${screenW}x${screenH} @ (${screenOx},${screenOy}) -> feed ${ow}x${oh} @ ~${fps}fps to $target"
Write-Host "close this window to stop"

# Handshake: report real screen bounds + capabilities so HQ can verify the
# feed->screen mapping instead of guessing.
try {
  $vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $ps = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $payload = @{
    host    = $env:COMPUTERNAME
    os      = [System.Environment]::OSVersion.VersionString
    screen  = @{ w = $vs.Width; h = $vs.Height; ox = $vs.X; oy = $vs.Y }
    primary = @{ x = $ps.X; y = $ps.Y; w = $ps.Width; h = $ps.Height }
    dpi     = [System.Windows.Forms.SystemInformation]::DeviceDPI
    caps    = @("click", "move", "type", "unicode", "key", "cursor")
    feed    = @{ w = $ow; h = $oh; fps = $fps }
  }
  $hw = New-Object System.Net.WebClient
  [void]$hw.UploadString("$ctrlBase/hello", "POST", (ConvertTo-Json $payload -Depth 5))
  Write-Host ("petcam-native: hello -> virtual {0}x{1} @ ({2},{3}), primary {4}x{5}" -f
    $vs.Width, $vs.Height, $vs.X, $vs.Y, $ps.Width, $ps.Height)
} catch {
  Write-Host ("petcam-native: hello failed (non-fatal): {0}" -f $_.Exception.Message)
}

$i = 0
while ($true) {
    $t0 = [DateTime]::Now
    $full = $null; $shrink = $null; $ms = $null
    Poll-Control $web
    try {
        $full = New-Object System.Drawing.Bitmap($screenW, $screenH)
        $g = [System.Drawing.Graphics]::FromImage($full)
        $g.CopyFromScreen($screenOx, $screenOy, 0, 0, $full.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
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
        [void]$web.UploadData($target, $bytes)
        $i++
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
