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
#     - INPUTUNION must be Explicit with BOTH fields at FieldOffset(0) -- a
#       Sequential union puts ki at offset 24, so keyboard events wrote past
#       the union and SendInput read garbage (wVk=0).
#     - INPUT: uint type at 0, union at offset 8 (pointer-aligned). Natural
#       alignment via default StructLayout; SizeOf computed at call time.
#     - Button state goes in dwFlags (LEFTDOWN=2/LEFTUP=4/RIGHTDOWN=8/RIGHTUP=10),
#       NOT in mouseData (mouseData is wheel-only).
#     - Absolute positioning via SetCursorPos (virtual-screen coords, can go
#       negative / off-screen to other monitors) -- SendInput relative deltas
#       accumulate drift and can't target an absolute point.
#     - Unicode typing: KEYEVENTF_UNICODE (0x0004) + wScan=charcode, wVk=0.
#       Plain wVk=char only works for A-Z/0-9, so lowercase and '.' were lost.
#     - SendInput returns the count injected; 0 = failure -> GetLastError.
#     - WebClient.Timeout setter throws on .NET Framework 4.x -- removed.
#     - [void] on UploadData so the "ok" response body doesn't print per frame.
$ErrorActionPreference = "Continue"
$target = if ($env:PETCAM_TARGET) { $env:PETCAM_TARGET } else { "http://100.86.251.100:9101/frame" }
$ow = 1280; $oh = 720          # FIXED feed -- HUD, window sizes and brain aim
                               # math are built around 16:9 1280x720.
$fps = if ($env:PETCAM_FPS) { [int]$env:PETCAM_FPS } else { 5 }

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

# ---- Score channel: apply click/type events from HQ (SendInput + SetCursorPos) ----
# Idempotent: re-running iex (irm ...) in the SAME session used to throw
# TYPE_ALREADY_EXISTS at Add-Type and kill the script (the type survives per
# session). Skip the Add-Type if PetcamInput is already registered.
# NOTE: if the in-session type is an OLDER revision (missing the new methods),
# the script degrades (method checks below) until iex is re-run in a FRESH
# PowerShell session -- that's when the new P/Invokes actually load.
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
  [StructLayout(LayoutKind.Sequential)]
  public struct POINT { public int x, y; }
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
  // SetLastError=true on every native call: on failure the Win32 error is
  // read via Marshal.GetLastWin32Error() IMMEDIATELY (before any other call),
  // because GetLastError is per-thread state.
  [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint n, INPUT[] p, int cb);
  [DllImport("user32.dll", SetLastError=true)] static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll", SetLastError=true)] static extern bool GetCursorPos(out POINT p);
  [DllImport("user32.dll", SetLastError=true)] static extern int ShowCursor(bool bShow);
  [DllImport("user32.dll", SetLastError=true)] static extern int GetSystemMetrics(int i);
  [DllImport("user32.dll", SetLastError=true)] static extern bool SetProcessDPIAware();
  [DllImport("user32.dll", SetLastError=true)] static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
  [DllImport("user32.dll", SetLastError=true)] static extern IntPtr GetDC(IntPtr h);
  [DllImport("user32.dll", SetLastError=true)] static extern int ReleaseDC(IntPtr h, IntPtr d);
  [DllImport("gdi32.dll",  SetLastError=true)] static extern int GetDeviceCaps(IntPtr h, int i);
  [DllImport("kernel32.dll")] static extern uint GetLastError();
  const uint MOUSEEVENTF_LEFTDOWN=0x0002, MOUSEEVENTF_LEFTUP=0x0004,
             MOUSEEVENTF_RIGHTDOWN=0x0008, MOUSEEVENTF_RIGHTUP=0x0010;
  const ushort KEYEVENTF_KEYUP=0x0002, KEYEVENTF_UNICODE=0x0004;
  const long DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4;
  const int SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79;
  const int LOGPIXELSX = 88;
  static uint Check(uint n, uint injected) {
    if (injected != n) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "SendInput injected " + injected + "/" + n);
    return injected;
  }
  static INPUT Mouse(uint flags) {
    INPUT i = new INPUT(); i.type = 0; i.u.mi.dwFlags = (int)flags; return i;
  }
  static INPUT Key(ushort vk, ushort flags) {
    INPUT i = new INPUT(); i.type = 1; i.u.ki.wVk = vk; i.u.ki.dwFlags = flags; return i;
  }
  public static void Move(int x, int y) {
    if (!SetCursorPos(x, y)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "SetCursorPos(" + x + "," + y + ")");
  }
  // Cursor readback in the SAME space as SetCursorPos (both user32, same
  // process DPI mode) -- the only honest way to verify where the cursor is.
  public static POINT CursorPos() {
    POINT p;
    if (!GetCursorPos(out p)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "GetCursorPos");
    return p;
  }
  // ShowCursor is a per-process counter; track our hide depth ourselves so
  // CursorReveal undoes exactly our hides. If this process dies mid-hide the
  // cursor reveals automatically (Windows resets a dead process's counter).
  static int cursorHides = 0;
  public static void CursorHide()   { if (cursorHides++ == 0) ShowCursor(false); }
  public static void CursorReveal() { if (cursorHides > 0 && --cursorHides == 0) ShowCursor(true); }
  // Virtual screen bounds straight from user32 (SM_xVIRTUALSCREEN): the
  // cross-check against WinForms' SystemInformation.VirtualScreen is a
  // MEASURED consistency check, not a guessed multiplier.
  public static int[] Metrics() {
    return new int[] { GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN),
                       GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN) };
  }
  // Establish ONE coordinate space for the whole process BEFORE any capture,
  // bounds, or cursor call: system-DPI-aware => GetSystemMetrics,
  // CopyFromScreen, SetCursorPos, GetCursorPos all agree on physical pixels.
  public static string DpiAware() {
    int err = 0;
    try {
      if (SetProcessDpiAwarenessContext(new IntPtr(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
        return "SetProcessDpiAwarenessContext(per-monitor-v2)";
      err = Marshal.GetLastWin32Error();
    } catch (System.EntryPointNotFoundException) { err = 0; }
    try {
      if (SetProcessDPIAware()) return "SetProcessDPIAware(system-aware)";
      err = Marshal.GetLastWin32Error();
    } catch (System.EntryPointNotFoundException) { err = 0; }
    throw new System.ComponentModel.Win32Exception(err, "DPI awareness could not be established");
  }
  // Measured physical DPI of the primary monitor (GetDeviceCaps LOGPIXELSX on
  // the screen DC). No multipliers anywhere: capture, bounds, cursor set and
  // cursor readback all live in this same physical space.
  public static int Dpi() {
    IntPtr dc = GetDC(IntPtr.Zero);
    if (dc.ToInt64() == 0 || dc.ToInt64() == -1)
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "GetDC(screen)");
    int dpi = 0;
    try { dpi = GetDeviceCaps(dc, LOGPIXELSX); } finally { ReleaseDC(IntPtr.Zero, dc); }
    if (dpi <= 0) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "GetDeviceCaps(LOGPIXELSX)");
    return dpi;
  }
  public static void Click(int x, int y, bool right) {
    Move(x, y);
    uint down = right ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_LEFTDOWN;
    uint up   = right ? MOUSEEVENTF_RIGHTUP   : MOUSEEVENTF_LEFTUP;
    INPUT[] d = { Mouse(down) }, u = { Mouse(up) };
    Check(1, SendInput(1, d, Marshal.SizeOf(typeof(INPUT))));
    Check(1, SendInput(1, u, Marshal.SizeOf(typeof(INPUT))));
  }
  // Pacing: when true, Type() fires word-by-word with human-ish gaps instead
  // of one SendInput burst (a burst reads as a bot; word cadence reads as
  // typing). Toggled live via HQ `/pause` body {pace:bool}; sender applies it
  // before each action. Default true (exam mode).
  public static bool Pace = true;
  static readonly System.Random PaceRng = new System.Random();
  public static void Type(string s) {
    if (!Pace) {
      int n = s.Length * 2;
      INPUT[] k = new INPUT[n];
      for (int i = 0; i < s.Length; i++) {
        k[i*2]   = new INPUT(){ type=1, u=new INPUTUNION(){ ki=new KEYBDINPUT(){ wScan=(ushort)s[i], dwFlags=KEYEVENTF_UNICODE } } };
        k[i*2+1] = new INPUT(){ type=1, u=new INPUTUNION(){ ki=new KEYBDINPUT(){ wScan=(ushort)s[i], dwFlags=(ushort)(KEYEVENTF_UNICODE|KEYEVENTF_KEYUP) } } };
      }
      Check((uint)n, SendInput((uint)n, k, Marshal.SizeOf(typeof(INPUT))));
      return;
    }
    // Word-by-word: ~9ms/char, 210-430ms between words (human typing cadence).
    foreach (string word in (s ?? "").Split(' ')) {
      for (int i = 0; i < word.Length; i++) {
        ushort ch = (ushort)word[i];
        INPUT[] k = {
          new INPUT(){ type=1, u=new INPUTUNION(){ ki=new KEYBDINPUT(){ wScan=ch, dwFlags=KEYEVENTF_UNICODE } } },
          new INPUT(){ type=1, u=new INPUTUNION(){ ki=new KEYBDINPUT(){ wScan=ch, dwFlags=(ushort)(KEYEVENTF_UNICODE|KEYEVENTF_KEYUP) } } } };
        Check(2, SendInput(2, k, Marshal.SizeOf(typeof(INPUT))));
        System.Threading.Thread.Sleep(9);
      }
      System.Threading.Thread.Sleep(210 + PaceRng.Next(220));
    }
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
  // Physical key state (F12 = 0x150): the STOP/START hotkey. GetAsyncKeyState
  // reads the physical keyboard, independent of which window (incl. the
  // lockdown exam window) has focus -- no hook, no message pump needed.
  [DllImport("user32.dll")] static extern short GetAsyncKeyState(int vKey);
  public static bool KeyState(int vk) { return (GetAsyncKeyState(vk) & 0x8000) != 0; }
 }
"@
}

$ctrlBase = ($target -replace '/frame$', '')
$script:lastSeq = 0
$script:paused = $false   # live from /state each poll; F12 toggle flips it
$script:f12Prev = $false  # edge-detect for the F12 STOP/START hotkey
$script:geoVer = 0
$script:cursorHidden = $false   # OS cursor hidden while spot drives (auto-resurfaces on idle)
$script:lastCursorAction = $null
$script:dpi = $null          # measured physical DPI (set in the DPI block below)

# ---- Centralized feed -> desktop mapping (the ONE place it happens) ----
#   screen_x = captureOx + feed_x * captureW / feedW
#   screen_y = captureOy + feed_y * captureH / feedH
# feedW/feedH are the ACTUAL feed dimensions the sender encodes ($ow/$oh);
# captureW/captureH/ox/oy are the measured capture bounds. Results are
# rejected if the feed coords are outside [0,feedW)x[0,feedH), then constrained
# to valid capture pixels after rounding. No hard-coded screen dimensions.
function Map-Feed {
  param([double]$fx, [double]$fy, [int]$feedW, [int]$feedH,
        [int]$capOx, [int]$capOy, [int]$capW, [int]$capH)
  if ($fx -lt 0 -or $fy -lt 0 -or $fx -ge $feedW -or $fy -ge $feedH) {
    return $null   # out of feed bounds -> reject, don't clamp
  }
  $sx = [int]([Math]::Round($capOx + $fx * $capW / $feedW))
  $sy = [int]([Math]::Round($capOy + $fy * $capH / $feedH))
  # constrain to the valid capture pixel rectangle [ox, ox+W) x [oy, oy+H)
  $sx = [Math]::Max($capOx, [Math]::Min($sx, $capOx + $capW - 1))
  $sy = [Math]::Max($capOy, [Math]::Min($sy, $capOy + $capH - 1))
  return @{ x = $sx; y = $sy }
}

# desktop -> feed (inverse, for cursor readback): feed = (screen - ox) * feedW / capW
function Map-Desktop {
  param([int]$sx, [int]$sy, [int]$feedW, [int]$feedH,
        [int]$capOx, [int]$capOy, [int]$capW, [int]$capH)
  return @{ x = [double]($sx - $capOx) * $feedW / $capW; y = [double]($sy - $capOy) * $feedH / $capH }
}

function Post-Result {
  param($Web, [int]$seq, $body)
  try {
    $body.session_id = $script:sessId
    $body.cmd_id = $seq
    [void]$Web.UploadString("$ctrlBase/result", "POST", (ConvertTo-Json $body -Depth 6))
  } catch {
    Write-Host ("score: result post failed (non-fatal): {0}" -f $_.Exception.Message)
  }
}

function Poll-Control {
  param($Web)
  # PAUSE GATE: when HQ says PAUSED, hold every /control event. We still
  # advance $script:lastSeq across the held events so nothing replays in a
  # burst when you resume (you hold = you decide when to continue; the held
  # actions are dropped, not queued). Pacing flag is synced from /state here.
  if ($script:paused) {
    try {
      $r = $Web.DownloadString("$ctrlBase/control?after=$script:lastSeq")
      $j = $r | ConvertFrom-Json
      if ($j -and $j.seq -lt $script:lastSeq) {
        $script:lastSeq = 0
        $r = $Web.DownloadString("$ctrlBase/control?after=0")
        $j = $r | ConvertFrom-Json
      }
      if ($j -and $j.seq -gt $script:lastSeq -and $j.events -and $j.events.Count -gt 0) {
        $script:lastSeq = $j.seq
        Write-Host ("score: PAUSED - holding {0} control event(s) up to seq {1} (not applied)" -f $j.events.Count, $j.seq)
      }
    } catch {}
    return
  }
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
      # Refresh the current geometry version + live pause/pacing flags from
      # /state so commands computed against a STALE map (display config
      # changed since) are rejected here and HQ's pause/pace toggles apply
      # before the next action.
      try {
        $stj = ($Web.DownloadString("$ctrlBase/state") | ConvertFrom-Json)
        if ($stj.geometry_version) { $script:geoVer = [int64]$stj.geometry_version }
        if ($null -ne $stj.paused) { $script:paused = [bool]$stj.paused }
        if ($null -ne $stj.paced)  { [PetcamInput]::Pace = [bool]$stj.paced }
      } catch {}
      foreach ($e in $j.events) {
        if ($null -eq $e) { continue }
        # Geometry version guard: an event stamped with a different geometry
        # version than the one the sender is currently running under was
        # computed against a stale capture/feed map -> do not apply it.
        $stale = ($e.geometry_version -and $script:geoVer -and ([int64]$e.geometry_version -ne $script:geoVer))
        if ($stale) {
          Write-Host ("score: seq {0} STALE geometry (v{1} != live v{2}) - applying with live map; HQ should re-stamp after a display change" -f $e.seq, $e.geometry_version, $script:geoVer)
          if ($e.seq -gt $script:lastSeq) { $script:lastSeq = $e.seq }   # don't re-fire it next poll
          continue # Reject commands aimed using stale geometry.
        }
        try {
          if ($e.type -eq 'click') {
            $m = Map-Feed $e.x $e.y $ow $oh $screenOx $screenOy $screenW $screenH
            if ($null -eq $m) {
              Write-Host ("score: seq {0} click REJECTED (feed {1},{2} outside {3}x{4})" -f $e.seq, $e.x, $e.y, $ow, $oh)
            } else {
              [void][PetcamInput]::CursorHide(); $script:cursorHidden = $true; $script:lastCursorAction = [DateTime]::Now
              [void][PetcamInput]::Click($m.x, $m.y, ($e.button -eq 2))
              Write-Host ("score: click feed {0},{1} -> screen {2},{3} (btn {4})" -f $e.x, $e.y, $m.x, $m.y, $e.button)
            }
          } elseif ($e.type -eq 'move') {
            $m = Map-Feed $e.x $e.y $ow $oh $screenOx $screenOy $screenW $screenH
            if ($null -eq $m) {
              Write-Host ("score: seq {0} move REJECTED (feed {1},{2} outside {3}x{4})" -f $e.seq, $e.x, $e.y, $ow, $oh)
              Post-Result $Web $e.seq @{ seq=$e.seq; requested_feed=@($e.x,$e.y); mapped=$null; actual=$null; actual_feed=$null; error="feed coords out of bounds"; capture=@{w=$screenW;h=$screenH;ox=$screenOx;oy=$screenOy}; dpr=$script:dpi; geometry_version=$script:geoVer }
            } else {
              [void][PetcamInput]::CursorHide(); $script:cursorHidden = $true; $script:lastCursorAction = [DateTime]::Now
              [void][PetcamInput]::Move($m.x, $m.y)
              # CURSOR READBACK: where the cursor ACTUALLY landed, in both
              # spaces. This is the ground truth that proves the map is right.
              $cp = [PetcamInput]::CursorPos()
              $af = Map-Desktop $cp.x $cp.y $ow $oh $screenOx $screenOy $screenW $screenH
              $dx = [Math]::Abs($cp.x - $m.x); $dy = [Math]::Abs($cp.y - $m.y)
              Write-Host ("score: move feed {0},{1} -> mapped {2},{3}, cursor now {4},{5} (dx {6} dy {7})" -f $e.x, $e.y, $m.x, $m.y, $cp.x, $cp.y, $dx, $dy)
              Post-Result $Web $e.seq @{ seq=$e.seq; requested_feed=@([double]$e.x,[double]$e.y); mapped=@([int]$m.x,[int]$m.y); actual=@([int]$cp.x,[int]$cp.y); actual_feed=@([math]::Round($af.x,2),[math]::Round($af.y,2)); error=$null; capture=@{w=$screenW;h=$screenH;ox=$screenOx;oy=$screenOy}; dpr=$script:dpi; geometry_version=$script:geoVer }
            }
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
        # PACE: human "reading the question" gap BETWEEN actions. Without it
        # spot clears an exam at bot speed -- the single fastest tell. 350-950ms
        # random, applied only when HQ pacing is on (synced from /state).
        if ([PetcamInput]::Pace) {
          Start-Sleep -Milliseconds (350 + (Get-Random -Maximum 600))
        }
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
# NOTE: no .Timeout assignment -- WebClient.Timeout setter throws on .NET 4.x.

# ---------------------------------------------------------------------------
# COORDINATE SPACE -- established ONCE, before ANY capture/bounds/cursor call.
#
# Everything (CopyFromScreen, GetSystemMetrics, SetCursorPos, GetCursorPos)
# must live in ONE process DPI mode, measured NOT guessed:
#   1. Make the process system-DPI-aware (SetProcessDpiAwarenessContext first,
#      SetProcessDPIAware as fallback; API availability checked, failures ->
#      Win32 error via Marshal.GetLastWin32Error, no silent continue).
#   2. Measure the physical DPI (GetDeviceCaps LOGPIXELSX) for the report.
#   3. Measure the virtual-screen bounds from user32 SM_xVIRTUALSCREEN AND
#      cross-check against WinForms SystemInformation.VirtualScreen -- a
#      measured consistency check, not a scaling multiplier.
# If either bounds source is unavailable, fail loud (no click path on a
# half-measured screen).
$hasReadback = ($null -ne ([PetcamInput].GetMethod("CursorPos", [System.Reflection.BindingFlags]::Public -bor [System.Reflection.BindingFlags]::Static)))
$hasDpiApi   = ($null -ne ([PetcamInput].GetMethod("DpiAware",     [System.Reflection.BindingFlags]::Public -bor [System.Reflection.BindingFlags]::Static)))
if (-not $hasReadback) {
  Write-Host "petcam-native: WARNING - running an older in-session PetcamInput (no CursorPos/DpiAware). Cursor readback + DPI measurement are OFF until you re-run iex in a FRESH PowerShell window."
}
if ($hasDpiApi) {
  try { $script:dpiMode = [PetcamInput]::DpiAware() }
  catch { Write-Host "petcam-native: DPI awareness FAILED (non-fatal, Win32): $($_.Exception.Message)" }
  try { $script:dpi = [PetcamInput]::Dpi() }
  catch { Write-Host "petcam-native: DPI measurement FAILED (non-fatal, Win32): $($_.Exception.Message)" }
}

# FULL VIRTUAL SCREEN (all monitors; origin can be negative) is the capture +
# click-mapping space, so the feed and the score channel cover every display.
function Measure-Bounds {
  # user32 first (matches the DPI mode we just established), WinForms as the
  # cross-check. Both must agree; a disagreement is a MEASURED inconsistency.
  $um = $null
  if ($hasReadback) { $um = [PetcamInput]::Metrics() }
  $vsb = [System.Windows.Forms.SystemInformation]::VirtualScreen
  if ($null -ne $um) {
    if ($um[2] -le 0 -or $um[3] -le 0) { throw "Invalid user32 capture bounds; refusing zero-sized capture" }
    if ($um[0] -ne $vsb.X -or $um[1] -ne $vsb.Y -or $um[2] -ne $vsb.Width -or $um[3] -ne $vsb.Height) {
      Write-Host ("petcam-native: WARN bounds disagree user32 ({0},{1},{2}x{3}) vs WinForms ({4},{5},{6}x{7}) - using user32" -f
                  $um[0], $um[1], $um[2], $um[3], $vsb.X, $vsb.Y, $vsb.Width, $vsb.Height)
    }
    return @{ x = $um[0]; y = $um[1]; w = $um[2]; h = $um[3] }
  }
  if (-not $vsb.Width -or -not $vsb.Height) { throw "VirtualScreen bounds unavailable (WinForms + user32)" }
  return @{ x = $vsb.X; y = $vsb.Y; w = $vsb.Width; h = $vsb.Height }
}
try {
  $b = Measure-Bounds
} catch {
  Write-Host "petcam-native: FATAL - could not measure capture bounds: $($_.Exception.Message)"
  exit 1
}
$screenOx = $b.x; $screenOy = $b.y; $screenW = $b.w; $screenH = $b.h
$dpiModeTxt = if ($null -ne $script:dpiMode) { $script:dpiMode } else { "DPI api unavailable" }
$dpiTxt = if ($null -ne $script:dpi) { $script:dpi } else { "?" }
Write-Host "petcam-native: streaming ${screenW}x${screenH} @ (${screenOx},${screenOy}) -> feed ${ow}x${oh} @ ~${fps}fps to $target (dpi $dpiTxt, $dpiModeTxt)"
Write-Host "close this window to stop"

# Display configuration can change under us (monitor unplugged, resolution
# change). Re-measure the bounds EVERY FRAME; when they move, the capture
# geometry changed -> HQ's geometry_version bumps (via our fresh /hello) and
# any in-flight event stamped with the old version is flagged stale.
function Refresh-Bounds {
  param([int]$curOx, [int]$curOy, [int]$curW, [int]$curH)
  try {
    $nb = Measure-Bounds
  } catch { return $false }   # transient; keep the last known-good bounds
  if ($nb.x -ne $curOx -or $nb.y -ne $curOy -or $nb.w -ne $curW -or $nb.h -ne $curH) {
    Write-Host ("petcam-native: DISPLAY CONFIG CHANGED - capture {0},{1},{2}x{3} -> {4},{5},{6}x{7}; re-handshaking" -f
                $curOx, $curOy, $curW, $curH, $nb.x, $nb.y, $nb.w, $nb.h)
    $script:bnd = $nb
    # re-announce so HQ re-stamps the geometry version
    $hw = New-Object System.Net.WebClient
    try {
      [void]$hw.UploadString("$ctrlBase/hello", "POST", (ConvertTo-Json @{
        host = $env:COMPUTERNAME; screen = @{ w = $nb.w; h = $nb.h; ox = $nb.x; oy = $nb.y }
        feed = @{ w = $ow; h = $oh; fps = $fps }; changed = $true
        session_id = $script:sessId } -Depth 5))
    } catch {}
    return $true
  }
  return $false
}

# Handshake: report real screen bounds + capabilities so HQ can verify the
# feed->screen mapping instead of guessing.
$script:bnd = @{ x = $screenOx; y = $screenOy; w = $screenW; h = $screenH }
# Session id: stable for the life of THIS PowerShell process. HQ uses it to
# (a) replace capability metadata only when a NEW sender session connects
#     (no stale readback flags carried across restarts), and (b) associate
#     cursor-readback results with the active session + command id.
$script:sessId = "send-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
try {
  $ps = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $winDpi = $null
  try { $winDpi = [System.Windows.Forms.SystemInformation]::DeviceDPI } catch {}
  $payload = @{
    host    = $env:COMPUTERNAME
    os      = [System.Environment]::OSVersion.VersionString
    screen  = @{ w = $b.w; h = $b.h; ox = $b.x; oy = $b.y }
    primary = @{ x = $ps.X; y = $ps.Y; w = $ps.Width; h = $ps.Height }
    dpi     = $script:dpi
    dpi_winforms = $winDpi
    dpi_mode = $script:dpiMode
    readback = $hasReadback
    caps    = @("click", "move", "type", "unicode", "key", "cursor")
    feed    = @{ w = $ow; h = $oh; fps = $fps }
    session_id = $script:sessId
  }
  $hw = New-Object System.Net.WebClient
  [void]$hw.UploadString("$ctrlBase/hello", "POST", (ConvertTo-Json $payload -Depth 5))
  Write-Host ("petcam-native: hello -> virtual {0}x{1} @ ({2},{3}), primary {4}x{5}, dpi {6}/{7}" -f
    $b.w, $b.h, $b.x, $b.y, $ps.Width, $ps.Height, $script:dpi, $winDpi)
} catch {
  Write-Host ("petcam-native: hello failed (non-fatal): {0}" -f $_.Exception.Message)
}

# F12 hotkey: background thread polls the physical keyboard at 25Hz
# (GetAsyncKeyState reads the real key, even over the lockdown exam window).
# Rising edge => POST a `key +` control event to HQ, which the sender applies
# via SendInput — opens a new Chrome tab (or whatever "+" means in the
# focused app). 700ms debounce so a double-tap doesn't double-fire. The
# physical key still reaches the focused app too (LDB swallows F12 in its
# window; if it lands elsewhere, worst case is a devtools flicker).
$web2 = New-Object System.Net.WebClient
$web2.Headers.Add("Content-Type", "application/json")
$t2 = New-Object System.Threading.Thread -ArgumentList ({
  $prev = $false; $lastToggle = [DateTime]::MinValue
  while ($true) {
    Start-Sleep -Milliseconds 40
    $down = [PetcamInput]::KeyState(0x150)   # F12
    if ($down -and -not $prev -and
        (([DateTime]::Now - $lastToggle).TotalMilliseconds -gt 700)) {
      $lastToggle = [DateTime]::Now
      try {
        $r = $web2.UploadString("$ctrlBase/control", "POST", '{"cmd":"key","key":"+","mods":[]}')
        $j = $r | ConvertFrom-Json
        Write-Host ("score: F12 pressed - key '+' enqueued (seq {0}, held={1})" -f $j.seq, $j.held)
      } catch {
        Write-Host ("score: F12 key-enqueue failed (HQ unreachable?): {0}" -f $_.Exception.Message)
      }
    }
    $prev = $down
  }
})
[void]$t2.Start()
Write-Host "petcam-native: F12 = '+' key (new tab / app-specific '+' action)"

$i = 0
while ($true) {
    $t0 = [DateTime]::Now
    $full = $null; $shrink = $null; $ms = $null
    Poll-Control $web
    # Display-config refresh: if the virtual screen moved/resized, adopt the
    # new capture bounds so the map (and HQ's geometry_version) stay honest.
    $rb = Refresh-Bounds $screenOx $screenOy $screenW $screenH
    if ($rb) { $screenOx = $script:bnd.x; $screenOy = $script:bnd.y; $screenW = $script:bnd.w; $screenH = $script:bnd.h }
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
    # Idle resurface: if we hid the OS cursor for spot's actions and it has
    # been quiet, reveal it so the physical mouse can never stay lost.
    # Default 30 min (user wants the cursor invisible while spot drives,
    # incl. paced gaps); override with $env:PETCAM_CURSOR_REVEAL_MS.
    $revealMs = if ($env:PETCAM_CURSOR_REVEAL_MS) { [int]$env:PETCAM_CURSOR_REVEAL_MS } else { 1800000 }
    if ($script:cursorHidden -and ($null -ne $script:lastCursorAction) -and
        (([DateTime]::Now - $script:lastCursorAction).TotalMilliseconds -gt $revealMs)) {
      [void][PetcamInput]::CursorReveal()
      $script:cursorHidden = $false
      Write-Host ("score: cursor resurfaced after {0:N0} ms of no spot actions" -f ([DateTime]::Now - $script:lastCursorAction).TotalMilliseconds)
    }
    $elapsed = ([DateTime]::Now - $t0).TotalMilliseconds
    $sleepMs = [Math]::Max(150, [int](1000.0 / $fps - $elapsed))
    Start-Sleep -Milliseconds $sleepMs
}
