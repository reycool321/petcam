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
POLL = 2.0
STALE_AFTER = 5.0          # seconds without a new frame before "stale"
PLACEHOLDER_IP = "100.86.251.100"   # our own IP => placeholder feed
PET_AGENT = "laptop"        # the pet's agent-state key for the remote machine
PET_POS_FILE = os.path.expanduser("~/.local/state/omarchy/pets/position.json")

FEED_W, FEED_H = 1280, 720   # fallback only. refresh_feed_dims() overrides from
                             # the receiver's /state (measured from ACTUAL
                             # received frames, not the sender's nominal claim).
                             # All aim math must use these — never the literal.
GEO = {"v": None, "at": 0.0}  # current geometry version (refreshed per poll)


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
    are stripped before the line is written."""
    try:
        row = {k: v for k, v in entry.items() if k not in ("before_img",)}
        with open(JOURNAL, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def verify_action(entry, settle=1.5):
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
    last_cmd = None
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

        # Drain command file. last_cmd dedupes ONLY consecutive repeats within
        # one poll cycle (e.g. a file written twice before consumption); it is
        # cleared each time the queue is empty so a genuinely repeated command
        # (click the same spot again) still fires.
        if os.path.exists(CMD_FILE):
            try:
                with open(CMD_FILE) as f:
                    cmd = json.load(f)
            except Exception:
                cmd = None
            if cmd is not None and cmd != last_cmd:
                try:
                    result = handle_cmd(cmd)
                except Exception as e:
                    result = "ERROR: %r" % e
                    log(f"handle_cmd exception: {e!r}", flush=True)
                last_cmd = cmd
                # consume
                try: os.remove(CMD_FILE)
                except OSError: pass
            else:
                journal({"t": time.time(), "cmd": cmd.get("cmd") if isinstance(cmd, dict) else cmd,
                         "result": "deduped (identical to previous command)"})
        else:
            last_cmd = None

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
    else:
        pet_think(f"cmd? {c}")


if __name__ == "__main__":
    main()
