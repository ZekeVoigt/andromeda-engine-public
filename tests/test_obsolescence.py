"""Spec 13's proof: per-test obsolescence, required-hunk attribution and its guard.

The Docker-free part covers ACs 1-8: the field, per-test scope on the committed corpus and on
one constructed case per scope rule, `hunks()` / `without()`, `mark_stale()`'s rule, and the
PASS reason. The `real` tests (ACs 11-13) grade in the task images.
"""

import asyncio
import inspect
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from andromeda.outcome.adapters.coding import adapter as adapter_mod
from andromeda.outcome.adapters.coding.adapter import (
    MAX_ABLATION_HUNKS,
    Ablations,
    HunkAblation,
    hunks,
    mark_obsoleted,
    mark_stale,
    removed_symbols,
    without,
)
from andromeda.outcome.core import verdict as verdict_mod
from andromeda.outcome.core.types import Check, Evidence
from andromeda.outcome.core.verdict import decide

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
CORPUS = FIXTURES / "verdict-corpus"
C580EBF0 = ("instance_qutebrowser__qutebrowser-c580ebf0801e5a3ecabc54f327498bb753c6d5f2"
            "-v2ef375ac784985212b1805e1d0431dc8f1b3c171")
F91ACE96 = ("instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111"
            "-v059c6fdc75567943479b23ebca7c07b5e9a7f34c")
ANSIBLE = ("instance_ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629"
           "-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5")
DISABLED_PER_URL = "tests/unit/components/test_hostblock.py::test_disabled_blocking_per_url"


def hidden(before, after, name="h"):
    return Check(name=name, kind="hidden", before=before, after=after)


def existing(before, after, name="e"):
    return Check(name=name, kind="existing", before=before, after=after)


def corpus_cells() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(CORPUS.rglob("*.json"))
            if p.parent.name not in ("sources", "ablations")]


def cell_inputs(cell: dict) -> tuple[list[Check], str, dict[str, str]]:
    sources = json.loads((CORPUS / cell["task"] / "sources" / f"{cell['patch']}.json").read_text())
    diff = (ROOT / cell["diff_path"]).read_text()
    return [Check.model_validate(c) for c in cell["checks"]], diff, sources


def file_rule(checks: list[Check], diff: str, sources: dict[str, str]) -> set[str]:
    """Spec 03's rule, kept here as the baseline per-test scope must reach."""
    patterns = [re.compile(rf"\b{re.escape(n)}\b") for n in removed_symbols(diff)]
    files = {p for p, src in sources.items() if any(pat.search(src) for pat in patterns)}
    return {c.name for c in checks if c.kind == "existing" and c.name.split("::", 1)[0] in files}


# --- AC1: the field ---------------------------------------------------------------------

def test_obsoleted_by_defaults_to_none_and_every_recorded_check_still_loads():
    assert existing("pass", "fail").obsoleted_by is None
    recorded = json.loads((FIXTURES / "replay" / "ansible-gold" / "checks.json").read_text())["checks"]
    for cell in corpus_cells():
        recorded += cell["checks"]
    assert len(recorded) > 1000
    for raw in recorded:
        assert Check.model_validate(raw).obsoleted_by is None


def test_a_reason_without_the_flag_is_refused():
    with pytest.raises(ValidationError, match="not obsoleted"):
        Check(name="e", kind="existing", before="pass", after="fail", obsoleted_by="required-hunk")
    ok = Check(name="e", kind="existing", before="pass", after="fail", obsoleted=True,
               obsoleted_by="deleted-symbol")
    assert Check.model_validate_json(ok.model_dump_json()) == ok


# --- AC2: per test on the committed corpus ---------------------------------------------

EXPECTED_MARKS = {(C580EBF0, "gold"): 10, (C580EBF0, "half"): 10, (F91ACE96, "gold"): 4}


def test_the_corpus_is_marked_per_test():
    seen = set()
    for cell in corpus_cells():
        checks, diff, sources = cell_inputs(cell)
        marked = [c for c in mark_obsoleted(checks, diff, sources) if c.obsoleted]
        key = (cell["task"], cell["patch"])
        assert len(marked) == EXPECTED_MARKS.get(key, 0), f"{key} {cell['oracle']}: {len(marked)} marked"
        if marked:
            seen.add(key)
        for c in marked:
            assert c.obsoleted_by == "deleted-symbol"
            test = c.name.split("::", 1)[1]
            assert (re.match(r"TestWiden::test_\w*widen_hostnames", test)
                    or test.startswith("TestHideQtWarning::")), c.name
        # every regression spec 03's rule reached, per-test scope reaches too
        reached = {c.name for c in checks if c.before == "pass" and c.after != "pass"} & file_rule(checks, diff, sources)
        assert reached <= {c.name for c in marked}, f"{key}: lost {reached - {c.name for c in marked}}"
    assert seen == set(EXPECTED_MARKS)


def test_the_file_rule_marked_many_more():
    """The measurement the spec opens with: 56 and 55 file-mates, down to 10 and 4."""
    by_key = {}
    for cell in corpus_cells():
        checks, diff, sources = cell_inputs(cell)
        by_key.setdefault((cell["task"], cell["patch"]), len(file_rule(checks, diff, sources)))
    assert by_key[(C580EBF0, "gold")] == 56 and by_key[(F91ACE96, "gold")] == 55


# --- AC3: per-test scope, one case each --------------------------------------------------

DELETES_WIDEN = """diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,2 +1,1 @@
-def widen(host):
-    return host
"""


def marks(src: str, *names: str) -> dict[str, str | None]:
    checks = [existing("pass", "fail", f"tests/t.py::{n}") for n in names]
    return {c.name.split("::", 1)[1]: c.obsoleted_by for c in mark_obsoleted(checks, DELETES_WIDEN, {"tests/t.py": src})}


def test_a_reference_in_the_test_itself_marks_it_and_not_its_file_mate():
    src = "from pkg import mod\n\ndef test_a():\n    mod.widen('x')\n\ndef test_b():\n    assert True\n"
    assert marks(src, "test_a", "test_b") == {"test_a": "deleted-symbol", "test_b": None}


def test_a_reference_only_in_a_decorator_marks():
    src = ("import pytest\nfrom pkg import mod\n\n"
           "@pytest.mark.parametrize('f', [lambda: mod.widen])\ndef test_a(f):\n    f()\n\n"
           "def test_b():\n    pass\n")
    assert marks(src, "test_a[f0]", "test_b") == {"test_a[f0]": "deleted-symbol", "test_b": None}


def test_a_reference_only_in_a_non_test_class_member_marks():
    src = ("from pkg import mod\n\nclass TestA:\n    def helper(self):\n        return mod.widen('x')\n\n"
           "    def test_x(self):\n        self.helper()\n\n"
           "class TestB:\n    def test_y(self):\n        pass\n")
    assert marks(src, "TestA::test_x", "TestB::test_y") == {"TestA::test_x": "deleted-symbol", "TestB::test_y": None}


def test_a_reference_in_a_nested_class_member_marks_through_both_classes():
    src = ("from pkg import mod\n\nclass TestA:\n    base = mod.widen\n\n"
           "    class TestInner:\n        def test_x(self):\n            pass\n\n"
           "    class TestOther:\n        def test_y(self):\n            pass\n")
    # TestA's non-test member reaches both of its nested classes' tests
    assert marks(src, "TestA::TestInner::test_x", "TestA::TestOther::test_y") == {
        "TestA::TestInner::test_x": "deleted-symbol", "TestA::TestOther::test_y": "deleted-symbol"}


def test_a_file_mate_test_in_the_same_class_does_not_mark():
    src = ("from pkg import mod\n\nclass TestA:\n    def test_x(self):\n        mod.widen('x')\n\n"
           "    def test_y(self):\n        pass\n")
    assert marks(src, "TestA::test_x", "TestA::test_y") == {"TestA::test_x": "deleted-symbol", "TestA::test_y": None}


def test_a_reference_only_in_a_fixture_two_parameters_deep_marks():
    src = ("import pytest\nfrom pkg import mod\n\n"
           "@pytest.fixture\ndef inner():\n    return mod.widen('x')\n\n"
           "@pytest.fixture()\ndef outer(inner):\n    return inner\n\n"
           "def test_a(outer):\n    assert outer\n\n"
           "def test_b(tmp_path):\n    pass\n")
    assert marks(src, "test_a", "test_b") == {"test_a": "deleted-symbol", "test_b": None}


def test_a_fixture_by_its_name_alias_autouse_and_usefixtures_all_reach():
    src = ("import pytest\nfrom pytest import fixture\nfrom pkg import mod\n\n"
           "@fixture(name='aliased')\ndef _impl():\n    return mod.widen\n\n"
           "@pytest.fixture\ndef used():\n    mod.widen('y')\n\n"
           "def test_alias(aliased):\n    pass\n\n"
           "@pytest.mark.usefixtures('used')\ndef test_use():\n    pass\n\n"
           "def test_neither(_impl):\n    pass\n")
    # `_impl` is not a fixture name: the alias replaced it
    assert marks(src, "test_alias", "test_use", "test_neither") == {
        "test_alias": "deleted-symbol", "test_use": "deleted-symbol", "test_neither": None}
    autouse = ("import pytest\nfrom pkg import mod\n\n"
               "@pytest.fixture(autouse=True)\ndef always():\n    mod.widen('z')\n\n"
               "def test_a():\n    pass\n")
    assert marks(autouse, "test_a") == {"test_a": "deleted-symbol"}


def test_a_module_level_import_marks_the_whole_file():
    src = "from pkg.mod import widen\n\ndef test_a():\n    pass\n\ndef test_b():\n    pass\n"
    assert marks(src, "test_a", "test_b") == {"test_a": "deleted-symbol", "test_b": "deleted-symbol"}


def test_an_unparseable_file_falls_back_to_the_whole_file():
    src = "from pkg import mod\n\ndef test_a(:\n    pass\n\ndef test_b():\n    mod.widen\n"
    assert marks(src, "test_a", "test_b") == {"test_a": "deleted-symbol", "test_b": "deleted-symbol"}


def test_an_unresolvable_name_falls_back_to_the_whole_file():
    src = "from pkg import mod\n\ndef test_a():\n    pass\n\ndef test_b():\n    mod.widen\n"
    # test_gone is not defined here (inherited, generated, ...): spec 03's file rule decides it
    assert marks(src, "test_a", "TestGone::test_x", "test_gone") == {
        "test_a": None, "TestGone::test_x": "deleted-symbol", "test_gone": "deleted-symbol"}
    assert marks("def test_a():\n    pass\n", "test_gone") == {"test_gone": None}


def test_whole_word_still_applies_per_test():
    src = "def test_a():\n    my_widen_helper()\n"
    assert marks(src, "test_a") == {"test_a": None}


# --- AC4: hunks() and without() ----------------------------------------------------------

def bad_patch(task: str, name: str) -> str:
    return (FIXTURES / "bad_patches" / task / f"{name}.diff").read_text()


@pytest.mark.parametrize("task,name,count", [(C580EBF0, "gold", 6), (ANSIBLE, "broken", 8)])
def test_hunks_and_without(task, name, count):
    diff = bad_patch(task, name)
    found = hunks(diff)
    assert len(found) == count
    per_file: dict[str, int] = {}
    for h in found:
        assert h.id == f"{h.path}@{per_file.setdefault(h.path, 0)}"
        per_file[h.path] += 1
        assert h.header.startswith("@@ ")
    bodies = {h.id: body for h, body in zip(found, _hunk_bodies(diff))}
    for h in found:
        rest = without(diff, h.id)
        assert bodies[h.id] not in rest
        for other, body in bodies.items():
            if other != h.id:
                assert body in rest, f"without({h.id}) lost {other}"
        # removing that hunk is the only change -- or, when it was its file's last, its whole block
        if any(x.path == h.path and x != h for x in found):
            assert rest == diff.replace(bodies[h.id], "", 1)
        else:
            assert f"diff --git a/{h.path} " not in rest and len(rest) < len(diff) - len(bodies[h.id])


def _hunk_bodies(diff: str) -> list[str]:
    """The text of each non-test hunk, in hunks() order, read independently of adapter.py."""
    out = []
    for block in re.split(r"^(?=diff --git )", diff, flags=re.M):
        m = re.search(r"^diff --git a/(\S+) b/(\S+)$", block, re.M)
        if not m or adapter_mod.is_test_file(m.group(2)) or adapter_mod.is_test_file(m.group(1)):
            continue
        out += re.split(r"^(?=@@)", block, flags=re.M)[1:]
    return out


def test_a_test_file_hunk_is_never_returned():
    diff = bad_patch(C580EBF0, "gold")
    test_block = ("diff --git a/tests/unit/test_x.py b/tests/unit/test_x.py\n--- a/tests/unit/test_x.py\n"
                  "+++ b/tests/unit/test_x.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert hunks(diff + test_block) == hunks(diff)
    with pytest.raises(KeyError):
        without(diff + test_block, "tests/unit/test_x.py@0")


def test_without_the_only_hunk_drops_the_whole_block():
    diff = DELETES_WIDEN + "diff --git a/pkg/b.py b/pkg/b.py\n--- a/pkg/b.py\n+++ b/pkg/b.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert [h.id for h in hunks(diff)] == ["pkg/mod.py@0", "pkg/b.py@0"]
    assert without(diff, "pkg/mod.py@0") == diff[len(DELETES_WIDEN):]
    assert without(diff, "pkg/b.py@0") == DELETES_WIDEN
    for bad in ("pkg/mod.py@1", "pkg/c.py@0", "pkg/mod.py@x"):
        with pytest.raises(KeyError):
            without(diff, bad)


# --- AC5: mark_stale -----------------------------------------------------------------------

REG = "tests/t.py::test_old"


def evidence_checks() -> list[Check]:
    return [
        hidden("fail", "pass", "h1"), hidden("fail", "pass", "h2"), hidden("fail", "fail", "h3"),
        hidden("pass", "pass", "h_kept"),
        existing("pass", "fail", REG),
        existing("pass", "pass", "tests/t.py::test_fine"),
        Check(name="tests/t.py::test_gone", kind="existing", before="pass", after="fail",
              obsoleted=True, obsoleted_by="deleted-symbol"),
    ]


def run(results: dict, **kw) -> Ablations:
    return Ablations(considered=True, hunks=[HunkAblation(id="pkg/mod.py@0", header="@@", results=results, **kw)])


# h3 fails under the full diff too, so "required" is strictly more than 1 failing valid check
RESTORES_AND_REQUIRED = {"h1": "fail", "h2": "pass", "h3": "fail", REG: "pass"}


def stale(ablations: Ablations) -> dict[str, str | None]:
    return {c.name: c.obsoleted_by for c in mark_stale(evidence_checks(), ablations)}


def test_restores_and_required_marks_required_hunk():
    out = mark_stale(evidence_checks(), run(RESTORES_AND_REQUIRED))
    marked = [c for c in out if c.obsoleted_by == "required-hunk"]
    assert [c.name for c in marked] == [REG] and marked[0].obsoleted is True


def test_restores_but_not_required_leaves_it():
    assert stale(run({"h1": "pass", "h2": "pass", "h3": "fail", REG: "pass"}))[REG] is None


def test_required_but_not_restores_leaves_it():
    assert stale(run({"h1": "fail", "h2": "fail", "h3": "fail", REG: "fail"}))[REG] is None


def test_an_absent_result_is_failing_for_hidden_and_not_restored_for_a_regression():
    assert stale(run({"h3": "fail", REG: "pass"}))[REG] == "required-hunk"   # h1/h2 absent: failing
    assert stale(run({"h1": "fail", "h2": "fail", "h3": "fail"}))[REG] is None  # REG absent


def test_a_hunk_that_did_not_apply_or_errored_gives_no_information():
    assert stale(run(RESTORES_AND_REQUIRED, applied=False))[REG] is None
    assert stale(run(RESTORES_AND_REQUIRED, infra_error="boom"))[REG] is None


def test_the_rule_is_read_off_results_not_the_recorded_fields():
    assert stale(run({"h1": "pass", "h2": "pass", "h3": "fail", REG: "pass"}, required=True, restores=[REG]))[REG] is None


def test_mark_stale_is_pure_and_touches_only_unmarked_regressions():
    original = evidence_checks()
    snapshot = [c.model_dump_json() for c in original]
    out = mark_stale(original, run(RESTORES_AND_REQUIRED))
    assert [c.model_dump_json() for c in original] == snapshot
    for before, after in zip(original, out):
        if before.name != REG:
            assert after.model_dump_json() == before.model_dump_json()
    # a skipped attribution changes nothing
    assert mark_stale(original, Ablations(considered=True, skipped="too-many-hunks")) == original


def test_max_ablation_hunks_is_a_constant():
    assert MAX_ABLATION_HUNKS == 12
    assert not re.search(r"environ\S*\(\s*['\"][A-Z_]*ABLATION", inspect.getsource(adapter_mod))


# --- AC8: decide()'s reasons ----------------------------------------------------------------

def test_a_deleted_symbol_mark_leaves_every_pass_reason_unchanged():
    gone = existing("pass", "fail", "tests/t.py::gone")
    for base in ([hidden("fail", "pass", f"h{i}") for i in range(5)],
                 [hidden("fail", "pass", f"h{i}") for i in range(4)] + [hidden("fail", "fail", "h4")]):
        plain = decide(Evidence(checks=base))
        forgiven = decide(Evidence(checks=base + [gone.model_copy(update={"obsoleted": True, "obsoleted_by": "deleted-symbol"})]))
        legacy = decide(Evidence(checks=base + [gone.model_copy(update={"obsoleted": True})]))
        assert plain[0] == "PASS" and plain == forgiven == legacy


def test_a_required_hunk_pass_names_it():
    stale_check = Check(name="tests/t.py::a", kind="existing", before="pass", after="fail",
                        obsoleted=True, obsoleted_by="required-hunk")
    other = stale_check.model_copy(update={"name": "tests/t.py::b"})
    clean = [hidden("fail", "pass", f"h{i}") for i in range(5)]
    assert decide(Evidence(checks=clean + [stale_check, other])) == (
        "PASS", "all 5 valid hidden checks pass, no regressions; "
                "2 existing checks stale, forgiven by required-hunk: tests/t.py::a")
    tolerated = clean[:4] + [hidden("fail", "fail", "h4")]
    assert decide(Evidence(checks=tolerated + [stale_check])) == (
        "PASS", "4/5 valid hidden checks pass, no regressions; 1 failing within the 20% tolerance: h4"
                "; 1 existing checks stale, forgiven by required-hunk: tests/t.py::a")


def test_decide_stays_sync_and_pure():
    assert not inspect.iscoroutinefunction(decide)
    src = inspect.getsource(verdict_mod)
    for forbidden in ("environ", "getenv", "open(", "import os", "Path", "asyncio"):
        assert forbidden not in src, forbidden


# --- ACs 6, 7: when attribution runs, and how it fails -------------------------------------

def diff_with(n: int) -> str:
    """One non-test file with n hunks, plus a test-file hunk that never counts."""
    body = "".join(f"@@ -{10 * i + 1} +{10 * i + 1} @@\n-a{i}\n+b{i}\n" for i in range(n))
    source = f"diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n{body}" if n else ""
    return source + ("diff --git a/tests/test_mod.py b/tests/test_mod.py\n--- a/tests/test_mod.py\n"
                     "+++ b/tests/test_mod.py\n@@ -1 +1 @@\n-x\n+y\n")


VALID = [hidden("fail", "pass", f"tests/test_outcome_x.py::h{i}") for i in range(5)]
REGRESSION = existing("pass", "fail", "tests/test_mod.py::test_old")
EXISTING_OK = existing("pass", "pass", "tests/test_mod.py::test_fine")


class FakeRunner:
    """Records every ablation call; hunk 1 is the one that restores and is required."""

    def __init__(self, fail_with: BaseException | None = None):
        self.calls, self.in_flight, self.max_in_flight = [], 0, 0
        self.fail_with = fail_with

    async def __call__(self, task, diff, hidden_dir, files, log_dir, label, canaries=()):
        self.calls.append((label, diff, list(files)))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            if self.fail_with is not None:
                raise self.fail_with
            without_1 = "+b1" not in diff
            results = {c.name: "pass" for c in VALID}
            if without_1:
                results[VALID[0].name] = results[VALID[1].name] = "fail"
            results[REGRESSION.name] = "pass" if without_1 else "fail"
            results[EXISTING_OK.name] = "pass"  # outside the watched set: not recorded
            return results
        finally:
            self.in_flight -= 1


@pytest.fixture
def grade(monkeypatch, tmp_path):
    """gather_evidence with every Docker seam stubbed: returns (evidence, ablation.json, runner)."""
    from types import SimpleNamespace

    from andromeda.outcome.adapters.coding import runner as runner_mod
    from andromeda.outcome.adapters.coding.adapter import CodingAdapter
    from andromeda.outcome.adapters.coding.docker import InfraError
    from andromeda.outcome.core.types import Request

    async def no_draw(task_, run_dir, **_):
        return []

    async def no_existing(self, task_, artifact, tests=()):
        return [], {}

    monkeypatch.setattr(adapter_mod, "write_hidden_tests", no_draw)
    monkeypatch.setattr(adapter_mod, "load_safe", lambda _id: SimpleNamespace(instance_id=_id, repo_language="python"))
    monkeypatch.setattr(CodingAdapter, "_existing_tests", no_existing)

    async def go(checks, diff, runner=None):
        runner = runner or FakeRunner()
        monkeypatch.setattr(runner_mod, "run_after", runner)

        async def fake_run_checks(task_, diff_, hidden_dir, hidden_files, existing_, log_dir, canaries=()):
            if checks is InfraError:
                raise InfraError("box died")
            return list(checks)

        monkeypatch.setattr(adapter_mod, "run_checks", fake_run_checks)
        run_dir = tmp_path / f"run{len(list(tmp_path.iterdir()))}"
        run_dir.mkdir()
        evidence = await CodingAdapter(run_dir).gather_evidence(Request(task_id="t", text=""), diff)
        return evidence, json.loads((run_dir / "ablation.json").read_text()), runner

    return go


def route_of(evidence):
    return verdict_mod.route(evidence)


@pytest.mark.parametrize("label,checks", [
    ("pass", VALID + [EXISTING_OK]),
    ("fail", [hidden("fail", "fail", f"h{i}") for i in range(5)] + [REGRESSION]),
    ("unverified: no valid hidden", [hidden("pass", "pass", "h")] + [REGRESSION]),
])
async def test_no_ablation_off_a_regressed_route(grade, label, checks):
    evidence, ablation, runner = await grade(checks, diff_with(3))
    assert runner.calls == []
    assert ablation == Ablations(considered=False).model_dump(mode="json")
    assert "ablation" not in evidence.artifacts


async def test_no_ablation_on_an_infra_error(grade):
    from andromeda.outcome.adapters.coding.docker import InfraError

    evidence, ablation, runner = await grade(InfraError, diff_with(3))
    assert route_of(evidence) == "unverified" and runner.calls == []
    assert ablation["considered"] is False


@pytest.mark.parametrize("label,checks,n,skipped", [
    ("hidden pass->fail", VALID + [hidden("pass", "fail", "tests/test_outcome_x.py::kept"), REGRESSION], 3,
     "hidden-regression"),
    ("0 hunks", VALID + [REGRESSION], 0, "no-hunks"),
    ("13 hunks", VALID + [REGRESSION], 13, "too-many-hunks"),
])
async def test_a_regressed_draw_the_guard_or_the_count_skips(grade, label, checks, n, skipped):
    evidence, ablation, runner = await grade(checks, diff_with(n))
    assert runner.calls == []
    assert ablation["considered"] is True and ablation["skipped"] == skipped
    assert ablation["hunks"] == [] and ablation["stale"] == []
    assert route_of(evidence) == "regressed"
    assert evidence.artifacts["ablation"].endswith("ablation.json")


async def test_twelve_hunks_is_not_too_many(grade):
    _, ablation, runner = await grade(VALID + [REGRESSION], diff_with(MAX_ABLATION_HUNKS))
    assert len(runner.calls) == MAX_ABLATION_HUNKS and ablation["skipped"] is None


async def test_a_qualifying_draw_runs_one_ablation_per_hunk_one_at_a_time(grade):
    diff = diff_with(3)
    evidence, ablation, runner = await grade(VALID + [REGRESSION, EXISTING_OK], diff)

    assert [label for label, _, _ in runner.calls] == ["hunk0", "hunk1", "hunk2"]
    # spec 18: the tests/test_mod.py block edits a base test module, so it is set aside before grading
    graded = diff.split("diff --git a/tests/test_mod.py")[0]
    assert [d for _, d, _ in runner.calls] == [without(graded, f"pkg/mod.py@{i}") for i in range(3)]
    assert runner.max_in_flight == 1
    # the regressions' files and the valid hidden checks' files, nothing else
    assert runner.calls[0][2] == ["tests/test_mod.py", "tests/test_outcome_x.py"]

    assert ablation["considered"] is True and ablation["skipped"] is None
    assert ablation["regressions"] == [REGRESSION.name] and ablation["valid_failing_full"] == 0
    by_id = {h["id"]: h for h in ablation["hunks"]}
    assert by_id["pkg/mod.py@1"]["restores"] == [REGRESSION.name] and by_id["pkg/mod.py@1"]["required"] is True
    assert by_id["pkg/mod.py@1"]["valid_failing"] == 2
    assert by_id["pkg/mod.py@0"]["restores"] == [] and by_id["pkg/mod.py@0"]["required"] is False
    assert EXISTING_OK.name not in by_id["pkg/mod.py@0"]["results"]
    assert ablation["stale"] == [REGRESSION.name]

    assert route_of(evidence) == "pass"
    marked = {c.name: c.obsoleted_by for c in evidence.checks}
    assert marked[REGRESSION.name] == "required-hunk" and marked[EXISTING_OK.name] is None
    assert decide(evidence)[1].endswith(f"forgiven by required-hunk: {REGRESSION.name}"
                                        "; 1 edited existing test modules graded at base: tests/test_mod.py")


async def test_an_infra_error_in_an_ablation_stays_out_of_the_evidence(grade):
    from andromeda.outcome.adapters.coding.docker import InfraError

    evidence, ablation, runner = await grade(VALID + [REGRESSION], diff_with(3), FakeRunner(InfraError("boom")))
    assert len(runner.calls) == 3
    assert evidence.infra_errors == [] and route_of(evidence) == "regressed"
    assert all(h["infra_error"] == "InfraError: boom" and h["required"] is False for h in ablation["hunks"])
    assert ablation["stale"] == []


async def test_a_hunk_that_does_not_apply_is_recorded_as_such(grade):
    from andromeda.outcome.adapters.coding.runner import NotApplied

    evidence, ablation, _ = await grade(VALID + [REGRESSION], diff_with(2), FakeRunner(NotApplied("nope")))
    assert [(h["applied"], h["infra_error"]) for h in ablation["hunks"]] == [(False, None), (False, None)]
    assert evidence.infra_errors == [] and route_of(evidence) == "regressed"


async def test_a_cancelled_ablation_propagates_and_marks_nothing(grade, tmp_path):
    with pytest.raises(asyncio.CancelledError):
        await grade(VALID + [REGRESSION], diff_with(3), FakeRunner(asyncio.CancelledError()))
    for written in tmp_path.rglob("*.json"):
        assert "required-hunk" not in written.read_text()


# --- ACs 11-13: graded for real, pinned oracles ----------------------------------------------

async def grade_for_real(run_dir: Path, task: str, diff: str) -> tuple[Evidence, dict]:
    """The real gather_evidence: real load_safe, real containers, the task's pinned oracle."""
    from andromeda.outcome.adapters.coding.adapter import CodingAdapter
    from andromeda.outcome.core.types import Request

    evidence = await CodingAdapter(run_dir, FIXTURES / "oracles" / task).gather_evidence(
        Request(task_id=task, text=""), diff)
    assert evidence.infra_errors == [], evidence.infra_errors
    written = Evidence.model_validate_json((run_dir / "checks.json").read_text())
    assert written == evidence, "checks.json is not the evidence gather_evidence returned"
    return evidence, json.loads((run_dir / "ablation.json").read_text())


CONSTRUCTED = FIXTURES / "obsolescence" / C580EBF0
WHITELISTED_LINE = "tests/unit/components/test_hostblock.py::test_whitelisted_lines[27.0.0.1-localhost.localdomain]"


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac11_gold_passes_with_its_stale_check_named(test_run_dir):
    evidence, ablation = await grade_for_real(test_run_dir, C580EBF0, bad_patch(C580EBF0, "gold"))

    verdict, reason = decide(evidence)
    assert verdict == "PASS", reason
    assert reason.endswith(f"1 existing checks stale, forgiven by required-hunk: {DISABLED_PER_URL}")
    assert [c.name for c in evidence.checks if c.obsoleted_by == "required-hunk"] == [DISABLED_PER_URL]
    assert evidence.artifacts["ablation"] == str(test_run_dir / "ablation.json")
    assert ablation["skipped"] is None and ablation["stale"] == [DISABLED_PER_URL]
    both = [h["id"] for h in ablation["hunks"] if DISABLED_PER_URL in h["restores"] and h["required"]]
    assert both == ["qutebrowser/components/hostblock.py@1"]


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac11_a_gratuitous_regression_still_counts(test_run_dir):
    evidence, ablation = await grade_for_real(
        test_run_dir, C580EBF0, (CONSTRUCTED / "gold-plus-unrelated.diff").read_text())

    assert verdict_mod.route(evidence) == "regressed"
    assert decide(evidence)[0] == "UNKNOWN"
    by_name = {c.name: c for c in evidence.checks}
    broken = by_name[WHITELISTED_LINE]
    assert (broken.before, broken.after, broken.obsoleted) == ("pass", "fail", False)
    assert by_name[DISABLED_PER_URL].obsoleted_by == "required-hunk"
    extra = next(h for h in ablation["hunks"] if h["id"] == "qutebrowser/components/hostblock.py@2")
    # its revert restores the whitelist line, but no valid hidden check needs it
    assert WHITELISTED_LINE in extra["restores"] and extra["required"] is False
    assert WHITELISTED_LINE not in ablation["stale"]


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac13_broken_diff_is_not_forgiven(test_run_dir):
    """The stop gate, measured first in the build: the guard fires -- the diff breaks a hidden
    check that passed on base -- so no hunk is reverted and the regressions stand."""
    evidence, ablation = await grade_for_real(test_run_dir, ANSIBLE, bad_patch(ANSIBLE, "broken"))

    assert verdict_mod.route(evidence) == "regressed"
    assert ablation["considered"] is True and ablation["skipped"] == "hidden-regression"
    assert ablation["hunks"] == [] and ablation["stale"] == []
    assert not any(c.obsoleted_by == "required-hunk" for c in evidence.checks)
    assert any(c.kind == "hidden" and c.before == "pass" and c.after != "pass" for c in evidence.checks)


WHITELIST_OVERRIDE = ("tests/unit/components/test_outcome_hostblock_subdomains.py"
                      "::test_whitelist_overrides_parent_domain_block")


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac12_a_defect_inside_the_required_hunk_is_pinned(test_run_dir):
    """The residual, pinned to the branch the pinned oracle was measured to take.

    Measured: the oracle's own whitelist check passes on base and fails under gold-in-hunk,
    so the guard fires and nothing is reverted -- the regression counts. The other branch,
    an oracle with no such preservation check, would forgive it: hostblock.py@1 would both
    restore test_blocking_with_whitelist and be required. That is L33. If this oracle ever
    stops covering the whitelist, this test fails here rather than the draw going to PASS
    silently.
    """
    evidence, ablation = await grade_for_real(
        test_run_dir, C580EBF0, (CONSTRUCTED / "gold-in-hunk.diff").read_text())

    by_name = {c.name: c for c in evidence.checks}
    override = by_name[WHITELIST_OVERRIDE]
    assert (override.kind, override.before, override.after) == ("hidden", "pass", "fail")
    assert ablation["considered"] is True and ablation["skipped"] == "hidden-regression"
    assert ablation["hunks"] == [] and ablation["stale"] == []
    assert verdict_mod.route(evidence) == "regressed" and decide(evidence)[0] == "UNKNOWN"
    whitelist = by_name["tests/unit/components/test_hostblock.py::test_blocking_with_whitelist"]
    assert (whitelist.before, whitelist.after, whitelist.obsoleted) == ("pass", "fail", False)
