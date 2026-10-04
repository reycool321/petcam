# Devpost project description (paste into the submission)

## What is it?

**petcam** — a desktop pet that is the crew's *spotter*: it watches a **live
video feed of a remote machine's screen** over the tailnet, reacts to it in
real time, and (when the mark is asleep) **lifts that machine's agent memory
back to HQ as data**. Zero-install on the remote side; one PowerShell line;
nothing lingers. A clean getaway.

## How it works (the 30-second version)

1. **Descend.** The remote Windows machine runs a one-liner
   (`iex (irm http://<hq>:9101/native.ps1)`) that captures its screen with
   built-in .NET (no ffmpeg, no Python installed) and POSTs JPEG frames to HQ.
2. **Recon.** HQ's stdlib-only receiver streams them into a small heist-HUD
   window; a Hermes-independent brain watches feed health and drives the
   desktop pet's pose + speech ("in on the mark's screen", "mark's screen
   went quiet").
3. **The score.** An event-driven control channel (`cmd.json`) lets an agent
   talk to / click the mark without touching the running daemon.
4. **Ascend.** `{"cmd":"loot"}` makes the pet **lift the mark's agent memory**
   (its session history) — read off disk, as data, no harness in the loop —
   and report what it lifted. Eval'd against a fixtures corpus: 4/4 pass.

## Why it's the theme

RowdyHacks XII is **Descend / Ascend**. We literally descend on a mark's
machine with a one-line camera, watch from the safehouse, and ascend with the
loot. The pet *is* the spotter. Theme isn't a reskin — it's the architecture
(mark / vault / recon / score / loot / getaway each map to a real component).

## Tech

- Python stdlib only for the core (receiver + brain); no framework, no Hermes
  in the runtime loop.
- Tailscale tailnet transport; HTTP MJPEG streaming; .NET `CopyFromScreen`
  capture on the camera side.
- Omarchy desktop pet driven through its stock CLI (nothing patched in).
- systemd user services + linger on HQ (headless-friendly, multi-OS by
  construction).

## What we'd build next (the score, phase 2)

Click/type **into** the mark: coordinate-mapped input injection through the
local mpv window first, then element-level UIA control on the mark's machine.
The control channel already routes `click` — the injection backend is next.
