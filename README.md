# petcam — the mark's screen, from the safehouse

**RowdyHacks XII · "Descend / Ascend"**

A desktop pet (the crew's *spotter*) that watches a **live video feed of a
remote machine's screen** over the tailnet, reacts to it, and — when the mark
is asleep — **lifts the mark's agent memory back to HQ** as data.

We descended on the mark's machine with a one-line, **zero-install** camera.
We ascended with the loot. Clean getaway: nothing lingers on the mark's side.

## The heist map

| Heist role     | petcam piece                                   |
|----------------|------------------------------------------------|
| **The mark**   | the remote Windows laptop (`100.80.61.26`)      |
| **The vault**  | its screen + its agent memory (session data)    |
| **HQ**         | the RTX 5090 box (receiver, viewer, pet, brain) |
| **The spotter**| the Omarchy desktop pet, bottom-right of HQ     |
| **Recon**      | live MJPEG feed of the mark's screen, 480×270   |
| **The score**  | event-driven control channel (`cmd.json`)       |
| **The loot**   | `loot` — lifts the mark's agent memory, on disk, as data |
| **Getaway**    | one PowerShell line on the mark; no install; camera is on-demand |

## Architecture

```
 Windows laptop (the mark)                RTX 5090 (HQ)
 ┌──────────────────────────┐  tailnet   ┌────────────────────────────────┐
 │ petcam-send-native.ps1   │  100.86…   │ receiver.py     (python stdlib)│
 │  .NET CopyFromScreen     │ ─────────► │  :9101  /frame /stream.mjpeg   │
 │  JPEG + HTTP POST @ ~2fps│            │            │ /state            │
 │  (zero-install)          │            │     mpv ◄───┘  (heist HUD view)│
 └──────────────────────────┘            │  brain.py (stdlib daemon)      │
                                         │   polls /state → drives pet    │
                                         │   drains cmd.json (control)    │
                                         │  pet: Omarchy plugin (stock)   │
                                         └────────────────────────────────┘
```

- **No Hermes in the runtime loop.** The pet, feed, and brain are plain
  systemd user services + `omarchy-shell` CLI. Hermes files are read **only
  as data**, only when the `loot` command fires.
- **Multi-OS by construction:** stdlib Python core; camera side has a
  zero-install PowerShell path (Windows), plus ffmpeg/Python variants; HQ
  side needs only a tailnet IP and (optionally) a desktop pet.

## Files

| File                      | Role (side)                                   |
|---------------------------|-----------------------------------------------|
| `receiver.py`             | HQ — stdlib HTTP/MJPEG receiver, :9101        |
| `brain.py`                | HQ — feed-health → pet state; `cmd.json` control; `loot` |
| `fake_sender.py`          | HQ — heist-HUD placeholder "mark" (demo feed) |
| `petcam-feed`             | HQ — mpv viewer + Hyprland window placement   |
| `petcam-send-native.ps1`  | MARK — zero-install .NET screen capture+POST  |
| `petcam-send.py`          | MARK — MJPEG stdin sender (ffmpeg path)       |
| `petcam-send.ps1`         | MARK — PowerShell sender variant              |
| `petcam-feed.bat`         | MARK — ffmpeg/Python launcher                 |
| `install.ps1`             | MARK — one-line bootstrap (winget ffmpeg/Py)  |
| `input.conf`              | HQ — mpv keybindings                          |
| `evals/`                  | eval harness, fixtures, and numbers           |

## Run it

**HQ (5090):**
```bash
systemctl --user start petcam-receiver petcam-brain   # services (linger enabled)
./petcam-feed                                          # opens the mpv HUD window
```

**Mark (Windows laptop), zero-install:**
```powershell
iex (irm "http://100.86.251.100:9101/native.ps1")
```
Close the window when the mark wakes. Nothing is installed, nothing lingers.

**Control channel (event-driven, no polling of your terminal):**
```bash
echo '{"cmd":"think","text":"crew, we are in"}' > ~/apps/petcam/cmd.json
echo '{"cmd":"loot"}'                            > ~/apps/petcam/cmd.json   # lift intel
echo '{"cmd":"click","x":640,"y":360}'           > ~/apps/petcam/cmd.json   # phase 2
```

## The loot channel (eval)

`evals/loot_eval.py` replays a **fixtures corpus** of synthetic agent
session dumps (the same schema the real harness writes:
`{request:{body:{messages:[{role,content}]}}}`) and checks that the brain's
loot extraction reports the exact user-turn counts. Numbers in
`evals/RESULTS.md` (reproducible: `python3 evals/loot_eval.py`).

## Phase 2 (the score, not yet in the loot)

Click/type **into** the mark: coordinate-mapped injection through the local
mpv window (ydotool/cua) first; cua-driver on the mark's machine for
element-level control after. `click` already routes through `cmd.json`.

## License

MIT
