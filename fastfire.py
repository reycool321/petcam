#!/usr/bin/env python3
"""Fast relay: POST key events straight to /control (no brain poll, no journal
round-trip). The mark's sender applies ALL pending events in one poll cycle.
Usage: fastfire.py <key> <mod> <n>   e.g. fastfire.py t ctrl 20
"""
import json, time, sys, urllib.request

CTRL = "http://100.86.251.100:9101/control"

def post(evt):
    req = urllib.request.Request(
        CTRL, data=json.dumps(evt).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)

key, mod, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
t0 = time.time()
for i in range(n):
    post({"cmd": "key", "key": key, "mods": [mod]})
dt = time.time() - t0
print("queued %d x %s+%s in %.3fs -> %.1f/s (HQ enqueue rate)" % (
    n, mod, key, dt, n/dt))
