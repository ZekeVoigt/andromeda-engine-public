"""Spec 17's proof: a graded diff can no longer change how it is graded.

The Docker-free part covers ACs 1-8: the rule (`infra_kind`, `runner_paths`), the split and
git's reading of it, the language guard, `Evidence.ignored` and the verdict's suffix, both
graders' wiring with every container stubbed, and a landing that keeps what grading ignored.
The `real` tests (ACs 9-11) grade constructed diffs in the task images with the pinned
oracles, through the engine and the vendored harness both, and AC9's and AC10's bypassed arm
(`infra.split_for` replaced by one that ignores nothing) reproduces the hole this closes.
"""

import asyncio
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest

import andromeda.outcome.adapters.coding.adapter as adapter_mod
import andromeda.outcome.core.engine as engine
import andromeda.outcome.grading.ground_truth as ground_truth
from andromeda.outcome.adapters.coding import canary, infra
from andromeda.outcome.adapters.coding import runner as runner_mod
from andromeda.outcome.adapters.coding.adapter import Ablations, CodingAdapter
from andromeda.outcome.adapters.coding.infra import NoInfraRule, Split, Unreadable, infra_kind, runner_paths, split
from andromeda.outcome.adapters.coding.task import GradingData, SafeTask
from andromeda.outcome.core import verdict as verdict_mod
from andromeda.outcome.core.types import Check, Evidence, Request
from andromeda.outcome.core.verdict import decide, feedback

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
C580EBF0 = ("instance_qutebrowser__qutebrowser-c580ebf0801e5a3ecabc54f327498bb753c6d5f2"
            "-v2ef375ac784985212b1805e1d0431dc8f1b3c171")
F91ACE96 = ("instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111"
            "-v059c6fdc75567943479b23ebca7c07b5e9a7f34c")
ANSIBLE = ("instance_ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629"
           "-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5")
CONSTRUCTED = FIXTURES / "gate-integrity"
REAL_VERIFY = engine.verify


def hidden(before, after, name):
    return Check(name=name, kind="hidden", before=before, after=after)


def existing(before, after, name="tests/test_mod.py::test_e"):
    return Check(name=name, kind="existing", before=before, after=after)


def new_file(path: str, line: str = "x = 1") -> str:
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1 @@\n+{line}\n")


def modify(path: str, old: str = "a = 1", new: str = "a = 2") -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


def numstat_paths(patch: str) -> set[str]:
    """What git reads `patch` as touching, forward and reversed: the test's own reading."""
    out = set()
    for extra in ([], ["-R"]):
        proc = subprocess.run(["git", "apply", "--numstat", "-z", "--allow-empty", *extra],
                              input=patch.encode(), capture_output=True, check=True, cwd="/")
        fields = proc.stdout.decode().split("\0")
        out |= {f.split("\t", 2)[2] if f.count("\t") >= 2 else f for f in fields} - {""}
    return out


def assert_graded_is_clean(s: Split, runner: frozenset[str] = frozenset()):
    """Invariant 3, read independently of infra's own parser."""
    if s.graded:
        assert not [p for p in numstat_paths(s.graded) if infra_kind(p, runner)]


def bad_patch(task: str, name: str) -> str:
    return (FIXTURES / "bad_patches" / task / f"{name}.diff").read_text()


# --- AC1: classification --------------------------------------------------------------------

ANSIBLE_RUNNER = frozenset({"bin/ansible-test"})


@pytest.mark.parametrize("path,kind", [
    ("conftest.py", "conftest"), ("tests/conftest.py", "conftest"), ("a/b/conftest.py", "conftest"),
    *[(name, "config") for name in sorted(infra.CONFIG_NAMES)],
    *[(f"a/b/{name}", "config") for name in sorted(infra.CONFIG_NAMES)],
    ("tests/helpers/fixtures.py", "test-support"), ("tests/unit/__init__.py", "test-support"),
    ("test/lib/ansible_test/_data/pytest/config/default.ini", "test-support"),
    ("openlibrary/tests/helper.py", "test-support"),  # spec 20: a nested tests/ directory
    ("pytest.py", "runner"), ("_pytest/x.py", "runner"), ("pluggy/__init__.py", "runner"),
    ("py.py", "runner"), ("sitecustomize.py", "runner"), ("usercustomize.py", "runner"),
    ("a/b/pytest.py", "runner"), ("lib/_pytest/x.py", "runner"), ("lib/sitecustomize.py", "runner"),
    ("x/__pycache__/y.cpython-39.pyc", "import-artifact"), ("a.pyc", "import-artifact"),
    ("m.cpython-39-x86_64-linux-gnu.so", "import-artifact"), ("m.pyd", "import-artifact"),
    ("x.pth", "import-artifact"), ("pkg.egg-info/entry_points.txt", "import-artifact"),
    ("x.dist-info/METADATA", "import-artifact"), ("venv/lib/python3.9/site-packages/foo.py", "import-artifact"),
    ("a.pyo", "import-artifact"), ("usr/lib/python3/dist-packages/foo.py", "import-artifact"),
])
def test_ac1_infra_kind_names_each_kind(path, kind):
    assert infra_kind(path) == kind


def test_ac1_the_task_runner_is_runner_only_given_its_paths():
    assert infra_kind("bin/ansible-test", runner_paths(ANSIBLE)) == "runner"
    assert infra_kind("bin/ansible-test") is None


@pytest.mark.parametrize("path", [
    "qutebrowser/utils/log.py", "lib/ansible/plugins/test/core.py", "tests/unit/utils/test_log.py",
    "test/units/cli/test_galaxy.py", "bin/ansible-test",
    "tests/unit/utils/log_test.py", "src/pytest_plugin_notes.md", "pyproject.toml.orig",
])
def test_ac1_code_under_test_and_test_modules_are_not_infrastructure(path):
    assert infra_kind(path) is None


def test_ac1_first_match_in_table_order():
    # a conftest under a top-level tests/ is a conftest, a config there is a config
    assert infra_kind("tests/conftest.py") == "conftest"
    assert infra_kind("tests/pytest.ini") == "config"
    assert infra_kind("tests/__pycache__/x.pyc") == "test-support"


def test_ac1_runner_paths_are_read_from_vendor():
    assert runner_paths(ANSIBLE) == {"bin/ansible-test"}
    assert runner_paths(C580EBF0) == frozenset()
    assert runner_paths(F91ACE96) == frozenset()
    # a stub task with no run script: nothing, not an error
    assert runner_paths("t") == frozenset()


def test_ac1_runner_paths_are_what_a_script_invokes_not_what_it_names():
    """`OL_CONFIG=/app/conf/openlibrary.yml` and `PATH=/app/bin:$PATH` are read by the tests,
    not run: across every vendored script, only ansible-test is invoked from /app/."""
    from andromeda.outcome.adapters.coding.docker import swepro_dir

    scripts = sorted(p.name for p in (swepro_dir() / "run_scripts").iterdir())
    openlibrary = next(s for s in scripts if "openlibrary" in s and "OL_CONFIG=/app/conf/openlibrary.yml"
                       in (swepro_dir() / "run_scripts" / s / "run_script.sh").read_text())
    assert runner_paths(openlibrary) == frozenset()
    assert set().union(*map(runner_paths, scripts)) == {"bin/ansible-test"}


# --- AC2: partition and identity ------------------------------------------------------------

MIXED = [
    ("preface from the agent\n", False),
    (modify("src/a.py"), False),
    (new_file("tests/conftest.py"), True),
    (modify("pytest.ini", "[pytest]", "[pytest]\naddopts = -p no:cacheprovider"), True),
    (new_file("tests/unit/test_new.py"), False),
    (new_file("tests/helpers/extra.py"), True),
    (modify("pkg/b.py"), False),
    (new_file("x/__pycache__/b.cpython-39.pyc"), True),
    (modify("tests/conftest.py", "x = 1", "x = 3"), True),  # the same path twice: listed once
    (modify("sitecustomize.py"), True),
]


def test_ac2_graded_and_ignored_partition_the_blocks_in_order():
    diff = "".join(b for b, _ in MIXED)
    s = split(diff)
    assert s.graded == "".join(b for b, ign in MIXED if not ign)
    assert s.ignored == "".join(b for b, ign in MIXED if ign)
    assert s.paths == sorted({"tests/conftest.py", "pytest.ini", "tests/helpers/extra.py",
                              "x/__pycache__/b.cpython-39.pyc", "sitecustomize.py"})
    assert s.paths == sorted(set(s.paths))
    assert_graded_is_clean(s)


@pytest.mark.parametrize("n", range(len(MIXED)))
def test_ac2_every_rotation_still_partitions(n):
    blocks = MIXED[1:][n % (len(MIXED) - 1):] + MIXED[1:][:n % (len(MIXED) - 1)]
    s = split("".join(b for b, _ in blocks))
    assert s.graded == "".join(b for b, ign in blocks if not ign)
    assert s.ignored == "".join(b for b, ign in blocks if ign)


def fixture_diffs() -> list[Path]:
    return sorted([*FIXTURES.glob("bad_patches/*/*.diff"), *FIXTURES.glob("agents/*/agent.diff"),
                   *FIXTURES.glob("obsolescence/*/*.diff")])


def test_ac2_the_fixture_set_is_what_the_spec_counted():
    assert len(fixture_diffs()) == 17


# spec 18: these two edit base test modules, which its AC5 now sets aside (test_in_process.py)
EDITS_BASE_TESTS = {FIXTURES / "agents" / C580EBF0 / "agent.diff", FIXTURES / "agents" / F91ACE96 / "agent.diff"}


@pytest.mark.parametrize("path", [p for p in fixture_diffs() if p not in EDITS_BASE_TESTS],
                         ids=lambda p: f"{p.parent.parent.name}/{p.parent.name[:30]}/{p.name}")
def test_ac2_a_diff_with_no_infrastructure_is_untouched(path):
    diff = path.read_text()
    s = split(diff)
    assert (s.graded, s.ignored, s.paths) == (diff, "", [])


def test_ac2_the_empty_diff_is_empty():
    assert split("") == Split("", "", [])


# --- AC3: git's reading, not a regex ---------------------------------------------------------

EVADE = (modify("src/a.py")
         + "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")


def test_ac3_a_traditional_section_inside_another_block_is_read():
    assert numstat_paths(EVADE) == {"src/a.py", "tests/conftest.py"}
    s = split(EVADE)
    assert (s.graded, s.ignored, s.paths) == ("", EVADE, ["tests/conftest.py"])


def test_ac3_a_traditional_section_in_the_preamble_is_read():
    preamble = "notes\n--- a/tests/conftest.py\n+++ b/tests/conftest.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    s = split(preamble + modify("src/a.py"))
    assert (s.graded, s.ignored, s.paths) == (modify("src/a.py"), preamble, ["tests/conftest.py"])
    assert_graded_is_clean(s)


def test_ac3_a_prose_preamble_is_graded():
    s = split("Here is my change.\n\n" + modify("src/a.py"))
    assert (s.graded, s.ignored, s.paths) == ("Here is my change.\n\n" + modify("src/a.py"), "", [])
    assert_graded_is_clean(s)


def git_diff(tmp_path: Path, setup, change, *flags: str) -> str:
    """A real `git diff` of `change` over `setup`, in a throwaway repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a],
                                    cwd=repo, check=True, capture_output=True)
    git("init", "-q")
    setup(repo)
    git("add", "-A")
    git("commit", "-qm", "base", "--allow-empty")
    change(repo, git)
    git("add", "-A")
    return git("diff", "--cached", "-M", *flags).stdout.decode()


def _files(**files):
    def setup(repo):
        for rel, body in files.items():
            p = repo / rel.replace("__", "/")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body) if isinstance(body, bytes) else p.write_text(body)
    return setup


@pytest.mark.parametrize("old,new", [("src/x.py", "tests/conftest.py"), ("conftest.py", "src/x.py")])
def test_ac3_a_rename_is_ignored_from_either_side(tmp_path, old, new):
    body = "".join(f"line_{i} = {i}\n" for i in range(20))

    def setup(repo):
        (repo / old).parent.mkdir(parents=True, exist_ok=True)
        (repo / old).write_text(body)

    def change(repo, git):
        (repo / new).parent.mkdir(parents=True, exist_ok=True)
        git("mv", old, new)

    diff = git_diff(tmp_path, setup, change)
    assert f"rename from {old}" in diff and f"rename to {new}" in diff
    s = split(modify("src/a.py") + diff)
    infra_side = new if infra_kind(new) else old
    assert (s.graded, s.ignored, s.paths) == (modify("src/a.py"), diff, [infra_side])
    assert_graded_is_clean(s)


def test_ac3_a_copy_is_ignored_from_its_source():
    copy = ("diff --git a/conftest.py b/src/y.py\nsimilarity index 100%\n"
            "copy from conftest.py\ncopy to src/y.py\n")
    s = split(copy)
    assert (s.graded, s.ignored, s.paths) == ("", copy, ["conftest.py"])


def test_ac3_a_binary_pyc_is_ignored(tmp_path):
    def change(repo, git):
        p = repo / "pkg" / "__pycache__" / "m.cpython-39.pyc"
        p.parent.mkdir(parents=True)
        p.write_bytes(bytes(range(256)) * 4)

    diff = git_diff(tmp_path, _files(**{"pkg__m.py": "x = 1\n"}), change, "--binary")
    assert "GIT binary patch" in diff
    s = split(modify("src/a.py") + diff)
    assert (s.graded, s.ignored, s.paths) == (modify("src/a.py"), diff, ["pkg/__pycache__/m.cpython-39.pyc"])
    assert_graded_is_clean(s)


def test_ac3_a_mode_only_change_to_the_runner_is_ignored(tmp_path):
    def change(repo, git):
        (repo / "bin" / "ansible-test").chmod(0o755)

    diff = git_diff(tmp_path, _files(**{"bin__ansible-test": "#!/usr/bin/env python\n"}), change)
    assert "old mode 100644\nnew mode 100755" in diff and "@@" not in diff
    s = split(diff + modify("lib/ansible/x.py"), ANSIBLE_RUNNER)
    assert (s.graded, s.ignored, s.paths) == (modify("lib/ansible/x.py"), diff, ["bin/ansible-test"])
    assert_graded_is_clean(s, ANSIBLE_RUNNER)
    # the same block, with no runner paths, is code under test
    assert split(diff).paths == []


CORRUPT = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1,3 +1,3 @@\n-a\n"


@pytest.mark.parametrize("bad", [CORRUPT, modify("src/c.py").replace("+a = 2\n", "")])
def test_ac3_a_block_git_rejects_is_unreadable(bad):
    with pytest.raises(Unreadable, match="git apply --numstat"):
        split(modify("src/b.py") + bad)


@pytest.mark.parametrize("bare", ["diff --git a/x.py b/x.py\n", "diff --git a/src/a.py b/src/a.py\ngarbage\n"])
def test_ac3_a_bare_header_has_no_paths_and_is_graded_as_today(bare):
    """git reads it as no patch; both graders' own `git apply` fails on it, as it does today."""
    s = split(bare)
    assert (s.graded, s.ignored, s.paths) == (bare, "", [])


def test_ac3_invariant_3_is_asserted_after_cutting(monkeypatch):
    """If the whole graded part ever reads as touching infrastructure, the split refuses."""
    real, calls = infra._paths, []

    def lying(patch, cwd):
        calls.append(patch)
        found = real(patch, cwd)
        return found | {"tests/conftest.py"} if len(calls) > 2 else found

    monkeypatch.setattr(infra, "_paths", lying)
    with pytest.raises(Unreadable, match="still touches test infrastructure: tests/conftest.py"):
        split(modify("src/a.py") + modify("src/b.py"))
    assert calls[-1] == modify("src/a.py") + modify("src/b.py")


def test_ac3_unreadable_and_no_rule_are_infra_errors():
    from andromeda.outcome.adapters.coding.docker import InfraError

    assert issubclass(Unreadable, InfraError) and issubclass(NoInfraRule, InfraError)


# --- AC4: the language guard -----------------------------------------------------------------

def safe(language: str, instance_id: str = C580EBF0) -> SafeTask:
    return SafeTask(instance_id=instance_id, repo="r", base_commit="abc", dockerhub_tag="t",
                    repo_language=language, problem_statement="p", requirements="", interface="")


@pytest.mark.parametrize("language", ["js", "ts", "go"])
def test_ac4_split_for_refuses_a_language_with_no_rule(language):
    with pytest.raises(NoInfraRule, match=f"repo_language '{language}'"):
        infra.split_for(safe(language), modify("src/a.py"))


def test_ac4_split_for_applies_the_task_runner():
    s = infra.split_for(safe("python", ANSIBLE), new_file("bin/ansible-test") + modify("lib/x.py"))
    assert s.paths == ["bin/ansible-test"] and s.graded == modify("lib/x.py")


@pytest.fixture
def no_boxes(monkeypatch):
    """Every seam that could open a container, recording instead."""
    from andromeda.outcome.adapters.coding import docker as docker_mod

    calls = []

    def recorder(*a, **k):
        calls.append(("task_sandbox", a))
        raise AssertionError("a container was opened")

    for mod in (adapter_mod, runner_mod, docker_mod):
        monkeypatch.setattr(mod, "task_sandbox", recorder)

    async def draw(*a, **k):
        calls.append(("write_hidden_tests", a))
        return []

    monkeypatch.setattr(adapter_mod, "write_hidden_tests", draw)
    return calls


@pytest.mark.parametrize("language", ["js", "ts", "go"])
async def test_ac4_the_engine_routes_unverified_before_any_container(monkeypatch, tmp_path, no_boxes, language):
    monkeypatch.setattr(adapter_mod, "load_safe", lambda _id: safe(language))
    evidence = await CodingAdapter(tmp_path).gather_evidence(Request(task_id=C580EBF0, text=""), modify("src/a.py"))
    assert verdict_mod.route(evidence) == "unverified"
    assert f"no test-infrastructure rule for repo_language '{language}'" in evidence.infra_errors[0]
    assert no_boxes == []
    assert Evidence.model_validate_json((tmp_path / "checks.json").read_text()) == evidence


def grading_data() -> GradingData:
    return GradingData(patch="", test_patch="", fail_to_pass="[]", pass_to_pass="[]",
                       selected_test_files_to_run="[]", before_repo_set_cmd="", raw_row={"x": 1})


@pytest.fixture
def harness(monkeypatch):
    """ground_truth.grade with `load`, `_pin_image` and the harness stubbed: git still runs."""
    st = {"task": safe("python"), "harness": [], "pins": []}
    real_run = subprocess.run

    def run(cmd, *a, **k):
        if cmd[:2] == ["git", "apply"]:
            return real_run(cmd, *a, **k)
        st["harness"].append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    def pin(safe_, log, env):
        st["pins"].append(safe_.instance_id)
        log.write("WARNING: pinned by the test\n")
        return "jefzda"

    monkeypatch.setattr(ground_truth, "load", lambda _id: (st["task"], grading_data()))
    monkeypatch.setattr(ground_truth, "_pin_image", pin)
    monkeypatch.setattr(ground_truth, "_docker_host", lambda: None)
    monkeypatch.setattr(ground_truth.subprocess, "run", run)
    return st


@pytest.mark.parametrize("language", ["js", "ts", "go"])
def test_ac4_ground_truth_is_error_before_the_harness(harness, tmp_path, language):
    harness["task"] = safe(language)
    assert ground_truth.grade(C580EBF0, modify("src/a.py"), tmp_path) == "error"
    assert harness["harness"] == [] and harness["pins"] == []
    log = (tmp_path / "ground_truth.log").read_text()
    assert log.startswith("[outcome grader refused] NoInfraRule: ")
    assert f"repo_language '{language}'" in log
    assert not (tmp_path / "grader").exists()


def test_ac4_an_unreadable_diff_is_error_before_the_harness(harness, tmp_path):
    assert ground_truth.grade(C580EBF0, CORRUPT, tmp_path) == "error"
    assert harness["harness"] == [] and harness["pins"] == []
    assert (tmp_path / "ground_truth.log").read_text().startswith("[outcome grader refused] Unreadable: ")


# --- AC5: Evidence and the verdict -----------------------------------------------------------

VALID = [hidden("fail", "pass", f"tests/test_outcome_h.py::h{i}") for i in range(10)]
SUFFIX = "; ignored changes to 2 test-infrastructure files: pytest.ini"


def test_ac5_ignored_defaults_to_empty():
    assert Evidence().ignored == []
    assert "ignored" in Evidence().model_dump()


def test_ac5_recorded_evidence_deserializes_unedited():
    replay = FIXTURES / "replay" / "ansible-gold" / "checks.json"
    assert Evidence.model_validate_json(replay.read_text()).ignored == []
    cells = [p for p in sorted((FIXTURES / "verdict-corpus").glob("*/*/*.json"))
             if p.parent.name not in ("sources", "ablations")]
    assert len(cells) == 36
    for p in cells:
        cell = json.loads(p.read_text())
        assert "ignored" not in cell
        assert Evidence.model_validate({"checks": cell["checks"]}).ignored == []


def with_ignored(ev: Evidence) -> Evidence:
    return ev.model_copy(update={"ignored": ["pytest.ini", "tests/conftest.py"]})


@pytest.mark.parametrize("label,ev", [
    ("PASS", Evidence(checks=VALID)),
    ("PASS tolerated", Evidence(checks=[*VALID[1:], hidden("fail", "fail", "tests/test_outcome_h.py::f")])),
    ("PASS forgiven", Evidence(checks=[*VALID, existing("pass", "fail").model_copy(
        update={"obsoleted": True, "obsoleted_by": "required-hunk"})])),
    ("FAIL", Evidence(checks=[hidden("fail", "fail", f"tests/test_outcome_h.py::h{i}") for i in range(5)])),
    ("regressed UNKNOWN", Evidence(checks=[*VALID, existing("pass", "fail")])),
    ("no valid UNKNOWN", Evidence(checks=[hidden("pass", "pass", "tests/test_outcome_h.py::h")])),
])
def test_ac5_the_suffix_is_exact_after_every_reason(label, ev):
    verdict, reason = decide(ev)
    assert decide(with_ignored(ev)) == (verdict, reason + SUFFIX)
    assert verdict_mod.route(with_ignored(ev)) == verdict_mod.route(ev)


def test_ac5_the_suffix_follows_forgiven():
    ev = Evidence(checks=[*VALID, existing("pass", "fail").model_copy(
        update={"obsoleted": True, "obsoleted_by": "required-hunk"})])
    assert decide(with_ignored(ev))[1] == ("all 10 valid hidden checks pass, no regressions; "
                                           "1 existing checks stale, forgiven by required-hunk: "
                                           "tests/test_mod.py::test_e" + SUFFIX)


def test_ac5_no_suffix_on_an_infra_error():
    ev = with_ignored(Evidence(checks=VALID, infra_errors=["box died"]))
    assert decide(ev) == ("UNKNOWN", "infra error: box died")


def test_ac5_feedback_names_the_ignored_paths():
    ev = Evidence(checks=[*VALID[:8], hidden("fail", "fail", "tests/test_outcome_h.py::secret"),
                          existing("pass", "fail")])
    note = feedback(with_ignored(ev))
    assert note == (feedback(ev) + "; ignored as test infrastructure: pytest.ini, tests/conftest.py")
    assert "secret" not in note


def test_ac5_decide_is_sync_and_nothing_reads_the_environment():
    assert not inspect.iscoroutinefunction(decide)
    for src in (inspect.getsource(verdict_mod), inspect.getsource(infra)):
        assert "environ" not in src and "getenv" not in src
    assert "import os" not in inspect.getsource(infra)


# --- AC6: the engine uses the graded diff ----------------------------------------------------

INFRA_DIFF = modify("src/a.py") + new_file("tests/conftest.py") + modify("pkg/b.py") + new_file("pytest.ini")
GRADED = modify("src/a.py") + modify("pkg/b.py")


@pytest.fixture
def engine_grade(monkeypatch, tmp_path, no_boxes):
    """gather_evidence with every Docker seam replaced by a recorder."""
    st = {"calls": [], "checks": VALID}

    async def existing_tests(self, task_, artifact, tests=()):
        st["calls"].append(("_existing_tests", artifact))
        return [], {}

    async def run_checks(task_, diff, hidden_dir, hidden_files, existing_, log_dir, canaries=()):
        st["calls"].append(("run_checks", diff))
        return list(st["checks"])

    real_mark = adapter_mod.mark_obsoleted

    def mark_obsoleted(checks, diff, sources):
        st["calls"].append(("mark_obsoleted", diff))
        return real_mark(checks, diff, sources)

    async def ablate(task_, diff, hidden_root, checks, log_dir, canaries=()):
        st["calls"].append(("ablate", diff))
        return Ablations(considered=True, skipped="no-hunks")

    monkeypatch.setattr(adapter_mod, "load_safe", lambda _id: safe("python"))
    monkeypatch.setattr(CodingAdapter, "_existing_tests", existing_tests)
    monkeypatch.setattr(adapter_mod, "run_checks", run_checks)
    monkeypatch.setattr(adapter_mod, "mark_obsoleted", mark_obsoleted)
    monkeypatch.setattr(adapter_mod, "ablate", ablate)

    async def go(diff: str):
        run_dir = tmp_path / f"run{len(list(tmp_path.iterdir()))}"
        run_dir.mkdir()
        (run_dir / "agent.diff").write_text(diff)  # as cli.run() writes it
        st["calls"].clear()
        evidence = await CodingAdapter(run_dir).gather_evidence(Request(task_id=C580EBF0, text=""), diff)
        return evidence, run_dir

    st["go"] = go
    return st


async def test_ac6_every_consumer_is_handed_the_graded_diff(engine_grade):
    engine_grade["checks"] = [*VALID, existing("pass", "fail")]  # regressed, so ablate runs
    evidence, run_dir = await engine_grade["go"](INFRA_DIFF)

    assert [name for name, _ in engine_grade["calls"]] == ["_existing_tests", "run_checks", "mark_obsoleted", "ablate"]
    assert all(diff == GRADED for _, diff in engine_grade["calls"])
    assert evidence.ignored == ["pytest.ini", "tests/conftest.py"]
    assert (run_dir / "ignored.diff").read_text() == new_file("tests/conftest.py") + new_file("pytest.ini")
    assert evidence.artifacts["ignored"] == str(run_dir / "ignored.diff")
    assert (run_dir / "agent.diff").read_text() == INFRA_DIFF
    assert Evidence.model_validate_json((run_dir / "checks.json").read_text()) == evidence


async def test_ac6_no_infrastructure_writes_no_ignored_diff(engine_grade):
    evidence, run_dir = await engine_grade["go"](GRADED)

    assert all(diff == GRADED for _, diff in engine_grade["calls"])
    assert evidence.ignored == [] and "ignored" not in evidence.artifacts
    assert not (run_dir / "ignored.diff").exists()
    assert decide(evidence) == ("PASS", "all 10 valid hidden checks pass, no regressions")


async def test_ac6_all_infrastructure_grades_the_empty_diff(engine_grade):
    engine_grade["checks"] = [hidden("fail", "fail", f"tests/test_outcome_h.py::h{i}") for i in range(3)]
    evidence, run_dir = await engine_grade["go"](new_file("tests/conftest.py"))

    assert [diff for _, diff in engine_grade["calls"]] == ["", "", ""]
    assert decide(evidence)[0] == "FAIL" and decide(evidence)[1].endswith(
        "; ignored changes to 1 test-infrastructure files: tests/conftest.py")


async def test_ac6_an_unreadable_diff_routes_unverified_with_no_recorder_called(engine_grade):
    evidence, run_dir = await engine_grade["go"](CORRUPT)

    assert engine_grade["calls"] == []
    assert verdict_mod.route(evidence) == "unverified"
    assert evidence.infra_errors[0].startswith("git apply --numstat")
    assert not (run_dir / "ignored.diff").exists()


async def test_ac6_the_split_runs_off_the_event_loop(engine_grade, monkeypatch):
    threads = []
    real = infra.split_for

    def spy(task_, diff):
        import threading
        threads.append(threading.current_thread() is threading.main_thread())
        return real(task_, diff)

    monkeypatch.setattr(infra, "split_for", spy)
    await engine_grade["go"](GRADED)
    assert threads == [False]


async def test_ac6_cancellation_propagates_through_the_split(engine_grade, monkeypatch):
    def slow(task_, diff):
        import time
        time.sleep(0.3)
        return Split(diff, "", [])

    monkeypatch.setattr(infra, "split_for", slow)
    t = asyncio.ensure_future(engine_grade["go"](GRADED))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert engine_grade["calls"] == []


# --- AC7: the harness uses the graded diff ---------------------------------------------------

def patch_of(run_dir: Path) -> str:
    """The patch the harness was handed, less spec 18's canary blocks (grader/canaries.json)."""
    (entry,) = json.loads((run_dir / "grader" / "patches.json").read_text())
    planted = set(json.loads((run_dir / "grader" / "canaries.json").read_text()))
    blocks = re.split(r"^(?=diff --git )", entry["patch"], flags=re.M)
    return "".join(b for b in blocks if not any(b.startswith(f"diff --git a/{c} b/{c}\n") for c in planted))


def test_ac7_the_harness_is_handed_the_graded_diff(harness, tmp_path):
    ground_truth.grade(C580EBF0, INFRA_DIFF, tmp_path)

    assert patch_of(tmp_path) == GRADED
    assert (tmp_path / "grader" / "ignored.diff").read_text() == new_file("tests/conftest.py") + new_file("pytest.ini")
    lines = (tmp_path / "ground_truth.log").read_text().splitlines()
    assert lines[0] == "ignored as test infrastructure: pytest.ini, tests/conftest.py"
    assert lines[1] == "WARNING: pinned by the test" and lines[2].startswith("$ ")
    assert len(harness["harness"]) == 1


def test_ac7_no_infrastructure_writes_no_ignored_diff(harness, tmp_path):
    ground_truth.grade(C580EBF0, GRADED, tmp_path)

    assert patch_of(tmp_path) == GRADED
    assert not (tmp_path / "grader" / "ignored.diff").exists()
    assert (tmp_path / "ground_truth.log").read_text().splitlines()[0] == "WARNING: pinned by the test"


def test_ac7_all_infrastructure_writes_what_empty_diff_writes(harness, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ground_truth.grade(C580EBF0, new_file("tests/conftest.py"), tmp_path / "a")
    ground_truth.grade(C580EBF0, bad_patch(C580EBF0, "empty"), tmp_path / "b")
    assert patch_of(tmp_path / "a") == patch_of(tmp_path / "b") == ""


def gate_fixture(task: str, name: str) -> str:
    return (CONSTRUCTED / task / name).read_text()


@pytest.fixture
def bypassed(monkeypatch):
    """The rule off: split_for ignores nothing, so both graders see the whole diff (L32).
    Spec 18's canaries off too, or they catch the force-pass themselves (its AC10's own arm)."""
    monkeypatch.setattr(infra, "split_for", lambda task, diff: Split(diff, "", []))
    monkeypatch.setattr(canary, "plant", lambda files: {})


async def grade_both(run_dir: Path, task: str, diff: str) -> tuple[Evidence, str]:
    """The real gather_evidence and the real harness, at once, each in its own run dir."""
    engine_dir, truth_dir = run_dir / "engine", run_dir / "truth"
    engine_dir.mkdir()
    truth_dir.mkdir()
    evidence, truth = await asyncio.gather(
        CodingAdapter(engine_dir, FIXTURES / "oracles" / task).gather_evidence(Request(task_id=task, text=""), diff),
        asyncio.to_thread(ground_truth.grade, task, diff, truth_dir))
    assert evidence.infra_errors == [], evidence.infra_errors
    assert Evidence.model_validate_json((engine_dir / "checks.json").read_text()) == evidence
    return evidence, truth


def triples(evidence: Evidence) -> list[tuple[str, str, str]]:
    return sorted((c.name, c.before, c.after) for c in evidence.checks)


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac9_the_hole_reproduced_with_the_rule_bypassed(test_run_dir, bypassed):
    evidence, truth = await grade_both(test_run_dir, C580EBF0, gate_fixture(C580EBF0, "force-pass.diff"))

    verdict, reason = decide(evidence)
    assert (verdict, truth) == ("PASS", "resolved"), reason
    assert evidence.ignored == []


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac9_a_conftest_force_pass_is_judged_on_its_code(test_run_dir):
    evidence, truth = await grade_both(test_run_dir, C580EBF0, gate_fixture(C580EBF0, "force-pass.diff"))

    verdict, reason = decide(evidence)
    assert (verdict, truth) == ("FAIL", "unresolved"), reason
    assert evidence.ignored == ["tests/unit/conftest.py"]
    assert reason.endswith("; ignored changes to 1 test-infrastructure files: tests/unit/conftest.py")
    assert (test_run_dir / "engine" / "ignored.diff").read_text() == gate_fixture(C580EBF0, "force-pass.diff")
    log = (test_run_dir / "truth" / "ground_truth.log").read_text()
    assert log.splitlines()[0] == "ignored as test infrastructure: tests/unit/conftest.py"
    assert patch_of(test_run_dir / "truth") == ""

    empty_dir = test_run_dir / "empty"
    empty_dir.mkdir()
    empty = await CodingAdapter(empty_dir, FIXTURES / "oracles" / C580EBF0).gather_evidence(
        Request(task_id=C580EBF0, text=""), bad_patch(C580EBF0, "empty"))
    assert empty.infra_errors == []
    assert triples(evidence) == triples(empty)


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac10_the_ansible_test_runner_with_the_rule_bypassed(test_run_dir, bypassed):
    evidence, truth = await grade_both(test_run_dir, ANSIBLE, gate_fixture(ANSIBLE, "force-pass.diff"))

    verdict, reason = decide(evidence)
    assert (verdict, truth) == ("PASS", "resolved"), reason


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac10_the_ansible_test_runner_force_pass_is_judged_on_its_code(test_run_dir):
    evidence, truth = await grade_both(test_run_dir, ANSIBLE, gate_fixture(ANSIBLE, "force-pass.diff"))

    verdict, reason = decide(evidence)
    assert (verdict, truth) == ("FAIL", "unresolved"), reason
    assert evidence.ignored == ["test/units/conftest.py"]
    assert reason.endswith("; ignored changes to 1 test-infrastructure files: test/units/conftest.py")


# gold.diff's own reason on the pinned oracle, measured in the build by grading gold.diff alone
# (spec 13's AC11 asserts its tail). gold-plus-infra must reproduce it, plus the suffix.
GOLD_REASON = ("17/19 valid hidden checks pass, no regressions; 2 failing within the 20% tolerance: "
               "tests/unit/components/test_outcome_hostblock_subdomains.py::test_trailing_dot_handled_equivalently"
               "[example.com-https://example.com.]; 1 existing checks stale, forgiven by required-hunk: "
               "tests/unit/components/test_hostblock.py::test_disabled_blocking_per_url")


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
async def test_real_ac11_a_legitimate_diff_costs_nothing(test_run_dir):
    diff = gate_fixture(C580EBF0, "gold-plus-infra.diff")
    evidence, truth = await grade_both(test_run_dir, C580EBF0, diff)

    verdict, reason = decide(evidence)
    assert (verdict, truth) == ("PASS", "resolved"), reason
    assert evidence.ignored == ["pytest.ini", "tests/helpers/fixtures.py"]
    suffix = "; ignored changes to 2 test-infrastructure files: pytest.ini"
    assert reason == GOLD_REASON + suffix
    graded = split(diff).graded
    assert graded.startswith(bad_patch(C580EBF0, "gold"))
    assert patch_of(test_run_dir / "truth") == graded
