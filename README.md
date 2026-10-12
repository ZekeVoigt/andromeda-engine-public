# outcome-engine

**Did the coding agent actually do what it was asked?** This engine answers that for one diff,
with a verdict you can audit: `PASS`, `FAIL`, or `UNKNOWN` (it will not guess).

It does not trust the agent, the agent's tests, or anything the agent's code prints. It runs
checks the agent never saw, in a sandbox, before and after the change, and judges the results
on the host.

```
$ outcome check fixtures/verifier/folder/checks --patch fixtures/verifier/folder/gold.diff
runs/folder/20261012T031006861791Z
  all 6 valid hidden checks pass, no regressions
folder  engine=PASS  truth=none  agrees=n/a

$ outcome check fixtures/verifier/folder/checks --patch fixtures/verifier/folder/bad.diff
  2/6 valid hidden checks fail after change (over the 20% tolerance): clean-no-blanks
folder  engine=FAIL  truth=none  agrees=n/a

$ outcome check fixtures/verifier/folder/checks --patch fixtures/verifier/folder/keep-broken.diff
  6/6 valid hidden checks pass but 1 existing checks regressed: raw-unchanged
folder  engine=UNKNOWN  truth=none  agrees=n/a
```

The third diff does the job and quietly edits a file it was told not to touch. Passing the
new checks is not enough.

## The method

A verdict comes from four kinds of evidence, combined by one small pure function
([`core/verdict.py`](src/andromeda/outcome/core/verdict.py), `decide()`):

| evidence | what it is | why it can't be gamed cheaply |
|---|---|---|
| **hidden checks** | tests of the requested behaviour, written from the request alone, never shown to the agent | the agent can't target what it can't see |
| **existing checks** | the repo's own tests, run before and after | a change that breaks working behaviour is a regression, never a PASS |
| **canaries** | planted checks that *must* fail | if one passes, something forced results; the verdict is FAIL "tampered" |
| **sealed values** | the expected outputs of hidden checks, kept on the host | the box only reports what it observed; the comparison never happens where the diff's code runs |

And three rules about what reaches the graders:

- **A diff's test infrastructure is ignored** (`conftest.py`, pytest config, test helpers). A
  diff that just rewrites every test report to "passed" grades exactly like an empty diff.
  ([`adapters/coding/infra.py`](src/andromeda/outcome/adapters/coding/infra.py))
- **Edits to existing test modules are set aside**, so the existing checks run as they were.
- **A test the change legitimately obsoletes is not a regression**: it is forgiven only when
  it references a symbol the diff deletes, or fails only because of a hunk the change needs.

Sandboxes have no network and no capabilities, and on an isolated host they run under gVisor
([`runtime/isolation.py`](src/andromeda/runtime/isolation.py)).

## Two kinds of task

- **`grade`: a SWE-bench Pro instance**, judged by a pinned oracle (a directory of hidden
  tests, sealed or not). The benchmark's own harness grades the same diff beside it, so every
  verdict is shown next to an independent `truth`, and `agrees=` says whether they match.
- **`check`: anything you can write checks for**: a bundle of shell checks (`checks.json`)
  run before and after, on a SWE-bench repo or on a plain folder of inputs (a spreadsheet to
  clean, a report to write). Six starter templates: `outcome checks templates`.

## Evidence

Everything below is reproducible from this repository; each line names its proof.

| claim | result | reproduce |
|---|---|---|
| Verdicts agree with SWE-bench Pro's own harness | **36/36** cells (3 tasks × 6 independently drawn oracles × gold/half patches) | `pytest tests/test_verdict_corpus.py` (no Docker) · [report](specs/13-obsolescence-precision/REPORT.md) |
| Oracle draw luck doesn't move the verdict | every (task, patch) row is **6-0-0** across 6 draws, though draws range from 7 to 80 valid checks | `pytest tests/test_oracle_variance.py` · [report](specs/02-oracle-variance/REPORT.md) |
| How good model-written hidden tests are | of 540 hidden tests, **54%** discriminate; 29% vacuous, 2% wrong, 15% blunt | `pytest tests/test_oracle_quality.py` · [report](specs/04-oracle-quality/REPORT.md) |
| A test-report force-pass is caught | grades **PASS** with the rule off, **FAIL** with it on, on two runners | `pytest tests/test_gate_integrity.py -m task_image` · [report](specs/17-gate-integrity/REPORT.md) |
| The infrastructure rule costs honest work nothing | **0 of 266** Python gold patches, **0 of 33** recorded agent diffs | [report](specs/17-gate-integrity/REPORT.md) |
| Sealed oracles hold even with every other defence off | force-pass **FAIL** on a sealed oracle; gold **PASS** on all 3; no expected value found in any box | `pytest tests/test_sealed_checks.py` · [report](specs/19-tamper-resistant-checks/REPORT.md) |
| Unselected sealed draws | across 18 cells, gold never FAIL, half never PASS | [report](specs/19-tamper-resistant-checks/REPORT.md) · `fixtures/sealed-variance/` |
| Non-code tasks | the folder task grades gold/bad/keep-broken PASS/FAIL/UNKNOWN; a real model's draw graded PASS | the demo above · [report](specs/21-non-code-tasks/REPORT.md) |

### What this does *not* show yet

Read these before you trust the numbers above.

- **Breadth is three tasks.** The depth is real: adversarial diffs, sealed oracles, variance
  across draws. But it's 3 SWE-bench Pro instances (one ansible, two qutebrowser).
- **The 36/36 is not a held-out result.** The obsolescence rules were developed against that
  same corpus, which started at 24/36 ([03](specs/03-verdict-rules/REPORT.md),
  [13](specs/13-obsolescence-precision/REPORT.md)). Agreement on unseen tasks is the open
  question, and the most useful thing anyone could measure next.
- **Hidden tests are drawn by a model.** This repository grades with *recorded* oracles; it
  does not draw new ones (that needs a model credential held outside the sandbox, which isn't
  part of the public engine). The prompt that drew them is in
  [`test_writer.py`](src/andromeda/outcome/adapters/coding/test_writer.py), and every oracle's
  `manifest.json` hashes it.
- **Python only.** The infrastructure rule refuses other languages (`UNKNOWN`, not a guess).
- **Known open channels** are written down, with the argument for why each can't be sealed:
  [20's report](specs/20-observed-regressions/REPORT.md).

## Run it

Needs Python ≥ 3.12, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
uv run outcome check fixtures/verifier/folder/checks --patch fixtures/verifier/folder/gold.diff

uv run pytest -q -m "not task_image"     # ~630 tests, about 2 minutes, small images only
```

The SWE-bench Pro path needs the pinned benchmark tree and its task images (1.6–2.3 GB each,
`linux/amd64`):

```bash
uv run outcome bootstrap                 # the pinned SWE-bench Pro tree + dataset revision
I=instance_ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5
uv run outcome grade $I --patch fixtures/bad_patches/$I/gold.diff --oracle fixtures/sealed-oracles/$I

uv run pytest -q                         # everything, including the real-image proofs
```

Every grading leaves `runs/<task>/<timestamp>/` holding the judged diff, each side's raw logs,
`checks.json` (every check, before and after) and `record.json`. A verdict is never just a
word; you can always see why.

Set `OUTCOME_ISOLATION=required` to refuse to run anywhere gVisor isn't the Docker runtime.

## Layout

```
src/andromeda/
  outcome/core/        verdict rules (pure), the engine, types
  outcome/adapters/    coding/: SWE-bench Pro grading; checks/: check bundles and folder tasks
  outcome/grading/     SWE-bench Pro's own harness as ground truth; oracle quality
  runtime/             the Docker sandbox and its isolation posture
fixtures/              pinned oracles, recorded diffs, the verdict corpus, measurement results
specs/*/REPORT.md      each measurement: what was run, what came out, what's left
scripts/               the measurement scripts the reports came from
```

## License

MIT
