#!/usr/bin/env python3
"""Placeholder feed for petcam, now themed as the HEIST: the 'MARK'S SCREEN'
recon feed. Posts an animated 1280x720 jpeg every 0.5s until the real laptop
ffmpeg/native feed is live. Pure placeholder = the mark's desk while we scope
the score. Stdlib + PIL only.

WHY IT EXISTS (judges may ask)
  Two jobs: (1) let the whole pipeline (receiver, mpv HUD, brain, pet, score
  channel) be demoed end-to-end WITHOUT the laptop present -- the demo must
  not depend on the mark being online; (2) it IS the theme: the heist HUD
  (comms bar, crosshair, loot manifest, getaway note) is the product's face,
  so the placeholder is not throwaway filler, it's the designed UI.
  When the real feed takes over, the placeholder is just `systemctl --user
  stop petcam-placeholder`; the brain tells them apart via /state's
  last_client (placeholder = our own tailnet IP).

HOW IT DRAWS
  One PIL Image per frame, drawn fresh, JPEG-quantized to ~70, POSTed to
  /frame. The only animation is frame index i driving the scanline position
  ((i*18) % mh) and the elapsed timer -- cheap on CPU, no video encode.
"""
import io, time, urllib.request
from PIL import Image, ImageDraw, ImageFont

URL = "http://100.86.251.100:9101/frame"
try:
    FONT   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
    MID    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    SMALL  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
except OSError:
    FONT = MID = SMALL = ImageFont.load_default()

# heist palette: dark navy vault, gold accent, teal signal, amber caution
BG     = (16, 20, 34)
GOLD   = (228, 192, 92)
TEAL   = (74, 222, 196)
AMBER  = (240, 170, 80)
RED    = (232, 92, 92)
DIM    = (120, 130, 165)
GOLD2  = (255, 214, 130)

t0 = time.time()
i = 0
while True:
    i += 1
    img = Image.new("RGB", (1280, 720), BG)
    d = ImageDraw.Draw(img)

    # --- top status bar (the crew's comms) ---
    d.rectangle([0, 0, 1279, 64], fill=(10, 13, 24))
    d.text((24, 16), "MARK'S SCREEN", font=MID, fill=GOLD)
    d.text((230, 20), "RECON FEED", font=SMALL, fill=TEAL)
    elapsed = int(time.time() - t0)
    d.text((430, 20), f"T+{elapsed:03d}s", font=SMALL, fill=DIM)
    # mission phase (placeholder runs RECON until the real feed arrives)
    d.text((1000, 16), "PHASE: RECON", font=MID, fill=AMBER)
    d.line([24, 63, 1256, 63], fill=(40, 48, 78), width=2)

    # --- center: the mark's desk (a mock workstation) ---
    d.text((24, 96), "scoping the mark's machine ...", font=MID, fill=DIM)
    # a fake monitor frame with a moving scanline (we're 'reading' the screen)
    mx, my, mw, mh = 24, 150, 900, 500
    d.rectangle([mx, my, mx + mw, my + mh], outline=(58, 70, 110), width=3)
    scan = (i * 18) % mh
    d.line([mx, my + scan, mx + mw, my + scan], fill=TEAL, width=2)
    # crosshair at center (the 'target' on the mark's screen)
    cx, cy = mx + mw // 2, my + mh // 2
    d.line([cx - 40, cy, cx + 40, cy], fill=GOLD2, width=2)
    d.line([cx, cy - 40, cx, cy + 40], fill=GOLD2, width=2)
    d.ellipse([cx - 60, cy - 60, cx + 60, cy + 60], outline=GOLD, width=2)
    d.text((mx + 16, my + 14), "MARK-PC / primary display", font=SMALL, fill=TEAL)
    d.text((mx + 16, my + mh - 34), f"frame {i}", font=SMALL, fill=DIM)

    # --- right rail: the loot manifest (what we're here for) ---
    rx = 950
    d.rectangle([rx - 12, 150, 1256, 650], outline=(58, 70, 110), width=2)
    d.text((rx, 166), "LOOT MANIFEST", font=MID, fill=GOLD)
    loot = [
        ("mark's agent memory", "intel", TEAL),
        ("sessions / skills",   "intel", TEAL),
        ("control channel",     "score", AMBER),
        ("zero footprint",      "escape", GOLD),
    ]
    y = 230
    for name, kind, col in loot:
        d.ellipse([rx, y, rx + 16, y + 16], fill=col)
        d.text((rx + 28, y - 2), name, font=SMALL, fill=(200, 210, 235))
        d.text((rx + 28, y + 20), kind, font=SMALL, fill=DIM)
        y += 74
    # getaway countdown (cosmetic, heist energy)
    d.line([rx, y + 8, 1250, y + 8], fill=(40, 48, 78), width=2)
    d.text((rx, y + 22), "GETAWAY", font=SMALL, fill=DIM)
    d.text((rx, y + 48), "when the mark sleeps", font=SMALL, fill=AMBER)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    req = urllib.request.Request(URL, data=buf.getvalue(), method="POST")
    urllib.request.urlopen(req, timeout=3)
    time.sleep(0.5)
