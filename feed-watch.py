#!/usr/bin/env python3
"""petcam feed watchdog: is the mark's feed actually usable RIGHT NOW?

Checks, in order:
  1. frame age   (receiver /state) -- sender alive?
  2. black frame (latest.jpg mean luminance) -- lid closed / display asleep?
     CopyFromScreen keeps streaming BLACK frames when the display is off;
     frame age looks healthy but the content is unusable. This is THE
     failure mode to catch.
  3. pause state -- so an alert says "feed fine, we're paused" vs "feed DEAD".

Exit code: 0 = usable, 1 = problem (stdout line states which).
Stdout is a single line: the verdict. Cron/alerting just forwards it.
"""
import os
import sys
import time
import urllib.request

BASE = os.environ.get("PETCAM_BASE", "http://100.86.251.100:9101")
HERE = os.path.dirname(os.path.abspath(__file__))
LATEST = os.path.join(HERE, "latest.jpg")

STALE_S = 15.0      # frame older than this => sender is down
BLACK_MEAN = 10.0   # JPEG mean luminance below this => display is asleep


def verdict():
    """Returns (exit_code, line). Lines are DETERMINISTIC (no timestamps):
    the cron monitor hashes them; the same verdict must hash the same or the
    agent wakes on every tick."""
    # 1. frame age
    try:
        with urllib.request.urlopen(BASE + "/state", timeout=5) as r:
            st = __import__("json").load(r)
    except Exception:
        return 1, "RECEIVER DOWN: %s:9101 unreachable (receiver service?)" % BASE

    paused = "pause ON" if st.get("paused") else "pause off"

    # 2. black-frame check on the latest stored frame (display asleep ->
    #    CopyFromScreen streams black; frame age looks healthy, content dead)
    age = time.time() - st.get("last_frame_at", 0)
    if age < 5 and os.path.exists(LATEST):
        try:
            from PIL import Image
            im = Image.open(LATEST).convert("L").resize((64, 36))
            data = im.tobytes()
            if sum(data) / len(data) < BLACK_MEAN:
                return 1, "FEED BLACK (screen mean < %.0f) -- lid closed or display asleep, %s" % (BLACK_MEAN, paused)
        except Exception:
            pass  # no PIL / unreadable frame: age-only check

    if age >= STALE_S:
        return 1, "FEED DEAD: no fresh frames (> %ds old), client=%s, %s" % (int(STALE_S), st.get("last_client"), paused)
    return 0, "ok (%s)" % paused


if __name__ == "__main__":
    code, line = verdict()
    print(line)
    sys.exit(code)
