"""Pure logic that decides which checks count, which tests are 'nearby', and agreement."""

from datetime import datetime, timezone

import pytest

from andromeda.outcome.adapters.coding.adapter import is_test_file, nearby_existing_tests, touched_files
from andromeda.outcome.adapters.coding.docker import InfraError
from andromeda.outcome.adapters.coding.runner import removed_files, to_checks
from andromeda.outcome.cli import agreement, summary_line
from andromeda.outcome.core.types import Evidence, OutcomeRecord, Request
from andromeda.outcome.core.verdict import decide

HIDDEN = "tests/unit/utils/test_outcome_x.py"
EXISTING = "tests/unit/utils/test_log.py"
FILES = [HIDDEN, EXISTING]


def by_key(checks):
    return {c.name: (c.kind, c.before, c.after) for c in checks}


# --- to_checks -------------------------------------------------------------

def test_hidden_test_missing_on_base_counts_as_fail():
    # typical: the hidden test imports an interface that does not exist on base
    checks = to_checks({f"{EXISTING}::t": "pass"}, {f"{HIDDEN}::a": "pass", f"{EXISTING}::t": "pass"}, FILES, [HIDDEN])
    assert by_key(checks)[f"{HIDDEN}::a"] == ("hidden", "fail", "pass")


def test_existing_test_moved_away_by_diff_is_not_a_regression():
    before = {f"{EXISTING}::moved": "pass", f"{EXISTING}::kept": "pass"}
    after = {f"{EXISTING}::kept": "pass"}
    assert by_key(to_checks(before, after, FILES, [HIDDEN])) == {f"{EXISTING}::kept": ("existing", "pass", "pass")}


def test_existing_results_on_one_side_only_is_infra_error():
    # reviewer finding 1: a collection abort on base would otherwise silently drop all regression checks
    before = {}
    after = {f"{HIDDEN}::a": "pass", f"{EXISTING}::broken": "fail"}
    with pytest.raises(InfraError, match="existing tests produced no results on base"):
        to_checks(before, after, FILES, [HIDDEN])


def test_no_results_anywhere_is_infra_error():
    with pytest.raises(InfraError):
        to_checks({}, {}, FILES, [HIDDEN])


def test_results_for_unrequested_files_are_ignored():
    before = {f"{EXISTING}::t": "pass", "tests/other/test_z.py::t": "fail"}
    after = {f"{EXISTING}::t": "pass", "tests/other/test_z.py::t": "pass"}
    assert set(by_key(to_checks(before, after, FILES, [HIDDEN]))) == {f"{EXISTING}::t"}


def test_only_hidden_files_requested_needs_no_existing_results():
    checks = to_checks({}, {f"{HIDDEN}::a": "pass"}, [HIDDEN], [HIDDEN])
    assert by_key(checks) == {f"{HIDDEN}::a": ("hidden", "fail", "pass")}


QTLOG = "tests/unit/utils/test_qtlog.py"
THREE = [HIDDEN, EXISTING, QTLOG]


def test_existing_file_silent_after_change_counts_as_regression():
    # round-2 finding 1: test_log.py fails to import after the diff, so the engine must not say PASS
    before = {f"{HIDDEN}::a": "fail", f"{EXISTING}::t1": "pass", f"{EXISTING}::t2": "pass", f"{QTLOG}::q": "pass"}
    after = {f"{HIDDEN}::a": "pass", f"{QTLOG}::q": "pass"}
    checks = to_checks(before, after, THREE, [HIDDEN])
    assert by_key(checks)[f"{EXISTING}::t1"] == ("existing", "pass", "error")
    assert decide(Evidence(checks=checks))[0] == "UNKNOWN"


def test_existing_file_deleted_by_diff_is_not_a_regression():
    before = {f"{HIDDEN}::a": "fail", f"{EXISTING}::t1": "pass", f"{QTLOG}::q": "pass"}
    after = {f"{HIDDEN}::a": "pass", f"{QTLOG}::q": "pass"}
    checks = to_checks(before, after, THREE, [HIDDEN], removed={EXISTING})
    assert f"{EXISTING}::t1" not in by_key(checks)
    assert decide(Evidence(checks=checks))[0] == "PASS"


def test_non_pytest_names_are_an_honest_infra_error():
    # round-2 finding 2: e.g. Go parsers emit bare "TestFoo"
    with pytest.raises(InfraError, match="do not map"):
        to_checks({}, {"TestFoo": "pass"}, ["pkg/foo_test.go"], ["pkg/foo_test.go"])


def test_removed_files_reads_delete_and_rename_headers():
    diff = (
        "diff --git a/tests/a.py b/tests/a.py\ndeleted file mode 100644\nindex 1..0\n--- a/tests/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        "diff --git a/tests/b.py b/tests/c.py\nsimilarity index 90%\nrename from tests/b.py\nrename to tests/c.py\n"
        "diff --git a/tests/d.py b/tests/d.py\nindex 1..2\n--- a/tests/d.py\n+++ b/tests/d.py\n@@ -1 +1 @@\n-deleted file mode\n+y\n"
    )
    assert removed_files(diff) == {"tests/a.py", "tests/b.py"}


# --- nearby existing tests ----------------------------------------------------

DIFF = """diff --git a/qutebrowser/utils/log.py b/qutebrowser/utils/log.py
--- a/qutebrowser/utils/log.py
+++ b/qutebrowser/utils/log.py
diff --git a/scripts/dev/run_vulture.py b/scripts/dev/run_vulture.py
"""

TRACKED = [
    "qutebrowser/utils/log.py",
    "tests/unit/utils/test_log.py",
    "tests/unit/utils/test_qtlog.py",
    "tests/unit/utils/test_urlutils.py",
    "scripts/dev/quit_segfault_test.sh",
    "scripts/dev/run_vulture.py",
    "tests/unit/browser/test_hints.py",
]


def test_touched_files_reads_diff_headers():
    assert touched_files(DIFF) == ["qutebrowser/utils/log.py", "scripts/dev/run_vulture.py"]


def test_is_test_file():
    assert is_test_file("tests/unit/utils/test_log.py")
    assert is_test_file("pkg/foo_test.go")
    assert not is_test_file("qutebrowser/utils/log.py")


def test_nearby_prefers_name_match_and_same_language():
    picked = nearby_existing_tests(touched_files(DIFF), TRACKED)
    # log.py -> name-matched test_log.py and test_qtlog.py; the .sh "test" next to run_vulture.py is excluded
    assert picked == ["tests/unit/utils/test_log.py", "tests/unit/utils/test_qtlog.py"]


def test_name_matched_tests_survive_the_cap():
    # round-3 finding 2: 25 unmatched tests next to a/x.py must not push out z/test_log.py
    tracked = [f"a/test_{i:02d}.py" for i in range(25)] + ["a/x.py", "z/log.py", "z/test_log.py"]
    picked = nearby_existing_tests(["a/x.py", "z/log.py"], tracked)
    assert len(picked) == 20
    assert picked[0] == "z/test_log.py"


# --- agreement + summary (AC3, AC9) -------------------------------------------

@pytest.mark.parametrize("verdict,truth,expected", [
    ("PASS", "resolved", True),
    ("PASS", "unresolved", False),
    ("FAIL", "unresolved", True),
    ("FAIL", "resolved", False),
    ("UNKNOWN", "resolved", None),
    ("UNKNOWN", "unresolved", None),
    ("PASS", "error", None),
    ("FAIL", "error", None),
])
def test_agreement_truth_table(verdict, truth, expected):
    assert agreement(verdict, truth) is expected


@pytest.mark.parametrize("agrees,shown", [(True, "true"), (False, "false"), (None, "n/a")])
def test_summary_line_format(agrees, shown):
    now = datetime.now(timezone.utc)
    record = OutcomeRecord(request=Request(task_id="inst_1", text=""), verdict="FAIL", reason="r",
                           evidence=Evidence(), ground_truth="unresolved", agrees=agrees,
                           started_at=now, finished_at=now)
    assert summary_line(record) == f"inst_1  engine=FAIL  truth=unresolved  agrees={shown}"
