#!/usr/bin/env python3
"""petcam send side (Windows): reads MJPEG bytes from stdin
(ffmpeg ... -f mpjpeg pipe:1), splits on JPEG SOI/EOI markers,
and POSTs each frame to the receiver. Stdlib only.

Usage (Windows, Tailscale connected):
  ffmpeg -f gdigrab -framerate 15 -i desktop -vf scale=1280:-2 -f mpjpeg pipe:1 ^
    | python petcam-send.py
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
        except Exception as e:  # receiver restarting, etc. — keep going
            print(f"frame drop: {e}", file=sys.stderr)
    print(f"sent {frames} frames", file=sys.stderr)


if __name__ == "__main__":
    main()
