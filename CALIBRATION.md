# Spot — cursor calibration record (PROVEN 2026-10-04)

Sender session `send-bd2463df120b`, readback=true, dpi=120 (per-monitor-v2).
Capture 1920x1080 @ (0,0); feed 1280x720; geometry_version 4262479270.

## Formula (sender-owned, Map-Feed)
desktop = capOx + feed_x * capW / feedW   (and y)
Scale = 1920/1280 = 1.5 ; offset = (0,0). Feed coords outside
[0,feedW)x[0,feedH) are rejected, not clamped.

## 5-point move test — requested vs GetCursorPos actual (all dx 0 dy 0)
| feed req | mapped desktop | actual desktop | delta |
|---|---|---|---|
| (400,400) | (600,600) | (600,600) | 0,0 |
| (640,360) center | (960,540) | (960,540) | 0,0 |
| (60,60) | (90,90) | (90,90) | 0,0 |
| (1220,60) | (1830,90) | (1830,90) | 0,0 |
| (60,660) | (90,990) | (90,990) | 0,0 |
| (1220,660) | (1830,990) | (1830,990) | 0,0 |

Proof = the move readback for that exact command id (session-gated).
A frame diff or "fired" is NOT a calibration result.

## Recalibrate when
- the sender process restarts (fresh session id),
- display config / resolution changes (capture bounds move -> geo bumps),
- a new sender revision is deployed.

## Per-action protocol
1. Fresh frame for every aim pass; locate the target on THAT frame.
2. Verify the intended UI outcome on a fresh shot before continuing.
3. Keep session/cmd matching (drive.py move) and stale-geometry rejection
   (sender drops events whose geometry_version != live).
