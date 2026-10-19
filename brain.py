#!/usr/bin/env python3
"""petcam brain: a standalone, Hermes-independent daemon. Heist flavor  - the
pet is the crew's *spotter* at HQ, watching the mark's screen (the feed).

Watches the petcam receiver (/state) and drives the Omarchy pet:
  - poses the pet based on feed health (live / stale / no frames)
  - speaks a bubble when the feed state changes (e.g. "in on the mark's screen")
  - executes commands from a control file so an agent (or you) can make the
    pet act on the other machine without touching the daemon:
        echo '{"cmd":"think","text":"..."}'  > petcam/cmd.json   (talk)
        echo '{"cmd":"click","x":640,"y":360}' > petcam/cmd.json (phase 2)
        echo '{"cmd":"loot"}'               > petcam/cmd.json   (lift intel)

Runs on the 5090. Talks to the pet only through the `omarchy-shell` CLI, so
the pet stays a stock Omarchy plugin  - nothing custom is patched into it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CMD_FILE = os.path.join(HERE, "cmd.json")
JOURNAL = os.path.join(HERE, "journal.jsonl")   # action -> observed outcome (the learning data)
STATE_URL = os.environ.get("PETCAM_STATE", "http://100.86.251.100:9101/state")
LATEST_URL = os.environ.get("PETCAM_LATEST", "http://100.86.251.100:9101/latest.jpg")
CONTROL_URL = os.environ.get("PETCAM_CONTROL", "http://100.86.251.100:9101/control")
CLIENT_URL = os.environ.get("PETCAM_CLIENT", "http://100.86.251.100:9101/client")
BOOKEND_URL = os.environ.get("PETCAM_BOOKEND", "http://100.86.251.100:9101/bookend")
PAUSE_URL = os.environ.get("PETCAM_PAUSE", "http://100.86.251.100:9101/pause")
POLL = 0.4
STALE_AFTER = 5.0          # seconds without a new frame before "stale"
PLACEHOLDER_IP = "100.86.251.100"   # our own IP => placeholder feed
PET_AGENT = "laptop"        # the pet's agent-state key for the remote machine
PET_POS_FILE = os.path.expanduser("~/.local/state/omarchy/pets/position.json")

FEED_W, FEED_H = 1280, 720   # fallback only. refresh_feed_dims() overrides from
                             # the receiver's /state (measured from ACTUAL
                             # received frames, not the sender's nominal claim).
                             # All aim math must use these — never the literal.
GEO = {"v": None, "at": 0.0}  # current geometry version (refreshed per poll)

ROUTINES_DIR = os.path.join(HERE, "routines")   # saved task artifacts (improvement #1)
ACTIVE_TASK = None   # task being captured: {"name","t0","journal_lines","frame_before"}


def refresh_feed_dims():
    """Feed dims = what the receiver measured off ACTUAL received frames
    (/state frame_w/frame_h), falling back to the sender's declared feed size
    from its /hello handshake. Keeps aim math in step with whatever geometry
    is really being emitted — no hard-coded 1280x720."""
    global FEED_W, FEED_H
    try:
        import urllib.request
        with urllib.request.urlopen(STATE_URL, timeout=2) as r:
            st = json.load(r)
        w, h = int(st.get("frame_w") or 0), int(st.get("frame_h") or 0)
        if w > 0 and h > 0:
            FEED_W, FEED_H = w, h
            v = st.get("geometry_version")
            if v is not None and v != GEO["v"]:
                if GEO["v"] is not None:
                    print("brain: geometry changed v %s -> %s (capture/feed bounds or client)" % (GEO["v"], v), flush=True)
                    pet_think("geometry changed - re-measure before aiming")
                GEO["v"], GEO["at"] = v, time.time()
            return
    except Exception:
        pass
    try:
        import urllib.request
        with urllib.request.urlopen(CLIENT_URL, timeout=2) as r:
            c = json.load(r)
        f = c.get("feed") or {}
        if int(f.get("w", 0)) > 0 and int(f.get("h", 0)) > 0:
            FEED_W, FEED_H = int(f["w"]), int(f["h"])
    except Exception:
        pass

_omarchy_shell = shutil.which("omarchy-shell") or "omarchy-shell"


def run(args):
    try:
        subprocess.run(args, check=False, capture_output=True, timeout=8)
    except Exception:
        pass


def pet_think(text):
    run([_omarchy_shell, "pets", "think", text])


def pet_state(state):
    # running|waiting|done|error|idle  -> pet animation
    run(["omarchy-pets-agent-state", PET_AGENT, state])


def fetch_state():
    import urllib.request
    try:
        with urllib.request.urlopen(STATE_URL, timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


FEED_W, FEED_H = 1280, 720        # (redundant re-statement; refresh_feed_dims
                                   # keeps the authoritative values live)


def ensure_ydotoold():
    """ydotool needs the uinput daemon; start it if absent (plain sudo works here)."""
    if shutil.which("ydotoold") or subprocess.run(
            ["pgrep", "-x", "ydotoold"], capture_output=True).returncode == 0:
        return True
    try:
        subprocess.run(["sudo", "ydotoold"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def window_geom():
    """Live petcam (mpv) feed-window geometry in GLOBAL HQ LOGICAL pixels:
    (wx, wy, ww, wh, monx, mony). The window's monitor origin is included so
    the caller can relate window-local and global coordinates; on a
    multi-monitor HQ box the feed can be parked on any monitor.
    hyprctl's at/size are LOGICAL (post-scale) pixels — the same unit the
    pet anchor and the QML monitor model use — so the contain-fit of the
    feed image into the window needs NO scale factor, even on a 1.6x
    fractional-scale display. Mixing in physical pixels anywhere in this
    chain is the old bug that mis-aimed by the scale factor."""
    try:
        out = subprocess.run(["hyprctl", "-j", "clients"],
                             capture_output=True, text=True, timeout=4).stdout
        mons = subprocess.run(["hyprctl", "-j", "monitors"],
                              capture_output=True, text=True, timeout=4).stdout
        for c in json.loads(out):
            if c.get("title") == "petcam":
                mlist = json.loads(mons)
                mi = c.get("monitor", 0)
                mon = mlist[mi] if mi < len(mlist) else {}
                return (c["at"][0], c["at"][1], c["size"][0], c["size"][1],
                        int(mon.get("x", 0)), int(mon.get("y", 0)))
    except Exception:
        pass
    return (2064, 890, 480, 270, 0, 0)   # default: 480x270 under the pet on DP-1


def pet_sprite_size():
    """Pet sprite size in px at the configured scale. The ellen-joe-pixel
    sheet is 192x208 per cell (8-col x 11-row sheet of 1536x2288); the pet
    renders at pets.json "scale" (0.25-4.0, default 0.5)."""
    scale = 0.5
    try:
        with open(os.path.expanduser("~/.config/omarchy/pets.json")) as f:
            scale = float(json.load(f).get("scale", 0.5))
    except Exception:
        pass
    return int(round(192 * scale)), int(round(208 * scale))


def monitors_map():
    """{monitor name: (origin_x, origin_y, scale)} from hyprctl."""
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors"],
                             capture_output=True, text=True, timeout=4).stdout
        return {m["name"]: (int(m["x"]), int(m["y"]), float(m.get("scale", 1.0)))
                for m in json.loads(out)}
    except Exception:
        return {"DP-1": (0, 0, 1.0)}


def pet_anchor_global():
    """The pet's character anchor: its FEET — bottom-center of the sprite.
    EXACT ANCHOR DEFINITION: (saved_top_left_x + spriteW/2, saved_top_left_y + spriteH),
    where the saved top-left is what fabri.ellen-pet's Service.qml writes on
    drag-release: savePosition(screenName, pet.x, pet.y) -> position.json.

    UNIT PROOF (why NO scale factor): the QML clamps its coords against the
    monitor model's size (`Math.min(saved.x, win.width - width)`) and computes
    centerGX = modelData.x + x + width/2 — so pet.x/y and hyprctl monitor
    at/size share ONE unit: LOGICAL (post-scale) pixels, exactly the space
    hyprctl reports. Multiplying by the monitor scale factor here would
    double-apply it. The old 1.6x-multiplied version landed the anchor at
    (3763,2160) for a pet parked at (2304,1246) — 1.6x too far in both axes.
    Returns None when the position/monitor lookup fails — callers REJECT the
    aim rather than fall back to a stale constant."""
    try:
        pos = json.load(open(PET_POS_FILE))
    except Exception:
        return None
    mons = monitors_map()
    for mon in ("DP-1", "HDMI-A-1"):      # DP-1 is where the feed is parked
        if mon in pos and mon in mons:
            ox, oy, _scale = mons[mon]    # origins are already logical
            sw, sh = pet_sprite_size()
            return (ox + int(round(int(pos[mon]["x"]) + sw / 2.0)),
                    oy + int(round(int(pos[mon]["y"]) + sh)))
    if "DP-1" in pos:                      # unknown monitor name: origin (0,0)
        sw, sh = pet_sprite_size()
        return (int(pos["DP-1"]["x"]) + sw // 2, int(pos["DP-1"]["y"]) + sh)
    return None


def pet_pointer(feed_x=None, feed_y=None):
    """The pet's position IS the pointer: the spotter stands over the feed,
    so the crosshair on the mark's screen = the pet's anchor (its FEET,
    pet_anchor_global()) mapped through the feed image's DISPLAYED viewport.

    COORDINATE SPACES (kept explicit):
      1. HQ desktop: global pixels; the feed image lives in the mpv window
         (title "petcam"), whose origin/size come from hyprctl.
      2. HQ image viewport: the mpv window renders /stream.mjpeg with
         object-fit: contain — the displayed image rectangle is the 16:9
         frame aspect-fit into the window content, and the letterbox black
         bars are EXCLUDED from the mapping (they are not feed pixels).
      3. Feed pixels: the actual JPEG frame dimensions (FEED_W x FEED_H,
         refreshed from the receiver, NOT a literal).

    The anchor is converted to its monitor-local coords, compared against the
    displayed-image rect (global), and mapped 1:1 into feed space. Result:
    (feed_x, feed_y) or None when the anchor is OUTSIDE the displayed image
    (pet parked over the bars / off the feed window) — callers reject, they
    do not clamp: a clamped aim silently fires at the wrong spot.
    feed_x/feed_y override for a one-shot aim (pet stays put); those must
    already be feed pixels (bounds-checked by the caller)."""
    if feed_x is not None and feed_y is not None:
        return float(feed_x), float(feed_y)
    anchor = pet_anchor_global()
    if anchor is None:
        return None
    wx, wy, ww, wh, monx, mony = window_geom()
    if ww <= 0 or wh <= 0:
        return None
    # displayed image rect: contain-fit of FEED_W x FEED_H into the window
    ar = FEED_W / FEED_H if FEED_H else 16.0 / 9.0
    if ww / float(wh) > ar:
        vw, vh = wh * ar, float(wh)
    else:
        vw, vh = float(ww), ww / ar
    vx = wx + (ww - vw) / 2.0              # window-local origin of the image
    vy = wy + (wh - vh) / 2.0
    axl = anchor[0] - monx                 # anchor in the window's monitor
    ayl = anchor[1] - mony
    if not (vx <= axl <= vx + vw and vy <= ayl <= vy + vh):
        return None                        # outside the displayed image
    return ((axl - vx) / vw * FEED_W, (ayl - vy) / vh * FEED_H)


def push_control(evt):
    """Push a control event to the receiver for the sender to apply on the
    mark's machine. Returns the receiver's reply (seq [+ geometry_version])
    or an error string."""
    import urllib.request
    try:
        req = urllib.request.Request(
            CONTROL_URL, data=json.dumps(evt).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            reply = json.load(r)
        extra = " (geo " + str(reply["geometry_version"]) + ")" \
                if reply.get("geometry_version") else ""
        if reply.get("held"):
            # Enqueued while the mark is PAUSED: the sender drains (drops) it
            # without applying. Say it LOUD so nobody thinks a held action
            # landed.
            return "HELD (pause ON) - NOT applied, dropped, not replayed (seq " + str(reply.get("seq")) + ")"
        return "fired (seq " + str(reply.get("seq")) + ")" + extra
    except Exception as e:
        return "control down: " + str(e)


def fetch_frame():
    """Current feed frame as raw JPEG bytes, or None when the feed is down."""
    import urllib.request
    try:
        with urllib.request.urlopen(LATEST_URL, timeout=3) as r:
            return r.read()
    except Exception:
        return None


def _thumb(jpeg_bytes):
    """Downscaled grayscale thumbnail (128x72) as a flat list of ints —
    enough to measure how much of the frame changed between two shots."""
    from io import BytesIO
    from PIL import Image
    try:
        im = Image.open(BytesIO(jpeg_bytes)).convert("L").resize((128, 72))
        return list(im.getdata())
    except Exception:
        return None


def journal(entry):
    """Append one action->outcome line to journal.jsonl (the harness's
    eval/learning data). Never throws. Large transient fields (frame images)
    are stripped before the line is written. While a task capture window is
    open (task_start..task_end), each step is ALSO copied into the active
    routine artifact (improvement #1) — the slice that becomes the replay."""
    try:
        row = {k: v for k, v in entry.items() if k not in ("before_img",)}
        with open(JOURNAL, "a") as f:
            f.write(json.dumps(row) + "\n")
        c = row.get("cmd")
        if ACTIVE_TASK is not None and c not in ("task_start", "task_end"):
            ACTIVE_TASK["journal_lines"].append(row)
    except Exception:
        pass


def verify_action(entry, settle=0.6):
    """Close the loop on a fired action: did the mark's screen actually
    respond? `entry["before_img"]` holds the pre-action frame bytes. Sleeps
    `settle` for the sender to apply + a new frame to arrive, then measures
    the pixel-diff ratio of the thumbnails. Returns the diff ratio (float)
    or None (feed down, unverifiable) and journaled entry carries
    `changed` (bool) + `diff` (ratio)."""
    time.sleep(settle)
    after = fetch_frame()
    before = entry.get("before_img")
    if not after or not before:
        entry["changed"] = None
        entry["diff"] = None
        entry["frame_after"] = None
        journal(entry)
        return None
    import hashlib
    entry["frame_after"] = hashlib.sha1(after).hexdigest()[:12]
    tb, ta = _thumb(before), _thumb(after)
    if tb is None or ta is None:
        entry["changed"] = None
        entry["diff"] = None
        journal(entry)
        return None
    diff = sum(1 for a, b in zip(tb, ta) if abs(a - b) > 24) / len(tb)
    diff = round(diff, 4)
    entry["diff"] = diff
    entry["changed"] = diff > 0.005   # ~1 pixel in 200: below that = no response
    journal(entry)
    return diff


# --- Improvement #1: save one successful agent-issued command sequence as an
# external, replayable routine. The TRIGGER is explicit, not detected: the
# agent (or a person) declares task_start before the steps and task_end AFTER
# the outcome check (#3) passes. The brain's job in between is pure data
# capture: journal the steps, stamp before/after frames (bookends), and at
# task_end package the artifact (steps + frames + check) under routines/.
# Nothing here touches the sender; the replay path is its own script.

def _bookend(label):
    """Stamp the receiver's currently-stored frame as the before/after
    boundary of the capture window (returns {n, sha1, at} or None)."""
    import urllib.request
    try:
        req = urllib.request.Request(BOOKEND_URL,
                data=json.dumps({"label": label}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def _save_frame(bk, path):
    """Copy the stored frame file for a bookend entry into the routine dir."""
    if not bk:
        return None
    try:
        src = os.path.join(HERE, "inbox", "frames",
                           "frame-%08d-%s.jpg" % (int(bk["n"]), bk["sha1"]))
        if os.path.exists(src):
            shutil.copy(src, path)
            return os.path.basename(path)
    except Exception:
        pass
    return None


def task_start(name):
    """Open a capture window for task `name`. Stamps t0 + the before frame."""
    global ACTIVE_TASK
    if ACTIVE_TASK is not None:
        return "task: already capturing %r (task_end it first)" % ACTIVE_TASK["name"]
    bk = _bookend("before")
    entry = {"t": time.time(), "cmd": "task_start", "name": str(name),
             "frame_before": (bk.get("sha1") if bk else None)}
    ACTIVE_TASK = {"name": str(name), "t0": time.time(), "journal_lines": [],
                   "frame_before": bk}
    res = "task: capturing %r (before frame %s)" % (name, entry["frame_before"] or "none")
    entry["result"] = res
    journal(entry)
    return res


def task_end(name=None, ok=True, check=None):
    """Close the capture window: stamp the after frame, slice the journaled
    steps, and write routines/<name>.{json,before.jpg,after.jpg}. `ok` marks
    whether the OUTCOME CHECK passed — save it either way, but only a passing
    run is replayable-as-verified. `check` (free form) records WHAT was
    verified (e.g. {"type":"landmark","desc":"calc window visible"})."""
    global ACTIVE_TASK
    if ACTIVE_TASK is None:
        return "task: nothing capturing (task_start first)"
    t = ACTIVE_TASK
    if name:
        t["name"] = str(name)
    bk = _bookend("after")
    entry = {"t": time.time(), "cmd": "task_end", "name": t["name"],
             "ok": bool(ok), "check": check,
             "frame_after": (bk.get("sha1") if bk else None)}
    os.makedirs(ROUTINES_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", t["name"])[:48] or "task"
    art = {"name": t["name"], "status": "ok" if ok else "failed",
           "t0": t["t0"], "t1": time.time(),
           "check": check,
           "frame_before": t["frame_before"], "frame_after": bk,
           "steps": t["journal_lines"]}
    art_path = os.path.join(ROUTINES_DIR, safe + ".json")
    with open(art_path, "w") as f:
        json.dump(art, f, indent=1)
    bfile = _save_frame(t["frame_before"], os.path.join(ROUTINES_DIR, safe + ".before.jpg"))
    afile = _save_frame(bk, os.path.join(ROUTINES_DIR, safe + ".after.jpg"))
    art["before_file"] = bfile
    art["after_file"] = afile
    with open(art_path, "w") as f:
        json.dump(art, f, indent=1)
    ACTIVE_TASK = None
    res = ("task: saved %s (%d steps, status %s, frames %s/%s)"
           % (safe, len(art["steps"]), art["status"],
              "yes" if bfile else "no", "yes" if afile else "no"))
    entry["result"] = res
    journal(entry)
    return res


def inject_click(feed_x=None, feed_y=None, button=1):
    """Score, via the pet: pointer = where the spotter stands (or the aim
    override), mapped into feed space and pushed down the control channel.
    The SENDER applies it on the mark's own screen (SendInput) — no loop.
    Then verifies: did the mark's screen actually respond? (outcome is
    journaled — the harness's learning data).
    Explicit feed coords must lie inside the CURRENT feed bounds
    (0 <= x < FEED_W, 0 <= y < FEED_H); out-of-bounds aims are REJECTED, not
    clamped. Pet-driven aims outside the displayed image are rejected too."""
    p = pet_pointer(feed_x, feed_y)
    if p is None:
        return "score: click rejected: pet is outside the feed image (reposition it over the pane)"
    fx, fy = p
    if feed_x is not None and feed_y is not None and not (0 <= fx < FEED_W and 0 <= fy < FEED_H):
        return "score: click rejected: feed %g,%g outside 0..%d x 0..%d" % (fx, fy, FEED_W, FEED_H)
    fx = min(max(int(round(fx)), 0), FEED_W - 1); fy = min(max(int(round(fy)), 0), FEED_H - 1)
    before_img = fetch_frame()
    entry = {"t": time.time(), "cmd": "click", "x": fx, "y": fy,
             "button": button, "before_img": before_img}
    res = push_control({"type": "click", "x": fx, "y": fy, "button": button})
    entry["result"] = res
    verify_action(entry)
    ch = entry.get("changed")
    verdict = {True: "mark responded (diff " + str(entry.get("diff")) + ")",
               False: "no visible response (diff " + str(entry.get("diff")) + ")",
               None: "unverifiable (feed down)"}[ch]
    return f"score: crosshair {fx},{fy} -> {res}; {verdict}"


def inject_move(feed_x=None, feed_y=None):
    """Score, via the pet: move the mark's cursor WITHOUT clicking. Coords in
    feed space; the SENDER maps to its own desktop and reads the cursor back.
    A journal entry is written (requested feed pos + fired seq) so drive.py's
    journal-wait works; the ground-truth readback (mapped/actual cursor)
    comes back from the sender via /result — verified there, not by pixel
    diff (a bare cursor move may not paint a new frame at all)."""
    fx, fy = pet_pointer(feed_x, feed_y)
    if fx is None:
        return "score: move rejected: pet is outside the feed image (reposition it over the pane)"
    if not (0 <= fx < FEED_W and 0 <= fy < FEED_H):
        return "score: move rejected: feed %g,%g outside 0..%d x 0..%d (stale aim?)" % (fx, fy, FEED_W, FEED_H)
    fx = int(round(fx)); fy = int(round(fy))
    entry = {"t": time.time(), "cmd": "move", "x": fx, "y": fy,
             "changed": None, "diff": None, "frame_after": None}
    res = push_control({"type": "move", "x": fx, "y": fy, "verify": True})
    entry["result"] = res
    journal(entry)
    return "score: cursor moved to feed %d,%d -> %s" % (fx, fy, res)


def inject_type(text):
    """Score, via the pet: keystrokes land on whatever has FOCUS on the mark's
    machine — position-independent (the sender's Type() sends unicode to the
    foreground window). The pet's crosshair is journaled as context, NOT a
    precondition: if the pet is outside the feed image the type still fires
    (position None in the journal). Clicks/moves are the position-gated
    ones — a click at a clamped crosshair is a silent miss, but a keystroke
    with a wrong position is just a wrong journal note."""
    p = pet_pointer()
    if p is not None:
        fx, fy = p
        fx = min(max(int(fx), 0), FEED_W - 1); fy = min(max(int(fy), 0), FEED_H - 1)
    else:
        fx = fy = None    # pet parked off the feed: type anyway, journal it bare
    before_img = fetch_frame()
    entry = {"t": time.time(), "cmd": "type", "text": text,
             "x": fx, "y": fy, "before_img": before_img}
    res = push_control({"type": "type", "text": text, "x": fx, "y": fy})
    entry["result"] = res
    verify_action(entry)
    ch = entry.get("changed")
    verdict = {True: "mark responded (diff " + str(entry.get("diff")) + ")",
               False: "no visible response (diff " + str(entry.get("diff")) + ")",
               None: "unverifiable (feed down)"}[ch]
    return f"score: typed {len(text)} chars at crosshair {fx},{fy} -> {res}; {verdict}"


def main():
    print(f"petcam brain: watching {STATE_URL} (poll {POLL}s)", flush=True)
    last_health = None
    last_cmd_mtime = None
    while True:
        refresh_feed_dims()
        st = fetch_state()
        now = time.time()
        if st is None:
            health = "offline"
        else:
            age = now - st.get("last_frame_at", 0)
            src = st.get("last_client", "?")
            is_placeholder = (src == PLACEHOLDER_IP)
            if st.get("frames", 0) == 0:
                health = "waiting"
            elif age > STALE_AFTER:
                health = "stale"
            else:
                health = "placeholder-live" if is_placeholder else "live"

        # Drive the pet pose from feed health (only on change)
        if health != last_health:
            if health == "offline":
                pet_state("error");  pet_think("lost the mark's feed - trace is gone")
            elif health == "waiting":
                pet_state("idle");   pet_think("waiting for the mark to power up")
            elif health == "stale":
                pet_state("waiting");pet_think("mark's screen went quiet")
            elif health == "placeholder-live":
                pet_state("running");pet_think("recon feed up (mock mark)")
            else:  # live
                pet_state("running");pet_think("in on the mark's screen")
            last_health = health

        # Drain command file. Dedup is MTIME-based, not content-based: a file
        # whose mtime matches the last consumed file is the SAME write (a
        # duplicate write before consumption, or a file we failed to delete)
        # and is skipped silently. A fresh write — even one whose content
        # equals the previous command (backspace x2) — is a new mtime and
        # FIRES. With the fast loop (0.4s), content-based dedup swallowed
        # legitimate repeated inputs.
        if os.path.exists(CMD_FILE):
            try:
                mtime = os.path.getmtime(CMD_FILE)
            except OSError:
                mtime = None
            try:
                with open(CMD_FILE) as f:
                    cmd = json.load(f)
            except Exception:
                cmd = None
            if cmd is not None and mtime != last_cmd_mtime:
                try:
                    result = handle_cmd(cmd)
                except Exception as e:
                    result = "ERROR: %r" % e
                    log(f"handle_cmd exception: {e!r}", flush=True)
                last_cmd_mtime = mtime
                # consume
                try: os.remove(CMD_FILE)
                except OSError: pass
        else:
            last_cmd_mtime = None

        time.sleep(POLL)


def handle_cmd(cmd):
    c = cmd.get("cmd")
    if c == "think":
        pet_think(str(cmd.get("text", "")))
    elif c == "state":
        pet_state(str(cmd.get("state", "idle")))
    elif c == "click":
        # The score, via the pet: where the spotter stands IS the crosshair on
        # the mark's screen. cmd "x"/"y" (feed 1280x720 space) aim a one-shot
        # override; omitted => fire exactly where the pet currently stands.
        # The event goes to the receiver's control channel; the sender applies
        # it on the MARK's machine (SendInput) — no self-referential HQ click.
        fx = float(cmd["x"]) if "x" in cmd else None
        fy = float(cmd["y"]) if "y" in cmd else None
        pet_think(inject_click(fx, fy, int(cmd.get("button", 1))))
    elif c == "type":
        pet_think(inject_type(str(cmd["text"])))
    elif c == "move":
        # Reposition the mark's cursor without clicking (aim before acting).
        # Explicit x/y (feed space) reject on out-of-bounds instead of
        # clamping — a clamped aim is a silent miss. Omitted => the pet's spot.
        fx = float(cmd["x"]) if "x" in cmd else None
        fy = float(cmd["y"]) if "y" in cmd else None
        pet_think(inject_move(fx, fy))
    elif c == "key":
        # Named keystroke, optional modifiers: {"cmd":"key","key":"t","mods":["ctrl"]}
        key = str(cmd.get("key", ""))
        mods = [str(m) for m in cmd.get("mods", [])]
        before_img = fetch_frame()
        entry = {"t": time.time(), "cmd": "key", "key": key, "mods": mods,
                 "before_img": before_img}
        res = push_control({"type": "key", "key": key, "mods": mods})
        entry["result"] = res
        verify_action(entry)
        ch = entry.get("changed")
        verdict = {True: "mark responded (diff " + str(entry.get("diff")) + ")",
                   False: "no visible response (diff " + str(entry.get("diff")) + ")",
                   None: "unverifiable (feed down)"}[ch]
        pet_think(f"score: key {key} {'+'.join(mods) if mods else ''} -> {res}; {verdict}")
    elif c == "rec_start":
        # Demo recorder ON (sender-side low-level hooks + batch flush).
        # Bookend BEFORE: stamp the currently-stored frame as the start
        # boundary (HQ receive time) so the window's first events can be
        # associated to real frames by timestamp (measured gaps).
        try:
            import urllib.request as _ur
            with _ur.urlopen(BOOKEND_URL, data=b'{"label":"before"}',
                             method="POST", timeout=3) as r:
                bk = json.load(r)
            pet_think("recorder: bookend-before frame %s (t %.2f)" % (bk.get("n"), bk.get("at", 0)))
        except Exception as e:
            pet_think("recorder: bookend-before failed (%r)" % (e,))
        res = push_control({"type": "rec_start"})
        pet_think(f"recorder: start -> {res}")
    elif c == "rec_stop":
        # Demo recorder OFF (clean unhook + final drain). Bookend AFTER:
        # stamp the currently-stored frame as the end boundary.
        res = push_control({"type": "rec_stop"})
        try:
            import urllib.request as _ur
            with _ur.urlopen(BOOKEND_URL, data=b'{"label":"after"}',
                             method="POST", timeout=3) as r:
                bk = json.load(r)
            pet_think("recorder: bookend-after frame %s (t %.2f)" % (bk.get("n"), bk.get("at", 0)))
        except Exception as e:
            pet_think("recorder: bookend-after failed (%r)" % (e,))
        pet_think(f"recorder: stop -> {res}")
    elif c == "rec_status":
        res = push_control({"type": "rec_status"})
        pet_think(f"recorder: status -> {res}")
    elif c == "loot":
        # Heist move: carry the mark's agent memory back to HQ. DATA ONLY  -
        # read the hermes session store off disk (no Hermes process in the loop),
        # surface a one-line summary as the pet's thought. This is the
        # "learn from Hermes" channel, framed as lifting intel.
        # A session dump = {request: {body: {messages: [{role, content}, ...]}}}
        try:
            from pathlib import Path
            import json as _json
            sess_dir = Path.home() / ".hermes" / "sessions"
            dumps = sorted([p for p in sess_dir.glob("request_dump_*.json")],
                           key=lambda p: p.stat().st_mtime, reverse=True)
            if not dumps:
                pet_think("loot: no intel in the vault yet")
            else:
                latest = dumps[0]
                size_kb = latest.stat().st_size // 1024
                try:
                    data = _json.loads(latest.read_text())
                    msgs = data.get("request", {}).get("body", {}).get("messages", [])
                    n_user = sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "user")
                    pet_think(f"loot lifted: {n_user} user turns in {len(msgs)} msgs, {len(dumps)} intel files ({size_kb}kb)")
                except Exception:
                    pet_think(f"loot lifted: {len(dumps)} intel files in the vault")
        except Exception as e:
            pet_think(f"loot failed: {e}")
    elif c == "task_start":
        # Improvement #1: open the capture window for a named task. Fired
        # BEFORE the steps; bookends the before frame. task_start() journals
        # its own marker line (with the result) — don't double-journal here.
        res = task_start(str(cmd.get("name", "task")))
        pet_think(res)
    elif c == "task_end":
        # Close the window AFTER the outcome check (#3) passes: package the
        # slice (steps + before/after frames + check) into routines/.
        res = task_end(name=(str(cmd["name"]) if cmd.get("name") else None),
                       ok=bool(cmd.get("ok", True)),
                       check=cmd.get("check"))
        pet_think(res)
    elif c == "recon":
        # Observation only: no action, just log what the mark's screen
        # currently looks like. Fingerprint + feed state -> journal.
        import urllib.request, hashlib
        raw = fetch_frame()
        fp = hashlib.sha1(raw).hexdigest()[:12] if raw else None
        entry = {"t": time.time(), "cmd": "recon", "frame_before": fp}
        try:
            with urllib.request.urlopen(STATE_URL, timeout=3) as r:
                st = json.load(r)
            entry["feed_age"] = round(time.time() - st.get("last_frame_at", 0), 1)
            entry["feed_frames"] = st.get("frames", 0)
        except Exception:
            entry["feed_age"] = None
        journal(entry)
        pet_think(f"recon: frame {fp or 'none'} (age {entry.get('feed_age')}s)")
    elif c == "journal":
        # Peek: last journal lines as the pet's thought (the harness's own
        # action->outcome memory, surfaced).
        try:
            lines = open(JOURNAL).read().strip().splitlines()[-3:]
            for ln in lines:
                e = json.loads(ln)
                ch = e.get("changed")
                verdict = {True: "responded", False: "silent", None: "n/a"}[ch]
                pet_think(f"journal: {e.get('cmd')} -> {verdict} ({e.get('result') or 'observed'})")
        except FileNotFoundError:
            pet_think("journal: empty (no actions logged yet)")
    elif c in ("pause", "resume"):
        # STOP/START the mark's action loop. Toggles the receiver's PAUSED
        # flag (sender drains /control while paused, so nothing fires).
        # pause => on=True, resume => on=False. Same channel the mark's F12
        # uses, so HQ and the physical hotkey stay in sync.
        import urllib.request
        body = {"on": (c == "pause"), "src": "brain"}
        try:
            req = urllib.request.Request(PAUSE_URL, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                out = json.load(r)
            pet_think(f"{'PAUSED' if out['paused']['on'] else 'resumed'} (src brain) - mark actions {out['paused']['on'] and 'HOLDING' or 'flowing'}")
        except Exception as e:
            pet_think(f"pause toggle failed: {e}")
    elif c == "pace":
        # Toggle human-pace typing on/off. {"cmd":"pace","on":true|false|null}
        import urllib.request
        want = cmd.get("on")
        body = {"pace": (None if want is None else bool(want)), "src": "brain"}
        try:
            req = urllib.request.Request(PAUSE_URL, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                out = json.load(r)
            pet_think(f"pacing {'ON (human cadence)' if out['paced']['on'] else 'OFF (burst)'} (src brain)")
        except Exception as e:
            pet_think(f"pace toggle failed: {e}")
    elif c == "pause-status":
        import urllib.request
        try:
            with urllib.request.urlopen(STATE_URL, timeout=3) as r:
                st = json.load(r)
            pet_think(f"pause={st.get('paused')} pace={st.get('paced')} (src {st.get('pause_src')})")
        except Exception as e:
            pet_think(f"pause-status failed: {e}")
    else:
        pet_think(f"cmd? {c}")


if __name__ == "__main__":
    main()
