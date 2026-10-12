# Spec 03 — Verdict rules and tolerance: results

Measured 2026-09-18, against the same 36 cells spec 02 recorded. Nothing was re-run in a
container to produce these verdicts: `fixtures/verdict-corpus/` holds each cell's checks, and
the test replays them through `mark_obsoleted` then `decide()` — the same two calls
`gather_evidence` and `engine.verify` make. Reproduce with:

```bash
uv run pytest -q tests/test_verdict_corpus.py     # 39 passed, no Docker, ~0.1s of work
uv run python scripts/build_verdict_corpus.py     # regenerate the corpus (Docker + vendor/)
```

## The headline

**Engine/ground-truth agreement goes from 24/36 to 30/36.** The 12 disagreements spec 02
found were two defects, not one; both are now fixed, and the fixes cost nothing on the cells
that already agreed.

```
  qutebrowser-f91ace96  gold   UNKNOWN -> PASS     6 cells   fixed by the obsolescence rule
  qutebrowser-c580ebf0  gold   FAIL    -> UNKNOWN  6 cells   tolerance fired; a 2nd cause remains
  everything else              unchanged          24 cells   no regression from either rule
```

The second line is the residual, and it is the reason this is 30/36 and not 36/36. See
**What is left** below.

## Per-cell

`valid` / `fail` are the valid hidden checks and how many of them still fail after the patch;
`%` is what the 20% tolerance is compared against. `obs` is checks marked obsoleted, `reg`
the regressions that survive marking.

| task | oracle | patch | valid | fail | % | obs | reg | before | after | truth |
|---|---|---|---|---|---|---|---|---|---|---|
| qutebrowser-f91ace96 | pinned | gold | 10 | 0 | 0.0 | 55 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-f91ace96 | draw-01 | gold | 7 | 0 | 0.0 | 55 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-f91ace96 | draw-02 | gold | 12 | 0 | 0.0 | 55 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-f91ace96 | draw-03 | gold | 13 | 0 | 0.0 | 55 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-f91ace96 | draw-04 | gold | 10 | 0 | 0.0 | 55 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-f91ace96 | draw-05 | gold | 10 | 0 | 0.0 | 55 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-f91ace96 | pinned | half | 10 | 7 | 70.0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-01 | half | 7 | 6 | 85.7 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-02 | half | 12 | 7 | 58.3 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-03 | half | 13 | 8 | 61.5 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-04 | half | 10 | 6 | 60.0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-05 | half | 10 | 6 | 60.0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | pinned | gold | 19 | 2 | 10.5 | 56 | 1 | FAIL | *UNKNOWN* | resolved |
| qutebrowser-c580ebf0 | draw-01 | gold | 16 | 1 | 6.2 | 56 | 1 | FAIL | *UNKNOWN* | resolved |
| qutebrowser-c580ebf0 | draw-02 | gold | 15 | 1 | 6.7 | 56 | 1 | FAIL | *UNKNOWN* | resolved |
| qutebrowser-c580ebf0 | draw-03 | gold | 19 | 3 | 15.8 | 56 | 1 | FAIL | *UNKNOWN* | resolved |
| qutebrowser-c580ebf0 | draw-04 | gold | 18 | 1 | 5.6 | 56 | 1 | FAIL | *UNKNOWN* | resolved |
| qutebrowser-c580ebf0 | draw-05 | gold | 19 | 2 | 10.5 | 56 | 1 | FAIL | *UNKNOWN* | resolved |
| qutebrowser-c580ebf0 | pinned | half | 19 | 8 | 42.1 | 56 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-01 | half | 16 | 6 | 37.5 | 56 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-02 | half | 15 | 7 | 46.7 | 56 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-03 | half | 19 | 10 | 52.6 | 56 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-04 | half | 18 | 7 | 38.9 | 56 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-05 | half | 19 | 9 | 47.4 | 56 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | pinned | gold | 25 | 0 | 0.0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-01 | gold | 15 | 0 | 0.0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-02 | gold | 80 | 0 | 0.0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-03 | gold | 29 | 0 | 0.0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-04 | gold | 45 | 0 | 0.0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-05 | gold | 23 | 0 | 0.0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | pinned | half | 25 | 25 | 100.0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-01 | half | 15 | 13 | 86.7 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-02 | half | 80 | 80 | 100.0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-03 | half | 29 | 29 | 100.0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-04 | half | 45 | 45 | 100.0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-05 | half | 23 | 23 | 100.0 | 0 | 0 | FAIL | FAIL | unresolved |

Italic `UNKNOWN` marks the six declared in `KNOWN_DIVERGENT`.

## Where the 20% threshold actually sits

The tolerance was specced from spec 02's table and the corpus confirms it, with room on both
sides. Across all 36 cells:

```
   gold, worst draw   c580ebf0 draw-03    3 of 19   15.8%   |
                                                            |  20% threshold
   half, mildest draw c580ebf0 draw-01    6 of 16   37.5%   |
```

Every other half cell is 38–100%. No gold cell exceeds 15.8% and no half cell falls below
37.5%, so the threshold could move anywhere in (15.8, 37.5] without changing a single
verdict. It is pinned at 20% because the boundary test (`3 of 15 PASS, 4 of 15 FAIL`) reads
as a round number, not because the data is tight there.

`tests/test_verdict_corpus.py` re-derives the agreement from the corpus and asserts the
divergent set is exactly the six above, so an edited report or a drifting rule fails `pytest`.

## What is left

> Spec 13 closes this: see [13-obsolescence-precision/REPORT.md](../13-obsolescence-precision/REPORT.md) -- per-test marking, and required-hunk attribution for `test_disabled_blocking_per_url`, take the corpus to 36/36. The text below stays as the record of what 03 measured.

**One existing test the gold *test patch* repairs, which the engine cannot see.**

`qutebrowser-c580ebf0` gold carries **11** regressions, not 10. The obsolescence rule reaches
ten of them and stops at the eleventh:

| regression | why | marked? |
|---|---|---|
| `tests/unit/config/test_configutils.py::TestWiden::test_widen_hostnames*` (10) | references `_widened_hostnames`, which the diff deletes | yes |
| `tests/unit/components/test_hostblock.py::test_disabled_blocking_per_url` (1) | names no deleted symbol | **no** |

That last check is in the task's own pass-list, and the thing that repairs it is the gold
**test patch** — `QUrl("blocked.example.com")` becomes `QUrl("https://blocked.example.com")`.
The engine never sees the test patch, by design: that is the whole point of writing hidden
tests instead of reading the answer. Confirmed in the task image at the base commit:

```bash
docker run --rm --platform linux/amd64 --entrypoint /bin/bash \
  jefzda/sweap-images@sha256:5909cff7... \
  -c 'cd /app && git reset -q --hard c580ebf0... \
      && grep -c "_widened_hostnames" tests/unit/components/test_hostblock.py'
# 0
```

So no symbol-based rule can reach it. Once the tolerance forgives the 1–3 hidden fails,
`decide()` falls through to the regression rule and returns UNKNOWN on all six cells.

**Why this was not fixed here.** The only widening that would catch it is marking tests of
*modified* symbols obsolete, not just deleted ones. That would stop existing tests of the
very function under change from counting as regressions at all — which is most of what the
regression rule is for. Trading a real safety rule for one cell's worth of agreement is a bad
deal, so spec 03 names the limitation instead of chasing it.

Note that the six land on **UNKNOWN**, not FAIL. The engine is now saying "I cannot tell"
about a correct patch, where before it said "this is wrong". That is a strictly better failure
mode, and it is the failure mode UNKNOWN exists for.

**Owner: spec 04, filed as ledger entry L23.** It is not spec 03's to close. The round-1
gap-check adjudicated it against [ROADMAP.md](../ROADMAP.md)'s own reasoning — the 02–04 band
is "oracle quality / gold FAIL+UNKNOWN cases", and this is exactly a gold UNKNOWN case — so it
became a row against an existing spec rather than a new number. [L24](../OPEN-ITEMS.md) was
filed alongside it: obsolescence is marked per test *file*, so a genuine regression sharing a
file with an obsoleted test stops counting. Both are blocked on the same question and carry the
same owner. The 6 cells are pinned in `KNOWN_DIVERGENT`, so neither can drift unnoticed.
