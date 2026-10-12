"""Runs test files on base and on base+diff using the official per-instance run script (how to run only).

Spec 18: the run's logs are parsed on the host, never in the box that ran the diff, with the
task's own vendored `parse_test_output`, and `fold` credits a name with a pass only when every
report of it says PASSED (non-pass wins). Canaries run beside the checks on both sides.

Spec 19: with `sealed` expectations, an observer module's report is discarded and its keys are
judged on the host (`seal.judge`) from the observations file the side hands back. The
expectations never enter the box: only the observers (in hidden_dir) and `outcome_seal` do.
"""

import asyncio
import importlib.util
import itertools
import json
import re
import shlex
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from andromeda.outcome.adapters.coding import seal
from andromeda.outcome.adapters.coding.docker import (
    InfraError,
    reset_to_base,
    swepro_dir,
    task_sandbox,
    timeout_from_env,
)
from andromeda.outcome.adapters.coding.task import SafeTask
from andromeda.outcome.core.types import Check, Status

PARSER_STATUS: dict[str, Status] = {"PASSED": "pass", "FAILED": "fail", "ERROR": "error", "SKIPPED": "error"}


def harness_env_exports(instance_id: str) -> str:
    """Same ENV the official entryscript exports, taken from the instance's Dockerfiles."""
    lines = []
    for kind in ("base_dockerfile", "instance_dockerfile"):
        dockerfile = swepro_dir() / "dockerfiles" / kind / instance_id / "Dockerfile"
        if dockerfile.exists():
            lines += [ln.strip().replace("ENV", "export", 1) for ln in dockerfile.read_text().splitlines()
                      if ln.strip().startswith("ENV")]
    return "\n".join(lines)


def _parse(instance_id: str, stdout: str, stderr: str) -> list[tuple[str, str]]:
    """(name, status name) per report, in report order, from the instance's vendored parser.

    Loaded by path under a fresh module name, registered in sys.modules only while it executes
    (its dataclass needs that). vendor/ is not patched. Sync: called off the event loop.
    """
    path = swepro_dir() / "run_scripts" / instance_id / "parser.py"
    name = f"_outcome_parser_{next(_parsers)}"
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            return [(str(r.name), r.status.name) for r in module.parse_test_output(stdout, stderr)]
        finally:
            sys.modules.pop(name, None)
    except Exception as e:  # anything the vendored parser raises is the run's failure, not ours
        raise InfraError(f"the vendored parser {path} failed on the host: {type(e).__name__}: {e}") from e


_parsers = itertools.count()


def _fold_names(results: Sequence[tuple[str, str]]) -> dict[str, str]:
    """Name -> the parser's status name: PASSED only when every report is PASSED, else the first
    non-pass status in report order. Names in first-seen order: the vendored output.json's."""
    out: dict[str, str] = {}
    for name, status in results:
        if out.get(name, "PASSED") == "PASSED":
            out[name] = status
    return out


def fold(results: Sequence[tuple[str, str]]) -> dict[str, Status]:
    """Pure: a name maps to `pass` only when every one of its reports is PASSED (spec 18)."""
    return {name: PARSER_STATUS.get(status, "error") for name, status in _fold_names(results).items()}


class NotApplied(InfraError):
    """`git apply --check` refused the patch: under run_after, a hunk revert that did not apply."""


# Every interpreter the box may run observers under gets `outcome_seal` in the site dir it
# really reads: `site.getsitepackages()` filtered by `sys.path`, not sysconfig's purelib, which
# on Debian's python3.9 (ansible's image) is a directory the interpreter never looks in. Not
# PYTHONPATH: ansible-test builds its own environment. Not a conftest, and no tracked file.
_PYTHONS = " ".join(["python", "python3", *(f"python3.{m}" for m in range(6, 14))])
PLANT_SEAL = (
    f"for p in {_PYTHONS}; do command -v \"$p\" >/dev/null 2>&1 && \"$p\" -c "
    "'import site, sys; print(next(d for d in site.getsitepackages() if d in sys.path))' 2>/dev/null; "
    "done | sort -u | while read -r d; do "
    f"mkdir -p \"$d\" && cp /workspace/{seal.HELPER_MODULE}.py \"$d/\" && echo \"$d\"; done; "
    f"rm -f /workspace/{seal.HELPER_MODULE}.py"
)


async def plant_seal(c) -> None:
    """Make `outcome_seal` importable by every Python in the box. Engine code only: the
    helper's bytes are a constant, and no expected value is in them. Both boxes use it."""
    with tempfile.TemporaryDirectory() as tmp:
        helper = Path(tmp) / f"{seal.HELPER_MODULE}.py"
        helper.write_text(seal.HELPER_SOURCE)
        await c.exec("mkdir -p /workspace", timeout=120, workdir="/")
        await c.copy_in(helper, f"/workspace/{seal.HELPER_MODULE}.py")
    _, out, _ = await c.exec(PLANT_SEAL, timeout=300, workdir="/")
    if not out.strip():
        raise InfraError(f"{seal.HELPER_MODULE}: no Python in the box to plant it for")


async def _observations(c, log_dir: Path, label: str) -> str:
    """The side's observations file, copied out as `<label>_observations.jsonl`. Missing is
    empty. Over MAX_OBSERVATION_BYTES is an InfraError, checked in the box first so a huge file
    is never transferred, and again on the host, since the box's own answer is not trusted."""
    dest = log_dir / f"{label}_observations.jsonl"
    code, out, _ = await c.exec(f"stat -c %s {seal.OBSERVATIONS}", timeout=120, check=False)
    if code == 0 and out.strip().isdigit() and int(out.strip()) > seal.MAX_OBSERVATION_BYTES:
        raise InfraError(f"{label}: {seal.OBSERVATIONS} is {out.strip()} bytes, over "
                         f"{seal.MAX_OBSERVATION_BYTES}")
    try:
        await c.copy_out(seal.OBSERVATIONS, dest)
    except InfraError:
        dest.write_bytes(b"")
    if dest.stat().st_size > seal.MAX_OBSERVATION_BYTES:
        raise InfraError(f"{label}: {seal.OBSERVATIONS} is {dest.stat().st_size} bytes, over "
                         f"{seal.MAX_OBSERVATION_BYTES}")
    return dest.read_text(errors="replace")


async def _run_side(task: SafeTask, diff: str | None, hidden_dir: Path, files: list[str], log_dir: Path,
                    label: str, *, apply_check: bool = False,
                    sealed: seal.Expectations | None = None) -> dict[str, Status]:
    # spec 19: only the observers this side runs are judged on it
    sealed = None if sealed is None else {rel: keys for rel, keys in sealed.items() if rel in files}
    observations = ""
    scripts = swepro_dir() / "run_scripts" / task.instance_id
    if not (scripts / "run_script.sh").exists():
        raise InfraError(f"no official run script for {task.instance_id} under {scripts}")
    # TemporaryDirectory is sync, so it wraps the async context manager instead of sharing
    # one `with` statement with it. It only holds a scratch copy of the patch.
    with tempfile.TemporaryDirectory() as tmp:
        async with task_sandbox(task.image, run_id=log_dir.parent.name) as c:
            await reset_to_base(c, task.base_commit)
            await c.exec("mkdir -p /workspace", timeout=120, workdir="/")
            await c.copy_in(scripts / "run_script.sh", "/workspace/run_script.sh")
            # an empty diff (agent changed nothing) makes `after` equal `before`, so the verdict is FAIL, not infra
            if diff is not None and diff.strip():
                patch = Path(tmp) / "patch.diff"
                patch.write_text(diff)
                await c.copy_in(patch, "/workspace/patch.diff")
                if apply_check:
                    code, _, err = await c.exec("git apply --check /workspace/patch.diff", timeout=600, check=False)
                    if code != 0:
                        raise NotApplied(f"{label}: the patch does not apply: {err.strip()[-800:]}")
                await c.exec("git apply -v /workspace/patch.diff", timeout=600)
            if hidden_dir.exists() and any(hidden_dir.iterdir()):
                await c.copy_in(f"{hidden_dir}/.", "/app/")
            if sealed is not None:
                await plant_seal(c)
                await c.exec(f"rm -f {seal.OBSERVATIONS}", timeout=120, workdir="/")
            await c.exec(
                f"{harness_env_exports(task.instance_id)}\n"
                f"bash /workspace/run_script.sh {shlex.quote(','.join(files))} "
                "> /workspace/stdout.log 2> /workspace/stderr.log\n",
                timeout=timeout_from_env("OUTCOME_RUNNER_TIMEOUT", 1800),
                check=False,
            )
            for name in ("stdout.log", "stderr.log"):
                try:
                    await c.copy_out(f"/workspace/{name}", log_dir / f"{label}_{name}")
                except InfraError:
                    pass
            if sealed is not None:
                observations = await _observations(c, log_dir, label)
    stdout, stderr = log_dir / f"{label}_stdout.log", log_dir / f"{label}_stderr.log"
    if not stdout.exists() or not stdout.read_text(errors="replace").strip():
        raise InfraError(f"{label}: run script produced no output")
    results = await asyncio.to_thread(
        _parse, task.instance_id, stdout.read_text(errors="replace"),
        stderr.read_text(errors="replace") if stderr.exists() else "")
    names = _fold_names(results)
    (log_dir / f"{label}_output.json").write_text(
        json.dumps({"tests": [{"name": n, "status": st} for n, st in names.items()]}, indent=2))
    if sealed is None:
        return fold(results)
    # spec 19: what the box said about an observer is discarded; the host judges its keys
    statuses = fold([(n, st) for n, st in results if _file_of(n) not in sealed])
    summary = seal.judge_summary(sealed, observations)
    statuses.update(seal.judge(sealed, observations))
    (log_dir / f"{label}_seal.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return statuses


def _file_of(test_name: str) -> str:
    return test_name.split("::", 1)[0]


def removed_files(diff: str) -> set[str]:
    """Files the diff deletes or renames away (their tests legitimately vanish)."""
    removed = set()
    for block in re.split(r"^(?=diff --git )", diff, flags=re.M):
        header = block.split("\n@@", 1)[0]
        old = re.match(r"diff --git a/(\S+) b/", header)
        if old and re.search(r"^deleted file mode", header, re.M):
            removed.add(old.group(1))
        removed.update(re.findall(r"^rename from (\S+)$", header, re.M))
    return removed


async def run_checks(task: SafeTask, diff: str, hidden_dir: Path, hidden_files: list[str],
                     existing_files: list[str], log_dir: str | Path, canaries: Sequence[str] = (),
                     sealed: seal.Expectations | None = None) -> list[Check]:
    """hidden_files are repo-relative paths inside hidden_dir; existing_files are repo-relative paths in /app.

    Spec 18: `canaries` (repo-relative, inside hidden_dir) run on both sides and come back as
    `kind="canary"`; with none, the return is exactly what it was before them.
    Spec 19: `sealed` judges the observers' keys on the host on both sides; with None, every
    return and every file written is byte-identical to 18's.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(set(hidden_files) | set(existing_files) | set(canaries))
    if not files:
        return []
    # passed on only when set, so a fixed-signature _run_side (a test's recorder) still fits
    kw = {} if sealed is None else {"sealed": sealed}
    before = await _run_side(task, None, hidden_dir, files, log_dir, "before", **kw)
    after = await _run_side(task, diff, hidden_dir, files, log_dir, "after", **kw)
    return to_checks(before, after, files, hidden_files, removed_files(diff), canaries=canaries)


async def run_after(task: SafeTask, diff: str, hidden_dir: Path, files: list[str], log_dir: str | Path,
                    label: str, canaries: Sequence[str] = (),
                    sealed: seal.Expectations | None = None) -> dict[str, Status]:
    """Spec 13: the after side only, of `files` against `diff`, in a fresh sandbox reset to base.

    One hunk's ablation run. The same sandbox, run script and parser as run_checks' after side;
    a diff that does not `git apply` raises NotApplied, any other failure InfraError. Spec 18's
    `canaries` run with `files`, and their statuses are returned with the rest. Spec 19's
    `sealed` judges the same keys as run_checks does, for the observers among `files`.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(set(files) | set(canaries)) if canaries else files
    kw = {} if sealed is None else {"sealed": sealed}
    return await _run_side(task, diff, hidden_dir, files, log_dir, label, apply_check=True, **kw)


def to_checks(before: dict[str, Status], after: dict[str, Status], files: list[str],
              hidden_files: list[str], removed: set[str] = frozenset(), canaries: Sequence[str] = ()) -> list[Check]:
    canary_set = set(canaries)
    hidden_set, file_set = set(hidden_files), set(files) - canary_set
    existing_set = file_set - hidden_set
    if not before and not after:
        raise InfraError(f"no test results on base or after change for {len(files)} files")
    if not any(_file_of(n) in file_set for n in [*before, *after]):
        raise InfraError("test results do not map to the requested files (runner expects pytest-style "
                         "'<file>::<test>' names)")
    if existing_set and not any(_file_of(n) in existing_set for n in before):
        raise InfraError("existing tests produced no results on base; cannot compare for regressions")
    for f in sorted(canary_set):
        on_base = [before[n] for n in before if _file_of(n) == f]
        if not on_base or "pass" in on_base:
            raise InfraError(f"canary {f} {'passed' if on_base else 'did not run'} on base: it cannot "
                             "guard this run's report")

    # an existing file that ran on base but went silent after the change (not deleted/renamed by the diff)
    # typically failed to import: its tests count as errors after, so regressions are not hidden
    silent_after = {f for f in existing_set - removed
                    if any(_file_of(n) == f for n in before) and not any(_file_of(n) == f for n in after)}

    checks = []
    for name in sorted(set(before) | set(after)):
        f = _file_of(name)
        if f in canary_set:
            # a canary absent after did not pass there: silenced is not tampered (spec 18)
            checks.append(Check(name=name, kind="canary", before=before.get(name, "fail"),
                                after=after.get(name, "fail")))
            continue
        if f not in file_set:
            continue
        if f in hidden_set:
            # a hidden test absent from one side did not pass there (typically an import error on base)
            checks.append(Check(name=name, kind="hidden", before=before.get(name, "fail"),
                                after=after.get(name, "fail")))
        elif name in before and name in after:
            checks.append(Check(name=name, kind="existing", before=before[name], after=after[name]))
        elif name in before and f in silent_after:
            checks.append(Check(name=name, kind="existing", before=before[name], after="error"))
        # otherwise the diff added, moved or deleted this test: not comparable, not a regression
    return checks
