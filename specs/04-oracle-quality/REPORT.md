# Spec 04 — Oracle quality: results

Measured 2026-09-19, against the same 18 (task, oracle) pairs spec 02 drew. Nothing was run
in a container to produce these numbers: `fixtures/verdict-corpus/` already holds each hidden
check's status on base, under the gold patch and under the half patch, and three observations
is the whole input. Reproduce with:

```bash
uv run pytest -q tests/test_oracle_quality.py                     # no Docker, ~0.1s of work
uv run python -m andromeda.outcome.grading.oracle_quality         # rewrite the scorecard
```

The verdict path is untouched. The grade is computed **from the gold patch**, and letting it
reach `decide()` would make the verdict a function of the answer the engine has to find on
its own. Spec 12 is the consumer; this spec measures and stops.

## The headline

**540 hidden tests were written across 18 oracles. 292 of them discriminate — 54%.**

```
  vacuous  155   29%   passed on base: tests nothing at all
  wrong     10    2%   a correct patch cannot pass it
  blunt     83   15%   valid, but the known-broken patch passes too
  good     292   54%   fails on base, passes under gold, fails under half
```

`blunt` is the new signal. `vacuous` was always derivable as `n_hidden - n_valid` and `wrong`
is spec 03's per-cell `fail` column, but nothing before this measured a hidden test that lets
a broken patch through — and 83 of them do.

**On all three tasks the pinned oracle scores below the best draw.** Spec 01 drew once and
kept it, and on no task did that single draw turn out to be the best one available:

<!-- headline:begin -->

| task | pinned | best draw | worst draw | spread | draws beating pinned | mean of the 5 draws |
|---|---|---|---|---|---|---|
| ansible-f327e65d | 0.68 | **0.88** (draw-02) | 0.24 (draw-01) | 0.64 | 3 of 5 | 0.60 |
| qutebrowser-c580ebf0 | 0.25 | **0.32** (draw-02) | 0.24 (draw-04) | 0.08 | **4 of 5** | 0.28 |
| qutebrowser-f91ace96 | 0.70 | **0.86** (draw-01) | 0.58 (draw-02) | 0.27 | 1 of 5 | 0.65 |

<!-- headline:end -->

`spread`, `draws beating pinned` and `mean of the 5 draws` are all computed on the
**unrounded** ratio `good / n_hidden`, not on the 2dp scores displayed beside them, and
`test_the_headline_table_is_derivable_from_the_scorecard` asserts all three that way. The
distinction is not cosmetic, and it bites in both directions:

- `ansible-f327e65d`, `draws beating pinned`: draw-05 scores 23/34 = 0.6765 against pinned's
  25/37 = 0.6757, so it genuinely beats the pin while both display as 0.68. Counting at 2dp
  would read **2 of 5** and hide it.
- `qutebrowser-f91ace96`, `spread`: 0.857143 − 0.583333 = 0.2738 → **0.27**. Subtracting the
  two displayed values instead gives 0.86 − 0.58 = 0.28, which is wrong by a rounding step.

So do not recompute either column from the rendered table — the test will reject it.

Pinned beats the mean of the draws on two tasks and loses to it on the third. On
`qutebrowser-c580ebf0` — the worst-scoring task, and the one where the engine already
disagrees with ground truth on all six gold cells — the pinned oracle is **below** the mean
(0.25 vs 0.28) and the median (0.28) of the draws it was chosen from, and four of five
independent draws beat it. So the pin is not reliably an above-average draw; it is one
sample, and on the task that needs the most help it landed in the bottom third.

This is the finding spec 02 could not reach. Spec 02 showed the *verdict* never moves between
draws — 6-0-0 in all six (task, patch) pairs. That is true and it is not reassuring: the
draws agree on the verdict while differing 0.24-to-0.88 in quality. Agreement was never
evidence of quality, and now there is a number saying so.

## Per-cell

From [fixtures/oracle-quality/scorecard.tsv](../../fixtures/oracle-quality/scorecard.tsv),
which `tests/test_oracle_quality.py` re-derives cell by cell. The fences below are read by
`test_the_report_table_is_derivable_from_the_committed_scorecard`: an edited table or a stale
scorecard fails `pytest`.

<!-- scorecard:begin -->

| task | oracle | n_hidden | vacuous | wrong | blunt | good | score |
|---|---|---|---|---|---|---|---|
| ansible-f327e65d | pinned | 37 | 12 | 0 | 0 | 25 | 0.68 |
| ansible-f327e65d | draw-01 | 54 | 39 | 0 | 2 | 13 | **0.24** |
| ansible-f327e65d | draw-02 | 91 | 11 | 0 | 0 | 80 | **0.88** |
| ansible-f327e65d | draw-03 | 69 | 40 | 0 | 0 | 29 | 0.42 |
| ansible-f327e65d | draw-04 | 58 | 13 | 0 | 0 | 45 | 0.78 |
| ansible-f327e65d | draw-05 | 34 | 11 | 0 | 0 | 23 | 0.68 |
| qutebrowser-c580ebf0 | pinned | 24 | 5 | 2 | 11 | 6 | 0.25 |
| qutebrowser-c580ebf0 | draw-01 | 19 | 3 | 1 | 10 | 5 | 0.26 |
| qutebrowser-c580ebf0 | draw-02 | 19 | 4 | 1 | 8 | 6 | **0.32** |
| qutebrowser-c580ebf0 | draw-03 | 23 | 4 | 3 | 9 | 7 | 0.30 |
| qutebrowser-c580ebf0 | draw-04 | 25 | 7 | 1 | 11 | 6 | **0.24** |
| qutebrowser-c580ebf0 | draw-05 | 25 | 6 | 2 | 10 | 7 | 0.28 |
| qutebrowser-f91ace96 | pinned | 10 | 0 | 0 | 3 | 7 | 0.70 |
| qutebrowser-f91ace96 | draw-01 | 7 | 0 | 0 | 1 | 6 | **0.86** |
| qutebrowser-f91ace96 | draw-02 | 12 | 0 | 0 | 5 | 7 | **0.58** |
| qutebrowser-f91ace96 | draw-03 | 13 | 0 | 0 | 5 | 8 | 0.62 |
| qutebrowser-f91ace96 | draw-04 | 10 | 0 | 0 | 4 | 6 | 0.60 |
| qutebrowser-f91ace96 | draw-05 | 10 | 0 | 0 | 4 | 6 | 0.60 |

<!-- scorecard:end -->

Bold marks each task's best and worst draw; the parser strips it.

## The dominant defect is different on every task

| task | totals | dominant defect |
|---|---|---|
| ansible-f327e65d | 343 hidden, 126 vacuous, 0 wrong, 2 blunt, 215 good — 0.63 | **vacuous** |
| qutebrowser-c580ebf0 | 135 hidden, 29 vacuous, 10 wrong, 59 blunt, 37 good — 0.27 | **blunt**, plus all 10 `wrong` |
| qutebrowser-f91ace96 | 62 hidden, 0 vacuous, 0 wrong, 22 blunt, 40 good — 0.65 | **blunt** |

**`ansible-f327e65d` writes tests that already passed.** 39 of 54 in draw-01 and 40 of 69 in
draw-03 passed at the base commit, so they assert behavior the change did not introduce. What
survives is good — 0 `wrong` and 2 `blunt` across all six oracles — so this is a volume
problem, not a correctness one: the writer pads the file, and the padding is inert. draw-02 is
the counter-example and the best cell in the corpus, 91 tests of which 80 discriminate.

**`qutebrowser-c580ebf0` writes tests the broken patch passes.** 8–11 `blunt` per cell, 59 of
135 overall, against only 37 good. The half patch implements enough of the change to satisfy
most of the oracle, so the oracle is not measuring the part that is missing. It is the worst
task in the corpus by a wide margin (0.27) and the one where drawing more oracles helps least
— every draw lands between 0.24 and 0.32.

**`qutebrowser-f91ace96` writes small, valid oracles that the broken patch still slips past.**
Zero vacuous and zero wrong across all six: every test it writes fails on base and passes
under gold, which is the cleanest behavior in the corpus. But 1–5 per cell are blunt — 22 of
62 overall, and 3 or more in five of the six cells; draw-01 is the exception at 1 of 7, and
it is also the best-scoring cell on the task. The defect here is not noise, it is
insufficient coverage of the specific thing the half patch omits.

## All 10 `wrong` checks are the same test, invented six times

Every `wrong` check in the corpus is on `qutebrowser-c580ebf0`, and every one is a
trailing-dot host-equivalence test. They are **10 distinct names** — not one test recorded
repeatedly — spread over all six independent draws:

| oracle | check |
|---|---|
| pinned | `test_outcome_hostblock_subdomains.py::test_trailing_dot_handled_equivalently[example.com-https://example.com.]` |
| pinned | `test_outcome_hostblock_subdomains.py::test_trailing_dot_handled_equivalently[example.com.-https://example.com]` |
| draw-01 | `test_outcome_hostblock.py::test_trailing_dot_request_host_matches_blocklist` |
| draw-02 | `test_outcome_hostblock_subdomains.py::test_trailing_dot_host_matches_non_trailing_blocklist_entry` |
| draw-03 | `test_outcome_hostblock_subdomains.py::test_trailing_dot_equivalence[example.com-example.com.]` |
| draw-03 | `test_outcome_hostblock_subdomains.py::test_trailing_dot_equivalence[example.com-sub.example.com.]` |
| draw-03 | `test_outcome_hostblock_subdomains.py::test_trailing_dot_equivalence[example.com.-example.com]` |
| draw-04 | `test_outcome_hostblock.py::test_trailing_dot_equivalent_to_no_trailing_dot` |
| draw-05 | `test_outcome_hostblock_subdomains.py::test_trailing_dot_hosts_match_like_normal_hosts[https://example.com./]` |
| draw-05 | `test_outcome_hostblock_subdomains.py::test_trailing_dot_hosts_match_like_normal_hosts[https://sub.example.com./]` |

(paths abbreviated; all are under `tests/unit/components/`.)

Six independent draws, given the same issue text, each reached for trailing-dot equivalence
and each wrote an assertion the official fix does not satisfy. That is not sampling noise —
it is a systematic misreading of the issue, and re-drawing cannot fix it because every draw
makes the same mistake. It is also why these 10 are exactly spec 03's per-cell `fail` column
for the gold cells (2, 1, 1, 3, 1, 2), and why the 20% tolerance exists at all: without it
these ten checks alone would fail a correct patch on all six c580ebf0 oracles.

## What is left

**A drawn oracle is worth grading before it is trusted, and nothing yet does.** The scorecard
is committed and asserted, but no code reads it: `decide()` does not see it, `pin` does not
prefer a better-scoring draw, and no quality floor fails the gate. That is deliberate — see
the spec's "Out of scope" — and **spec 12 is the consumer.** The evidence it needs is now on
disk, and it changed the case for consensus. Ledger entry
[L16](../OPEN-ITEMS.md) held that consensus was unjustified on spec 02's evidence, because
the six oracles never disagreed on a verdict — 36 cells, 6-0-0 every time. They do disagree
on quality, 0.24 to 0.88 on `ansible-f327e65d`, which is the new evidence that row named as
its reopening trigger. It reopens the case for **weighting**, not for a majority vote: a
scheme that prefers a better-scoring oracle has something to act on where a counting scheme
still does not. L16's row now says so.

**`blunt` is measured against one known-broken patch, not against broken patches in general.**
`fixtures/bad_patches/<task>/half.diff` is a single mutant. A test called `blunt` here is
blunt *against that mutation*; it might discriminate perfectly against a different one. The
83 blunt checks are therefore a lower bound on oracle quality, not a verdict on each test —
a second mutant would move the number and nothing in this spec pins which direction.

**The `before == "error"` branch of `classify` is unexercised.** No hidden check in the corpus
errors on base (378 `existing` checks do, 0 hidden), so the branch that routes errors to
`vacuous` never fires on real data. It exists so `n_hidden - vacuous` equals `decide()`'s
valid count by construction rather than by luck, and
`test_no_hidden_check_errors_on_base` pins the fact that it is unexercised, so the day it
starts firing is visible rather than silent.

**The test is Docker-free; the suite's leak guard is not.** `tests/test_oracle_quality.py`
does 0.11 s of work and touches no container — `uv run pytest -q tests/test_oracle_quality.py
--noconftest` runs it in **0.11 s**. Run normally it reports ~1.2 s, because `tests/conftest.py`
has an autouse fixture that shells out to `docker ps` around every test in the suite. That
cost is the harness's, not this spec's, and it is the same for every test file in the repo.
