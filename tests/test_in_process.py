"""Spec 18's proof: a check's result can no longer be written by the diff's own code.

The Docker-free part covers ACs 1-7: base test modules set aside by the split (L37), the
canary (`canary.plant`, `canary.as_diff`), the host-side parse and non-pass-wins `fold`, the
runner's canaries, the verdict's `tampered` rule and `bt` suffix, and both graders' wiring with
every container stubbed. The `real` tests grade in the task images with the pinned oracles,
through the engine and the vendored harness both: gold and a stale-test edit cost nothing
(ACs 9, 13), and 17's force-pass, with 17's rule bypassed, is caught by the canaries alone
(AC10's evidence; the SPEC's gap note says what stands in for ACs 10-12's fixtures).
"""

import asyncio
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

import andromeda.outcome.adapters.coding.adapter as adapter_mod
import andromeda.outcome.grading.ground_truth as ground_truth
from andromeda.outcome.adapters.coding import canary, infra
from andromeda.outcome.adapters.coding import runner as runner_mod
from andromeda.outcome.adapters.coding.docker import InfraError
from andromeda.outcome.adapters.coding.infra import TEST_MODULE, Split, Unreadable, split
from andromeda.outcome.adapters.coding.runner import PARSER_STATUS, fold
from andromeda.outcome.core import verdict as verdict_mod
from andromeda.outcome.core.types import Check, Evidence
from andromeda.outcome.core.verdict import admits, decide, feedback, rank, route
from andromeda.outcome.adapters.coding.adapter import Ablations, CodingAdapter, ablate
from andromeda.outcome.adapters.coding.task import GradingData
from andromeda.outcome.core.types import Request
from test_gate_integrity import (ANSIBLE, C580EBF0, F91ACE96, FIXTURES, assert_graded_is_clean, git_diff,
                                 modify, new_file, numstat_paths, safe)


def created_paths(patch: str) -> set[str]:
    """What git reads `patch` as creating, forward: the test's own reading of `--summary`."""
    proc = subprocess.run(["git", "apply", "--summary", "-z", "--allow-empty"],
                          input=patch.encode(), capture_output=True, check=True, cwd="/")
    lines = proc.stdout.decode().splitlines()
    return {line.split(" ", 4)[4] for line in lines if line.startswith(" create mode ")}


def assert_graded_edits_no_base_test(s: Split):
    """Invariant 5, read independently of infra's own parser: the graded part touches no test
    module it does not create."""
    if s.graded:
        created = created_paths(s.graded)
        assert not [p for p in numstat_paths(s.graded) if TEST_MODULE.match(PurePosixPath(p).name)
                    and p not in created and not _renamed_to(s.graded, p)]


def _renamed_to(patch: str, path: str) -> bool:
    return f"rename to {path}\n" in patch or f"copy to {path}\n" in patch


# --- AC5: the split sets aside base test modules --------------------------------------------

HOSTBLOCK = "tests/unit/components/test_hostblock.py"
GALAXY = "test/units/cli/test_galaxy.py"
LOG, LOG2, QTLOG = "tests/unit/utils/test_log.py", "tests/unit/utils/test_log2.py", "tests/unit/utils/test_qtlog.py"


def delete(path: str, line: str = "x = 1") -> str:
    return (f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n"
            f"@@ -1 +0,0 @@\n-{line}\n")


def rename(tmp_path: Path, old: str, new: str) -> str:
    body = "".join(f"line_{i} = {i}\n" for i in range(20))

    def setup(repo):
        (repo / old).parent.mkdir(parents=True, exist_ok=True)
        (repo / old).write_text(body)

    def change(repo, git):
        git("mv", old, new)

    diff = git_diff(tmp_path, setup, change)
    assert f"rename from {old}" in diff and f"rename to {new}" in diff
    return diff


TRADITIONAL = (modify("qutebrowser/x.py")
               + f"--- a/{QTLOG}\n+++ b/{QTLOG}\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")


@pytest.mark.parametrize("block,tests", [
    (modify(HOSTBLOCK), [HOSTBLOCK]),
    (delete(GALAXY), [GALAXY]),
    (TRADITIONAL, [QTLOG]),
    (modify("tests/unit/utils/log_test.py"), ["tests/unit/utils/log_test.py"]),
], ids=["modify", "delete", "traditional-section", "suffix-name"])
def test_ac5_an_edit_to_a_base_test_module_is_set_aside(block, tests):
    s = split(modify("src/a.py") + block)
    assert (s.graded, s.ignored, s.paths, s.tests) == (modify("src/a.py"), block, [], tests)
    assert_graded_edits_no_base_test(s)
    assert_graded_is_clean(s)


def test_ac5_a_rename_sets_aside_its_old_path(tmp_path):
    diff = rename(tmp_path, LOG, LOG2)
    s = split(modify("src/a.py") + diff)
    assert (s.graded, s.ignored, s.paths, s.tests) == (modify("src/a.py"), diff, [], [LOG])
    assert_graded_edits_no_base_test(s)


def test_ac5_a_rename_onto_a_test_name_is_graded(tmp_path):
    """A rename's target is created; its source, not a test module, is code under test."""
    diff = rename(tmp_path, "src/helper.py", "src/test_helper.py")
    s = split(diff)
    assert (s.graded, s.ignored, s.paths, s.tests) == (diff, "", [], [])


@pytest.mark.parametrize("block", [
    new_file("tests/unit/components/test_agent_extra.py"),
    modify("lib/ansible/plugins/test/core.py"),
    modify("qutebrowser/utils/testing.py"),
], ids=["created-test-module", "ansible-test-plugin", "not-a-test-name"])
def test_ac5_these_stay_graded(block):
    s = split(block)
    assert (s.graded, s.ignored, s.paths, s.tests) == (block, "", [], [])


def test_ac5_a_nested_helper_is_ignored_as_test_support():
    """Spec 20: `openlibrary/tests/helper.py` is 17's test-support, so it is in `paths`, not `tests`."""
    block = modify("openlibrary/tests/helper.py")
    s = split(block)
    assert (s.graded, s.ignored, s.paths, s.tests) == ("", block, ["openlibrary/tests/helper.py"], [])


def test_ac5_infrastructure_and_a_base_test_are_recorded_by_kind():
    both = modify(HOSTBLOCK) + "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    s = split(modify("src/a.py") + both + modify(GALAXY))
    assert (s.graded, s.ignored) == (modify("src/a.py"), both + modify(GALAXY))
    assert (s.paths, s.tests) == (["tests/conftest.py"], sorted([HOSTBLOCK, GALAXY]))


def test_ac5_the_same_module_twice_is_listed_once():
    s = split(modify(HOSTBLOCK) + modify("src/a.py") + modify(HOSTBLOCK, "b = 1", "b = 2"))
    assert s.tests == [HOSTBLOCK] and s.graded == modify("src/a.py")


def test_ac5_invariant_5_is_asserted_after_cutting(monkeypatch):
    """If the whole graded part ever reads as editing a base test module, the split refuses."""
    real, calls = infra._base_tests, []

    def lying(patch, cwd, touched):
        calls.append(patch)
        return real(patch, cwd, touched) | ({HOSTBLOCK} if len(calls) > 2 else set())

    monkeypatch.setattr(infra, "_base_tests", lying)
    with pytest.raises(Unreadable, match=f"still edits a base test module: {HOSTBLOCK}"):
        split(modify("src/a.py") + modify("src/b.py"))
    assert calls[-1] == modify("src/a.py") + modify("src/b.py")


def fixture_diffs() -> list[Path]:
    return sorted([*FIXTURES.glob("bad_patches/*/*.diff"), *FIXTURES.glob("agents/*/agent.diff"),
                   *FIXTURES.glob("obsolescence/*/*.diff"), *FIXTURES.glob("gate-integrity/*/*.diff")])


AGENT_TESTS = {
    FIXTURES / "agents" / C580EBF0 / "agent.diff": [HOSTBLOCK, "tests/unit/utils/test_urlutils.py"],
    FIXTURES / "agents" / F91ACE96 / "agent.diff": [LOG, QTLOG],
}


@pytest.mark.parametrize("path", fixture_diffs(), ids=lambda p: f"{p.parent.parent.name}/{p.parent.name[:30]}/{p.name}")
def test_ac5_every_fixture_diff_splits_as_17_or_sets_aside_its_edits(path, monkeypatch):
    diff = path.read_text()
    s = split(diff)
    assert_graded_edits_no_base_test(s)
    assert_graded_is_clean(s)
    if path in AGENT_TESTS:
        assert s.tests == AGENT_TESTS[path] and s.paths == []
        assert all(f"diff --git a/{t} b/{t}" in s.ignored and t not in numstat_paths(s.graded)
                   for t in s.tests)
        return
    assert s.tests == []
    monkeypatch.setattr(infra, "_base_tests", lambda patch, cwd, touched: set())
    assert s == split(diff)  # 17's split exactly


def test_ac5_the_fixture_set_covers_both_agents_and_the_ansible_one():
    diffs = fixture_diffs()
    assert FIXTURES / "agents" / ANSIBLE / "agent.diff" in diffs
    assert set(AGENT_TESTS) <= set(diffs) and len(diffs) == 20


# --- AC1: canary planting --------------------------------------------------------------------

HIDDEN = ["tests/unit/components/test_outcome_hostblock_extra.py", "tests/unit/utils/test_outcome_urlutils.py",
          "tests/unit/utils/test_outcome_more.py", "test_outcome_top.py"]
# measured in the build on the pinned oracles (REPORT.md), then pinned: ansible gold's reason is
# the same with canaries bypassed and on, and the c580ebf0 agent fixture grades PASS / resolved
ANSIBLE_GOLD_REASON = "all 25 valid hidden checks pass, no regressions"
AC13_VERDICT, AC13_TRUTH = ("PASS", "pass"), "resolved"
PLANTED = re.compile(r"^(?:(.+)/)?test_outcome_[0-9a-f]{16}\.py$")


def test_ac1_one_canary_per_distinct_directory():
    planted = canary.plant(HIDDEN)
    dirs = sorted(str(PurePosixPath(p).parent) for p in planted)
    assert dirs == sorted({str(PurePosixPath(p).parent) for p in HIDDEN}) == [".", "tests/unit/components",
                                                                               "tests/unit/utils"]
    assert all(PLANTED.match(p) for p in planted) and not set(planted) & set(HIDDEN)
    assert canary.CANARIES_PER_DIR == 1


def test_ac1_names_are_fresh_per_call():
    one, two = canary.plant(HIDDEN), canary.plant(HIDDEN)
    assert not set(one) & set(two) and not set(one.values()) & set(two.values())


def test_ac1_nothing_in_nothing_out():
    assert canary.plant([]) == {} and canary.as_diff({}) == ""


def test_ac1_a_collision_with_an_input_path_is_redrawn(monkeypatch):
    tokens = iter(["0" * 16, "1" * 16, "2" * 16])
    monkeypatch.setattr(canary.secrets, "token_hex", lambda n: next(tokens))
    planted = canary.plant(["a/test_outcome_" + "0" * 16 + ".py"])
    assert list(planted) == ["a/test_outcome_" + "1" * 16 + ".py"]
    assert "def test_" + "2" * 16 + "():" in planted["a/test_outcome_" + "1" * 16 + ".py"]


def test_ac1_each_canary_collects_one_test_that_fails(tmp_path):
    planted = canary.plant(HIDDEN)
    for rel, source in planted.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source)
    for rel in planted:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "-q",
                               "--rootdir", str(tmp_path), "-c", "/dev/null", str(tmp_path / rel)],
                              capture_output=True, text=True, cwd=tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert re.search(r"^1 failed in ", proc.stdout.splitlines()[-1]), proc.stdout
        assert f"FAILED {rel}::test_" in proc.stdout


def test_ac1_the_source_imports_nothing_and_reads_no_env():
    planted = canary.plant(HIDDEN)
    for source in planted.values():
        assert "import" not in source and "assert" in source
    src = inspect.getsource(canary)
    assert "environ" not in src and "getenv" not in src


def test_ac1_as_diff_round_trips_through_git_as_creates():
    planted = canary.plant(HIDDEN)
    patch = canary.as_diff(planted)
    assert numstat_paths(patch) == set(planted) == created_paths(patch)
    proc = subprocess.run(["git", "apply", "--numstat", "-z"], input=patch.encode(), capture_output=True,
                          check=True, cwd="/")
    records = [r for r in proc.stdout.decode().split("\0") if r]
    assert sorted(r.split("\t")[2] for r in records) == sorted(planted)
    assert all(r.split("\t")[:2] == [str(len(planted[r.split("\t")[2]].splitlines())), "0"] for r in records)


def test_ac1_as_diff_applies_to_a_tree(tmp_path):
    planted = canary.plant(HIDDEN)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "apply"], input=canary.as_diff(planted).encode(), cwd=tmp_path, check=True)
    assert {rel: (tmp_path / rel).read_text() for rel in planted} == planted


# --- AC2: the fold ---------------------------------------------------------------------------

@pytest.mark.parametrize("reports,status", [
    ([("t", "PASSED"), ("t", "FAILED")], "fail"),
    ([("t", "FAILED"), ("t", "PASSED")], "fail"),
    ([("t", "PASSED"), ("t", "ERROR")], "error"),
    ([("t", "PASSED"), ("t", "PASSED")], "pass"),
    ([("t", "PASSED"), ("t", "SKIPPED"), ("t", "FAILED")], "error"),
    ([("t", "FAILED"), ("t", "ERROR"), ("t", "PASSED")], "fail"),
    *[([("t", name)], mapped) for name, mapped in PARSER_STATUS.items()],
    ([("t", "XWHAT")], "error"),
])
def test_ac2_non_pass_wins(reports, status):
    assert fold(reports) == {"t": status}


def test_ac2_names_keep_first_seen_order_and_are_independent():
    reports = [("b", "PASSED"), ("a", "FAILED"), ("b", "PASSED"), ("a", "PASSED"), ("c", "SKIPPED")]
    assert list(fold(reports).items()) == [("b", "pass"), ("a", "fail"), ("c", "error")]
    assert fold([]) == {}


def test_ac2_fold_is_pure():
    src = inspect.getsource(fold) + inspect.getsource(runner_mod._fold_names)
    assert "environ" not in src and "time" not in src and "open(" not in src


PARSER_LOGS = sorted((FIXTURES / "in-process" / "parser-logs").iterdir())


def test_ac2_one_recorded_pair_per_parser_family():
    families = sorted(p.name.split("__")[0] for p in PARSER_LOGS)
    assert families == ["instance_ansible", "instance_qutebrowser"]


@pytest.mark.parametrize("pair", PARSER_LOGS, ids=lambda p: p.name.split("__")[0])
def test_ac2_the_host_parse_and_fold_equal_the_recorded_output(pair):
    reports = runner_mod._parse(pair.name, (pair / "after_stdout.log").read_text(),
                                (pair / "after_stderr.log").read_text())
    recorded = json.loads((pair / "after_output.json").read_text())["tests"]
    assert len(recorded) > 20
    assert list(runner_mod._fold_names(reports).items()) == [(t["name"], t["status"]) for t in recorded]
    assert fold(reports) == {t["name"]: PARSER_STATUS.get(t["status"], "error") for t in recorded}


def test_ac2_a_forged_line_after_the_real_one_no_longer_wins():
    """The qutebrowser parser matches per line: a later `PASSED` for a failed name is a second
    report, which the vendored last-wins dedupe credited and the fold does not."""
    pair = next(p for p in PARSER_LOGS if "qutebrowser" in p.name)
    stdout = (pair / "after_stdout.log").read_text()
    failed = re.findall(r"^(\S+::\S+) FAILED", stdout, re.M) or ["tests/unit/x.py::test_x"]
    forged = stdout + "".join(f"{name} PASSED\n" for name in failed)
    if not re.search(r"FAILED", stdout):
        forged = f"{failed[0]} FAILED\n" + forged
    reports = runner_mod._parse(pair.name, forged, "")
    last_wins = {n: PARSER_STATUS[s] for n, s in {n: s for n, s in reports}.items()}
    assert all(last_wins[n] == "pass" for n in failed)
    assert all(fold(reports)[n] == "fail" for n in failed)


def test_ac2_the_vendored_parser_raising_is_an_infra_error(monkeypatch, tmp_path):
    (tmp_path / "run_scripts" / "i").mkdir(parents=True)
    (tmp_path / "run_scripts" / "i" / "parser.py").write_text(
        "def parse_test_output(stdout_content, stderr_content):\n    raise ValueError('bad log')\n")
    monkeypatch.setattr(runner_mod, "swepro_dir", lambda: tmp_path)
    before = set(sys.modules)
    with pytest.raises(InfraError, match="vendored parser .* failed on the host: ValueError: bad log"):
        runner_mod._parse("i", "x", "")
    with pytest.raises(InfraError, match="failed on the host"):
        runner_mod._parse("missing", "x", "")
    assert set(sys.modules) == before


class FakeBox:
    """A sandbox whose run script leaves scripted logs: _run_side's host half runs for real."""

    def __init__(self, logs: dict[str, str]):
        self.logs, self.execs = logs, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def exec(self, cmd, **kw):
        self.execs.append(cmd)
        return 0, "", ""

    async def copy_in(self, src, dst):
        pass

    async def copy_out(self, src, dst):
        name = Path(src).name
        if name not in self.logs:
            raise InfraError(f"no {name}")
        Path(dst).write_text(self.logs[name])


def test_ac2_run_side_parses_on_the_host_and_writes_the_output(monkeypatch, tmp_path):
    pair = next(p for p in PARSER_LOGS if "qutebrowser" in p.name)
    box = FakeBox({"stdout.log": (pair / "after_stdout.log").read_text(), "stderr.log": ""})
    monkeypatch.setattr(runner_mod, "task_sandbox", lambda image, run_id: box)
    monkeypatch.setattr(runner_mod, "reset_to_base", lambda c, commit: asyncio.sleep(0))
    task = type("T", (), {"instance_id": pair.name, "image": "img", "base_commit": "abc"})()
    log_dir = tmp_path / "run" / "checks_logs"
    log_dir.mkdir(parents=True)
    got = asyncio.run(runner_mod._run_side(task, None, tmp_path / "none", ["tests/x.py"], log_dir, "after"))
    assert not any("parser.py" in cmd for cmd in box.execs)
    assert (log_dir / "after_output.json").read_bytes() == (pair / "after_output.json").read_bytes()
    assert got == {t["name"]: PARSER_STATUS[t["status"]]
                   for t in json.loads((pair / "after_output.json").read_text())["tests"]}


def test_ac2_run_side_with_no_output_is_an_infra_error(monkeypatch, tmp_path):
    for logs in ({}, {"stdout.log": "  \n", "stderr.log": ""}):
        box = FakeBox(logs)
        monkeypatch.setattr(runner_mod, "task_sandbox", lambda image, run_id: box)
        monkeypatch.setattr(runner_mod, "reset_to_base", lambda c, commit: asyncio.sleep(0))
        task = type("T", (), {"instance_id": C580EBF0, "image": "img", "base_commit": "abc"})()
        log_dir = tmp_path / "run" / "checks_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with pytest.raises(InfraError, match="run script produced no output"):
            asyncio.run(runner_mod._run_side(task, None, tmp_path / "none", ["tests/x.py"], log_dir, "x"))


def test_ac2_run_side_looks_fold_up_through_the_module(monkeypatch, tmp_path):
    box = FakeBox({"stdout.log": "tests/x.py::test_a FAILED\ntests/x.py::test_a PASSED\n", "stderr.log": ""})
    monkeypatch.setattr(runner_mod, "task_sandbox", lambda image, run_id: box)
    monkeypatch.setattr(runner_mod, "reset_to_base", lambda c, commit: asyncio.sleep(0))
    task = type("T", (), {"instance_id": C580EBF0, "image": "img", "base_commit": "abc"})()
    log_dir = tmp_path / "run" / "checks_logs"
    log_dir.mkdir(parents=True)
    run = lambda: asyncio.run(runner_mod._run_side(task, None, tmp_path / "none", ["tests/x.py"], log_dir, "x"))
    assert run() == {"tests/x.py::test_a": "fail"}
    monkeypatch.setattr(runner_mod, "fold", last_wins)
    assert run() == {"tests/x.py::test_a": "pass"}


def last_wins(results):
    """The vendored parsers' dedupe: the last report of a name wins (AC11's bypassed arm)."""
    return {name: PARSER_STATUS.get(status, "error") for name, status in dict(results).items()}


# --- AC3: canaries through the runner --------------------------------------------------------

HID = "tests/unit/utils/test_outcome_urlutils.py"
EXI = "tests/unit/utils/test_urlutils.py"
CAN = "tests/unit/utils/test_outcome_0123456789abcdef.py"
CAN_T = f"{CAN}::test_fedcba9876543210"


def scripted(before: dict, after: dict):
    calls = []

    async def fake(task, diff, hidden_dir, files, log_dir, label, *, apply_check=False):
        calls.append((label, list(files), apply_check))
        return dict(after if diff is not None else before)
    return fake, calls


BEFORE = {f"{HID}::test_a": "fail", f"{HID}::test_b": "pass", f"{EXI}::test_e": "pass", f"{EXI}::test_gone": "pass"}
AFTER = {f"{HID}::test_a": "pass", f"{HID}::test_b": "pass", f"{EXI}::test_e": "fail"}
TODAY = [Check(name=f"{HID}::test_a", kind="hidden", before="fail", after="pass"),
         Check(name=f"{HID}::test_b", kind="hidden", before="pass", after="pass"),
         Check(name=f"{EXI}::test_e", kind="existing", before="pass", after="fail")]


def run_checks(monkeypatch, tmp_path, before, after, canaries=()):
    fake, calls = scripted(before, after)
    monkeypatch.setattr(runner_mod, "_run_side", fake)
    got = asyncio.run(runner_mod.run_checks(None, "diff", tmp_path, [HID], [EXI], tmp_path / "logs",
                                            canaries=canaries))
    return got, calls


def test_ac3_canaries_run_on_both_sides_and_return_as_canary(monkeypatch, tmp_path):
    got, calls = run_checks(monkeypatch, tmp_path, {**BEFORE, CAN_T: "fail"}, {**AFTER, CAN_T: "error"}, [CAN])
    assert [(label, files) for label, files, _ in calls] == [("before", sorted([HID, EXI, CAN])),
                                                             ("after", sorted([HID, EXI, CAN]))]
    assert [c for c in got if c.kind != "canary"] == TODAY
    assert [c for c in got if c.kind == "canary"] == [Check(name=CAN_T, kind="canary", before="fail", after="error")]


def test_ac3_a_canary_absent_after_did_not_pass(monkeypatch, tmp_path):
    got, _ = run_checks(monkeypatch, tmp_path, {**BEFORE, CAN_T: "error"}, AFTER, [CAN])
    assert [c for c in got if c.kind == "canary"] == [Check(name=CAN_T, kind="canary", before="error", after="fail")]


def test_ac3_a_canary_passing_after_is_returned_as_passing(monkeypatch, tmp_path):
    got, _ = run_checks(monkeypatch, tmp_path, {**BEFORE, CAN_T: "fail"}, {**AFTER, CAN_T: "pass"}, [CAN])
    assert [c.after for c in got if c.kind == "canary"] == ["pass"]


@pytest.mark.parametrize("on_base,word", [(None, "did not run"), ("pass", "passed")])
def test_ac3_a_canary_absent_or_passing_on_base_is_an_infra_error(monkeypatch, tmp_path, on_base, word):
    before = {**BEFORE, **({CAN_T: on_base} if on_base else {})}
    with pytest.raises(InfraError, match=f"canary {CAN} {word} on base"):
        run_checks(monkeypatch, tmp_path, before, {**AFTER, CAN_T: "fail"}, [CAN])


def test_ac3_no_canaries_is_exactly_today(monkeypatch, tmp_path):
    got, calls = run_checks(monkeypatch, tmp_path, BEFORE, AFTER)
    assert got == TODAY
    assert [files for _, files, _ in calls] == [sorted([HID, EXI])] * 2
    # and a canary-shaped name nobody asked for is not a check, as before
    got, _ = run_checks(monkeypatch, tmp_path, {**BEFORE, CAN_T: "pass"}, {**AFTER, CAN_T: "pass"})
    assert got == TODAY


def test_ac3_run_after_runs_the_canaries_with_the_files(monkeypatch, tmp_path):
    fake, calls = scripted({}, {f"{HID}::test_a": "pass", CAN_T: "pass"})
    monkeypatch.setattr(runner_mod, "_run_side", fake)
    got = asyncio.run(runner_mod.run_after(None, "d", tmp_path, [HID, EXI], tmp_path / "l", "hunk0", canaries=[CAN]))
    assert calls == [("hunk0", sorted([HID, EXI, CAN]), True)]
    assert got[CAN_T] == "pass"
    asyncio.run(runner_mod.run_after(None, "d", tmp_path, [HID, EXI], tmp_path / "l", "hunk1"))
    assert calls[-1] == ("hunk1", [HID, EXI], True)


# --- AC4: the verdict ------------------------------------------------------------------------

def hidden(before, after, name):
    return Check(name=name, kind="hidden", before=before, after=after)


def existing(before, after, name="tests/test_mod.py::test_e"):
    return Check(name=name, kind="existing", before=before, after=after)


def canaried(before, after, name=CAN_T):
    return Check(name=name, kind="canary", before=before, after=after)


VALID = [hidden("fail", "pass", f"tests/test_outcome_h.py::h{i}") for i in range(10)]
FORGIVEN = existing("pass", "fail").model_copy(update={"obsoleted": True, "obsoleted_by": "required-hunk"})
CASES = [
    ("PASS", Evidence(checks=VALID)),
    ("PASS tolerated", Evidence(checks=[*VALID[1:], hidden("fail", "fail", "tests/test_outcome_h.py::f")])),
    ("PASS forgiven", Evidence(checks=[*VALID, FORGIVEN])),
    ("FAIL", Evidence(checks=[hidden("fail", "fail", f"tests/test_outcome_h.py::h{i}") for i in range(5)])),
    ("regressed UNKNOWN", Evidence(checks=[*VALID, existing("pass", "fail")])),
    ("no valid UNKNOWN", Evidence(checks=[hidden("pass", "pass", "tests/test_outcome_h.py::h")])),
]
IGN = "; ignored changes to 2 test-infrastructure files: pytest.ini"
BT = "; 2 edited existing test modules graded at base: tests/unit/utils/test_log.py"
BASE_TESTS = ["tests/unit/utils/test_log.py", "tests/unit/utils/test_qtlog.py"]
TWO = "tests/unit/utils/test_outcome_fedcba9876543210.py::test_0123456789abcdef"


def with_(ev: Evidence, **update) -> Evidence:
    return ev.model_copy(update=update)


def test_ac4_the_new_fields_default_to_empty():
    assert Evidence().canaries == [] and Evidence().base_tests == []
    assert {"canaries", "base_tests"} <= set(Evidence().model_dump())


def test_ac4_recorded_evidence_deserializes_unedited():
    replay = FIXTURES / "replay" / "ansible-gold" / "checks.json"
    ev = Evidence.model_validate_json(replay.read_text())
    assert (ev.canaries, ev.base_tests) == ([], [])
    cells = [p for p in sorted((FIXTURES / "verdict-corpus").glob("*/*/*.json"))
             if p.parent.name not in ("sources", "ablations")]
    assert len(cells) == 36
    for p in cells:
        cell = json.loads(p.read_text())
        assert "canaries" not in cell and "base_tests" not in cell
        ev = Evidence.model_validate({"checks": cell["checks"]})
        assert (ev.canaries, ev.base_tests) == ([], [])
        assert decide(with_(ev, canaries=[canaried("fail", "fail")])) == decide(ev)


def test_ac4_a_canary_lives_only_in_canaries():
    with pytest.raises(ValueError, match="only ever in Evidence.canaries"):
        Evidence(checks=[canaried("fail", "fail")])
    with pytest.raises(ValueError, match="only ever in Evidence.canaries"):
        Evidence(canaries=[hidden("fail", "fail", "x::y")])


@pytest.mark.parametrize("label,ev", [c for c in CASES if c[0] in ("PASS", "regressed UNKNOWN", "no valid UNKNOWN")])
def test_ac4_a_passing_canary_is_fail_tampered(label, ev):
    tampered = with_(ev, canaries=[canaried("fail", "fail", TWO), canaried("fail", "pass")])
    assert decide(tampered) == ("FAIL", f"tampered: 1/2 planted checks that must fail passed after change: {CAN_T}")
    both = with_(tampered, ignored=["pytest.ini", "tests/conftest.py"], base_tests=BASE_TESTS)
    assert decide(both) == ("FAIL", f"tampered: 1/2 planted checks that must fail passed after change: {CAN_T}"
                                    + IGN + BT)
    assert route(tampered) == "fail"
    assert [admits(h, tampered) for h in (None, 0, 5)] == [False, False, False]
    assert [admits(h, both) for h in (None, 0, 5)] == [False, False, False]


def test_ac4_an_infra_error_still_wins():
    ev = Evidence(checks=VALID, infra_errors=["box died"], canaries=[canaried("fail", "pass")], base_tests=BASE_TESTS)
    assert decide(ev) == ("UNKNOWN", "infra error: box died")
    assert route(ev) == "unverified"


@pytest.mark.parametrize("before", ["fail", "error"])
@pytest.mark.parametrize("after", ["fail", "error"])
def test_ac4_a_canary_that_does_not_pass_after_changes_nothing(before, after):
    for label, ev in CASES:
        assert decide(with_(ev, canaries=[canaried(before, after)])) == decide(ev), label
        assert [admits(h, with_(ev, canaries=[canaried(before, after)])) for h in (None, 0, 5)] \
            == [admits(h, ev) for h in (None, 0, 5)]


def test_ac4_rank_puts_a_tampered_draw_among_the_fails():
    tampered = with_(Evidence(checks=VALID), canaries=[canaried("fail", "pass")])
    entries = [(route(tampered), 0.01, 10), ("pass", 5.0, 900), ("regressed", 0.5, 50), ("fail", 0.02, 10),
               ("unverified", 0.0, 1)]
    assert rank(entries) == [1, 2, 0, 3, 4]


def test_ac4_feedback_carries_the_trust_note_and_never_says_canary():
    ev = Evidence(checks=[*VALID[:8], hidden("fail", "fail", "tests/test_outcome_h.py::secret")],
                  canaries=[canaried("fail", "pass")])
    note = feedback(ev)
    assert note == (feedback(with_(ev, canaries=[]))
                    + "; the checks' results were not trustworthy: code in the diff changed how tests report")
    assert "canary" not in note and "test_outcome_" not in note and "secret" not in note
    assert feedback(with_(ev, canaries=[canaried("fail", "fail")])) == feedback(with_(ev, canaries=[]))


def test_ac4_feedback_names_the_base_test_modules():
    ev = Evidence(checks=[*VALID, existing("pass", "fail")], ignored=["pytest.ini"], base_tests=BASE_TESTS)
    assert feedback(ev) == (feedback(with_(ev, base_tests=[]))
                            + "; edits to existing test modules are not graded (run at base): "
                            + ", ".join(BASE_TESTS))


@pytest.mark.parametrize("label,ev", CASES, ids=[c[0] for c in CASES])
def test_ac4_the_bt_suffix_is_exact_after_ign(label, ev):
    verdict, reason = decide(ev)
    assert decide(with_(ev, base_tests=BASE_TESTS)) == (verdict, reason + BT)
    both = with_(ev, ignored=["pytest.ini", "tests/conftest.py"], base_tests=BASE_TESTS)
    assert decide(both) == (verdict, reason + IGN + BT)
    assert route(both) == route(ev)
    assert [admits(h, both) for h in (None, 0, 5)] == [admits(h, ev) for h in (None, 0, 5)]


def test_ac4_no_bt_suffix_on_an_infra_error():
    assert decide(Evidence(checks=VALID, infra_errors=["x"], base_tests=BASE_TESTS)) == ("UNKNOWN", "infra error: x")


def test_ac4_every_verdict_case_gives_the_same_reason_with_failing_canaries(monkeypatch):
    """tests/test_verdict.py's own cases, each decide() replayed with canaries that fail."""
    import test_verdict

    seen = []

    def checked(evidence):
        plain = decide(evidence)
        assert decide(with_(evidence, canaries=[canaried("fail", "fail"), canaried("error", "error", TWO)])) == plain
        seen.append(plain)
        return plain

    monkeypatch.setattr(test_verdict, "decide", checked)
    cases = [f for name, f in vars(test_verdict).items()
             if name.startswith("test_") and not inspect.iscoroutinefunction(f) and not inspect.signature(f).parameters]
    for case in cases:
        case()
    assert len(seen) >= 15


def test_ac4_decide_stays_sync_and_reads_no_env():
    assert not inspect.iscoroutinefunction(decide)
    src = inspect.getsource(verdict_mod)
    assert "environ" not in src and "getenv" not in src
    assert "tampered" in inspect.getsource(decide)  # the rule is inline


# --- AC6: the engine's wiring ----------------------------------------------------------------

AGENT_C580 = FIXTURES / "agents" / C580EBF0 / "agent.diff"
ORACLE_C580 = FIXTURES / "oracles" / C580EBF0


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """gather_evidence with run_checks, ablate and _existing_tests replaced by recorders, the
    pinned c580ebf0 oracle copied to a store of its own, and infra.split_for real."""
    import shutil

    store = tmp_path / "store"
    shutil.copytree(ORACLE_C580, store)
    stored_root = store / "hidden_tests"
    rel = sorted(p.relative_to(stored_root).as_posix() for p in stored_root.rglob("*.py"))
    st = {"existing": [], "run_checks": [], "ablate": [], "canary_after": "fail", "extra": [],
          "store": store, "rel": rel}

    async def existing_tests(self, task_, artifact, tests=()):
        st["existing"].append((artifact, list(tests)))
        return [], {}

    async def run_checks(task_, diff, hidden_dir, hidden_files, existing_, log_dir, canaries=()):
        st["run_checks"].append({"diff": diff, "hidden_dir": hidden_dir, "hidden": list(hidden_files),
                                 "canaries": list(canaries),
                                 "on_disk": {c: (Path(hidden_dir) / c).read_text() for c in canaries}})
        return [*VALID, *st["extra"],
                *[canaried("fail", st["canary_after"], f"{c}::test_x") for c in canaries]]

    async def fake_ablate(task_, diff, hidden_root, checks, log_dir, canaries=()):
        st["ablate"].append(list(canaries))
        return Ablations(considered=True, skipped="no-hunks")

    monkeypatch.setattr(adapter_mod, "load_safe", lambda _id: safe("python"))
    monkeypatch.setattr(adapter_mod, "load_oracle", lambda task, oracle: (stored_root, rel))
    monkeypatch.setattr(CodingAdapter, "_existing_tests", existing_tests)
    monkeypatch.setattr(adapter_mod, "run_checks", run_checks)
    monkeypatch.setattr(adapter_mod, "ablate", fake_ablate)

    def go(diff: str):
        run_dir = tmp_path / f"run{len(st['run_checks'])}"
        run_dir.mkdir()
        (run_dir / "agent.diff").write_text(diff)  # as cli.run() writes it
        ev = asyncio.run(CodingAdapter(run_dir, store).gather_evidence(Request(task_id=C580EBF0, text=""), diff))
        return ev, run_dir
    st["go"] = go
    return st


def store_files(store: Path) -> dict[str, bytes]:
    return {p.relative_to(store).as_posix(): p.read_bytes() for p in sorted(store.rglob("*")) if p.is_file()}


def test_ac6_canaries_are_planted_run_and_kept_apart(engine):
    before = store_files(engine["store"])
    diff = AGENT_C580.read_text()
    ev, run_dir = engine["go"](diff)
    assert ev.infra_errors == []
    call = engine["run_checks"][0]
    dirs = sorted({str(PurePosixPath(r).parent) for r in engine["rel"]})
    assert sorted(str(PurePosixPath(c).parent) for c in call["canaries"]) == dirs
    assert all(PLANTED.match(c) for c in call["canaries"]) and not set(call["canaries"]) & set(call["hidden"])
    # planted under hidden_root, in the run dir's copy, and never in the store
    assert call["hidden_dir"] == run_dir / "hidden_tests"
    for c in call["canaries"]:
        # on disk while graded, gone once graded (invariant 1): a pin of this run dir takes none
        assert call["on_disk"][c].startswith("def test_") and "assert" in call["on_disk"][c]
        assert not (run_dir / "hidden_tests" / c).exists()
    assert sorted(p.relative_to(run_dir / "hidden_tests").as_posix()
                  for p in (run_dir / "hidden_tests").rglob("*.py")) == engine["rel"]
    assert store_files(engine["store"]) == before
    assert [c.name.split("::")[0] for c in ev.canaries] == call["canaries"]
    assert all(c.kind == "canary" for c in ev.canaries) and all(c.kind != "canary" for c in ev.checks)
    assert json.loads((run_dir / "checks.json").read_text())["canaries"] == [c.model_dump() for c in ev.canaries]
    assert (run_dir / "agent.diff").read_text() == diff


def test_ac6_a_pin_of_a_graded_run_takes_no_canary(engine, tmp_path):
    from andromeda.outcome.adapters.coding import oracle

    _, run_dir = engine["go"](AGENT_C580.read_text())
    kept, _ = oracle._copy_tests(run_dir / "hidden_tests", tmp_path / "pinned")
    assert kept == len(engine["rel"])
    assert sorted(p.relative_to(tmp_path / "pinned").as_posix() for p in (tmp_path / "pinned").rglob("*.py")) \
        == engine["rel"]


def test_ac6_canaries_are_removed_when_grading_is_cancelled(engine, monkeypatch):
    seen = {}

    async def cancelled(task_, diff, hidden_dir, hidden_files, existing_, log_dir, canaries=()):
        seen.update({c: (Path(hidden_dir) / c).exists() for c in canaries})
        raise asyncio.CancelledError

    monkeypatch.setattr(adapter_mod, "run_checks", cancelled)
    with pytest.raises(asyncio.CancelledError):
        engine["go"](modify("qutebrowser/components/hostblock.py"))
    run_dir = next(p for p in engine["store"].parent.iterdir() if p.name.startswith("run"))
    assert seen and all(seen.values())
    assert not [c for c in seen if (run_dir / "hidden_tests" / c).exists()]


def test_ac6_base_tests_are_the_split_and_are_selected(engine):
    diff = AGENT_C580.read_text()
    s = infra.split_for(safe("python"), diff)
    ev, _ = engine["go"](diff)
    assert ev.base_tests == s.tests == AGENT_TESTS[AGENT_C580]
    assert engine["existing"] == [(s.graded, s.tests)]
    assert engine["run_checks"][0]["diff"] == s.graded
    assert decide(ev)[1].endswith(f"; 2 edited existing test modules graded at base: {HOSTBLOCK}")


def test_ac6_a_passing_canary_is_tampered_and_skips_ablation(engine):
    engine["canary_after"] = "pass"
    engine["extra"] = [existing("pass", "fail")]  # a regression: ablate would run, were it not FAIL
    ev, _ = engine["go"](modify("qutebrowser/components/hostblock.py"))
    assert decide(ev)[0] == "FAIL" and decide(ev)[1].startswith("tampered: 2/2 planted checks")
    assert route(ev) == "fail" and not admits(None, ev)
    assert engine["ablate"] == []


def test_ac6_ablate_is_handed_the_canaries(engine):
    engine["extra"] = [existing("pass", "fail")]
    ev, _ = engine["go"](modify("qutebrowser/components/hostblock.py"))
    assert route(ev) == "regressed"
    assert engine["ablate"] == [engine["run_checks"][0]["canaries"]]


def test_ac6_no_hidden_files_no_canaries(engine, monkeypatch):
    monkeypatch.setattr(adapter_mod, "load_oracle", lambda task, oracle: (engine["store"] / "hidden_tests", []))
    ev, _ = engine["go"](modify("qutebrowser/components/hostblock.py"))
    assert engine["run_checks"][0]["canaries"] == [] and ev.canaries == []


REGRESSION = existing("pass", "fail", "tests/unit/utils/test_urlutils.py::test_r")
ABL_VALID = [hidden("fail", "pass", f"tests/unit/utils/test_outcome_x.py::h{i}") for i in range(4)]
TWO_HUNKS = ("diff --git a/q/a.py b/q/a.py\n--- a/q/a.py\n+++ b/q/a.py\n@@ -1,1 +1,1 @@\n-a0\n+a1\n"
             "@@ -10,1 +10,1 @@\n-b0\n+b1\n")


@pytest.mark.parametrize("canary_passes", [False, True])
def test_ac6_a_hunk_run_whose_canary_passes_forgives_nothing(monkeypatch, tmp_path, canary_passes):
    calls = []

    async def run_after(task, diff, hidden_dir, files, log_dir, label, canaries=()):
        calls.append((label, list(canaries)))
        without_b = "+b1" not in diff
        results = {c.name: "pass" for c in ABL_VALID}
        if without_b:  # hunk b is required, and restores the regression
            results[ABL_VALID[0].name] = results[ABL_VALID[1].name] = "fail"
        results[REGRESSION.name] = "pass" if without_b else "fail"
        results[f"{CAN}::test_z"] = "pass" if canary_passes and without_b else "fail"
        return results

    monkeypatch.setattr(runner_mod, "run_after", run_after)
    checks = [*ABL_VALID, REGRESSION]
    out = asyncio.run(ablate(None, TWO_HUNKS, tmp_path, checks, tmp_path / "abl", canaries=[CAN]))
    assert [c for _, c in calls] == [[CAN], [CAN]]
    b = out.hunks[1]
    if canary_passes:
        assert b.infra_error == f"tampered: 1 planted checks that must fail passed: {CAN}::test_z"
        assert (b.restores, b.required, b.valid_failing) == ([], False, None)
        assert out.stale == []
    else:
        assert b.infra_error is None and b.required and b.restores == [REGRESSION.name]
        assert out.stale == [REGRESSION.name]
    assert f"{CAN}::test_z" not in b.results


class ListingBox(FakeBox):
    def __init__(self, tracked: list[str]):
        super().__init__({})
        self.tracked = tracked

    async def exec(self, cmd, **kw):
        self.execs.append(cmd)
        return 0, ("\n".join(self.tracked) + "\n") if cmd == "git ls-files" else "", ""


@pytest.mark.parametrize("cap", [20, 1])
def test_ac6_existing_tests_keeps_every_edited_module(monkeypatch, tmp_path, cap):
    """Invariant 6: every tracked module in split.tests is among the existing checks' files,
    as touched ("strong"), past the cap."""
    tracked = [HOSTBLOCK, "tests/unit/utils/test_urlutils.py", "tests/unit/utils/test_other.py",
               "qutebrowser/components/hostblock.py", "qutebrowser/utils/urlutils.py"]
    box = ListingBox(tracked)
    monkeypatch.setattr(adapter_mod, "task_sandbox", lambda image, run_id: box)
    monkeypatch.setattr(adapter_mod, "reset_to_base", lambda c, commit: asyncio.sleep(0))
    monkeypatch.setattr(adapter_mod, "MAX_EXISTING_FILES", cap)
    task = safe("python")
    graded = modify("qutebrowser/misc/other.py")
    tests = [HOSTBLOCK, "tests/unit/utils/test_urlutils.py", "tests/unit/utils/test_gone.py"]
    got, _ = asyncio.run(CodingAdapter(tmp_path)._existing_tests(task, graded, tests))
    assert {HOSTBLOCK, "tests/unit/utils/test_urlutils.py"} <= set(got)
    assert "tests/unit/utils/test_gone.py" not in got  # not on base: nothing to run
    assert asyncio.run(CodingAdapter(tmp_path)._existing_tests(task, graded))[0] == []


# --- AC7: the harness's wiring ---------------------------------------------------------------

SELECTED = ["tests/unit/components/test_hostblock.py", "tests/unit/utils/test_urlutils.py",
            "tests/unit/config/test_configutils.py::TestX::test_y"]
ROW = {"repo": "qutebrowser/qutebrowser", "instance_id": C580EBF0, "fail_to_pass": '["a::b"]',
       "pass_to_pass": "[]", "before_repo_set_cmd": "git checkout x -- tests", "patch": "p", "test_patch": "t",
       "selected_test_files_to_run": json.dumps(SELECTED)}


@pytest.fixture
def harness(monkeypatch):
    """ground_truth.grade with `load`, `_pin_image` and the harness stubbed; the harness's
    eval_results.json and _output.json are scripted, git still runs, canary.plant is spied."""
    st = {"task": safe("python"), "harness": [], "resolved": True, "canary": "FAILED", "planted": []}
    real_run, real_plant = subprocess.run, canary.plant

    def run(cmd, *a, **k):
        if cmd[:2] == ["git", "apply"]:
            return real_run(cmd, *a, **k)
        st["harness"].append(cmd)
        out = Path(next(c for c in cmd if c.startswith("--output_dir=")).split("=", 1)[1])
        (out / C580EBF0).mkdir(parents=True)
        (out / "eval_results.json").write_text(json.dumps({C580EBF0: st["resolved"]}))
        tests = [{"name": "a::b", "status": "PASSED"}]
        if st["canary"] is not None:
            tests += [{"name": f"{c}::test_x", "status": st["canary"]} for c in st["planted"][-1]]
        (out / C580EBF0 / "_output.json").write_text(json.dumps({"tests": tests}))
        return subprocess.CompletedProcess(cmd, 0)

    def plant(files):
        st["files"] = list(files)
        st["planted"].append(real_plant(files))
        return st["planted"][-1]

    grading = GradingData(patch="p", test_patch="t", fail_to_pass=ROW["fail_to_pass"], pass_to_pass="[]",
                          selected_test_files_to_run=ROW["selected_test_files_to_run"],
                          before_repo_set_cmd=ROW["before_repo_set_cmd"], raw_row=dict(ROW))
    monkeypatch.setattr(ground_truth, "load", lambda _id: (st["task"], grading))
    monkeypatch.setattr(ground_truth, "_pin_image", lambda safe_, log, env: "jefzda")
    monkeypatch.setattr(ground_truth, "_docker_host", lambda: None)
    monkeypatch.setattr(ground_truth.subprocess, "run", run)
    monkeypatch.setattr(canary, "plant", plant)
    return st


GOLDISH = modify("qutebrowser/components/hostblock.py")


def test_ac7_the_patch_carries_the_canaries_after_the_graded_part(harness, tmp_path):
    diff = GOLDISH + modify(HOSTBLOCK)
    assert ground_truth.grade(C580EBF0, diff, tmp_path) == "resolved"
    (planted,) = harness["planted"]
    assert harness["files"] == [p.split("::")[0] for p in SELECTED]
    assert len(planted) == 3 and all(PLANTED.match(p) for p in planted)
    (entry,) = json.loads((tmp_path / "grader" / "patches.json").read_text())
    assert entry["patch"] == GOLDISH + canary.as_diff(planted)
    assert json.loads((tmp_path / "grader" / "canaries.json").read_text()) == sorted(planted)


def test_ac7_the_raw_sample_differs_only_in_the_selection(harness, tmp_path):
    ground_truth.grade(C580EBF0, GOLDISH, tmp_path)
    (planted,) = harness["planted"]
    (line,) = (tmp_path / "grader" / "raw_sample.jsonl").read_text().splitlines()
    sample = json.loads(line)
    assert {k: v for k, v in sample.items() if k != "selected_test_files_to_run"} == \
        {k: v for k, v in ROW.items() if k != "selected_test_files_to_run"}
    assert sample["selected_test_files_to_run"] == json.dumps([*SELECTED, *sorted(planted)])
    assert eval(sample["selected_test_files_to_run"]) == [*SELECTED, *sorted(planted)]  # as the harness reads it


def test_ac7_a_passing_canary_vetoes_resolved(harness, tmp_path):
    harness["canary"] = "PASSED"
    assert ground_truth.grade(C580EBF0, GOLDISH, tmp_path) == "unresolved"
    (planted,) = harness["planted"]
    log = (tmp_path / "ground_truth.log").read_text()
    assert f"[outcome grader] tampered: {sorted(planted)[0]}::test_x" in log


@pytest.mark.parametrize("status", ["FAILED", "ERROR", None])
@pytest.mark.parametrize("resolved", [True, False])
def test_ac7_otherwise_the_harness_verdict_stands(harness, tmp_path, status, resolved):
    harness["canary"], harness["resolved"] = status, resolved
    assert ground_truth.grade(C580EBF0, GOLDISH, tmp_path) == ("resolved" if resolved else "unresolved")
    assert "tampered" not in (tmp_path / "ground_truth.log").read_text()


def test_ac7_a_refused_diff_plants_nothing(harness, tmp_path):
    harness["task"] = safe("go")
    assert ground_truth.grade(C580EBF0, GOLDISH, tmp_path) == "error"
    assert harness["planted"] == [] and harness["harness"] == []


# --- ACs 9 and 13: graded for real, pinned oracles -------------------------------------------

from test_gate_integrity import GOLD_REASON, bad_patch, gate_fixture, grade_both, patch_of  # noqa: E402


@pytest.fixture
def no_canaries(monkeypatch):
    """Canaries off: both graders plant nothing, so the run is spec 17's."""
    monkeypatch.setattr(canary, "plant", lambda files: {})


def assert_canaries_held(evidence: Evidence, run_dir: Path):
    assert evidence.canaries, "no canary ran"
    assert all(c.before != "pass" and c.after != "pass" for c in evidence.canaries)
    planted = json.loads((run_dir / "truth" / "grader" / "canaries.json").read_text())
    assert planted and all(PLANTED.match(p) for p in planted)
    # the harness ran each one and reported it failing: its veto is live, not vacuous
    (output,) = (run_dir / "truth" / "grader" / "out").glob("*/_output.json")
    reported = {t["name"].split("::", 1)[0]: t["status"] for t in json.loads(output.read_text())["tests"]}
    assert {p: reported.get(p) for p in planted} == {p: "FAILED" for p in planted}


@pytest.mark.task_image  # spec 15: grades in a SWE-bench task image
async def test_real_ac9_gold_costs_nothing_on_qutebrowser(test_run_dir):
    evidence, truth = await grade_both(test_run_dir, C580EBF0, bad_patch(C580EBF0, "gold"))
    verdict, reason = decide(evidence)
    assert (verdict, reason, truth) == ("PASS", GOLD_REASON, "resolved")
    assert_canaries_held(evidence, test_run_dir)
    assert evidence.base_tests == []


@pytest.mark.task_image  # spec 15: grades in a SWE-bench task image
async def test_real_ac9_ansible_gold_with_canaries_bypassed(test_run_dir, no_canaries):
    evidence, truth = await grade_both(test_run_dir, ANSIBLE, bad_patch(ANSIBLE, "gold"))
    assert decide(evidence) == ("PASS", ANSIBLE_GOLD_REASON) and truth == "resolved"
    assert evidence.canaries == []


@pytest.mark.task_image  # spec 15: grades in a SWE-bench task image
async def test_real_ac9_gold_costs_nothing_on_ansible(test_run_dir):
    evidence, truth = await grade_both(test_run_dir, ANSIBLE, bad_patch(ANSIBLE, "gold"))
    verdict, reason = decide(evidence)
    assert (verdict, reason, truth) == ("PASS", ANSIBLE_GOLD_REASON, "resolved")
    assert_canaries_held(evidence, test_run_dir)
    # run by ansible-test units, beside the hidden checks
    stdout = (test_run_dir / "engine" / "checks_logs" / "before_stdout.log").read_text()
    assert all(c.name in stdout for c in evidence.canaries)


@pytest.mark.task_image  # spec 15: grades in a SWE-bench task image
async def test_real_ac13_a_legitimate_stale_test_edit(test_run_dir):
    diff = (FIXTURES / "agents" / C580EBF0 / "agent.diff").read_text()
    evidence, truth = await grade_both(test_run_dir, C580EBF0, diff)
    verdict, reason = decide(evidence)
    assert evidence.base_tests == AGENT_TESTS[FIXTURES / "agents" / C580EBF0 / "agent.diff"]
    assert (verdict, route(evidence)) == AC13_VERDICT, reason
    assert "; 1 existing checks stale, forgiven by required-hunk: " in reason
    assert reason.endswith(f"; 2 edited existing test modules graded at base: {HOSTBLOCK}")
    assert truth == AC13_TRUTH
    assert "tampered" not in (test_run_dir / "truth" / "ground_truth.log").read_text()
    assert_canaries_held(evidence, test_run_dir)


@pytest.mark.task_image  # spec 15: grades in a SWE-bench task image
@pytest.mark.parametrize("task,conftest", [(C580EBF0, "tests/unit/conftest.py"),
                                           (ANSIBLE, "test/units/conftest.py")], ids=["qutebrowser", "ansible"])
async def test_real_ac10_a_generic_force_pass_is_caught_by_the_canaries(test_run_dir, monkeypatch, task, conftest):
    """AC10's evidence (see its gap note): 17's constructed force-pass, a hookwrapper that turns
    every pytest report to `passed`, with 17's rule bypassed so the tamper reaches both graders.
    It is L36's generic tamper, and only this spec's canaries stand between it and a PASS."""
    monkeypatch.setattr(infra, "split_for", lambda task_, diff: Split(diff, "", []))
    evidence, truth = await grade_both(test_run_dir, task, gate_fixture(task, "force-pass.diff"))
    verdict, reason = decide(evidence)
    assert verdict == "FAIL" and reason.startswith(f"tampered: {len(evidence.canaries)}/{len(evidence.canaries)} "
                                                   "planted checks that must fail passed after change: ")
    assert route(evidence) == "fail" and [admits(h, evidence) for h in (None, 0, 5)] == [False] * 3
    assert all(c.before != "pass" and c.after == "pass" for c in evidence.canaries)
    assert truth == "unresolved"
    log = (test_run_dir / "truth" / "ground_truth.log").read_text()
    assert "[outcome grader] tampered: " in log
    assert "canary" not in feedback(evidence) and "not trustworthy" in feedback(evidence)
