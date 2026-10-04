#!/usr/bin/env python3
"""petcam send side (Windows): the ffmpeg-camera path for the mark's machine.

WHY THIS FILE EXISTS
  There are three camera options for the mark (see README). This one is the
  high-quality path: ffmpeg grabs the desktop (gdigrab) and re-encodes to
  MJPEG, which is much sharper than the zero-install .NET path. It trades
  that quality for a one-time `winget install Gyan.FFmpeg Python.Python`.

  Role in the pipeline:  ffmpeg (screen -> MJPEG bytes) --stdin--> THIS FILE
  (split the byte stream into whole JPEGs) --HTTP POST--> receiver.py :9101.

WHY THE SOI/EOI SPLITTING
  ffmpeg's mpjpeg muxer concatenates raw JPEGs with no length framing.
  A JPEG frame starts with FFD8FF (SOI) and ends with FFD9 (EOI). We buffer
  stdin until the next EOI, yield that frame, and repeat. JPEG byte streams
  have no other FFD9, so this split is lossless for our purposes.

  The 4 MiB guard: if the stream desyncs (bad marker, receiver restart
  dropping a frame mid-buffer), we drop everything except the last MiB and
  re-search for the next SOI instead of growing without bound.
"""
import os
import sys
import urllib.request

TARGET = os.environ.get("PETCAM_TARGET", "http://100.86.251.100:9101/frame")
CHUNK = 1 << 16


def split_jpegs(stream):
    buf = b""
    started = False
    n = 0
    while True:
        piece = stream.read(CHUNK)
        if not piece:
            break
        buf += piece
        while True:
            if not started:
                i = buf.find(b"\xff\xd8\xff")
                if i < 0:
                    break
                if i > 0:
                    buf = buf[i:]
                started = True
            j = buf.find(b"\xff\xd9")
            if j < 0:
                # drop bytes far beyond a plausible frame to avoid unbounded growth
                if len(buf) > 4 << 20:
                    buf = buf[-(1 << 20):]
                    started = False
                break
            frame = buf[: j + 2]
            yield frame
            n += 1
            buf = buf[j + 2:]
            if len(buf) < 4:
                started = False


def main():
    frames = 0
    for frame in split_jpegs(sys.stdin.buffer):
        if len(frame) < 128:
            continue
        try:
            req = urllib.request.Request(TARGET, data=frame, method="POST")
            urllib.request.urlopen(req, timeout=3)
            frames += 1
        except Exception as e:  # receiver restarting, etc.  - keep going
            print(f"frame drop: {e}", file=sys.stderr)
    print(f"sent {frames} frames", file=sys.stderr)


if __name__ == "__main__":
    main()
