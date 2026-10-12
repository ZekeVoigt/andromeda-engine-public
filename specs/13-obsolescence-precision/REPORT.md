# Spec 13 report — obsolescence precision

Engine/ground-truth agreement on the committed corpus: **30/36 → 36/36**, `KNOWN_DIVERGENT`
empty. Per-test obsolescence cuts spec 03's file-rule marks from 56/56/55 to 10/10/4 without
losing a regression that rule reached. Required-hunk attribution forgives the one check spec 03
could not reach, `test_disabled_blocking_per_url`, in all six `c580ebf0` gold cells.
`broken.diff` is not forgiven (AC13), and the guard catches the in-hunk residual (AC12) on the
pinned oracle.

## Per corpus cell (AC14)

- `marked (file rule, 03)` is what spec 03's `mark_obsoleted` marked.
- `marked per test` is what spec 13's marks, every one `deleted-symbol`.
- `required-hunk` is what `mark_stale` added from the committed ablation.
- `reg` counts the regressions that survive both.

A verdict in bold is one that moved.

| task | oracle | patch | marked (file rule, 03) | marked per test | required-hunk | reg | spec 03 | spec 13 | truth |
|---|---|---|---|---|---|---|---|---|---|
| ansible-f327e65d | pinned | gold | 0 | 0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-01 | gold | 0 | 0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-02 | gold | 0 | 0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-03 | gold | 0 | 0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-04 | gold | 0 | 0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | draw-05 | gold | 0 | 0 | 0 | 0 | PASS | PASS | resolved |
| ansible-f327e65d | pinned | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-01 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-02 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-03 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-04 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| ansible-f327e65d | draw-05 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | pinned | gold | 56 | 10 | 1 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-c580ebf0 | draw-01 | gold | 56 | 10 | 1 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-c580ebf0 | draw-02 | gold | 56 | 10 | 1 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-c580ebf0 | draw-03 | gold | 56 | 10 | 1 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-c580ebf0 | draw-04 | gold | 56 | 10 | 1 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-c580ebf0 | draw-05 | gold | 56 | 10 | 1 | 0 | UNKNOWN | **PASS** | resolved |
| qutebrowser-c580ebf0 | pinned | half | 56 | 10 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-01 | half | 56 | 10 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-02 | half | 56 | 10 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-03 | half | 56 | 10 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-04 | half | 56 | 10 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-c580ebf0 | draw-05 | half | 56 | 10 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | pinned | gold | 55 | 4 | 0 | 0 | PASS | PASS | resolved |
| qutebrowser-f91ace96 | draw-01 | gold | 55 | 4 | 0 | 0 | PASS | PASS | resolved |
| qutebrowser-f91ace96 | draw-02 | gold | 55 | 4 | 0 | 0 | PASS | PASS | resolved |
| qutebrowser-f91ace96 | draw-03 | gold | 55 | 4 | 0 | 0 | PASS | PASS | resolved |
| qutebrowser-f91ace96 | draw-04 | gold | 55 | 4 | 0 | 0 | PASS | PASS | resolved |
| qutebrowser-f91ace96 | draw-05 | gold | 55 | 4 | 0 | 0 | PASS | PASS | resolved |
| qutebrowser-f91ace96 | pinned | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-01 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-02 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-03 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-04 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |
| qutebrowser-f91ace96 | draw-05 | half | 0 | 0 | 0 | 0 | FAIL | FAIL | unresolved |

The six half cells of `c580ebf0` keep their 10 per-test marks and stay FAIL on tolerance. They
never route `regressed`, so they are never ablated. Only the six gold cells change.

## Per ablated cell, every hunk (AC9, AC14)

These are `fixtures/verdict-corpus/<c580ebf0>/ablations/<oracle>-gold.json`, measured once by
`uv run python scripts/build_verdict_corpus.py --ablations-only` (36 after-side runs, 26:43 on
the Mac Colima). `valid failing` counts the valid hidden checks that fail under the full diff
and under the diff without that hunk.

| oracle | hunk | restores | valid failing (full → without) | required |
|---|---|---|---|---|
| draw-01 | `qutebrowser/components/hostblock.py@0` | -- | 1 → 6 | yes |
| draw-01 | `qutebrowser/components/hostblock.py@1` | `test_disabled_blocking_per_url` | 1 → 6 | yes |
| draw-01 | `qutebrowser/config/configutils.py@0` | -- | 1 → 2 | yes |
| draw-01 | `qutebrowser/config/configutils.py@1` | -- | 1 → 2 | yes |
| draw-01 | `qutebrowser/utils/urlutils.py@0` | -- | 1 → 16 | yes |
| draw-01 | `qutebrowser/utils/urlutils.py@1` | -- | 1 → 16 | yes |
| draw-02 | `qutebrowser/components/hostblock.py@0` | -- | 1 → 7 | yes |
| draw-02 | `qutebrowser/components/hostblock.py@1` | `test_disabled_blocking_per_url` | 1 → 7 | yes |
| draw-02 | `qutebrowser/config/configutils.py@0` | -- | 1 → 2 | yes |
| draw-02 | `qutebrowser/config/configutils.py@1` | -- | 1 → 2 | yes |
| draw-02 | `qutebrowser/utils/urlutils.py@0` | -- | 1 → 15 | yes |
| draw-02 | `qutebrowser/utils/urlutils.py@1` | -- | 1 → 15 | yes |
| draw-03 | `qutebrowser/components/hostblock.py@0` | -- | 3 → 10 | yes |
| draw-03 | `qutebrowser/components/hostblock.py@1` | `test_disabled_blocking_per_url` | 3 → 10 | yes |
| draw-03 | `qutebrowser/config/configutils.py@0` | -- | 3 → 4 | yes |
| draw-03 | `qutebrowser/config/configutils.py@1` | -- | 3 → 4 | yes |
| draw-03 | `qutebrowser/utils/urlutils.py@0` | -- | 3 → 19 | yes |
| draw-03 | `qutebrowser/utils/urlutils.py@1` | -- | 3 → 19 | yes |
| draw-04 | `qutebrowser/components/hostblock.py@0` | -- | 1 → 7 | yes |
| draw-04 | `qutebrowser/components/hostblock.py@1` | `test_disabled_blocking_per_url` | 1 → 7 | yes |
| draw-04 | `qutebrowser/config/configutils.py@0` | -- | 1 → 2 | yes |
| draw-04 | `qutebrowser/config/configutils.py@1` | -- | 1 → 2 | yes |
| draw-04 | `qutebrowser/utils/urlutils.py@0` | -- | 1 → 18 | yes |
| draw-04 | `qutebrowser/utils/urlutils.py@1` | -- | 1 → 18 | yes |
| draw-05 | `qutebrowser/components/hostblock.py@0` | -- | 2 → 9 | yes |
| draw-05 | `qutebrowser/components/hostblock.py@1` | `test_disabled_blocking_per_url` | 2 → 9 | yes |
| draw-05 | `qutebrowser/config/configutils.py@0` | -- | 2 → 3 | yes |
| draw-05 | `qutebrowser/config/configutils.py@1` | -- | 2 → 3 | yes |
| draw-05 | `qutebrowser/utils/urlutils.py@0` | -- | 2 → 19 | yes |
| draw-05 | `qutebrowser/utils/urlutils.py@1` | -- | 2 → 19 | yes |
| pinned | `qutebrowser/components/hostblock.py@0` | -- | 2 → 8 | yes |
| pinned | `qutebrowser/components/hostblock.py@1` | `test_disabled_blocking_per_url` | 2 → 8 | yes |
| pinned | `qutebrowser/config/configutils.py@0` | -- | 2 → 3 | yes |
| pinned | `qutebrowser/config/configutils.py@1` | -- | 2 → 3 | yes |
| pinned | `qutebrowser/utils/urlutils.py@0` | -- | 2 → 19 | yes |
| pinned | `qutebrowser/utils/urlutils.py@1` | -- | 2 → 19 | yes |

**Every hunk is `required`.** That is expected, not a weakness of the rule: gold's hunks depend
on each other, so reverting any one of them breaks more of the request.
- `hostblock.py@0` imports `urlutils`.
- `urlutils.py@1` defines `widened_hostnames`.
- `configutils.py@0` deletes `_widened_hostnames` in favour of it.

`restores` decides. In every oracle exactly one hunk restores `test_disabled_blocking_per_url`:
`qutebrowser/components/hostblock.py@1`, `_is_blocked`'s body. So that hunk alone is the cause,
and the request needs it. AC11's `gold-plus-unrelated.diff` is the counter-case: its extra hunk
restores `test_whitelisted_lines[27.0.0.1-localhost.localdomain]` but is not required, so that
regression still counts.

## AC13 — `broken.diff`, the stop gate

This was measured first, before the corpus step, with the adapter's `__main__` on the pinned
oracle. The verdict is UNKNOWN (`22/25 valid hidden checks pass but 16 existing checks
regressed`), and the route is `regressed`. The `ablation.json` reads `considered: true, skipped:
"hidden-regression"`: a hidden check that passed on base fails under the diff, so the guard
refuses attribution and **no hunk is reverted**.

The spec predicted a different reason: no single hunk restores the `test_collection_loader.py`
checks. The guard stops the draw before that point, and AC13 allows either reason.

The proof is `tests/test_obsolescence.py::test_real_ac13_broken_diff_is_not_forgiven`, plus
these modules, whose broken.diff assertions were not touched:

| module | result |
|---|---|
| `tests/test_gate.py -k ac4` | 3 passed |
| `tests/test_landing_gate.py -k ac9` | 1 passed |
| `tests/test_race.py` | 50 passed, plus the re-pinned hash test below (1 passed, run on its own) |
| `tests/test_loop_race.py` | 26 passed |

**One edit to those modules, and why.** Two of them pin `sha256(inspect.getsource(decide))`:
`tests/test_race.py` (C09 AC10) and `tests/test_landing_gate.py` (C11 AC10). Each pin guards
against its own spec editing `decide()`. Spec 13 has to edit `decide()`, because AC8 makes a
PASS name its required-hunk check. So both constants were re-pinned, with the old sha kept in a
comment. No assertion about a grade changed.

## AC12 — the residual, measured branch

`gold-in-hunk.diff` is gold without `_is_blocked`'s whitelist early return. On the pinned
oracle, `test_outcome_hostblock_subdomains.py::test_whitelist_overrides_parent_domain_block`
goes **pass → fail**. The guard fires (`skipped: "hidden-regression"`) and nothing is reverted.
The draw stays `regressed` (UNKNOWN, `17/19`), with both `test_blocking_with_whitelist` and
`test_disabled_blocking_per_url` counted.

**Measured branch: the guard.** An oracle without that preservation check would have forgiven
the draw, because `hostblock.py@1` restores the whitelist test and is required. That gap is
**L33**, filed at landing.

## Suite wall time (AC14)

| | tests | wall time |
|---|---|---|
| before (C20's proof, `28caeac`) | 1441 passed, 9 skipped | 33:11 |
| after (spec 13, `scripts/suite` on the VM) | 1490 passed, 9 skipped | 36:59 (2219.28 s) |

The container work this spec adds:
- `tests/test_obsolescence.py -k real` makes 4 gradings (4:10 on the Mac).
- Two of those gradings ablate, at about 45 s a run: gold's 6 hunks and gold-plus-unrelated's 7.
- AC12's and AC13's gradings skip on the guard and use no container.
- Every other real grading in the suite that routes `regressed` now ablates too, unless its
  guard fires.
