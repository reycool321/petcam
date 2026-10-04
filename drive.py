#!/usr/bin/env python3
"""petcam drive: the proven score-channel driver for the mark's machine.

Saved 2026-10-04 after live verification: clicks and unicode types land on
the mark (feed 1280x720 == the mark's full virtual screen, downscaled; the
native.ps1 sender maps back with px = ox + feedX*screenW/1280).

Usage (all coords in FEED space, 0-1280 x 0-720):
  drive.py shot [path]        # fetch /latest.jpg (default inbox/shot-<ts>.jpg)
  drive.py click X Y [btn]    # left-click at feed X,Y (btn 2 = right)
  drive.py type "text"        # unicode type at the current cursor
  drive.py key KEY [MOD ...]  # enter, l, esc, ...  mods: ctrl alt shift win
  drive.py open-url "URL"     # chrome recipe: focus omnibox (ctrl+l), type, enter
  drive.py state              # feed health one-liner

Every action waits on the journal (brain writes the outcome + screen diff)
and reports it. Pitfalls baked in:
  - changed:false / diff 0.0 does NOT mean the input failed: the verify
    frame is captured milliseconds after the event, and Chrome tab/window
    transitions can paint a frame later. Verify with a FRESH shot + vision.
  - The mark's window layout changes under you (the user rearranges things).
    Re-shoot before every aim pass; don't reuse stale coords across passes.
  - ctrl+l is the robust omnibox focus (no aiming) once the Chrome window
    itself has focus. Give Chrome focus by clicking inside it first.
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("PETCAM_BASE", "http://100.86.251.100:9101")
CMD_FILE = os.path.join(HERE, "cmd.json")
JOURNAL = os.path.join(HERE, "journal.jsonl")


def get(path, timeout=5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.read()


def shot(path=None):
    if path is None:
        os.makedirs(os.path.join(HERE, "inbox"), exist_ok=True)
        path = os.path.join(HERE, "inbox", "shot-" + time.strftime("%H%M%S") + ".jpg")
    open(path, "wb").write(get("/latest.jpg"))
    return path


def state():
    st = json.loads(get("/state"))
    age = time.time() - st["last_frame_at"]
    ctl = time.time() - st.get("last_control_poll_at", 0)
    health = "live" if age < 10 else "stale" if age < 120 else "dead"
    print(f"{health}: frame age {age:.1f}s, ctrl poll {ctl:.1f}s, "
          f"frames {st['frames']}, client {st['last_client']}")
    return health


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
    """Write cmd.json; the brain consumes it and journals the outcome."""
    with open(CMD_FILE, "w") as f:
        json.dump(cmd, f)
    if not wait:
        time.sleep(1.0)
        return None
    mark = _journal_mark()
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


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    c = a[0]
    if c == "shot":
        print(shot(a[1] if len(a) > 1 else None))
    elif c == "state":
        state()
    elif c == "click":
        x, y = float(a[1]), float(a[2])
        btn = int(a[3]) if len(a) > 3 else 1
        e = send({"cmd": "click", "x": x, "y": y, "button": btn})
        print(f"click {x},{y} btn{btn}: {e.get('result') if e else '?'}; {_verdict(e)}")
    elif c == "type":
        e = send({"cmd": "type", "text": a[1]})
        print(f"type {len(a[1])} chars: {e.get('result') if e else '?'}; {_verdict(e)}")
    elif c == "key":
        key = a[1].lower()
        mods = [m.lower() for m in a[2:]]
        e = send({"cmd": "key", "key": key, "mods": mods})
        print(f"key {key} {'+'.join(mods) if mods else ''}: {e.get('result') if e else '?'}; {_verdict(e)}")
    elif c == "open-url":
        url = a[1]
        if len(a) > 2 and a[2] != "--no-focus":
            # give Chrome window focus first: caller must pass the feed coords
            # of a safe point inside the Chrome window
            fx, fy = float(a[3]), float(a[4])
            e = send({"cmd": "click", "x": fx, "y": fy, "button": 1})
            print(f"focus chrome at {fx},{fy}: {e.get('result') if e else '?'}; {_verdict(e)}")
            time.sleep(0.8)
        e = send({"cmd": "key", "key": "l", "mods": ["ctrl"]})
        print(f"ctrl+l: {e.get('result') if e else '?'}; {_verdict(e)}")
        time.sleep(0.8)
        e = send({"cmd": "type", "text": url})
        print(f"type {url}: {e.get('result') if e else '?'}; {_verdict(e)}")
        time.sleep(0.8)
        e = send({"cmd": "key", "key": "enter"})
        print(f"enter: {e.get('result') if e else '?'}; {_verdict(e)}")
        time.sleep(3)
        print(shot())
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
