# Spec 17 report — gate integrity

A diff's test infrastructure no longer reaches either grader. Two constructed force-pass
diffs reproduce the hole, one per runner. Each hookwrapper conftest rewrites every pytest
report to `passed` and changes nothing in the code under test. With the rule bypassed they
grade engine **PASS** and ground truth **`resolved`**. With the rule on they grade **FAIL** and
**`unresolved`**, check for check what `empty.diff` grades. A legitimate diff that adds a
fixture, a marker and a test module beside gold still grades gold's exact reason, plus the
suffix, and `resolved`. The rule costs **0 of 266** Python gold patches and **0 of 33** recorded
agent diffs.

## What the rule ignores on real diffs (AC13)

`uv run python scripts/gate_integrity_scan.py --root ~/Desktop/andromeda`. `--root` points at
the main checkout, because `runs/` and `coordination/` are per-clone and a worktree has none of
its own.

```
# ScaleAI/SWE-bench_Pro@7ab5114: 731 rows, 266 of them Python

## gold patch (source side), Python rows: 266 diffs
  instance_ansible__ansible-b2a289dcbb702003377221e25f62c8a3608f0e89-v173091e2e36d38c978002990795f66cfc0af30ad: setup.cfg (config)
  instance_internetarchive__openlibrary-7c8dc180281491ccaa1b4b43518506323750d1e4-v298a7a812ceed28c4c18355a091f1b268fe56d86: pyproject.toml (config)
  instance_internetarchive__openlibrary-c506c1b0b678892af5cb22c1c1dbc35d96787a0a-v0f5aece3601a5b4419f7ccec1dbda2071be28ee4: pyproject.toml (config)
  instance_qutebrowser__qutebrowser-233cb1cc48635130e5602549856a6fa4ab4c087f-v35616345bb8052ea303186706cec663146f0f184: pytest.ini (config)
  -> 4 touch test infrastructure; per kind: {'config': 4}

# recorded agent diffs: 1040 agent.diff files, 33 distinct non-empty
  tasks: 3 real (ansible__ansible-f327e65d11bb90, qutebrowser__qutebrowser-c580eb, qutebrowser__qutebrowser-f91ace), 6 from scripted draws
  edit an existing test module (L37, not this rule): 7

## recorded agent diffs: 33 diffs
  -> 0 touch test infrastructure; per kind: {}

## verdict fixtures: 17 diffs
  -> 0 touch test infrastructure; per kind: {}

| source | touch test infrastructure |
|---|---|
| gold | 4 |
| agents | 0 |
| fixtures | 0 |
```

Why ignoring each gold hit moves no check. The harness checks out the gold test files itself,
and the oracle writes against base, so neither can depend on these blocks:

| task | ignored | why no f2p/p2p check can move |
|---|---|---|
| `ansible-b2a289dc` | `setup.cfg` | a classifier and `python_requires`: packaging metadata that no pytest option reads |
| `openlibrary-7c8dc180` | `pyproject.toml` | ruff per-file ignores; no `[tool.pytest]` change |
| `openlibrary-c506c1b0` | `pyproject.toml` | ruff per-file ignores; no `[tool.pytest]` change |
| `qutebrowser-233cb1cc` | `pytest.ini` | a `js_headers` marker, used only by `tests/end2end/features/misc.feature`, which is not among its selected unit files |

The agent sample is small. The 33 distinct diffs span **three** real tasks (the three pinned
ones), and the rest are the suite's scripted draws. **7** of the 33 edit an existing test
module. This rule does not touch those; they are L37's.

`runner_paths` over all 1000 vendored `run_script.sh` files yields `bin/ansible-test` only
(49 ansible tasks). The first build read every `/app/<path>` the scripts *name*. That also
caught `OL_CONFIG=/app/conf/openlibrary.yml`, and misclassified two openlibrary gold patches
(`53e02a22`, `7cbfb812`) as `runner`. The rule now takes a path only where it is invoked as a
command word. With that change the scan reproduces the spec's measured 4.

## The real-container verdicts, both ways (ACs 9-11)

The pinned `@store` oracles. The engine is `CodingAdapter.gather_evidence`, and ground truth
is `ground_truth.grade` (the vendored harness). "Bypassed" means `infra.split_for`
monkeypatched to `Split(diff, "", [])`, so both graders see the whole diff: L32.

| AC | task | diff | rule | engine | reason | ground truth |
|---|---|---|---|---|---|---|
| 9 | qutebrowser-c580ebf0 | `force-pass.diff` | bypassed | **PASS** | all 19 valid hidden checks pass, no regressions | **`resolved`** |
| 9 | qutebrowser-c580ebf0 | `force-pass.diff` | on | **FAIL** | 19/19 valid hidden checks fail after change (over the 20% tolerance): …`test_disabled_blocking_still_respected_for_subdomains`; ignored changes to 1 test-infrastructure files: tests/unit/conftest.py | **`unresolved`** |
| 9 | qutebrowser-c580ebf0 | `empty.diff` | on | FAIL | 19/19 valid hidden checks fail after change … (the same checks, `(name, before, after)` equal) | — |
| 10 | ansible-f327e65d | `force-pass.diff` | bypassed | **PASS** | all 25 valid hidden checks pass, no regressions | **`resolved`** |
| 10 | ansible-f327e65d | `force-pass.diff` | on | **FAIL** | 25/25 valid hidden checks fail after change (over the 20% tolerance): …`test_validate_collection_name_rejects_python_keywords[assert.test]`; ignored changes to 1 test-infrastructure files: test/units/conftest.py | **`unresolved`** |
| 11 | qutebrowser-c580ebf0 | `gold-plus-infra.diff` | on | **PASS** | gold's exact reason (17/19 pass; 2 within tolerance; 1 existing check stale, forgiven by required-hunk: `test_disabled_blocking_per_url`) + "; ignored changes to 2 test-infrastructure files: pytest.ini" | **`resolved`** |

`gold.diff` alone was graded once in the build, on the same oracle, to pin that reason
(`GOLD_REASON` in the test). With the rule on, `ignored` is exactly
`["pytest.ini", "tests/helpers/fixtures.py"]`. The new test module is graded, not ignored.

**The exploit, fixed once (as AC9 says to).** The first `c580ebf0` force-pass edited the
tracked `tests/conftest.py`. With the rule bypassed, the harness graded it `resolved`: the hole
is real there. The engine, though, graded it `unverified`: `nearby_existing_tests` picks a
touched, tracked file under `tests/` as an existing check, and pytest collects nothing from a
conftest. So the engine's PASS arm did not reproduce. The fixture now adds a new
`tests/unit/conftest.py` over both hidden checks' directories, which reproduces PASS and
`resolved`. The ansible diff was `test/units/conftest.py` on the first try. Measured in the
image first, that conftest turned the oracle's two hidden files from 25 FAILED / 12 PASSED into
37 PASSED.

Mac Colima, `uv run pytest -q tests/test_gate_integrity.py -k real`: 5 passed in 3:14.

## Observation: spec 16's `checks` kind

Spec 16 landed while this was built. Its `ChecksAdapter` judges a free-text task by the
operator's sealed bash checks, not by pytest, so `infra.split_for` does not run on it. Both
SWE-bench graders, the only callers the spec names, do call it. What counts as
infrastructure for an operator's bash checks is spec 16's question, not a pytest rule. The
in-process channel this leaves open is the same one L36 names.

## The suite (AC12)

`scripts/suite` on the VM. Before: 37:39 at C22's merge (`009eca9`, 1520 passed, 9 skipped).
After: **1757 passed, 10 skipped in 4136.21s (1:08:56)**, on the merged tree (main with specs 14
and 16) on `a gate VM`, with runsc as dockerd's default (spec 14). ACs 9-11 pass there
under gVisor as they did on the Mac's runc. That is not comparable to 37:39 like for like: 14
moved every container onto runsc, and 14 and 16 added their own proofs, so the suite grew from
1520 to 1757 tests. Spec 16's merged-tree proof took 1:05:45; this spec's five real tests add
about three minutes of it.

The runs before that, each fixed in its own gap-check round:

- round 2: 4 failed, 1649 passed, 9 skipped in 2618.83s. `test_oracle_store`'s bare
  `diff --git` header was refused, and the scan's docstring named a grading field.
- round 3: 67 failed, 4 errors. Spec 14 had landed and made runsc the VM's default before this
  branch had 14's code. Fixed by merging main.
