#!/usr/bin/env python3
"""windowcheck — window-scoped success checker (improvement #1 follow-up).

Reads the success condition from the routine artifact's `check` field and
verifies it on a FRESH, SESSION-MATCHED feed frame:

  1. STALENESS + SESSION gate. Fetch /state. Reject if the newest frame is
     older than check.max_age_s (STALE), or the feeding client / frame size
     does not match the session the routine was captured under (MISMATCH).
  2. IDENTIFY. Locate the intended window (check.window_title) on the fresh
     frame. A real window TITLE is a word that is ALONE ON ITS ROW: a large
     gap to the next word on the right and few same-line neighbours (the
     min/max/close buttons are icons, not text). The same word in chat/body
     text is EMBEDDED IN A SENTENCE: a tiny right gap and many neighbours.
     Reject when there is NO clean title (not identified) or MORE THAN ONE
     (ambiguous).
  3. OCR INSIDE BOUNDS. Derive the window's CURRENT bounds from the located
     title and OCR only that region — never the whole screen — so the
     assistant's own DM chat text cannot contaminate the verdict.
  4. TOKENS. Require every check.tokens word to appear inside the window
     region. (window_title gives "calculator"; the body must add e.g.
     "standard" = Standard mode.)
  5. EVIDENCE. Store the verdict + the frame + OCR text + geometry + gate
     results with the run. TRUST IS NOT UPDATED HERE — the ledger stays
     manual until the hexagon bridge is wired.

Runs entirely on the HQ side. The sender (native.ps1) is untouched.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from io import BytesIO

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "routines", "runs")

# --- identification thresholds (measured against real title vs chat body) ---
# real calc title: right-gap 121, same-line-mates 3
# chat-body word:  right-gap 5,   same-line-mates 11
GAP_MIN = 50      # min gap (px) to the nearest word on the right to count as a title
MATE_MAX = 6      # max same-line neighbour words to count as a title
TITLE_YEDGE = 120 # a window title lives near the top third of a window, not mid-page

# --- window-bounds offsets (from the captured calculator window) -----------
WIN_LEFT = 40     # title is left-aligned; extend left of the word a little
WIN_RIGHT = 420   # cover the window body width
WIN_TOP = 30      # a little above the title row
WIN_BOTTOM = 420  # cover the window body height


def _ocr_tsv(frame_bytes):
    """Tesseract TSV OCR (3x upscale, psm 11). Returns a list of
    {text,x,y,w,h} for confident words, or None on OCR failure."""
    from PIL import Image
    try:
        im = Image.open(BytesIO(frame_bytes)).convert("RGB")
        W, H = im.size
        im2 = im.resize((W * 3, H * 3), Image.LANCZOS)
        tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        im2.save(tf.name)
        r = subprocess.run(["tesseract", tf.name, "stdout", "--psm", "11", "tsv"],
                           capture_output=True, text=True, timeout=150)
        os.unlink(tf.name)
    except Exception:
        return None
    words = []
    for line in r.stdout.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12:
            continue
        try:
            conf = float(f[10])
        except Exception:
            continue
        txt = f[11].strip()
        if not txt or conf < 40:
            continue
        x, y, w, h = (int(v) // 3 for v in f[6:10])
        words.append({"text": txt, "x": x, "y": y, "w": w, "h": h})
    return words


def _anchor_near(words, cand, anchor_token, dx_max=90, dy_min=8, dy_max=70):
    """A real window is ANCHORED: the mode word (e.g. 'standard') sits just
    BELOW the title, close in x. The DM chat has 'calculator' but never this
    geometric relationship, so requiring the anchor rejects the chat body."""
    for w in words:
        if w["text"].lower() != anchor_token:
            continue
        dy = w["y"] - cand["y"]
        dx = abs(w["x"] - cand["x"])
        if dy_min <= dy <= dy_max and dx <= dx_max:
            return True
    return False


def _clean_titles(words, title_token, anchor_token=None):
    """Return the positions of words that look like a WINDOW TITLE for
    title_token: alone on its row (big right gap, few neighbours) AND, if an
    anchor_token is given, with that word anchored just below (a real
    window's mode tab). Chat/body occurrences of the title word fail this
    filter — they are embedded in a sentence and lack the anchor."""
    cands = []
    for w in words:
        if w["text"].lower() != title_token:
            continue
        ty0, ty1 = w["y"], w["y"] + w["h"]
        right = [m for m in words
                 if m is not w and m["x"] > w["x"] + w["w"]
                 and not (m["y"] > ty1 or m["y"] + m["h"] < ty0)]
        right.sort(key=lambda m: m["x"])
        right_gap = (right[0]["x"] - (w["x"] + w["w"])) if right else 10 ** 6
        mates = [m for m in words
                 if m is not w and not (m["y"] > ty1 or m["y"] + m["h"] < ty0)]
        clean = right_gap >= GAP_MIN and len(mates) <= MATE_MAX \
            and w["y"] <= TITLE_YEDGE
        if clean and anchor_token and not _anchor_near(words, w, anchor_token):
            clean = False
        if clean:
            cands.append({**w, "right_gap": right_gap, "mates": len(mates)})
    return cands


def _window_bounds(cand, W, H):
    x0 = max(0, cand["x"] - WIN_LEFT)
    x1 = min(W, cand["x"] + WIN_RIGHT)
    y0 = max(0, cand["y"] - WIN_TOP)
    y1 = min(H, cand["y"] + WIN_BOTTOM)
    return x0, y0, x1, y1


def _ocr_region(frame_bytes, x0, y0, x1, y1):
    """OCR only the given screen region. Returns the raw text or None."""
    from PIL import Image
    try:
        im = Image.open(BytesIO(frame_bytes)).convert("RGB")
        W, H = im.size
        crop = im.crop((x0, y0, x1, y1))
        crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
        tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        crop.save(tf.name)
        r = subprocess.run(["tesseract", tf.name, "stdout", "--psm", "11"],
                           capture_output=True, text=True, timeout=120)
        os.unlink(tf.name)
        return r.stdout or ""
    except Exception:
        return None


def display_state(base_url="http://100.86.251.100:9101", now=None, state=None,
                  frame=None, lit_thresh=8, solid_frac=0.02, grid=32, store=True):
    """Laptop-closed / display-off detector. HQ-side only.

    The mark's sender (native.ps1) captures the screen with CopyFromScreen:
    with the lid closed / display off the frame comes out SOLID BLACK (and the
    frame stream goes stale). A live desktop is never solid black — it always
    has a taskbar, windows, or wallpaper.

    Two independent signals, both reported:
      - frame_age  (from /state): is the feed still being produced?
      - lit_frac   (pixel grid over /latest.jpg): is the frame solid black?

    Verdicts:
      'live'                 — frame has lit content (lid open)
      'closed-or-display-off'— frame solid black (lid closed or screen off)
      'feed-down'            — /state or /latest.jpg unreachable
    """
    now = now or time.time()
    if state is None:
        try:
            state = json.loads(_fetch(base_url + "/state", 5))
        except Exception as e:
            return {"status": "feed-down", "detail": "state: %r" % e}
    if frame is None:
        try:
            frame = _fetch(base_url + "/latest.jpg", 5)
        except Exception as e:
            return {"status": "feed-down",
                    "detail": "frame: %r (age %.1fs)" % (
                        e, now - float(state.get("last_frame_at", 0))),
                    "frame_age_s": now - float(state.get("last_frame_at", 0))}
    age = now - float(state.get("last_frame_at", 0))
    try:
        from PIL import Image
        im = Image.open(BytesIO(frame)).convert("RGB")
        th = im.resize((grid, max(1, int(grid * im.height / im.width))),
                       Image.LANCZOS)
        W, H = th.size
        lit = 0
        for y in range(H):
            for x in range(W):
                r, g, b = th.getpixel((x, y))
                if r > lit_thresh or g > lit_thresh or b > lit_thresh:
                    lit += 1
        frac = lit / max(1, W * H)
    except Exception as e:
        return {"status": "feed-down", "detail": "frame decode: %r" % e,
                "frame_age_s": age}
    status = "live" if frac >= solid_frac else "closed-or-display-off"
    rec = {
        "status": status,
        "frame_age_s": round(age, 2),
        "lit_frac": round(frac, 4),
        "solid_black": status == "closed-or-display-off",
        "client": state.get("last_client"),
        "geometry_version": state.get("geometry_version"),
        "frame_sha1": hashlib.sha1(frame).hexdigest()[:12],
        "now": now,
    }
    if store:
        os.makedirs(RUNS, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        with open(os.path.join(RUNS, "display-%s.jpg" % ts), "wb") as f:
            f.write(frame)
        rec["evidence_frame"] = "display-%s.jpg" % ts
        with open(os.path.join(RUNS, "display-%s.json" % ts), "w") as f:
            json.dump(rec, f, indent=1)
        rec["rec_path"] = os.path.join(RUNS, "display-%s.json" % ts)
    return rec


def _fetch(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def gate(state, now, check):
    """STALENESS + SESSION gate. Returns (ok, verdict, detail)."""
    max_age = check.get("max_age_s", 3.0)
    age = now - float(state.get("last_frame_at", 0))
    if age > max_age:
        return False, "stale", "frame age %.2fs > max %.2fs" % (age, max_age)
    sess = check.get("session", {})
    if sess.get("client") and state.get("last_client") != sess["client"]:
        return False, "session-mismatch", "client %s != expected %s" % (
            state.get("last_client"), sess["client"])
    for k in ("frame_w", "frame_h"):
        if sess.get(k) and state.get(k) != sess[k]:
            return False, "session-mismatch", "%s %s != expected %s" % (
                k, state.get(k), sess[k])
    return True, "fresh", "age %.2fs, client %s, %sx%s" % (
        age, state.get("last_client"), state.get("frame_w"), state.get("frame_h"))


def check_artifact(art, state, frame_bytes, now=None):
    """Core verdict from an artifact's check + a (already-gated) fresh frame.
    Returns (passed, verdict, evidence)."""
    now = now or time.time()
    check = art.get("check") or {}
    from PIL import Image
    im = Image.open(BytesIO(frame_bytes)).convert("RGB")
    W, H = im.size
    title_token = (check.get("window_title") or "calculator").lower()
    anchor_token = (check.get("anchor_token") or "standard").lower()
    tokens = [t.lower() for t in (check.get("tokens") or ["calculator", "standard"])]

    words = _ocr_tsv(frame_bytes)
    if words is None:
        return False, "ocr-failed", {"words": None}
    cands = _clean_titles(words, title_token, anchor_token=anchor_token)
    if not cands:
        return False, "not-identified", {
            "reason": "no clean window title for %r (a bare match in chat/body text is not a window)" % title_token}
    if len(cands) > 1:
        return False, "ambiguous", {
            "reason": "%d clean title candidates for %r" % (len(cands), title_token),
            "candidates": cands}
    cand = cands[0]
    x0, y0, x1, y1 = _window_bounds(cand, W, H)
    region_text = _ocr_region(frame_bytes, x0, y0, x1, y1) or ""
    low = region_text.lower()
    missing = [t for t in tokens if t not in low]
    passed = not missing
    evidence = {
        "window_title": title_token,
        "title_at": [cand["x"], cand["y"]],
        "bounds": [x0, y0, x1, y1],
        "region_tokens_present": [t for t in tokens if t in low],
        "missing_tokens": missing,
        "region_ocr": region_text[:1200],
    }
    if passed:
        return True, "pass", evidence
    return False, "missing-tokens", evidence


def run(name, base_url="http://100.86.251.100:9101", max_age=None,
        state=None, frame=None, now=None, store=True):
    """Live + testable entrypoint. `state`/`frame` are injectable so the
    STALE / MISMATCH branches can be exercised without freezing the feed."""
    now = now or time.time()
    art = json.load(open(os.path.join(HERE, "routines", name + ".json")))
    check = art.get("check") or {}
    if check.get("type") not in (None, "window-ocr"):
        # Back-compat: an old {type:vision} check is upgradable.
        check = dict(check)
        check["type"] = "window-ocr"
        check.setdefault("window_title", "calculator")
        check.setdefault("tokens", ["calculator", "standard"])
        check.setdefault("max_age_s", 3.0)
        check.setdefault("session", {"client": "100.80.61.26",
                                     "frame_w": 1280, "frame_h": 720})
        art["check"] = check
        if store:
            with open(os.path.join(HERE, "routines", name + ".json"), "w") as f:
                json.dump(art, f, indent=1)
    if max_age is not None:
        check["max_age_s"] = max_age

    if state is None:
        state = json.loads(_fetch(base_url + "/state", 5))
    if frame is None:
        frame = _fetch(base_url + "/latest.jpg", 5)

    ok, verdict, detail = gate(state, now, check)
    if not ok:
        passed = False
        evidence = {"gate": verdict, "gate_detail": detail,
                    "state": {k: state.get(k) for k in
                              ("last_frame_at", "last_client", "frame_w", "frame_h",
                               "geometry_version")}}
    else:
        passed, verdict, evidence = check_artifact(art, state, frame, now)
        evidence["gate"] = detail

    rec: dict = {
        "name": name, "ts": time.strftime("%Y%m%d-%H%M%S"),
        "check_type": check.get("type"),
        "passed": bool(passed), "verdict": verdict,
        "session_state": {k: state.get(k) for k in
                          ("last_frame_at", "last_client", "frame_w", "frame_h",
                           "geometry_version")},
        "frame_sha1": hashlib.sha1(frame).hexdigest()[:12],
        "now": now,
    }
    if store:
        os.makedirs(RUNS, exist_ok=True)
        ts = rec["ts"]
        with open(os.path.join(RUNS, "wincheck-%s-%s.jpg" % (name, ts)), "wb") as f:
            f.write(frame)
        rec["evidence_frame"] = "wincheck-%s-%s.jpg" % (name, ts)
        rec["evidence"] = evidence
        with open(os.path.join(RUNS, "wincheck-%s-%s.json" % (name, ts)), "w") as f:
            json.dump(rec, f, indent=1)
        rec["rec_path"] = os.path.join(RUNS, "wincheck-%s-%s.json" % (name, ts))
    return rec


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "display":
        r = display_state(store="--no-store" not in a)
        print("status=%s" % r["status"])
        print(json.dumps(r, indent=1)[:900])
    else:
        name = a[0] if a else "open-calculator"
        kw = {}
        if "--max-age" in a:
            kw["max_age"] = float(a[a.index("--max-age") + 1])
        if "--no-store" in a:
            kw["store"] = False
        r = run(name, **kw)
        print("verdict=%s passed=%s" % (r["verdict"], r["passed"]))
        print(json.dumps(r.get("evidence", {}), indent=1)[:1500])
        if r.get("rec_path"):
            print("evidence: %s" % r["rec_path"])
