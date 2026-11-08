import json, time, urllib.request
URL = "http://100.86.251.100:9101/state"
LOG = "/home/fabri/apps/petcam/inbox/pause-watch.log"
prev = None
def get():
    try:
        with urllib.request.urlopen(URL, timeout=3) as r:
            return json.load(r)
    except Exception as e:
        return {"err": str(e)}
last = time.time()
with open(LOG, "w") as f:
    f.write("[%s] watcher start\n" % time.strftime("%H:%M:%S"))
    f.flush()
while True:
    d = get()
    now = time.time()
    if "err" in d:
        if now - last > 10:
            with open(LOG, "a") as f:
                f.write("[%s] state unreachable: %s\n" % (time.strftime("%H:%M:%S"), d["err"]))
                f.flush()
            last = now
        time.sleep(0.4)
        continue
    sig = (d.get("paused"), d.get("pause_src"))
    if sig != prev:
        with open(LOG, "a") as f:
            f.write("[%s] FLIP: paused=%s src=%s\n" % (time.strftime("%H:%M:%S"), d.get("paused"), d.get("pause_src")))
            f.flush()
        prev = sig
    time.sleep(0.4)
