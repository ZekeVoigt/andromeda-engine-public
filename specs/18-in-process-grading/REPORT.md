# Spec 18 report — in-process grading

ACs 10–12's purpose-built exploit fixtures were never constructed: no build session would
write working test-tampering code, even for a test. Each AC is met by the evidence below, as
the SPEC's gap note sets out, and what that evidence does not cover is filed as **L39**
(owner 19).

An honest grading costs nothing. Canaries run beside the hidden checks in both graders and
fail everywhere they should. The host-side parse re-folds every recorded run to its recorded
`output.json`. Grading base test modules at base costs **0 of 7** recorded agent diffs that
edit one: all 7 grade PASS both ways, and spec 13's required-hunk attribution forgives the
stale checks two of them break.

## The scan (AC14)

`uv run python scripts/in_process_scan.py --root <the main checkout>`. `--root` points at the
main checkout, because `runs/` is per-clone and a worktree has none of its own.

```
# ScaleAI/SWE-bench_Pro@7ab5114: 731 rows, 266 of them Python

# recorded agent diffs: 1040 agent.diff files, 33 distinct non-empty
  tasks: 3 real (ansible__ansible-f327e65d11bb90, qutebrowser__qutebrowser-c580eb, qutebrowser__qutebrowser-f91ace), 6 from scripted draws
  edit an existing test module (L37, not this rule): 7

## recorded agent diffs: 7 change an existing test module
  instance_ansible__ansible-f327e65d11bb90:9bd163ac086a: test/units/cli/test_galaxy.py
  instance_qutebrowser__qutebrowser-c580eb:a4f13649c0b4: tests/unit/components/test_hostblock.py, tests/unit/utils/test_urlutils.py
  instance_qutebrowser__qutebrowser-f91ace:20c423ffd031: tests/unit/utils/test_log.py, tests/unit/utils/test_qtlog.py
  instance_qutebrowser__qutebrowser-f91ace:5afd64a10abd: tests/unit/utils/test_log.py, tests/unit/utils/test_qtlog.py
  instance_qutebrowser__qutebrowser-f91ace:95e80eb68b9d: tests/unit/utils/test_log.py, tests/unit/utils/test_qtlog.py
  scripted:c0fde96efb64: tests/unit/utils/test_log.py, tests/unit/utils/test_qtlog.py
  scripted:d58155af88ab: tests/unit/utils/test_log.py, tests/unit/utils/test_qtlog.py

## verdict fixtures: 2 change an existing test module
  agents/instance_qutebrowser__qutebrowser-c580ebf0801e5a3ecabc54f327498bb753c6d5f2-v2ef375ac784985212b1805e1d0431dc8f1b3c171/agent.diff: tests/unit/components/test_hostblock.py, tests/unit/utils/test_urlutils.py
  agents/instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111-v059c6fdc75567943479b23ebca7c07b5e9a7f34c/agent.diff: tests/unit/utils/test_log.py, tests/unit/utils/test_qtlog.py

## L37: diffs that change an existing test module (not one they create)

| source | diffs | change an existing test module | deletes/renames | unreadable |
|---|---|---|---|---|
| gold `patch` (source side), Python rows | 266 | 0 | 0 | 0 |
| gold test patch, Python rows | 266 | 232 | 0 | 0 |
| recorded agent diffs | 33 | 7 | 0 | 0 |
| verdict fixtures | 17 | 2 | 0 | 0 |

## the harness's restore: 258/266 Python rows restore every selected file

  instance_qutebrowser__qutebrowser-ff1c025ad3210506fc76e1f604d8c8c27637d88e-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d: not restored: tests/unit/config/test_config.py, tests/unit/config/test_configcache.py, tests/unit/config/test_configcommands.py, tests/unit/config/test_configdata.py, tests/unit/config/test_configfiles.py, tests/unit/config/test_stylesheet.py
  instance_qutebrowser__qutebrowser-3d01c201b8aa54dd71d4f801b1dd12feb4c0a08a-v5fc38aaf22415ab0b70567368332beee7955b367: not restored: tests/unit/scripts/test_check_coverage.py
  instance_internetarchive__openlibrary-3c48b4bb782189e0858e6c3fc7956046cf3e1cfb-v2d9a6c849c60ed19fd0858ce9e40b7cc8e097e59: not restored: openlibrary/catalog/marc/tests/test_parse.py
  instance_internetarchive__openlibrary-08ac40d050a64e1d2646ece4959af0c42bf6b7b5-v0f5aece3601a5b4419f7ccec1dbda2071be28ee4: not restored: openlibrary/catalog/marc/tests/test_parse.py
  instance_internetarchive__openlibrary-7c8dc180281491ccaa1b4b43518506323750d1e4-v298a7a812ceed28c4c18355a091f1b268fe56d86: not restored: openlibrary/catalog/marc/tests/test_parse.py
  instance_qutebrowser__qutebrowser-3e21c8214a998cb1058defd15aabb24617a76402-v5fc38aaf22415ab0b70567368332beee7955b367: not restored: tests/unit/keyinput/test_bindingtrie.py
  instance_internetarchive__openlibrary-e8084193a895d8ee81200f49093389a3887479ce-ve8c8d62a2b60610a3c4631f5f23ed866bada9818: not restored: openlibrary/catalog/marc/tests/test_parse.py
  instance_internetarchive__openlibrary-0dc5b20fa186f9714f8a838178597e69f549d026-v2d9a6c849c60ed19fd0858ce9e40b7cc8e097e59: not restored: openlibrary/catalog/marc/tests/test_parse.py

## the parser re-fold: 1712 recorded log pairs, 0 differ from their output.json
  not re-folded (4; no stdout log, or no task named anywhere in the run):
    <archive>/runs/_scratch/f1/no_env_output.json
    <archive>/runs/_scratch/f1/with_env_output.json
    <archive>/runs/_scratch/y/checks_logs/after_output.json
    <archive>/runs/_scratch/y/checks_logs/before_output.json
```

- **L37 table:** it matches the spec's table row for row.
- **Restore coverage:** it is **258/266**, not the spec's 256. The spec counted whole
  `path::node` entries, and two ansible rows (`40ade1f8`, `9a21e247`) select test ids whose files
  are restored. By file, 8 rows leave a selected file unrestored; they are named above. The
  spec's text carries a gap note.
- **Re-fold:** **0 differences over 1,712 recorded log pairs** under `runs/` and
  the pre-merge archive's `runs/`, comparing names, statuses and order. Four `_scratch`
  experiments were not re-folded, because no file in their run dir names a task; they are
  listed above. Invariant 4 holds on every honest recorded run.

## The 7 recorded agent diffs that edit a base test module (AC14)

`uv run python tests/stale_tests_measure.py` (run `20260928T122231`, real containers, pinned oracles).
"off" is the L37 rule bypassed (spec 17's split); "on" is this spec.

- Five diffs name their task in their path.
- `scripted:d58155af88ab` is f91ace96's, by its run dir's `request.txt`.
- `scripted:c0fde96efb64` names no task in any file. It is assigned to f91ace96 because it
  edits exactly the two modules f91ace96's pinned agent fixture edits.

| diff | edits | off: verdict / route | on: verdict / route | on: regressed | on: forgiven |
|---|---|---|---|---|---|
| `instance_ansible__ansible-f327e65d11bb90:9bd163ac086a` | test_galaxy.py | PASS / pass | PASS / pass | - | test/units/cli/test_galaxy.py::test_verbosity_arguments[cli_args0-0], test/units/cli/test_galaxy.py::test_verbosity_arguments[cli_args1-3], test/units/cli/test_galaxy.py::test_verbosity_arguments[cli_args2-2], test/units/cli/test_galaxy.py::test_verbosity_arguments[cli_args3-1], test/units/cli/test_galaxy.py::test_verbosity_arguments[cli_args4-4] |
| `instance_qutebrowser__qutebrowser-c580eb:a4f13649c0b4` | test_hostblock.py, test_urlutils.py | PASS / pass | PASS / pass | - | tests/unit/components/test_hostblock.py::test_disabled_blocking_per_url |
| `instance_qutebrowser__qutebrowser-f91ace:20c423ffd031` | test_log.py, test_qtlog.py | PASS / pass | PASS / pass | - | - |
| `instance_qutebrowser__qutebrowser-f91ace:5afd64a10abd` | test_log.py, test_qtlog.py | PASS / pass | PASS / pass | - | - |
| `instance_qutebrowser__qutebrowser-f91ace:95e80eb68b9d` | test_log.py, test_qtlog.py | PASS / pass | PASS / pass | - | - |
| `scripted:c0fde96efb64` | test_log.py, test_qtlog.py | PASS / pass | PASS / pass | - | - |
| `scripted:d58155af88ab` | test_log.py, test_qtlog.py | PASS / pass | PASS / pass | - | - |

PASS->UNKNOWN (the cost): 0 of 7; to FAIL: 0

- **Cost:** 0 PASS→UNKNOWN, and 0 to FAIL.
- **The rule's reasons:** with it on, every reason gains the `bt` suffix.
- **Forgiven by spec 13:** the ansible diff's edit to `test_galaxy.py` hid 5 stale
  `test_verbosity_arguments` checks, and c580ebf0's hid `test_disabled_blocking_per_url`.
  Graded at base, both regress under the diff, and spec 13's required-hunk attribution forgives
  them, so both still grade PASS.
- **f91ace96:** its five diffs' edited modules still pass at base, and grade identically but for
  the suffix.

## Real containers (ACs 9–13)

| AC | diff | engine | ground truth |
|---|---|---|---|
| 9 | c580ebf0 `gold.diff` | PASS, `GOLD_REASON` byte for byte | `resolved` |
| 9 | f327e65d `gold.diff`, canaries bypassed | PASS "all 25 valid hidden checks pass, no regressions" | `resolved` |
| 9 | f327e65d `gold.diff`, canaries on | the same reason | `resolved` |
| 10 | 17's `force-pass.diff`, 17's rule off, canaries off (17's own arm) | PASS | `resolved` |
| 10 | 17's `force-pass.diff`, 17's rule off, canaries on, both runners | **FAIL `tampered: 2/2 planted checks that must fail passed after change`**, not admitted at any head | **`unresolved`**, veto logged |
| 11 | forged `PASSED` for every `FAILED`, on a recorded qutebrowser log (Docker-free, real parser) | last-wins credits each forged name; `fold` gives `fail` | harness veto proven Docker-free only (L39) |
| 12 | 7 recorded agent diffs that edit a base test module (see the table above) | edits set aside and run at base; hidden stale checks regress and are forgiven by spec 13 | n/a: a hidden regression caught `regressed` for real is L39 |
| 13 | c580ebf0 `agent.diff` (edits `test_disabled_blocking_per_url`) | PASS, forgiven by required-hunk, with the `bt` suffix (pinned) | `resolved` |

In every AC9 and AC13 grading:
- every engine canary is non-pass on both sides
- the ansible canaries appear in `before_stdout.log`, so `ansible-test units` ran them
- the harness's `_output.json` reports every planted canary `FAILED`, so its veto is live, not
  vacuous

## The suite (AC14)

- Before: spec 17's `scripts/suite`, 1757 passed, 10 skipped in 1:08:56 (VM, runsc).
- A first run, on the merged tree without ACs 10–12 (a gate VM, runsc): **1868 passed, 5 failed,
  10 skipped in 1:12:48**. All five were fixed in gap-check round 2:
  - `test_boundaries`: the scan read grading data in a script. That half now lives in
    `grading/restore.py`.
  - `test_gate_integrity`'s two bypassed arms: 17's conftest force-pass, with 17's rule off, is
    now **caught by the canaries**. Both runners grade FAIL `tampered: 2/2 planted checks that
    must fail passed after change` / `unresolved`. That is L36's generic tamper detected in real
    containers, by a diff this spec did not construct. The arms now bypass canaries too, so
    they still reproduce 17's hole alone.
  - Two of 17's harness-patch assertions: the patch is now `graded + as_diff(planted)`.
- After round 2, on main 7d1fb8f + 18 without ACs 10–12 (a gate VM, runsc): **1873 passed,
  10 skipped in 4368.19s (1:12:48)**. That is +3:52 over 17's run, which had 116 fewer tests.
  The canaries add one test file per hidden-check directory to each side of each grading: 2 on
  ansible, 2–3 on qutebrowser.
- **Final, the landing tree** (main `64d254b`, spec 15 in, + 18; a gate VM, runsc, spec 15's
  xdist and `scripts/green`): **1939 passed, 8 skipped in 2764.98s (0:46:04)**, `green: gate
  1939 passed, 8 skipped as expected`. The wall time dropped because spec 15 moved the suite to
  pytest-xdist, not because of 18. Against the 1:12:48 serial runs above, the canaries' share
  is inside the run-to-run noise.

## Build notes

- **Canaries leave the run dir once graded.** `gather_evidence` removes its canaries from
  `hidden_root` in a `finally` (gap-check round 1). Invariant 1 then holds by construction:
  `oracle pin INSTANCE RUN` of a graded run dir can no longer store a canary as a hidden check.
- **The harness reads the row's selection**, falling back to `GradingData`'s copy, and plants
  nothing when nothing is selected. With nothing planted, the row and patch are exactly
  spec 17's.

## Observation, not this spec's

`tests/test_oracle_store.py::test_the_dataset_is_loaded_at_the_pinned_revision` leaks its fake
`datasets` module when `datasets` was never imported before it: its `finally` pops the fake and
assigns it straight back. A later `task.load()` then gets `[]` (`'list' object has no attribute
'filter'`). It is present on 17's tree too (`caa33c0`). Only a partial run hits it, for example
`test_oracle_store.py` then `test_race.py`; the full suite imports `datasets` first. It is
outside this spec's AC8 list of edits, so it is not fixed here.

## Filed

- **L38** (owner 19): a selective or engine-aware tamper.
- **L39** (owner 19): ACs 10–12's purpose-built reproductions.
