# Loot channel — eval numbers

Reproducible: `python3 evals/loot_eval.py` (exits 0 on all-pass).

## What the eval proves

The brain's `loot` move extracts user-turn counts from agent session dumps
using the **exact schema the real harness writes on disk**
(`request.body.messages[]`, `role == "user"`). The fixtures corpus is a set of
deterministic synthetic dumps (3/7/12 user turns + a deliberately malformed one
to exercise the fallback path).

## Results (run 2026-10-03, 23:5x CDT)

| Fixture                  | Expected user turns | Reported | Verdict |
|--------------------------|--------------------:|---------:|---------|
| fixture_01_small.json    |                   3 |        3 | PASS    |
| fixture_02_medium.json   |                   7 |        7 | PASS    |
| fixture_03_large.json    |                  12 |       12 | PASS    |
| fallback (malformed)     |  "N intel files…"   | "4 intel files in the vault" | PASS |

**4/4 assertions passed.**

## Live (non-fixture) confirmation

Against the real `~/.hermes/sessions/` store the pet reported:
`loot lifted: 8 user turns in 229 msgs, 71 intel files (464kb)` —
data-only read, no Hermes process in the loop.
