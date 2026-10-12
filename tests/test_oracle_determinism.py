"""The payoff: the same patch judged twice by the same pinned oracle produces the same Check list.

This is the proof that pinning actually bought determinism. It cannot be faked with stubs --
the oracle is the real stored tree and both sides run in real containers (four of them: base
and base+diff, twice).

It is skipped, loudly, until the ansible oracle has been drawn and pinned by hand. That draw
needs Claude credentials and ~40 minutes; see the skip reason for the exact commands.
"""

import json
from pathlib import Path

import pytest

from andromeda.outcome.adapters.coding.oracle import STORE, load_oracle
from andromeda.outcome.adapters.coding.runner import run_checks
from andromeda.outcome.adapters.coding.task import SafeTask
from andromeda.outcome.core.types import Check, Evidence
from andromeda.outcome.core.verdict import decide

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "ansible-gold"
INSTANCE_ID = json.loads((FIXTURE / "task.json").read_text())["instance_id"]
ORACLE_DIR = STORE / INSTANCE_ID

# Duplicated from test_replay_equivalence.py on purpose: that test and its fixture are spec
# 00's proof and must stay untouched, so this file must not be able to change them by import.
EXISTING_FILES = [
    "test/units/utils/collection_loader/__init__.py",
    "test/units/utils/collection_loader/test_collection_loader.py",
]

pytestmark = pytest.mark.skipif(
    not (ORACLE_DIR / "manifest.json").is_file(),
    reason=(
        f"no pinned oracle at {ORACLE_DIR}. Draw and pin it first (needs ANTHROPIC_API_KEY or "
        f"CLAUDE_CODE_OAUTH_TOKEN, ~40 min):\n"
        f"  RUN=runs/_oracle/{INSTANCE_ID}/$(date -u +%Y%m%dT%H%M%SZ)\n"
        f"  uv run python -m andromeda.outcome.adapters.coding.test_writer {INSTANCE_ID} $RUN\n"
        f"  uv run python -m andromeda.outcome.adapters.coding.oracle pin {INSTANCE_ID} $RUN"
    ),
)


@pytest.fixture(scope="module")
def task() -> SafeTask:
    # From the fixture, not load_safe(): hermetic, no HuggingFace, no warm dataset cache.
    return SafeTask(**json.loads((FIXTURE / "task.json").read_text()))


@pytest.fixture(scope="module")
def gold_diff() -> str:
    return (FIXTURE / "gold.diff").read_text()


@pytest.fixture(scope="module")
def pinned(task) -> tuple[Path, list[str]]:
    return load_oracle(task, ORACLE_DIR)


def test_the_pinned_oracle_is_hidden_tests_only(pinned):
    """Guard: the run below is only meaningful if the store holds what it claims to."""
    root, rel = pinned
    assert rel, "pinned oracle has no files"
    assert all(Path(r).name.startswith("test_outcome_") for r in rel), rel
    assert all((root / r).is_file() for r in rel)
    assert not (root / "manifest.json").exists(), "the manifest must not ride into /app"


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_the_same_pinned_oracle_judges_the_same_patch_the_same_way(task, gold_diff, pinned, tmp_path):
    """Four real container runs: base and base+diff, twice over."""
    root, rel = pinned

    first = await run_checks(task, gold_diff, root, rel, EXISTING_FILES, tmp_path / "first")
    second = await run_checks(task, gold_diff, root, rel, EXISTING_FILES, tmp_path / "second")

    # both sides really executed: these logs are copied out of the containers
    for run in ("first", "second"):
        for label in ("before", "after"):
            stdout = tmp_path / run / f"{label}_stdout.log"
            assert stdout.is_file() and len(stdout.read_text()) > 1000, f"{run}/{label} produced no real output"

    def key(c: Check) -> str:
        return c.name

    assert sorted(first, key=key) == sorted(second, key=key)
    assert [decide(Evidence(checks=c))[0] for c in (first, second)] == ["PASS", "PASS"]
