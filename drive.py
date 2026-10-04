#!/usr/bin/env python3
"""petcam drive: the score-channel driver for the mark's machine.

COORDINATE SPACES (kept explicit — this is the whole point of the 2026-10-04
calibration fix):
  feed pixels    the ACTUAL JPEG frame dimensions (from /state frame_w/h,
                 which the receiver measures off the real received frames).
                 drive.py takes feed pixels. Never the literal 1280x720.
  HQ viewport    the displayed image rectangle in the mpv feed window
                 (title "petcam"): a contain-fit of the frame aspect into the
                 window — the letterbox black bars are NOT part of it.
  Windows desktop the mark's capture bounds (virtual screen) + origin, from
                 the sender's /client handshake. The sender owns the
                 feed->desktop mapping (native.ps1 Map-Feed); HQ never
                 multiplies by screen dimensions itself.

Usage (all coords in FEED pixels):
  drive.py move X Y        # move-only (no click). Polls the sender's cursor
                           # readback and reports requested vs actual.
  drive.py click X Y [btn]
  drive.py type "text"
  drive.py key KEY [MOD ...]
  drive.py open-url "URL" [--no-focus]
  drive.py shot [path] [--marker file] [--viewport]
                     # --marker: draw a 2-line (x,y per line) marker file as a
                     #   red crosshair on the output only — the captured frame
                     #   used for result verification is never modified.
                     # --viewport: trim letterbox bars, keep the image rect.
  drive.py state
  drive.py geometry       # the full map: feed dims, capture bounds, geo ver,
                           # HQ viewport rect (feed px + window-local px)

Rules baked in:
  - Explicit coords are feed pixels; out-of-bounds (0 <= x < feedW,
    0 <= y < feedH) is REJECTED, not clamped — a clamped aim is a silent miss.
  - Pet coords (pet pointer) are converted from HQ position -> image viewport
    -> feed pixels by the brain (reject outside the image, no edge snapping).
  - Every action waits on the journal (brain writes the outcome + screen diff)
    and reports it. changed:false / diff 0.0 does NOT mean the input failed:
    the verify frame is captured milliseconds after the event. Verify with a
    FRESH shot + vision.
  - The mark's window layout changes under you. Re-shoot before every aim
    pass; don't reuse stale coords across passes.
  - ctrl+l is the robust omnibox focus (no aiming) once the Chrome window has
    focus. Give Chrome focus by clicking inside it first.
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("PETCAM_BASE", "http://100.86.251.100:9101")
CMD_FILE = os.path.join(HERE, "cmd.json")
JOURNAL = os.path.join(HERE, "journal.jsonl")
SEQ_RE = re.compile(r"seq (\d+)")


def get(path, timeout=5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.read()


def jget(path, timeout=5):
    return json.loads(get(path, timeout))


# ---------------------------------------------------------------- feed frame
def frame_dims():
    """ACTUAL frame geometry from /state (receiver measures it off the
    received JPEGs). Falls back to the sender's declared feed, then 1280x720."""
    st = jget("/state")
    w, h = int(st.get("frame_w") or 0), int(st.get("frame_h") or 0)
    if w > 0 and h > 0:
        return w, h
    c = jget("/client")
    f = c.get("feed") or {}
    w, h = int(f.get("w") or 0), int(f.get("h") or 0)
    return (w or 1280, h or 720)


def check_feed(x, y):
    """Reject (not clamp) coords outside the current feed frame."""
    fw, fh = frame_dims()
    if not (0 <= x < fw and 0 <= y < fh):
        sys.exit("REJECTED: feed %g,%g outside 0..%d x 0..%d (feed is the real "
                 "frame size; re-aim)" % (x, y, fw, fh))
    return int(round(x)), int(round(y))


def geometry():
    """The full, explicit map. Feed pixels <-> desktop pixels live in the
    sender (Map-Feed); this is the same formula on the HQ side, from the
    SAME measured inputs (/state frame dims + /client bounds) — never
    hard-coded screen dimensions."""
    st = jget("/state")
    c = jget("/client")
    fw, fh = int(st.get("frame_w") or 0), int(st.get("frame_h") or 0)
    if not (fw and fh):
        f = c.get("feed") or {}
        fw, fh = int(f.get("w") or 1280), int(f.get("h") or 720)
    sc = c.get("screen") or {}
    ox, oy = int(sc.get("ox") or 0), int(sc.get("oy") or 0)
    cw, ch = int(sc.get("w") or 0), int(sc.get("h") or 0)
    import subprocess
    vx = vy = vw = vh = None
    try:
        out = subprocess.run(["hyprctl", "-j", "clients"], capture_output=True,
                             text=True, timeout=4).stdout
        mons = json.loads(subprocess.run(["hyprctl", "-j", "monitors"],
                                         capture_output=True, text=True, timeout=4).stdout)
        for cl in json.loads(out):
            if cl.get("title") == "petcam":
                mi = cl.get("monitor", 0)
                mon = mons[mi] if mi < len(mons) else {}
                wx, wy, ww, wh = cl["at"][0], cl["at"][1], cl["size"][0], cl["size"][1]
                ar = fw / fh
                if ww / float(wh) > ar:
                    vw, vh = wh * ar, float(wh)
                else:
                    vw, vh = float(ww), ww / ar
                vx = wx + (ww - vw) / 2 - int(mon.get("x", 0))
                vy = wy + (wh - vh) / 2 - int(mon.get("y", 0))
                vx_win, vy_win = (ww - vw) / 2, (wh - vh) / 2   # window-local
                break
    except Exception:
        pass
    geo = {
        "feed_pixels": [fw, fh],
        "windows_desktop": {"capture": [ox, oy, cw, ch],
                            "dpr": c.get("dpi"),
                            "dpr_winforms": c.get("dpi_winforms"),
                            "dpi_mode": c.get("dpi_mode"),
                            "readback": c.get("readback")},
        "map": "screen = captureOx + feed_x * captureW / feedW (sender-owned)",
        "hq_image_viewport": ({"monitor_local_px": [round(vx, 1), round(vy, 1)],
                               "window_local_px": [round(vx_win, 1), round(vy_win, 1)],
                               "displayed_size_px": [round(vw, 1), round(vh, 1)]}
                              if vx is not None else None),
        "geometry_version": st.get("geometry_version"),
        "client_at": c.get("at"),
    }
    return geo


def state():
    st = jget("/state")
    age = time.time() - st["last_frame_at"]
    ctl = time.time() - st.get("last_control_poll_at", 0)
    health = "live" if age < 10 else "stale" if age < 120 else "dead"
    print(f"{health}: frame age {age:.1f}s, ctrl poll {ctl:.1f}s, "
          f"frames {st['frames']}, client {st['last_client']}, "
          f"frame {st.get('frame_w')}x{st.get('frame_h')}, geo {st.get('geometry_version')}")
    return health


# ------------------------------------------------------------------- journal
def _journal_mark():
    try:
        return os.path.getsize(JOURNAL)
    except OSError:
        return 0


def _journal_since(mark):
    with open(JOURNAL, "rb") as f:
        f.seek(mark)
        data = f.read().decode("utf-8", "replace")
    out = []
    for ln in data.strip().splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def send(cmd, wait=True, timeout=20):
    """Write cmd.json; the brain consumes it and journals the outcome.
    The journal mark is taken BEFORE the write — otherwise the brain can
    consume+fire within the 2s poll gap and the entry lands before our mark."""
    mark = _journal_mark()
    with open(CMD_FILE, "w") as f:
        json.dump(cmd, f)
    if not wait:
        time.sleep(1.0)
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in _journal_since(mark):
            if e.get("cmd") == cmd.get("cmd"):
                time.sleep(0.4)   # let the brain consume the cmd file
                return e
        time.sleep(0.8)
    return None


def _verdict(e):
    if e is None:
        return "no journal entry (brain down?)"
    ch = e.get("changed")
    return {True: "mark responded (diff %s)" % e.get("diff"),
            False: "no visible diff (verify with a fresh shot — not proof of failure)",
            None: "unverifiable"}[ch]


# ------------------------------------------------------------- cursor readback
def move_result(seq, timeout=15):
    """Poll /results for the sender's cursor readback for a fired seq."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = jget("/results?seq=" + str(seq))
        except Exception:
            r = None
        if r and r.get("result"):
            return r["result"]
        time.sleep(0.5)
    return None


def cmd_move(x, y):
    """Move-only. Sends the cursor move through the existing command path
    (brain -> /control -> sender), then reports the sender's ACTUAL cursor
    readback against the requested position. No click is fired."""
    fx, fy = check_feed(x, y)
    e = send({"cmd": "move", "x": fx, "y": fy})
    res = e.get("result") if e else "?"
    seq = None
    if res:
        m = SEQ_RE.search(res)
        if m:
            seq = int(m.group(1))
    print("move %d,%d: %s" % (fx, fy, res))
    if not seq:
        print("  (no seq in result — readback unavailable: sender is the old"
              " revision until re-run in a fresh PowerShell window)")
        return
    r = move_result(seq)
    if r is None:
        print("  readback: none yet (sender on the old revision? old session?)")
        return
    req = r.get("requested_feed"); mapped = r.get("mapped"); actual = r.get("actual")
    af = r.get("actual_feed"); cap = r.get("capture") or {}
    if r.get("error"):
        print("  readback: %s" % r["error"])
        return
    print("  requested feed : %s" % req)
    print("  mapped desktop : %s (capture %sx%s @ %s,%s)" %
          (mapped, cap.get("w"), cap.get("h"), cap.get("ox"), cap.get("oy")))
    print("  actual desktop : %s  (dpr %s)" % (actual, r.get("dpr")))
    if mapped and actual:
        dx, dy = actual[0] - mapped[0], actual[1] - mapped[1]
        ok = abs(dx) <= 1 and abs(dy) <= 1
        print("  actual feed    : %s   [requested vs actual: %s (dx %d dy %d)]" %
              (af, "MATCH" if ok else "OFFSET", dx, dy))
    if ok:
        # Diagnostic marker: the ACTUAL cursor in feed coords, drawn by the
        # HQ viewer (shot --marker). The captured frame itself is untouched.
        mk = os.path.join(HERE, "inbox", "marker-latest.txt")
        os.makedirs(os.path.dirname(mk), exist_ok=True)
        with open(mk, "w") as f:
            f.write("%s\n%s\n" % (af[0], af[1]))
        print("  marker: %s (use: drive.py shot --marker %s)" % (mk, mk))


# ------------------------------------------------------------------ shooting
def shot(path=None, marker=None, viewport=False):
    if path is None:
        os.makedirs(os.path.join(HERE, "inbox"), exist_ok=True)
        path = os.path.join(HERE, "inbox", "shot-" + time.strftime("%H%M%S") + ".jpg")
    raw = get("/latest.jpg")
    data = raw
    if marker:
        _draw_marker(raw, path, marker)
    elif viewport:
        data = _trim_viewport(raw, path)
    else:
        open(path, "wb").write(raw)
    return path


def _draw_marker(raw, path, marker_file):
    """Draw the marker crosshair (2 lines: x y) on a COPY only — the captured
    frame used for result verification is never modified."""
    from PIL import Image, ImageDraw
    import io
    with open(marker_file) as f:
        vals = [float(t) for t in f.read().replace("\n", " ").split()]
    mx, my = vals[0], vals[1]
    im = Image.open(io.BytesIO(raw))
    d = ImageDraw.Draw(im)
    r = max(8, im.width // 100)
    for col in ((255, 40, 40), (255, 255, 255)):
        d.ellipse([mx - r, my - r, mx + r, my + r], outline=col, width=2)
        d.line([mx - r * 1.6, my, mx + r * 1.6, my], fill=col, width=2)
        d.line([mx, my - r * 1.6, mx, my + r * 1.6], fill=col, width=2)
    im.save(path, "JPEG", quality=92)


def _trim_viewport(raw, path):
    """Keep only the displayed image rectangle (drop letterbox bars)."""
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(raw))
    fw, fh = frame_dims()
    c = jget("/client")
    sc = c.get("screen") or {}
    cw, ch = int(sc.get("w") or fw), int(sc.get("h") or fh)
    ar = fw / fh
    if cw / float(ch) > ar:
        vw, vh = ch * ar, ch
        x0, y0 = (cw - int(vw)) // 2, 0
    else:
        vw, vh = cw, int(cw / ar)
        x0, y0 = 0, (ch - vh) // 2
    fx0, fy0 = x0 / cw * fw, y0 / ch * fh
    fx1, fy1 = (x0 + vw) / cw * fw, (y0 + vh) / ch * fh
    im = im.crop((int(fx0), int(fy0), int(fx1), int(fy1)))
    im.save(path, "JPEG", quality=92)
    return path


# --------------------------------------------------------------------- main
def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    c = a[0]
    if c == "shot":
        path = None; marker = None; viewport = False
        i = 1
        while i < len(a):
            if a[i] == "--marker":
                marker = a[i + 1]; i += 2
            elif a[i] == "--viewport":
                viewport = True; i += 1
            else:
                path = a[i]; i += 1
        print(shot(path, marker, viewport))
    elif c == "state":
        state()
    elif c == "geometry":
        print(json.dumps(geometry(), indent=2))
    elif c == "move":
        cmd_move(float(a[1]), float(a[2]))
    elif c == "click":
        x, y = float(a[1]), float(a[2])
        btn = int(a[3]) if len(a) > 3 else 1
        fx, fy = check_feed(x, y)
        e = send({"cmd": "click", "x": fx, "y": fy, "button": btn})
        print("click %d,%d btn%d: %s; %s" % (fx, fy, btn, e.get("result") if e else "?", _verdict(e)))
    elif c == "type":
        e = send({"cmd": "type", "text": a[1]})
        print("type %d chars: %s; %s" % (len(a[1]), e.get("result") if e else "?", _verdict(e)))
    elif c == "key":
        key = a[1].lower()
        mods = [m.lower() for m in a[2:]]
        e = send({"cmd": "key", "key": key, "mods": mods})
        print("key %s %s: %s; %s" % (key, "+".join(mods) if mods else "",
                                     e.get("result") if e else "?", _verdict(e)))
    elif c == "open-url":
        url = a[1]
        if len(a) > 2 and a[2] != "--no-focus":
            fx, fy = check_feed(float(a[3]), float(a[4]))
            e = send({"cmd": "click", "x": fx, "y": fy, "button": 1})
            print("focus chrome at %d,%d: %s; %s" % (fx, fy, e.get("result") if e else "?", _verdict(e)))
            time.sleep(0.8)
        e = send({"cmd": "key", "key": "l", "mods": ["ctrl"]})
        print("ctrl+l: %s; %s" % (e.get("result") if e else "?", _verdict(e)))
        time.sleep(0.8)
        e = send({"cmd": "type", "text": url})
        print("type %s: %s; %s" % (url, e.get("result") if e else "?", _verdict(e)))
        time.sleep(0.8)
        e = send({"cmd": "key", "key": "enter"})
        print("enter: %s; %s" % (e.get("result") if e else "?", _verdict(e)))
        time.sleep(3)
        print(shot())
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
