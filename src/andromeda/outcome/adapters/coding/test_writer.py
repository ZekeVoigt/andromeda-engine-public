"""Container B: a separate Claude writes hidden tests from the request alone (no diff, no grading data).

Spec 19: a draw is sealed by default (ORACLE_FORM). The writer writes observers under /hidden
and the values a correct change produces under /expected; both come back to the run dir, and
only the observers ever enter a grading box. This box is the one box that holds expectations,
and it never holds a diff.
"""

from pathlib import Path
from typing import Literal

from andromeda.outcome.adapters.coding.seal import HELPER_MODULE, KEY, OBSERVATIONS
from andromeda.outcome.adapters.coding.task import SafeTask
from andromeda.runtime.sandbox import InfraError

HIDDEN_PREFIX = "test_outcome_"
# Where the oracle writes, inside its container; copied out once the draw ends (spec 14).
HIDDEN_DIR = "/hidden"
# Spec 19: where a sealed draw writes its expectations; copied out to run_dir/expected/.
EXPECTED_DIR = "/expected"
EXPECTED = "expected"
# A constant, not a flag (spec 03's reasoning): a live draw is sealed.
ORACLE_FORM: Literal["reported", "sealed"] = "sealed"

INSTRUCTIONS = f"""You are writing acceptance tests for a change that has NOT been implemented yet.
The repository at /app is the unchanged code. Do not modify anything under /app.

Write test files that pass only once the change described below is correctly implemented.
- Put them under /hidden, mirroring the repository's own test layout, e.g. if the repo keeps
  tests in /app/tests/unit/foo/ write /hidden/tests/unit/foo/{HIDDEN_PREFIX}<topic>.py
- Every file name must start with `{HIDDEN_PREFIX}`.
- Use the repository's existing test framework and conventions.
- Test the behavior described, through the interfaces described; do not test implementation details.
- You may run tests against /app to check they fail today for the right reason (missing behavior,
  not a syntax or import mistake in your test beyond the missing interface).
Work autonomously; do not ask questions.

--- CHANGE REQUEST ---
"""


# Every rule of INSTRUCTIONS, word for word, then the seal's. INSTRUCTIONS' bytes never change:
# every reported oracle's manifest hashes build_prompt(task, "reported").
SEALED_INSTRUCTIONS = INSTRUCTIONS.split("Work autonomously;")[0] + f"""
These tests are SEALED: they observe values, and the values a correct change produces are kept
apart from them, where the code under test can never read them.
- Each test module is an observer. It does `from {HELPER_MODULE} import observe, observe_call`
  (already importable, do not write it), drives the code under test, and calls
  `observe("<key>", value)` with the value the code produced.
- Never assert the requested behavior: whether an observer module passes or fails is ignored.
  Use `observe_call("<key>", fn, *args, **kwargs)` where the change should raise; it records
  {{"returned": <value>}} or {{"raised": "<exception class name>"}}.
- For each observer, write the expected values under {EXPECTED_DIR} at the same relative path with
  a `.json` suffix: {HIDDEN_DIR}/tests/unit/foo/{HIDDEN_PREFIX}<topic>.py gets
  {EXPECTED_DIR}/tests/unit/foo/{HIDDEN_PREFIX}<topic>.json, one JSON object mapping each key to
  the value a correct change produces. Every observer needs its file, and every key in it must
  be observed when the tests run.
- A key matches ^{KEY.pattern}$ and is unique across all your
  files. A key built at run time, such as f"widened/{{hostname}}", is fine.
- NEVER write an expected value anywhere under {HIDDEN_DIR}: not in code, a comment, a docstring,
  a parametrize id, or a key. The code under test must be what produces it. A value that has to
  appear as an input too (a string the change must leave as it is) counts: observe something
  composite instead, such as the list of results for several inputs.
- Values are JSON data, so convert with list(), str(), sorted() or a type's name, and round
  floats. They are compared exactly as canonical JSON: 1 is not 1.0, and true is not 1.
- Prefer one composite observation that mixes inputs the change affects with inputs it must
  leave alone (for example, the list of blocked flags for eight hosts) over one boolean per check.
- Observations are appended to {OBSERVATIONS}. You may delete
  it, run your observers against /app, and read it to see what the unchanged code produces.
Work autonomously; do not ask questions.

--- CHANGE REQUEST ---
"""


def build_prompt(task: SafeTask, form: Literal["reported", "sealed"] = ORACLE_FORM) -> str:
    """Each form hashes its own prompt; "reported" is today's bytes, unchanged (spec 19)."""
    return (INSTRUCTIONS if form == "reported" else SEALED_INSTRUCTIONS) + task.request_text()


async def write_hidden_tests(task: SafeTask, run_dir: str | Path, *,
                             route: object | None = None,
                             form: Literal["reported", "sealed"] = ORACLE_FORM) -> list[Path]:
    """Draw the oracle's hidden tests: a separate model, in its own box with no diff and no
    grading data, given `build_prompt(task, form)` and nothing else.

    The public engine grades with a supplied oracle only. A live draw needs a model credential
    held outside the box (a broker) and a per-role egress policy, and neither is in this
    repository; the recorded oracles under fixtures/ were drawn with exactly the prompt above.
    """
    raise InfraError(f"no oracle for {task.instance_id}: this engine grades with a supplied "
                     "oracle (--oracle DIR); drawing one live is not part of the public engine")
