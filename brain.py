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
STATE_URL = os.environ.get("PETCAM_STATE", "http://100.86.251.100:9101/state")
CONTROL_URL = os.environ.get("PETCAM_CONTROL", "http://100.86.251.100:9101/control")
POLL = 2.0
STALE_AFTER = 5.0          # seconds without a new frame before "stale"
PLACEHOLDER_IP = "100.86.251.100"   # our own IP => placeholder feed
PET_AGENT = "laptop"        # the pet's agent-state key for the remote machine
PET_POS_FILE = os.path.expanduser("~/.local/state/omarchy/pets/position.json")

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


FEED_W, FEED_H = 1280, 720        # source frame space the senders emit


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
    """Live petcam window geometry (at x,y / size w,h); fallback to the defaults."""
    try:
        out = subprocess.run(["hyprctl", "-j", "clients"],
                             capture_output=True, text=True, timeout=4).stdout
        for c in json.loads(out):
            if c.get("title") == "petcam":
                return (c["at"][0], c["at"][1], c["size"][0], c["size"][1])
    except Exception:
        pass
    return (2064, 890, 480, 270)


def screen_geom():
    """Primary monitor size on the HQ box (fallback 1920x1080)."""
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors"],
                             capture_output=True, text=True, timeout=4).stdout
        m = json.loads(out)[0]
        return (int(m["width"]), int(m["height"]))
    except Exception:
        return (1920, 1080)


def pet_pointer(feed_x=None, feed_y=None):
    """The pet's position IS the pointer: the spotter stands beside/over the
    feed, so the crosshair on the mark's screen = the pet's location relative
    to the feed viewport, mapped 1:1 into feed space (1280x720) and clamped.
    feed_x/feed_y override for a one-shot aim (pet stays put)."""
    if feed_x is not None and feed_y is not None:
        return feed_x, feed_y
    px = py = None
    try:
        pos = json.load(open(PET_POS_FILE))
        for mon in ("DP-1", "HDMI-A-1"):
            if mon in pos:
                px, py = pos[mon]["x"], pos[mon]["y"]
                break
    except Exception:
        pass
    if px is None:
        px, py = 2304, 1246            # known default anchor
    wx, wy, ww, wh = window_geom()
    fx = (px - wx) / ww * FEED_W if ww else FEED_W / 2
    fy = (py - wy) / wh * FEED_H if wh else FEED_H / 2
    return fx, fy


def push_control(evt):
    """Push a control event to the receiver for the sender to apply on the
    mark's machine. Returns the receiver's reply (seq) or an error string."""
    import urllib.request
    try:
        req = urllib.request.Request(
            CONTROL_URL, data=json.dumps(evt).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return "fired (seq " + str(json.load(r).get("seq")) + ")"
    except Exception as e:
        return "control down: " + str(e)


def inject_click(feed_x=None, feed_y=None, button=1):
    """Score, via the pet: pointer = where the spotter stands (or the aim
    override), mapped into feed space and pushed down the control channel.
    The SENDER applies it on the mark's own screen (SendInput) — no loop."""
    fx, fy = pet_pointer(feed_x, feed_y)
    fx = min(max(fx, 0), FEED_W); fy = min(max(fy, 0), FEED_H)
    res = push_control({"type": "click", "x": fx, "y": fy, "button": button})
    return f"score: crosshair {fx},{fy} (spotter spot) -> {res}"


def inject_type(text):
    """Score, via the pet: the cursor moves to the crosshair (the spotter's
    spot), then keystrokes land there — so whatever is under the crosshair
    on the mark's screen receives the typing."""
    fx, fy = pet_pointer()
    fx = min(max(int(fx), 0), FEED_W); fy = min(max(int(fy), 0), FEED_H)
    res = push_control({"type": "type", "text": text, "x": fx, "y": fy})
    return f"score: typed {len(text)} chars at crosshair {fx},{fy} -> {res}"


def main():
    print(f"petcam brain: watching {STATE_URL} (poll {POLL}s)", flush=True)
    last_health = None
    last_cmd = None
    while True:
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

        # Drain command file
        if os.path.exists(CMD_FILE):
            try:
                with open(CMD_FILE) as f:
                    cmd = json.load(f)
            except Exception:
                cmd = None
            if cmd is not None and cmd != last_cmd:
                handle_cmd(cmd)
                last_cmd = cmd
                # consume
                try: os.remove(CMD_FILE)
                except OSError: pass

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
        pet_think(inject_type(str(cmd.get("text", ""))))
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
    else:
        pet_think(f"cmd? {c}")


if __name__ == "__main__":
    main()
