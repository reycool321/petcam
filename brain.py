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
POLL = 2.0
STALE_AFTER = 5.0          # seconds without a new frame before "stale"
PLACEHOLDER_IP = "100.86.251.100"   # our own IP => placeholder feed
PET_AGENT = "laptop"        # the pet's agent-state key for the remote machine

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
        # Phase 2: forward a click into the remote machine. For now we surface it
        # as a thought; real injection lands when the control channel exists.
        pet_think(f"click @ {cmd.get('x')},{cmd.get('y')} (pending control link)")
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
