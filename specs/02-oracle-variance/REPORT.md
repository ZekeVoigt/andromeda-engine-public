# Spec 02 — Oracle variance: results

Measured 2026-09-18. 3 tasks x 6 oracles x 2 patches = 36 cells, from 15 new draws plus the
oracle spec 01 pinned. Every cell ran `outcome run --patch <fixture> --oracle <dir>` and its
verdict was read from a real `record.json`. Reproduce with:

```bash
uv run python scripts/oracle_variance.py --check-only   # 0 draws remaining, 0 cells remaining
uv run pytest -q tests/test_oracle_variance.py
```

## The headline

**The verdict did not move at all.** All six cells agree in all six (task, patch) pairs —
every row below is 6-0-0. The oracles themselves vary enormously while this happens: the
number of *valid* hidden checks a draw produces ranges from 7 to 13 on one task and from 15
to 80 on another. A draw can be twice the size of its neighbour and still return the same
verdict.

That was the question spec 02 was asked, and the answer is that draw luck is not the
problem. What the 36 cells exposed instead is that the stable verdict is **wrong on two of
the three tasks**, and for two different reasons — neither of them oracle variance.

## Distribution

Counts across the 6 oracles (`pinned` + `draw-01..05`), so every row sums to 6. The `pinned`
column is spec 01's own draw, showing where it sits in its distribution.

<!-- distribution:start -->
| task | patch | PASS | FAIL | UNKNOWN | pinned |
|---|---|---|---|---|---|
| qutebrowser-f91ace96 | gold | 0 | 0 | 6 | UNKNOWN |
| qutebrowser-f91ace96 | half | 0 | 6 | 0 | FAIL |
| qutebrowser-c580ebf0 | gold | 0 | 6 | 0 | FAIL |
| qutebrowser-c580ebf0 | half | 0 | 6 | 0 | FAIL |
| ansible-f327e65d | gold | 6 | 0 | 0 | PASS |
| ansible-f327e65d | half | 0 | 6 | 0 | FAIL |<!-- distribution:end -->

`tests/test_oracle_variance.py` re-derives this table from `fixtures/variance/results.tsv`
and asserts equality, so an edited report or a stale TSV fails `pytest`.

## What spec 03 should take from this

**1. Do not add a tolerance for draw luck. There is no draw luck to absorb.**

Spec 03 was waiting to learn whether `gold` gives `PASS=6` or `PASS=4 FAIL=2`. It gives 6-0-0
in every pair. A tolerance sized for oracle variance would be sized for a phenomenon that did
not appear in 36 cells, and it would mask the two real defects below rather than fix them.

**2. The regression rule counts a correctly obsoleted test as a regression.**

`qutebrowser-f91ace96` gold is `UNKNOWN` in all 6 cells, always with the same reason: *all N
valid hidden checks pass but 4 existing checks regressed*. The four are identical in all six
runs:

```
tests/unit/utils/test_log.py::TestHideQtWarning::test_filtered[Hello]
tests/unit/utils/test_log.py::TestHideQtWarning::test_filtered[Hello World]
tests/unit/utils/test_log.py::TestHideQtWarning::test_filtered[  Hello World  ]
tests/unit/utils/test_log.py::TestHideQtWarning::test_unfiltered
```

The gold patch **moves `hide_qt_warning` from `qutebrowser/utils/log.py` to
`qutebrowser/utils/qtlog.py`** — that relocation is what the task asks for. `test_log.py`
tests the function at its old home, so those four failing is the patch *working*. The hidden
tests agree: every draw wrote `test_outcome_qtlog_hide_warning.py` against the new location
and every one passes.

A constant across six independent draws is not caused by the drawn tests. This is
[verdict.py:19-26](../../src/andromeda/outcome/core/verdict.py#L19-L26) treating "existing
test that passed on base and fails after" as a regression, with no way to express "the change
was supposed to break this one". SWE-bench Pro's own grading names the existing tests that
must keep passing; the engine runs all of them. **Spec 03 owns this, and it is a correctness
bug, not a threshold to tune.**

**3. A handful of bad hidden tests turns a resolved patch into FAIL — and this one may want a tolerance.**

`qutebrowser-c580ebf0` gold is `FAIL` in all 6 cells although its ground truth is `resolved`
— a false FAIL under every oracle. But unlike case 2 the *count* moves:

| oracle | failing / valid |
|---|---|
| pinned | 2 / 19 |
| draw-01 | 1 / 16 |
| draw-02 | 1 / 15 |
| draw-03 | 3 / 19 |
| draw-04 | 1 / 18 |
| draw-05 | 2 / 19 |

Every draw writes between 1 and 3 hidden tests that a correct patch cannot pass, out of 15 to
19. **If spec 03 wants a tolerance anywhere, this is the number to argue from: the worst draw
needs 3/19 ≈ 16% forgiven, the mildest 1/18 ≈ 5.6%.** Note what that costs — `half.diff` on the
same task fails 6 to 10 of the same denominators, so a threshold set at 16% still separates
gold from half here, but not by much. Spec 03 should decide whether that margin is worth
having, or whether the bad tests are spec 04's problem to grade out instead.

**4. Oracle size is not oracle quality.**

`ansible-f327e65d` is the one task that behaves: `PASS` under all 6 oracles, `FAIL` on half
under all 6. Its draws are also the most erratic in size — `n_hidden` 34 to 91, `n_valid` 15
to 80. `draw-01` drew 54 hidden tests of which only 15 were valid: 39 tests that passed on
base and therefore tested nothing about the change. The verdict survived that, but 39 wasted
tests per draw is real cost and a real blind spot. That belongs to spec 04 (oracle quality),
not to spec 03.

## Limitation

Of the five fields `load_oracle` re-checks on every load, the committed
`fixtures/variance/tasks/<id>.json` freezes `base_commit` on both sides — the gate compares
the stored manifest against a stored task, so **it cannot catch a dataset row moving under a
draw**. The other four (`image`, `dataset_revision`, `claude_version`, `prompt_sha256`) are
code- or environment-side and remain live checks. `scripts/oracle_variance.py` uses the live
`load_safe` rather than these files and reports drift instead of overwriting them, so a moved
row surfaces the next time the script runs.

## Supporting table — all 36 cells

`reason` is whitespace-collapsed and truncated to 80 characters, as committed.

| task | oracle | patch | verdict | truth | n_hidden | n_valid | reason |
|---|---|---|---|---|---|---|---|
| qutebrowser-f91ace96 | pinned | gold | UNKNOWN | resolved | 10 | 10 | all 10 valid hidden checks pass but 4 existing checks regressed: tests/unit/u... |
| qutebrowser-f91ace96 | pinned | half | FAIL | unresolved | 10 | 10 | 7/10 valid hidden checks fail after change: tests/unit/utils/test_outcome_qtl... |
| qutebrowser-f91ace96 | draw-01 | gold | UNKNOWN | resolved | 7 | 7 | all 7 valid hidden checks pass but 4 existing checks regressed: tests/unit/ut... |
| qutebrowser-f91ace96 | draw-01 | half | FAIL | unresolved | 7 | 7 | 6/7 valid hidden checks fail after change: tests/unit/utils/test_outcome_qtlo... |
| qutebrowser-f91ace96 | draw-02 | gold | UNKNOWN | resolved | 12 | 12 | all 12 valid hidden checks pass but 4 existing checks regressed: tests/unit/u... |
| qutebrowser-f91ace96 | draw-02 | half | FAIL | unresolved | 12 | 12 | 7/12 valid hidden checks fail after change: tests/unit/utils/test_outcome_qtl... |
| qutebrowser-f91ace96 | draw-03 | gold | UNKNOWN | resolved | 13 | 13 | all 13 valid hidden checks pass but 4 existing checks regressed: tests/unit/u... |
| qutebrowser-f91ace96 | draw-03 | half | FAIL | unresolved | 13 | 13 | 8/13 valid hidden checks fail after change: tests/unit/utils/test_outcome_qtl... |
| qutebrowser-f91ace96 | draw-04 | gold | UNKNOWN | resolved | 10 | 10 | all 10 valid hidden checks pass but 4 existing checks regressed: tests/unit/u... |
| qutebrowser-f91ace96 | draw-04 | half | FAIL | unresolved | 10 | 10 | 6/10 valid hidden checks fail after change: tests/unit/utils/test_outcome_qtl... |
| qutebrowser-f91ace96 | draw-05 | gold | UNKNOWN | resolved | 10 | 10 | all 10 valid hidden checks pass but 4 existing checks regressed: tests/unit/u... |
| qutebrowser-f91ace96 | draw-05 | half | FAIL | unresolved | 10 | 10 | 6/10 valid hidden checks fail after change: tests/unit/utils/test_outcome_hid... |
| qutebrowser-c580ebf0 | pinned | gold | FAIL | resolved | 24 | 19 | 2/19 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | pinned | half | FAIL | unresolved | 24 | 19 | 8/19 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-01 | gold | FAIL | resolved | 19 | 16 | 1/16 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-01 | half | FAIL | unresolved | 19 | 16 | 6/16 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-02 | gold | FAIL | resolved | 19 | 15 | 1/15 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-02 | half | FAIL | unresolved | 19 | 15 | 7/15 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-03 | gold | FAIL | resolved | 23 | 19 | 3/19 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-03 | half | FAIL | unresolved | 23 | 19 | 10/19 valid hidden checks fail after change: tests/unit/components/test_outco... |
| qutebrowser-c580ebf0 | draw-04 | gold | FAIL | resolved | 25 | 18 | 1/18 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-04 | half | FAIL | unresolved | 25 | 18 | 7/18 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-05 | gold | FAIL | resolved | 25 | 19 | 2/19 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| qutebrowser-c580ebf0 | draw-05 | half | FAIL | unresolved | 25 | 19 | 9/19 valid hidden checks fail after change: tests/unit/components/test_outcom... |
| ansible-f327e65d | pinned | gold | PASS | resolved | 37 | 25 | all 25 valid hidden checks pass, no regressions |
| ansible-f327e65d | pinned | half | FAIL | unresolved | 37 | 25 | 25/25 valid hidden checks fail after change: test/units/galaxy/test_outcome_v... |
| ansible-f327e65d | draw-01 | gold | PASS | resolved | 54 | 15 | all 15 valid hidden checks pass, no regressions |
| ansible-f327e65d | draw-01 | half | FAIL | unresolved | 54 | 15 | 13/15 valid hidden checks fail after change: test/units/utils/collection_load... |
| ansible-f327e65d | draw-02 | gold | PASS | resolved | 91 | 80 | all 80 valid hidden checks pass, no regressions |
| ansible-f327e65d | draw-02 | half | FAIL | unresolved | 91 | 80 | 80/80 valid hidden checks fail after change: test/units/utils/collection_load... |
| ansible-f327e65d | draw-03 | gold | PASS | resolved | 69 | 29 | all 29 valid hidden checks pass, no regressions |
| ansible-f327e65d | draw-03 | half | FAIL | unresolved | 69 | 29 | 29/29 valid hidden checks fail after change: test/units/utils/collection_load... |
| ansible-f327e65d | draw-04 | gold | PASS | resolved | 58 | 45 | all 45 valid hidden checks pass, no regressions |
| ansible-f327e65d | draw-04 | half | FAIL | unresolved | 58 | 45 | 45/45 valid hidden checks fail after change: test/units/galaxy/test_outcome_v... |
| ansible-f327e65d | draw-05 | gold | PASS | resolved | 34 | 23 | all 23 valid hidden checks pass, no regressions |
| ansible-f327e65d | draw-05 | half | FAIL | unresolved | 34 | 23 | 23/23 valid hidden checks fail after change: test/units/galaxy/test_outcome_c... |
