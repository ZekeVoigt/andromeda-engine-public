# Spec 19 report — tamper-resistant checks

A hidden check's result is now computed on the host, from values the box hands back, against
expected values that never enter a box that runs the diff's code.

**Measured for real:**
- 17's force-pass, with 17's rule and 18's canaries both bypassed, grades **PASS** on
  c580ebf0's reported oracle. That is the hole, as 17 and 18 reproduced it.
- The same diff grades **FAIL** on c580ebf0's sealed oracle. Its hidden checks are byte-equal
  to `empty.diff`'s: it rewrote every report and moved no sealed check.
- Gold grades PASS / `resolved` on all three sealed oracles, with every key observed.
- A search of every grading box of c580ebf0's gold run finds no strong expected value.
- Across 18 cells of unselected sealed draws, gold is never FAIL and half is never PASS.

## How `outcome_seal` becomes importable

The spec left the mechanism to the build. The helper goes into each interpreter's own site dir.

- For each of `python`, `python3`, `python3.6` ... `python3.13` present in the box, the site
  dir is `next(d for d in site.getsitepackages() if d in sys.path)` (`runner.PLANT_SEAL`).
- It is not a conftest, it touches no tracked file, and it needs no `PYTHONPATH`.

Measured through the real `run_script.sh` before any wiring:

| image | interpreter the runner uses | where the helper had to go |
|---|---|---|
| c580ebf0, f91ace96 (qutebrowser) | `/usr/local/bin/python` 3.9 / 3.11, pytest | `/usr/local/lib/python3.X/site-packages` |
| ansible f327e65d | `/usr/bin/python` 3.9.5 via ansible-test's execv wrapper, xdist `-n auto --boxed` | `/usr/local/lib/python3.9/dist-packages` |

**The trap:** the plan's first candidate, `sysconfig.get_paths()["purelib"]`, gives
`/usr/lib/python3.9/site-packages` on the ansible image. Debian's python3.9 never reads that
directory, so `ansible-test units` failed with `ModuleNotFoundError: No module named
'outcome_seal'` while qutebrowser worked.

AC10 proves the choice on both runners: every expected key was observed on gold's after side.

## The pinned sealed oracles (AC9)

| task | keys | strong | share | leaks | draws | gold on it |
|---|---|---|---|---|---|---|
| ansible-f327e65d | 14 | 12 | 86% | 0 | 1 | PASS / resolved — `all 11 valid hidden checks pass, no regressions` |
| qutebrowser-c580ebf0 | 5 | 4 | 80% | 0 | 2 | PASS / resolved — `all 5 valid hidden checks pass, no regressions; 1 existing checks stale, forgiven by required-hunk: tests/unit/components/test_hostblock.py::test_disabled_blocking_per_url` |
| qutebrowser-f91ace96 | 6 | 6 | 100% | 0 | 1 | PASS / resolved — `all 6 valid hidden checks pass, no regressions` |

About c580ebf0's draws:
- Its first draw had 9 keys, 6 strong (67%). That is under AC9's 80%, so it was not pinned.
  Three of its weak keys were bare lengths of widened hostname lists.
- One attempt between the two never reached the writer: the Mac's disk was full (`No space left
  on device` copying the Claude binary in), so it is not counted as a draw.

Every pinned oracle observes composite values, for example:
- the blocked flag for 17 URLs;
- the widened sequences of 8 hostnames;
- `validate_collection_name`'s outcome for a list of names.

## The measurement (AC12)

`scripts/sealed_variance.py` drew 2 sealed oracles per task and kept each one. It graded gold
and half on those and on the pinned one, 18 cells in `fixtures/sealed-variance/results.tsv`:

| task | oracle | keys | strong | leaked | gold | half |
|---|---|---|---|---|---|---|
| qutebrowser-f91ace96 | pinned | 6 | 6 | 0 | PASS / resolved | FAIL / unresolved (4/6) |
| qutebrowser-f91ace96 | draw-01 | 7 | 7 | 0 | PASS / resolved | FAIL / unresolved (5/7) |
| qutebrowser-f91ace96 | draw-02 | 6 | 6 | 0 | PASS / resolved | FAIL / unresolved (4/6) |
| qutebrowser-c580ebf0 | pinned | 5 | 4 | 0 | PASS / resolved | FAIL / unresolved (3/5) |
| qutebrowser-c580ebf0 | draw-01 | 5 | 5 | 0 | PASS / resolved | FAIL / unresolved (3/4) |
| qutebrowser-c580ebf0 | draw-02 | 4 | 4 | 0 | PASS / resolved | FAIL / unresolved (2/3) |
| ansible-f327e65d | pinned | 14 | 12 | 0 | PASS / resolved | FAIL / unresolved (8/11) |
| ansible-f327e65d | draw-01 | 20 | 17 | 0 | PASS / resolved | FAIL / unresolved |
| ansible-f327e65d | draw-02 | 8 | 8 | 0 | PASS / resolved | FAIL / unresolved |

**Gold is never FAIL and half is never PASS**, and there is no UNKNOWN. Every one of the 6
unselected draws is 0-leak, and 5 of the 6 are 100% strong (ansible draw-01 is 17/20).

Beside spec 02's **reported** cells for the same tasks and patches (`fixtures/variance/`, 6
oracles each):

| task | patch | reported (spec 02, 6 oracles) | sealed (19, 3 oracles) |
|---|---|---|---|
| qutebrowser-f91ace96 | gold | UNKNOWN 6 (4 existing checks regressed) | PASS 3 |
| qutebrowser-f91ace96 | half | FAIL 6 | FAIL 3 |
| qutebrowser-c580ebf0 | gold | FAIL 6 (1–3 hidden checks failing) | PASS 3 |
| qutebrowser-c580ebf0 | half | FAIL 6 | FAIL 3 |
| ansible-f327e65d | gold | PASS 6 | PASS 3 |
| ansible-f327e65d | half | FAIL 6 | FAIL 3 |

Spec 02's cells were graded by the engine as it stood then. That was before spec 03's 20%
hidden-fail tolerance, spec 13's required-hunk attribution and 18's base-test rule. Its gold
UNKNOWNs and FAILs are those rules' findings, which the later specs acted on. The comparison
shows sealing costs no correct verdict. It does not show sealing is what changed them.

**Every draw was kept.** The ansible variance draws were each *attempted* several times:
- your account's rate limit rejected `claude -p` with a 429 before any oracle was written;
- the script retries such an infra failure and never retries a finished draw;
- `pin(check=False)` would have kept a leaking or unloadable finished draw as drawn. None was
  either.

## ACs

| AC | status | evidence |
|---|---|---|
| 1 the seal's pure parts | proven | `test_ac1_*`: canonical, strength, 11 `load` refusals, every lint form, no env, the helper run for real |
| 2 the judge | proven | `test_ac2_*`: every bullet; canonical equality; junk, `inf`, deep nesting and a truncated tail counted |
| 3 forgeries | proven | `test_ac3_*` over `fixtures/sealed/<c580ebf0>/`, `empty.diff`'s real bytes. The seven forgeries: (a) reports rewritten, (b) before replayed, (c) every literal the box knows, (d) deleted / truncated / padded, and a+b. Each leaves every valid hidden check non-pass and FAIL; under (a), `after_seal.json` is byte-equal. **Control:** the reported path (`sealed=None`) credits (a) with PASS |
| 4 the runner's wiring | proven | `test_ac4_*`, a recording box: 18's files and bytes with `sealed=None`, observers' reports dropped, oversize both ways, invariant 1 over every copy, command and kwarg |
| 5 the store | proven | `test_ac5_*`: all 18 stored oracles load unedited as reported; a sealed pin round-trips; drift over `expected/`; refusals leave the store byte-equal |
| 6 the writer | proven | `test_ac6_*` (one in a real box); isolation AC8 and primer pass unmodified |
| 7 the adapter | proven | `test_ac7_*`, including a stray file under a fresh draw's `/hidden` (gap-check round 1) |
| 8 nothing else moves | proven | zero edits to any existing test: `git diff main -- tests/` touches only `test_sealed_checks.py` |
| 9 sound sealed oracles | proven | `test_ac9_*`, and the table above |
| 10 gold, for real | proven | `test_real_ac10_*` on Colima and on the VM (runsc, `OUTCOME_ISOLATION=required`); c580ebf0's 8 grading boxes (before, after, 6 ablation hunks) searched on the host, with a positive control that finds gold's values in the after box's own observations |
| 11 the tamper, for real | proven | `test_real_ac11_*`: empty and half FAIL; force-pass PASS reported / FAIL sealed; hidden triples equal empty's; truth `resolved` |
| 12 the measurement | proven | the tables above; `test_ac12_*` |
| 13 the record and the suite | proven | this file; the suite line below |

## Two decisions the spec left open

- **`seal.load` is bound, not defined, under that name.** `tests/test_boundaries.py` must pass
  unmodified (AC8). It pins the exact set of engine modules defining a top-level `load`. So
  `seal.py` defines `_load` and binds `load = _load`, and the interface is still `seal.load`.
- **`pin(check=False)`, for the variance script alone.** The spec asks `pin` to refuse a leak
  and also asks the variance script to keep every draw. Both hold only if the script can keep a
  refused draw. It was never needed: no unselected draw leaked.

## Found building, fixed in scope

- **Gap-check round 1: the adapter linted only the observers.** A fresh draw copies its *whole*
  `hidden_tests/` into the box, so a stray data file holding an expected value would have
  carried it past invariant 1. It now lints every file it will copy in.
- **An observer whose every key leaks** stays in the expectations, keyed to nothing
  (`seal.without`). Its runner report is still discarded and can never become a hidden check.
- **`1e400` parses to `inf`** without calling `parse_constant`, and a forged line can nest past
  the parser's depth. Both are junk lines to `judge`, never a crash, so a forged file can only
  fail a check or, oversized, make the side `unverified`.

## L39's record

Spec 18's three unconstructed reproductions were each working test-tampering code that the 18
build and the operator declined to write:
- an import-time `_pytest` patch;
- an `atexit` forger of `PASSED` lines;
- a regression hidden by a test-module edit.

For the hidden checks, the first two are now covered without such code. A sealed check's status
is a function of the expectations and the observation bytes alone (invariant 2), and any
rewritten report is forgery (a):
- AC4 shows the runner discards an observer's own report;
- AC3 shows no forgery of the returned bytes moves a sealed check;
- AC11 shows 17's committed force-pass, which rewrites every report, graded FAIL for real.

The hidden regression is an existing check's, so it is **L40**'s. No exploit code was written
for this spec.

## The suite

`scripts/suite`, a gate VM (runsc, `OUTCOME_ISOLATION=required`), on the build:
- **2037 passed, 8 skipped, 1 failed in 0:34:54**, the one failure being AC12's table (not yet
  drawn then);
- the 8 skips are `tests/expected_skips/gate.txt`'s, so no new expected skip;
- that is against 18's 0:46:04 on a gate VM (4 cores; a gate VM has 8).

The final line, after AC12's cells:

`scripts/suite` on a gate VM (runsc, `OUTCOME_ISOLATION=required`), the landing tree (main `5368dae` + 19): **`green: gate 2038 passed, 8 skipped as expected`**, exit 0, in 2073.47 s (0:34:33). ACs 10-11's real gradings ran on gVisor there as well as on Colima. After main moved to C23's merge (`20fbdee`), the tree was re-proven: `scripts/suite` on a gate VM (runsc, 4 cores), main `20fbdee` + 19: **`green: gate 2069 passed, 8 skipped as expected`**, exit 0, in 4194.87 s (1:09:54). Main moved once more, to E09's merge (`16e7275`), and the tree was proven again: `scripts/suite` on a gate VM, main `16e7275` + 19: **`green: gate 2140 passed, 10 skipped as expected`**, exit 0, in 2237.56 s (0:37:17) -- E09 added the two new listed skips, so none is 19's.
