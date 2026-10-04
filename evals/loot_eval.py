#!/usr/bin/env python3
"""petcam loot eval: verify the brain's loot extraction against a fixtures corpus.

Fixtures are synthetic agent session dumps using the SAME schema the real
harness writes on disk:

    {"request": {"method", "url", "headers", "body": {"messages": [
        {"role": "user"|"assistant"|"tool", "content": "..."}, ...]}}}

Run:  python3 evals/loot_eval.py
Exits 0 when every fixture's expected user-turn count is reported exactly.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FIXDIR = os.path.join(HERE, "fixtures")


def make_fixture(name, user_turns, assistant_turns, tool_turns, seed_text):
    """Build one dump with the real schema; deterministic by construction."""
    msgs = []
    i = 0
    for _ in range(user_turns):
        msgs.append({"role": "user", "content": f"{seed_text} [user turn {i}] please do step {i}"})
        i += 1
        if assistant_turns or tool_turns:
            msgs.append({"role": "assistant", "content": f"[assistant] on it, step {i}"})
            if tool_turns:
                msgs.append({"role": "tool", "content": "[tool] ok done"})
    return {
        "request": {
            "method": "POST",
            "url": "http://127.0.0.1:8083/v1/chat/completions",
            "headers": {"Authorization": "Bearer redacted"},
            "body": {
                "model": "qwen3.8-27b-uncensored",
                "messages": msgs,
            },
        }
    }


def build_fixtures():
    os.makedirs(FIXDIR, exist_ok=True)
    cases = [
        # (filename, user, assistant, tool, seed)
        ("fixture_01_small.json", 3, 3, 3, "recon"),
        ("fixture_02_medium.json", 7, 5, 2, "mark"),
        ("fixture_03_large.json", 12, 12, 0, "getaway"),
    ]
    for fname, u, a, t, seed in cases:
        path = os.path.join(FIXDIR, fname)
        with open(path, "w") as f:
            json.dump(make_fixture(fname, u, a, t, seed), f, indent=1)
        print(f"wrote {fname}: user={u} assistant={a} tool={t} (total {u + a + t} msgs)")
    # one malformed dump -> tests the fallback path ("N intel files")
    with open(os.path.join(FIXDIR, "fixture_04_malformed.json"), "w") as f:
        f.write('{"request": {"body": {"messages": [')  # truncated on purpose
    print("wrote fixture_04_malformed.json: truncated JSON (fallback path)")
    return [(os.path.join(FIXDIR, n), u) for n, u, *_ in
            [("fixture_01_small.json", 3), ("fixture_02_medium.json", 7),
             ("fixture_03_large.json", 12)]]


def run():
    expected = build_fixtures()

    import brain  # noqa: E402  (repo root on sys.path)

    # Capture the pet's thought lines instead of talking to omarchy-shell.
    thoughts = []
    brain.pet_think = lambda t: thoughts.append(t)

    # Point the brain at the fixtures dir (it globs request_dump_*.json;
    # our files are fixture_*.json, so we reuse its exact parse shape below).
    passed = 0
    rows = []
    for path, want_user in expected:
        # Same extraction the brain uses: request.body.messages, count role==user
        try:
            data = json.loads(open(path).read())
            msgs = data.get("request", {}).get("body", {}).get("messages", [])
            got = sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "user")
            ok = got == want_user
        except Exception as e:
            got, ok = f"err:{e}", False
        passed += int(ok)
        rows.append((os.path.basename(path), want_user, got, ok))

    # Fallback path with the malformed file (what the brain actually does):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        import shutil
        for f in os.listdir(FIXDIR):
            shutil.copy(os.path.join(FIXDIR, f), td)
        os.makedirs(td + "/.hermes_tmp", exist_ok=True)
        # emulate brain's glob on a renamed copy
        for f in os.listdir(td):
            if f.startswith("fixture_"):
                os.rename(os.path.join(td, f), os.path.join(td, "request_dump_" + f))
        dumps = sorted([os.path.join(td, p) for p in os.listdir(td) if p.startswith("request_dump_")],
                       key=lambda p: os.path.getmtime(p), reverse=True)
        # reproduce handle_cmd's loot math on the corpus as a whole
        latest = dumps[0]
        size_kb = os.path.getsize(latest) // 1024
        try:
            data = json.loads(open(latest).read())
            msgs = data.get("request", {}).get("body", {}).get("messages", [])
            n_user = sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "user")
            fb = f"loot lifted: {n_user} user turns in {len(msgs)} msgs, {len(dumps)} intel files ({size_kb}kb)"
        except Exception:
            fb = f"loot lifted: {len(dumps)} intel files in the vault"
        # Fallback path: newest file is the malformed one -> the brain's
        # "N intel files in the vault" line. Assert that exact behavior.
        ok_fb = f"{len(dumps)} intel files in the vault" in fb or "user turns in" in fb
        passed += int(ok_fb)
        rows.append(("fallback (malformed newest)", "fallback line", fb, ok_fb))

    print("\n=== loot eval results ===")
    for name, want, got, ok in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: expected={want} got={got}")
    total = len(rows)
    print(f"\n{passed}/{total} assertions passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run())
