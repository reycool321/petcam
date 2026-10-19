#!/usr/bin/env python3
"""petcam routine replay (improvement #1): re-fire a saved routine's steps
through the SAME brain command path drive.py uses (cmd.json -> brain ->
/control -> sender), with per-step verification. A separate script on
purpose: a broken replay experiment must never break the running sender.

Usage:
  python3 replay.py <name> [--dry-run] [--settle 0.8] [--check calc]

The success verdict is an EXPLICIT check, not whole-screen similarity
(plain-text spec 2026-10-05): a routine is only marked successful if the
check PASSES on a fresh screenshot; all commands firing is not enough.
Check result + evidence (OCR text + frame hash + evidence file) are stored
with each run under routines/runs/<name>-<ts>.json.

Checkers:
  calc   "Calculator is visible in the foreground in Standard mode" —
         OCR (tesseract, 2x upscale) the fresh frame and require BOTH
         "Calculator" and "Standard" in the read text. Evidence = saved
         frame + the raw OCR text + a pass/fail verdict.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTINES = os.path.join(HERE, "routines")


def thumb(b):
    from io import BytesIO
    from PIL import Image
    try:
        im = Image.open(BytesIO(b)).convert("L").resize((128, 72))
        return list(im.getdata())
    except Exception:
        return None


def diff(a, b):
    if a is None or b is None:
        return None
    return round(sum(1 for x, y in zip(a, b) if abs(x - y) > 24) / len(a), 4)


def ocr_frame(jpeg_bytes):
    """Tesseract OCR of a fresh feed frame (2x upscale, psm 11). Returns the
    raw text or None when the frame can't be read. This is the positive
    signal behind the explicit check — words ON the screen, not pixel noise."""
    import subprocess, tempfile
    from io import BytesIO
    from PIL import Image
    try:
        im = Image.open(BytesIO(jpeg_bytes)).convert("RGB")
        im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        im.save(tf.name)
        r = subprocess.run(["tesseract", tf.name, "stdout", "--psm", "11"],
                           capture_output=True, text=True, timeout=90)
        os.unlink(tf.name)
        return r.stdout or ""
    except Exception:
        return None


def check_calc(jpeg_bytes):
    """Explicit check (plain-text spec): 'Calculator is visible in the
    foreground in Standard mode.' Requires BOTH 'calculator' and 'standard'
    in the OCR'd fresh frame. A failed launch (e.g. calc never came up, or a
    different app is foreground) does NOT contain these words -> reject."""
    txt = ocr_frame(jpeg_bytes)
    if txt is None:
        return False, "unreadable frame (OCR failed)"
    low = txt.lower()
    ok = "calculator" in low and "standard" in low
    return ok, ("both terms found" if ok
                else "missing: %s" % [t for t in ("calculator", "standard")
                                      if t not in low])


def store_run_evidence(name, art, steps_fired, check, passed, ocr_text, frame_bytes):
    """Plain-text spec: 'Store the check result and evidence with each run.'
    Writes routines/runs/<name>-<ts>.json + the evidence frame; returns the
    evidence path. Never raises (a failed evidence store must not mask the
    verdict)."""
    import hashlib
    try:
        runs = os.path.join(ROUTINES, "runs")
        os.makedirs(runs, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        frame_file = "%s-%s.jpg" % (name, ts)
        with open(os.path.join(runs, frame_file), "wb") as f:
            f.write(frame_bytes)
        rec = {"name": name, "ts": ts,
               "routine_status": art.get("status"),
               "steps_fired": steps_fired, "steps_total": len(art.get("steps", [])),
               "check": check, "passed": bool(passed),
               "frame_sha1": hashlib.sha1(frame_bytes).hexdigest()[:12],
               "evidence_frame": frame_file,
               "ocr_text": (ocr_text or "")[:2000]}
        rec_path = os.path.join(runs, "%s-%s.json" % (name, ts))
        with open(rec_path, "w") as f:
            json.dump(rec, f, indent=1)
        return rec_path
    except Exception as e:
        print("evidence store failed: %r" % e)
        return None


def replay(name, dry=False, settle=0.8, check=None):
    import drive
    import urllib.request
    path = os.path.join(ROUTINES, name + ".json")
    if not os.path.exists(path):
        avail = sorted(p[:-5] for p in os.listdir(ROUTINES) if p.endswith(".json")) \
            if os.path.isdir(ROUTINES) else []
        print("no routine %r (available: %s)" % (name, ", ".join(avail) or "none"))
        return 2
    art = json.load(open(path))
    if art.get("status") != "ok":
        print("routine %s was saved with status %s — replay is an experiment, not a verified run"
              % (name, art.get("status")))
    steps = [s for s in art.get("steps", [])
             if isinstance(s.get("result"), str) and s["result"].startswith("fired (seq")]
    skipped = len(art.get("steps", [])) - len(steps)
    print("routine %s (%s): %d replayable steps (%d skipped)"
          % (name, art.get("status", "?"), len(steps), skipped))
    if dry:
        for i, s in enumerate(steps):
            c = s.get("cmd")
            extra = {
                "click": "(%s,%s)" % (s.get("x"), s.get("y")),
                "key": ("key=%s mods=%s" % (s.get("key"), "+".join(s.get("mods") or []))),
                "type": ("text=%r" % (s.get("text", "")[:32],)),
            }.get(c, "")
            print("  %2d. %s %s" % (i + 1, c, extra))
        return 0
    fired_count = 0
    for i, s in enumerate(steps):
        c = s.get("cmd")
        if c == "click":
            e = drive.send({"cmd": "click", "x": s.get("x"), "y": s.get("y"),
                            "button": s.get("button", 1)}, timeout=25)
        elif c == "key":
            e = drive.send({"cmd": "key", "key": s.get("key"),
                            "mods": s.get("mods") or []}, timeout=25)
        elif c == "type":
            e = drive.send({"cmd": "type", "text": s.get("text", "")}, timeout=25)
        else:
            print("  %2d. skip (unknown step %r)" % (i + 1, c))
            continue
        if e is None:
            print("  %2d. %s -> NO journal entry (brain down? timeout) — STOP" % (i + 1, c))
            return 1
        fired = str(e.get("result", "")).startswith("fired (seq")
        print("  %2d. %s -> %s; %s" % (i + 1, c, e.get("result"), drive._verdict(e)))
        if not fired:
            print("  STOP: step %d did not fire" % (i + 1))
            return 1
        fired_count += 1
        time.sleep(settle)
    # Explicit success verdict (plain-text spec): the check on a FRESH,
    # SESSION-MATCHED screenshot decides, not 'all commands fired'. The check
    # is READ FROM THE ROUTINE ARTIFACT (art['check']) — a window-ocr check
    # goes through windowcheck (stale/session gate + window identification +
    # region OCR). A failing check stores its evidence and returns 1.
    check = check or (art.get("check") or {}).get("type")
    if check:
        import windowcheck
        try:
            rec = windowcheck.run(name, store=True)
        except Exception as e:
            print("check %s: could not evaluate (feed down? %r) — NOT marked successful" % (check, e))
            return 1
        print("check window-ocr: %s (%s)" % (
            "PASS" if rec["passed"] else "FAIL", rec["verdict"]))
        if rec.get("rec_path"):
            print("evidence: %s" % rec["rec_path"])
        if not rec["passed"]:
            print("routine NOT marked successful (check rejected — all commands fired is not enough)")
            return 1
        print("routine marked successful (explicit window check passed)")
        return 0
    print("replay complete (no --check; all commands fired is NOT a success verdict — add one)")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0 if args else 2)
    name = os.path.splitext(os.path.basename(args[0]))[0]
    dry = "--dry-run" in args
    settle = 0.8
    if "--settle" in args:
        settle = float(args[args.index("--settle") + 1])
    check = None
    if "--check" in args:
        check = args[args.index("--check") + 1]
    sys.exit(replay(name, dry=dry, settle=settle, check=check))
