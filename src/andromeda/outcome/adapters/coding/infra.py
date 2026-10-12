"""Spec 17: a graded diff cannot change how it is graded.

Both graders -- `CodingAdapter.gather_evidence` and `ground_truth.grade` -- call `split_for`
first and apply only its `graded` part. A file block that touches test infrastructure (the
files that decide which checks run, how they run, or how their results are read) is
`ignored`: judged as if it were not there, and still landed with the rest of the diff.

The rule is the constants below: no env var, no CLI flag, no per-run switch (spec 03's
reasoning for HIDDEN_FAIL_TOLERANCE). Git decides what a block touches, never a regex over its
header: a traditional `---`/`+++` section inside another file's block is applied by `git
apply`, so it is read by `git apply` too.

Spec 18 (L37): a block that changes, deletes or renames a test module that exists on base is
set aside the same way, so the existing checks run the base bytes of every module they read.
Git decides what a block creates: a `create mode` path, or a rename's or copy's target.
"""

import re
import subprocess
import tempfile
from pathlib import PurePosixPath
from typing import Literal, NamedTuple

from andromeda.outcome.adapters.coding.docker import InfraError, swepro_dir
from andromeda.outcome.adapters.coding.task import SafeTask

InfraKind = Literal["conftest", "config", "test-support", "runner", "import-artifact"]

# pytest picks its inifile and rootdir from these, at any depth
CONFIG_NAMES = frozenset({"pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "tox.ini", "setup.cfg",
                          "pyproject.toml"})
# a module `<name>.py` or a package dir `<name>/`, at any depth: shadows the runner
RUNNER_NAMES = frozenset({"pytest", "_pytest", "pluggy", "py", "sitecustomize", "usercustomize"})
# a top-level `test/` or `tests/`: what conftest imports and the runner loads
TEST_ROOTS = frozenset({"test", "tests"})
# spec 20: a `tests/` directory below the top level is test-support too (openlibrary/tests/).
# Not `test`: lib/ansible/plugins/test/ is Ansible source, its Jinja test plugins.
NESTED_TEST_ROOTS = frozenset({"tests"})
TEST_MODULE = re.compile(r"^(test_.*|.*_test)\.py$")
CREATE = re.compile(r"^ create mode \d+ (.+)$", re.M)
IMPORT_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".pyd", ".pth"})
SITE_DIRS = frozenset({"site-packages", "dist-packages"})
# the repo paths a run_script.sh invokes as /app/<path>: a command word, at the start of a line
# or after a shell separator. `OL_CONFIG=/app/conf/openlibrary.yml` or `PATH=/app/bin:$PATH`
# names a path the tests read, not the runner, and `cd /app/test/x` runs nothing.
APP_PATH = re.compile(r"(?:^|[;&|(]|\b(?:exec|bash|sh|python[\d.]*|source))[ \t]*/app/([\w.+/-]+)", re.M)


class Unreadable(InfraError):
    """git rejected a block, or the graded part still reads as touching infrastructure."""


class NoInfraRule(InfraError):
    """The task's language has no test-infrastructure rule, so nothing of it may be graded."""


class Split(NamedTuple):
    graded: str
    ignored: str
    paths: list[str]  # the ignored blocks' infrastructure paths, sorted, unique
    tests: list[str] = []  # spec 18: the ignored blocks' base test-module paths, sorted, unique


def infra_kind(path: str, runner: frozenset[str] = frozenset()) -> InfraKind | None:
    """The kind of test infrastructure `path` is, first match in the spec's table order. Pure."""
    p = PurePosixPath(path)
    parts, name, dirs = p.parts, p.name, p.parts[:-1]
    if name == "conftest.py":
        return "conftest"
    if name in CONFIG_NAMES:
        return "config"
    if not TEST_MODULE.match(name) and ((len(parts) > 1 and parts[0] in TEST_ROOTS)
                                        or NESTED_TEST_ROOTS.intersection(dirs[1:])):
        return "test-support"
    if (name.endswith(".py") and name[:-3] in RUNNER_NAMES) or RUNNER_NAMES.intersection(dirs) or path in runner:
        return "runner"
    if (p.suffix in IMPORT_SUFFIXES or "__pycache__" in dirs or SITE_DIRS.intersection(parts)
            or any(d.endswith((".egg-info", ".dist-info")) for d in dirs)):
        return "import-artifact"
    return None


def runner_paths(instance_id: str) -> frozenset[str]:
    """The repo-relative paths the task's vendored run_script.sh invokes as /app/<path>.

    Read from vendor/, never the image. Empty when the task has no run script (a stub task);
    run_checks refuses that task on its own.
    """
    script = swepro_dir() / "run_scripts" / instance_id / "run_script.sh"
    if not script.is_file():
        return frozenset()
    return frozenset(m.rstrip("/.") for m in APP_PATH.findall(script.read_text()) if m.rstrip("/."))


def _numstat(patch: str, cwd: str, *, reverse: bool) -> list[str]:
    # --allow-empty: text git reads as no patch at all -- prose before the first header, or a
    # bare `diff --git` header -- has no paths and is graded, as the graders take it today
    cmd = ["git", "apply", "--numstat", "-z", "--allow-empty", *(["-R"] if reverse else [])]
    proc = subprocess.run(cmd, input=patch.encode("utf-8", "surrogateescape"), capture_output=True, cwd=cwd)
    if proc.returncode != 0:
        raise Unreadable(f"git apply --numstat{' -R' if reverse else ''} rejects the diff: "
                         f"{proc.stderr.decode(errors='replace').strip()[-400:]}")
    # records are `added\tdeleted\tpath\0`; a rename or copy under -z is `added\tdeleted\t\0old\0new\0`
    out, fields = [], proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    i = 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if not record:
            continue
        path = record.split("\t", 2)[2] if record.count("\t") >= 2 else record
        if path:
            out.append(path)
        else:
            out += [f for f in fields[i:i + 2] if f]
            i += 2
    return out


def _paths(patch: str, cwd: str) -> set[str]:
    """Every path git reads `patch` as touching: forward, and with -R for rename and copy sources."""
    return {*_numstat(patch, cwd, reverse=False), *_numstat(patch, cwd, reverse=True)}


def _base_tests(patch: str, cwd: str, touched: set[str]) -> set[str]:
    """The test modules `patch` touches that it does not create: they exist on base (spec 18).

    Created is git's reading, forward: a `create mode` summary line, or a path only the forward
    numstat names (a rename's or copy's target). A rename's or a delete's old path is not created.
    """
    test = {p for p in touched if TEST_MODULE.match(PurePosixPath(p).name)}
    if not test:
        return set()
    proc = subprocess.run(["git", "apply", "--summary", "-z", "--allow-empty"],
                          input=patch.encode("utf-8", "surrogateescape"), capture_output=True, cwd=cwd)
    if proc.returncode != 0:
        raise Unreadable(f"git apply --summary rejects the diff: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    created = set(CREATE.findall(proc.stdout.decode("utf-8", "surrogateescape")))
    created |= set(_numstat(patch, cwd, reverse=False)) - set(_numstat(patch, cwd, reverse=True))
    return test - created


def split(diff: str, runner: frozenset[str] = frozenset()) -> Split:
    """Cut `diff` into graded and ignored file blocks, byte-equal and in order.

    Blocks are cut at `^diff --git ` as `adapter._file_blocks` cuts them; text before the first
    header is a block of its own. A block is ignored when any path git reads it as touching is
    infrastructure or a base test module. Runs outside any repository, on the host: no container.
    """
    blocks = [b for b in re.split(r"^(?=diff --git )", diff, flags=re.M) if b]
    graded, ignored, paths, tests = [], [], set(), set()
    with tempfile.TemporaryDirectory() as cwd:
        for block in blocks:
            touched = _paths(block, cwd)
            infra = {p for p in touched if infra_kind(p, runner)}
            base = _base_tests(block, cwd, touched)
            (ignored if infra or base else graded).append(block)
            paths |= infra
            tests |= base
        kept = "".join(graded)
        if kept:
            touched = _paths(kept, cwd)
            if left := sorted(p for p in touched if infra_kind(p, runner)):
                raise Unreadable(f"the graded part still touches test infrastructure: {', '.join(left)}")
            if left := sorted(_base_tests(kept, cwd, touched)):
                raise Unreadable(f"the graded part still edits a base test module: {', '.join(left)}")
    return Split(kept, "".join(ignored), sorted(paths), sorted(tests))


def split_for(task: SafeTask, diff: str) -> Split:
    """The one entry point both graders call, through this module so a test can replace it."""
    if task.repo_language != "python":
        raise NoInfraRule(f"{task.instance_id}: no test-infrastructure rule for repo_language "
                          f"{task.repo_language!r} (spec 17 has a python rule only), so it is not graded")
    return split(diff, runner_paths(task.instance_id))
