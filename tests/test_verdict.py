from pathlib import Path

from andromeda.outcome.adapters.coding.adapter import mark_obsoleted, removed_symbols
from andromeda.outcome.core.engine import verify
from andromeda.outcome.core.types import Check, Evidence, Request
from andromeda.outcome.core.verdict import decide


def hidden(before, after, name="h"):
    return Check(name=name, kind="hidden", before=before, after=after)


def existing(before, after, name="e"):
    return Check(name=name, kind="existing", before=before, after=after)


def test_no_valid_hidden_checks_is_unknown():
    assert decide(Evidence(checks=[existing("pass", "pass")]))[0] == "UNKNOWN"


def test_infra_error_is_unknown():
    ev = Evidence(checks=[hidden("fail", "pass")], infra_errors=["docker died"])
    assert decide(ev)[0] == "UNKNOWN"


def test_hidden_pass_plus_regression_is_unknown():
    ev = Evidence(checks=[hidden("fail", "pass"), existing("pass", "fail")])
    assert decide(ev)[0] == "UNKNOWN"


def test_any_valid_hidden_fail_is_fail():
    ev = Evidence(checks=[hidden("fail", "pass", "a"), hidden("fail", "error", "b")])
    assert decide(ev)[0] == "FAIL"


def test_all_pass_no_regression_is_pass():
    ev = Evidence(checks=[hidden("fail", "pass"), existing("pass", "pass"), existing("fail", "fail")])
    assert decide(ev)[0] == "PASS"


def test_hidden_checks_passing_on_base_are_excluded():
    # passes-on-base hidden check that fails after must not produce FAIL; it is simply ignored
    only_base_passing = Evidence(checks=[hidden("pass", "fail")])
    assert decide(only_base_passing)[0] == "UNKNOWN"
    mixed = Evidence(checks=[hidden("pass", "fail", "ignored"), hidden("fail", "pass", "real")])
    assert decide(mixed)[0] == "PASS"


def test_hidden_fail_wins_over_regression():
    ev = Evidence(checks=[hidden("fail", "fail"), existing("pass", "fail")])
    assert decide(ev)[0] == "FAIL"


def test_hidden_checks_erroring_on_base_are_excluded():
    assert decide(Evidence(checks=[hidden("error", "pass")]))[0] == "UNKNOWN"


async def test_engine_turns_adapter_exception_into_unknown():
    class Boom:
        async def gather_evidence(self, request, artifact):
            raise RuntimeError("no docker")

    verdict, reason, evidence = await verify(Request(task_id="t", text="x"), "diff", Boom())
    assert verdict == "UNKNOWN" and "no docker" in reason and evidence.infra_errors


# --- spec 03: bounded hidden-fail tolerance -------------------------------------------

def test_tolerance_boundary_is_at_or_below_twenty_percent():
    """AC3: 3 of 15 is PASS (exactly 20%), 4 of 15 is FAIL. Pins `>` rather than `>=`."""
    def cell(n_failing, n_valid=15):
        checks = [hidden("fail", "fail", f"f{i}") for i in range(n_failing)]
        checks += [hidden("fail", "pass", f"p{i}") for i in range(n_valid - n_failing)]
        return Evidence(checks=checks)

    assert decide(cell(3))[0] == "PASS"
    assert decide(cell(4))[0] == "FAIL"


def test_tolerance_is_a_proportion_not_a_count():
    # 1 of 4 is 25% -- over tolerance, even though 3 of 15 (20%) was forgiven above
    ev = Evidence(checks=[hidden("fail", "fail", "a")] + [hidden("fail", "pass", f"p{i}") for i in range(3)])
    assert decide(ev)[0] == "FAIL"


def test_forgiven_hidden_failure_is_named_in_the_pass_reason():
    checks = [hidden("fail", "fail", "flaky_oracle_test")]
    checks += [hidden("fail", "pass", f"p{i}") for i in range(14)]
    verdict, reason = decide(Evidence(checks=checks))
    assert verdict == "PASS"
    assert "flaky_oracle_test" in reason, f"forgiven failure vanished from the reason: {reason}"


def test_obsoleted_existing_check_is_not_a_regression():
    """AC4: the same broken existing check is UNKNOWN unmarked, PASS once obsoleted."""
    broken = existing("pass", "fail", "tests/test_log.py::test_hide_qt_warning")
    assert decide(Evidence(checks=[hidden("fail", "pass"), broken]))[0] == "UNKNOWN"

    obsoleted = broken.model_copy(update={"obsoleted": True})
    verdict, reason = decide(Evidence(checks=[hidden("fail", "pass"), obsoleted]))
    assert verdict == "PASS", reason


def test_obsoleted_does_not_forgive_a_live_regression():
    # one marked, one not: the unmarked one still produces UNKNOWN
    ev = Evidence(checks=[
        hidden("fail", "pass"),
        existing("pass", "fail", "old.py::gone").model_copy(update={"obsoleted": True}),
        existing("pass", "fail", "live.py::broke"),
    ])
    verdict, reason = decide(ev)
    assert verdict == "UNKNOWN" and "live.py::broke" in reason


def test_hidden_fail_over_tolerance_still_wins_over_obsoleted():
    ev = Evidence(checks=[hidden("fail", "fail"),
                          existing("pass", "fail").model_copy(update={"obsoleted": True})])
    assert decide(ev)[0] == "FAIL"


def test_tolerance_constant_is_the_only_number():
    from andromeda.outcome.core import verdict as verdict_module
    assert verdict_module.HIDDEN_FAIL_TOLERANCE == 0.20


# --- spec 03: obsolescence detection --------------------------------------------------

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
F91ACE96 = ("instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111"
            "-v059c6fdc75567943479b23ebca7c07b5e9a7f34c")


def test_removed_symbols_finds_a_relocated_function_and_class():
    """AC5: the f91ace96 gold patch moves both out of log.py and into qtlog.py."""
    diff = (FIXTURES / "bad_patches" / F91ACE96 / "gold.diff").read_text()
    assert removed_symbols(diff) == {"hide_qt_warning", "QtWarningFilter"}


def test_removed_symbols_ignores_a_signature_edited_in_place():
    """AC5: removed and re-added in the same file is an edit, not a deletion."""
    diff = """diff --git a/pkg/mod.py b/pkg/mod.py
index 1111111..2222222 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,3 +1,3 @@
-def widen(hostname):
-    return hostname
+def widen(hostname, *, strict=False):
+    return hostname if not strict else hostname.strip()
"""
    assert removed_symbols(diff) == set()


def test_removed_symbols_skips_test_files():
    # a deleted test helper is not a source symbol; marking on it would be circular
    diff = """diff --git a/tests/test_mod.py b/tests/test_mod.py
--- a/tests/test_mod.py
+++ b/tests/test_mod.py
@@ -1,2 +1,1 @@
-def helper():
-    pass
"""
    assert removed_symbols(diff) == set()


def test_removed_symbols_ignores_nested_defs():
    # __init__/filter move with their class; whole-word matching them would mark everything
    diff = """diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,6 +1,1 @@
-class Thing:
-    def __init__(self):
-        pass
-    def filter(self, record):
-        return True
"""
    assert removed_symbols(diff) == {"Thing"}


def test_mark_obsoleted_marks_only_files_referencing_a_removed_symbol():
    diff = (FIXTURES / "bad_patches" / F91ACE96 / "gold.diff").read_text()
    sources = {
        "tests/unit/utils/test_log.py": "def test_hide(caplog):\n    with log.hide_qt_warning('x'):\n        pass\n",
        "tests/unit/utils/test_urlutils.py": "def test_url():\n    assert True\n",
    }
    checks = [
        existing("pass", "fail", "tests/unit/utils/test_log.py::test_hide"),
        existing("pass", "fail", "tests/unit/utils/test_urlutils.py::test_url"),
        hidden("fail", "pass", "tests/unit/utils/test_log.py::test_outcome_new"),
    ]
    marked = {c.name: c.obsoleted for c in mark_obsoleted(checks, diff, sources)}
    assert marked["tests/unit/utils/test_log.py::test_hide"] is True
    assert marked["tests/unit/utils/test_urlutils.py::test_url"] is False
    # a hidden check is never obsoleted, whatever file it lives in
    assert marked["tests/unit/utils/test_log.py::test_outcome_new"] is False


def test_mark_obsoleted_requires_a_whole_word_match():
    diff = (FIXTURES / "bad_patches" / F91ACE96 / "gold.diff").read_text()
    sources = {"tests/t.py": "def test_x():\n    my_hide_qt_warning_helper()\n"}
    assert mark_obsoleted([existing("pass", "fail", "tests/t.py::test_x")], diff, sources)[0].obsoleted is False


def test_mark_obsoleted_is_a_noop_without_removals_and_never_mutates():
    diff = "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    original = [existing("pass", "fail", "tests/t.py::test_x")]
    out = mark_obsoleted(original, diff, {"tests/t.py": "hide_qt_warning"})
    assert [c.obsoleted for c in out] == [False] and original[0].obsoleted is False


def test_obsoleted_survives_a_json_round_trip():
    """AC1: the field serializes, and a checks.json written before it still loads."""
    marked = existing("pass", "fail").model_copy(update={"obsoleted": True})
    assert Check.model_validate_json(marked.model_dump_json()).obsoleted is True
    assert Check.model_validate_json('{"name":"e","kind":"existing","before":"pass","after":"pass"}').obsoleted is False
