"""Replay a recorded oracle through the rewired container layer and demand the same Evidence.

The naive proof -- `outcome run <task> --patch gold.diff` -> PASS -- cannot work here.
gather_evidence calls write_hidden_tests on every verification, so a re-run draws a brand
new oracle, and a FAIL would be indistinguishable between "the rewire broke the container
layer" and "this draw was worse". That measures oracle variance, which is a later spec's
job. So the hidden tests, the diff and the task are all replayed from a recorded run and
only the container layer is re-executed.

This is the slow test: it runs a real SWE-bench Pro task image twice, base then base+diff.
It is seconds rather than minutes only because the official run script takes a file list and
_run_side passes just the 4 selected files.
"""

import json
from pathlib import Path

import pytest

from andromeda.outcome.adapters.coding.adapter import CodingAdapter, nearby_existing_tests, touched_files
from andromeda.outcome.adapters.coding.runner import run_checks
from andromeda.outcome.adapters.coding.task import SafeTask
from andromeda.outcome.core.types import Check, Evidence
from andromeda.outcome.core.verdict import decide

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "ansible-gold"

# The existing test files the recorded run selected, read back off that run's
# checks_logs/before_stdout.log. Pinned here so the assertion has a fixed target instead of
# echoing whatever the code returns today.
#
# __init__.py is in the list because is_test_file() counts any .py under a `test/` path, and
# it contributes no checks (it holds no tests). Only test_collection_loader.py shows up in
# checks.json, with 57 existing checks -- which is why the plan's prose named just the one.
EXISTING_FILES = [
    "test/units/utils/collection_loader/__init__.py",
    "test/units/utils/collection_loader/test_collection_loader.py",
]
CHECK_BEARING_EXISTING_FILE = "test/units/utils/collection_loader/test_collection_loader.py"

RECORDED_SHAPE = {
    "total": 115,
    ("hidden", "fail", "pass"): 36,    # the valid ones decide() actually counts
    ("hidden", "pass", "pass"): 22,    # filtered out by decide(): no signal
    ("existing", "pass", "pass"): 57,  # no regressions
}


@pytest.fixture(scope="module")
def task() -> SafeTask:
    # Built from the fixture, not load_safe(): load_safe hits HuggingFace, and the proof
    # has to be hermetic -- no network, no warm dataset cache.
    return SafeTask(**json.loads((FIXTURE / "task.json").read_text()))


@pytest.fixture(scope="module")
def gold_diff() -> str:
    return (FIXTURE / "gold.diff").read_text()


@pytest.fixture(scope="module")
def recorded() -> Evidence:
    return Evidence.model_validate_json((FIXTURE / "checks.json").read_text())


@pytest.fixture(scope="module")
def hidden_rel() -> list[str]:
    root = FIXTURE / "hidden_tests"
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py"))


def test_the_fixture_is_the_recorded_run(recorded, hidden_rel):
    """Guard: every later assertion compares against this file, so pin its shape.

    A truncated or re-recorded checks.json would make the replay pass against the wrong
    target without anything else noticing.
    """
    assert len(hidden_rel) == 2, hidden_rel
    assert len(recorded.checks) == RECORDED_SHAPE["total"]
    assert recorded.infra_errors == []
    counts = {}
    for c in recorded.checks:
        counts[(c.kind, c.before, c.after)] = counts.get((c.kind, c.before, c.after), 0) + 1
    assert counts == {k: v for k, v in RECORDED_SHAPE.items() if k != "total"}
    assert all(Path(p).name.startswith("test_outcome_") for p in hidden_rel)
    # every existing check in the recording comes from the one file that actually holds tests
    assert {c.name.split("::", 1)[0] for c in recorded.checks if c.kind == "existing"} == \
        {CHECK_BEARING_EXISTING_FILE}


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_tracked_files_selects_the_recorded_existing_test(task, gold_diff):
    """Exercises the large-output path for real.

    `git ls-files` on ansible is thousands of lines. Under agent-world's 8KB cap this
    silently returns a truncated file list, nearby_existing_tests picks a different set,
    and the regression half of the evidence is quietly wrong with no error anywhere.
    Deterministic because the base commit is fixed.
    """
    tracked = await CodingAdapter(FIXTURE)._tracked_files(task)

    assert len(tracked) > 5000, f"only {len(tracked)} tracked files -- output was truncated"
    assert "[truncated]" not in tracked[-1]

    assert nearby_existing_tests(touched_files(gold_diff), tracked) == EXISTING_FILES


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_replayed_checks_match_the_recording_and_still_decide_pass(
        task, gold_diff, recorded, hidden_rel, tmp_path):
    """One run_checks, both assertions: two real container runs, base and base+diff."""
    log_dir = tmp_path / "checks_logs"
    checks = await run_checks(task, gold_diff, FIXTURE / "hidden_tests", hidden_rel,
                              EXISTING_FILES, log_dir)

    # Both sides really executed in a container: these logs are copied out of it, and this
    # test is worthless if it ever starts passing without them.
    for label in ("before", "after"):
        stdout = log_dir / f"{label}_stdout.log"
        assert stdout.is_file() and len(stdout.read_text()) > 1000, f"{label} side produced no real output"

    def key(c: Check) -> str:
        return c.name

    assert sorted(checks, key=key) == sorted(recorded.checks, key=key)

    verdict, reason = decide(Evidence(checks=checks))
    assert verdict == "PASS", reason
