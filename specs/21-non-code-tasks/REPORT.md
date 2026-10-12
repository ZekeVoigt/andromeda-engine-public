# Spec 21 report — non-code tasks

L41: a `checks` task was a coding task underneath. Since 21, a bundle with `"on": "inputs"`
carries an `inputs/` folder and names no SWE-bench instance. The inputs are laid down as `/app`
in one pinned image, `FOLDER_IMAGE`, at a base commit the host computes with no box. The
worker's result is a `--binary` diff against it, judged by sealed checks, built from six
engine-shipped starters. What follows is the record the SPEC's AC13 asks for.

## AC4: FOLDER_IMAGE's baseline

`python:3.12-bookworm@sha256:dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0`
(the index digest, read from the registry with `docker buildx imagetools inspect` and pinned in
`adapters/coding/task.py`). Probed bare -- no `install_claude`, no `furnish` -- with
`test_baseline.resolves()`, on the Mac's Colima (`tests/test_folder_tasks.py::test_real_ac4_…`):

| row | present |
|---|---|
| bash, git, python3, pip, gcc, g++, make, tar, gzip, patch, diff, grep, sed, awk, find, ssh, ps | yes (17 of 17) |
| claude | no -- `install_claude` puts it there, never an image |
| `/etc/ssl/certs/ca-certificates.crt` | yes |

`python3 --version` is 3.12.14, `git --version` 2.39.5. `/app` under `task_sandbox` exists and is
empty, so `lay_base` has nothing to refuse. `ASSERTED` is not shrunk: the image carries it all.

## AC9: the folder fixture graded for real

`outcome task new --ask "$(cat ask.txt)" --checks <a copy of fixtures/verifier/folder/checks>`,
the copy deleted, then `cli.run(task_id=T, patch_path=F)`, in real `FOLDER_IMAGE` boxes on the
Mac's Colima. The bundle is six `change` checks over every template and one `keep`
(`unchanged raw.csv`):

| diff | verdict | route | merge | what decided it |
|---|---|---|---|---|
| `gold.diff` | **PASS** | pass | `landed`, seq 1, T `done` | `all 6 valid hidden checks pass, no regressions`; every `change` check `before=fail after=pass`, `raw-unchanged` `pass/pass` |
| `bad.diff` | **FAIL** | fail | -- | `clean-no-blanks` and `clean-total` fail after (a blank email left in, the total 32.75, not 25.50): 2 of 6, over 20 %. `feedback()` names no `change` check |
| `keep-broken.diff` | **UNKNOWN** | `regressed` | `rejected`, kept as `conflicts/1.diff` byte-equal | `raw-unchanged` `pass -> fail`; `feedback()` names it and nothing hidden |

Ground truth is `none` in each, and `grade()` is never called. Before the gold after side's box
is torn down, `/app/out/chart.png` is byte-equal to `gold/out/chart.png` (the diff is a `GIT
binary patch`), and nothing under `/app` is a `checks.json`, an `outcome_templates` dir or a
`.py` file.

## AC10: a scripted live-path draw

`outcome run --task T` with no `--patch`, in-process: the real `run_agent`, `task_sandbox`,
`lay_base`, `isolate_repo`, `furnish` (with `claude` stubbed present), the primer, board sync,
C11's gate and `ChecksAdapter`. Only Claude is a script, which copies `gold/` into `/app`.

- **PASS**, `merge landed`, `landed_seq 1`, T `done`.
- `agent.diff` holds `diff --git a/out/chart.png b/out/chart.png` and a `GIT binary patch`.
- `tasks/T/head/1.cumulative.diff`, applied in a fresh box laid down through `lay_base`, rebuilds
  a tree whose files are byte-equal to `inputs/` plus `gold/`.
- The prompt, and `agent.prompt.txt`, begin with `FOLDER_PREAMBLE`.

## AC12: a live draw

Drawn by a real model, unattended, on a remote gate VM that held the model credential (the
laptop held none), from the folder fixture's ask and sealed checks. One draw, 244 s of wall
time, exit 0:

- **PASS**: `all 6 valid hidden checks pass, no regressions`; `merge landed`, `landed_seq 1`;
  ground truth `none`.

| check | kind | before | after |
|---|---|---|---|
| clean-parses | hidden | fail | pass |
| clean-no-blanks | hidden | fail | pass |
| clean-total | hidden | fail | pass |
| summary-shape | hidden | fail | pass |
| report-headings | hidden | fail | pass |
| report-is-text | hidden | fail | pass |
| raw-unchanged | existing | pass | pass |

- The draw: `total_cost_usd` **1.10** over 22 turns (213 s), 22 `broker_request`s, all 200.
- `agent.diff`'s files: `clean.py`, `minipng.py` (the worker's own scripts, left in /app, so part
  of its result), `out/chart.png` (a `GIT binary patch`), `out/clean.csv`, `out/report.md`,
  `out/summary.json`. `raw.csv` is untouched.
- `events.jsonl`'s `run_started`: `isolation = {mode: required, runtime: runsc, default_runtime:
  runsc, egress: enforced}`, `job = j-20260930T053738-c7b293`. Three direct connects to
  `api.anthropic.com:443` were `egress_denied`; the model was reached through the broker only.
- The prompt, `agent.prompt.txt`, begins with `FOLDER_PREAMBLE`.

This is L41's "draws and judges it for real": PASS, not UNKNOWN.

## AC11: nothing else moves

With the build complete, container-free (`-k "not real"`, on the Mac):

```
uv run pytest -q -n 4 tests/test_verifier.py tests/test_checks_control.py \
    tests/test_observed_regressions.py tests/test_task.py tests/test_boundaries.py \
    tests/test_agent_replay.py tests/test_sealed_checks.py tests/test_folder_tasks.py -k "not real"
```

All pass, the modules unmodified. `core/verdict.py`'s sha256 equals 20's landing tree's
(`tests/test_folder_tasks.py` pins it). The one edited test is `tests/test_cli_surface.py`,
whose two tests pin the exact subcommand set and gain `checks` (the SPEC's gap note).

## Decisions made in the build

The SPEC's gap note lists each one: git's owner-exec rule, the exec bit in the `inputs/`
manifest lines (slugify's refs unchanged), the `inputs/` check after `checks.json` parses,
`box()`'s shape, the CSV templates' reading of empty lines, `json_schema`'s `type`, the
templates' own path refusals, `add`'s ARGS after `--`, the edited `test_cli_surface.py`, and
AC12 through E09's host.

## The suite (AC14)

`scripts/suite` on a gate VM (runsc, `OUTCOME_ISOLATION=required`), on the landing tree: origin/main
`2285616` (spec 20 landed) + 21, at `7022bad`:

```
2442 passed, 10 skipped, 11 warnings in 2330.47s (0:38:50)
green: gate 2442 passed, 10 skipped as expected
```

- There is no new expected skip: `tests/expected_skips/` is unedited by 21. ACs 4, 9 and 10 use
  `FOLDER_IMAGE`, not a task image, so they are unmarked and ran here.
- It runs in tens of minutes, against 20's 0:37:51.
- It passed on its first run.
