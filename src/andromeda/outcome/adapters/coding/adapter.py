"""CodingAdapter: turns a request + diff into Evidence (hidden tests it writes + nearby existing tests)."""

import ast
import asyncio
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple
from uuid import uuid4

from pydantic import BaseModel, Field

from andromeda.outcome.adapters.coding.docker import InfraError, Sandbox, reset_to_base, task_sandbox
from andromeda.outcome.adapters.coding import canary, infra, seal
from andromeda.outcome.adapters.coding.oracle import load_expectations, load_oracle
from andromeda.outcome.adapters.coding import runner
from andromeda.outcome.adapters.coding.runner import NotApplied, run_checks
from andromeda.outcome.adapters.coding.task import SafeTask, load_safe
from andromeda.outcome.adapters.coding.test_writer import EXPECTED, write_hidden_tests
from andromeda.outcome.core.types import Check, Evidence, Request, Status
from andromeda.outcome.core.verdict import route as verdict_route

MAX_EXISTING_FILES = int(os.environ.get("OUTCOME_MAX_EXISTING_FILES", 20))
DIFF_PATH = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.M)


def touched_files(diff: str) -> list[str]:
    return sorted({b for _, b in DIFF_PATH.findall(diff)})


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(re.search(r"(^test_|_test\.|\.test\.|\.spec\.|Test\.)", p.name)) or "tests" in p.parts or "test" in p.parts


def nearby_existing_tests(touched: list[str], tracked: list[str]) -> list[str]:
    """Repo test files in the same directories/modules as the files the diff touches."""
    tracked_tests = [f for f in tracked if is_test_file(f)]
    strong: set[str] = set()  # touched test files and name-matched tests: kept first when capping
    picked: set[str] = set()
    for src in touched:
        if is_test_file(src):
            if src in tracked_tests:
                strong.add(src)
            continue
        p = PurePosixPath(src)
        # same language only (a .sh "test" makes pytest abort the whole run), and tests in the same dir
        # or in a test dir whose tail matches the source dir (tests/unit/utils ~ pkg/utils)
        in_module = [t for t in tracked_tests
                     if PurePosixPath(t).suffix == p.suffix
                     and (PurePosixPath(t).parent == p.parent
                          or (p.parent.name and PurePosixPath(t).parent.name == p.parent.name))]
        named = [t for t in in_module if p.stem in PurePosixPath(t).stem]
        (strong if named else picked).update(named or in_module)
    return (sorted(strong) + sorted(picked - strong))[:MAX_EXISTING_FILES]


# `def`/`class` at column 0 only. A nested `def __init__`/`def filter` moves with its class
# and, matched as a whole word against test sources, would mark half the repo obsolete.
DEF_OR_CLASS = re.compile(r"^([-+])(?:def|class)\s+(\w+)", re.M)


def removed_symbols(diff: str) -> set[str]:
    """Top-level names the diff deletes from non-test files.

    Per-block subtraction is the point: a name removed and re-added inside the same file is a
    signature edit, not a deletion. The same name removed here and added in *another* file is a
    relocation -- and tests still pointing at the old home are obsolete, not regressed.
    """
    removed: set[str] = set()
    for block in re.split(r"^(?=diff --git )", diff, flags=re.M):
        files = touched_files(block)
        if not files or any(is_test_file(f) for f in files):
            continue
        # drop the ---/+++ header lines before reading +/- markers off the body
        body = "\n".join(ln for ln in block.splitlines() if not ln.startswith(("---", "+++")))
        gone = {name for sign, name in DEF_OR_CLASS.findall(body) if sign == "-"}
        added = {name for sign, name in DEF_OR_CLASS.findall(body) if sign == "+"}
        removed |= gone - added
    return removed


def _fixture(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, bool] | None:
    """(the name a test requests it by, autouse) when `fn` is a pytest fixture, else None."""
    for d in fn.decorator_list:
        call = d if isinstance(d, ast.Call) else None
        target = ast.unparse(call.func if call else d)
        if target != "fixture" and not target.endswith(".fixture"):
            continue
        kw = {k.arg: k.value for k in (call.keywords if call else [])}
        alias = kw.get("name")
        name = alias.value if isinstance(alias, ast.Constant) and isinstance(alias.value, str) else fn.name
        autouse = isinstance(kw.get("autouse"), ast.Constant) and kw["autouse"].value is True
        return name, autouse
    return None


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = fn.args
    return {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]} - {"self", "cls"}


def _usefixtures(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> set[str]:
    """The fixture names a `@pytest.mark.usefixtures("a", "b")` decorator requests."""
    return {arg.value for d in node.decorator_list
            if isinstance(d, ast.Call) and ast.unparse(d.func).endswith("usefixtures")
            for arg in d.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}


def _is_test_member(node: ast.stmt) -> bool:
    """pytest's own collection prefixes: a `test*` function or a `Test*` class."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name.startswith("test")
    return isinstance(node, ast.ClassDef) and node.name.startswith("Test")


def _scope(tree: ast.Module, src: str, check_name: str) -> str | None:
    """The source a check's own code reaches, or None when its name does not resolve.

    `path::A::B::test_x[param]` -> class A, nested class B, function test_x. Its scope is the
    function with its decorators, the non-test members (and headers) of A and B, and every
    same-file fixture it requests by parameter name, followed through the fixtures' own
    parameters. Autouse and `usefixtures` fixtures are requested too: pytest runs them.
    """
    rest = check_name.split("::", 1)[1] if "::" in check_name else ""
    parts = rest.split("[", 1)[0].split("::")
    if not parts[-1]:
        return None
    body, classes = tree.body, []
    for cls_name in parts[:-1]:
        # the last definition wins, as it does when Python runs the file
        cls = [n for n in body if isinstance(n, ast.ClassDef) and n.name == cls_name]
        if not cls:
            return None
        classes.append(cls[-1])
        body = cls[-1].body
    fns = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == parts[-1]]
    if not fns:
        return None
    fn = fns[-1]

    def seg(node: ast.AST) -> str:
        decorators = getattr(node, "decorator_list", [])
        return "\n".join(ast.get_source_segment(src, n) or "" for n in [*decorators, node])

    pieces = [seg(fn)]
    requested = _params(fn) | _usefixtures(fn)
    fixtures: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for scope_body in [tree.body, *(c.body for c in classes)]:  # inner classes override
        for n in scope_body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (fx := _fixture(n)):
                fixtures[fx[0]] = n
                if fx[1]:
                    requested.add(fx[0])
    for cls in classes:
        pieces += [seg(d) for d in cls.decorator_list] + [seg(b) for b in [*cls.bases, *cls.keywords]]
        requested |= _usefixtures(cls)
        for member in cls.body:
            if member in classes or _is_test_member(member):
                continue
            pieces.append(seg(member))
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                requested |= _params(member)
    seen: set[str] = set()
    while pending := requested - seen:
        name = pending.pop()
        seen.add(name)
        if name in fixtures:
            pieces.append(seg(fixtures[name]))
            requested |= _params(fixtures[name])
    return "\n".join(pieces)


def mark_obsoleted(checks: list[Check], diff: str, test_sources: dict[str, str]) -> list[Check]:
    """Flag existing checks whose own code references a symbol the diff deleted. Pure.

    Per test (spec 13, L24): the check's scope, `_scope()`. The whole file is marked when a
    module-level statement references a removed symbol (a broken import errors every test in
    it), when the file does not parse, or when the check's name does not resolve in it --
    spec 03's file rule, so this never forgives less than an unparseable file did.
    """
    removed = removed_symbols(diff)
    if not removed:
        return list(checks)
    patterns = [re.compile(rf"\b{re.escape(name)}\b") for name in removed]

    def hit(text: str) -> bool:
        return any(p.search(text) for p in patterns)

    whole: dict[str, bool] = {}  # path -> the whole file is marked
    trees: dict[str, ast.Module | None] = {}
    for path, src in test_sources.items():
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            tree = None
        trees[path] = tree
        whole[path] = hit(src) if tree is None else any(
            hit(ast.get_source_segment(src, n) or "") for n in tree.body
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))

    def obsolete(c: Check) -> bool:
        path = c.name.split("::", 1)[0]
        if c.kind != "existing" or path not in test_sources:
            return False
        if whole[path]:
            return True
        tree = trees[path]
        scope = None if tree is None else _scope(tree, test_sources[path], c.name)
        return hit(test_sources[path] if scope is None else scope)

    return [c.model_copy(update={"obsoleted": True, "obsoleted_by": "deleted-symbol"}) if obsolete(c) else c
            for c in checks]


# --- spec 13: required-hunk attribution ---------------------------------------------------

# At most this many non-test hunks are reverted, one ablation run each. A constant, not an
# env var, for HIDDEN_FAIL_TOLERANCE's reason: a verdict whose rule varies per machine is not
# a verdict. c580ebf0's gold.diff has 6; ansible-f327e65d's broken.diff has 8.
MAX_ABLATION_HUNKS = 12


class Hunk(NamedTuple):
    id: str  # "<path>@<index>", 0-based within the file
    path: str
    header: str  # its "@@ ... @@" line


def _file_blocks(diff: str) -> list[str]:
    return re.split(r"^(?=diff --git )", diff, flags=re.M)


def _split_hunks(block: str) -> list[str]:
    """[file header, hunk 0, hunk 1, ...], joining back to `block` byte for byte."""
    return re.split(r"^(?=@@)", block, flags=re.M)


def _block_path(block: str) -> str | None:
    m = DIFF_PATH.search(block)
    if not m or any(is_test_file(f) for f in m.groups()):
        return None  # a test file's hunks are never reverted
    return m.group(2)


def hunks(diff: str) -> list[Hunk]:
    """The diff's hunks in non-test files, in diff order. Pure."""
    out = []
    for block in _file_blocks(diff):
        if (path := _block_path(block)) is None:
            continue
        for i, part in enumerate(_split_hunks(block)[1:]):
            out.append(Hunk(f"{path}@{i}", path, part.split("\n", 1)[0]))
    return out


def without(diff: str, hunk_id: str) -> str:
    """The diff with that one hunk removed and the rest byte-equal. Pure.

    A file block left with no hunks is dropped whole: a header with no hunk is not a patch.
    """
    blocks = _file_blocks(diff)
    for b, block in enumerate(blocks):
        if (path := _block_path(block)) is None or not hunk_id.startswith(f"{path}@"):
            continue
        parts = _split_hunks(block)
        index = hunk_id[len(path) + 1:]
        if index.isdigit() and int(index) < len(parts) - 1:
            del parts[int(index) + 1]
            blocks[b] = "".join(parts) if len(parts) > 1 else ""
            return "".join(blocks)
    raise KeyError(f"no hunk {hunk_id!r} in the diff")


class HunkAblation(BaseModel):
    """One `without(diff, id)` run: its after-side results, and what they attribute."""
    id: str
    header: str
    applied: bool = True
    infra_error: str | None = None
    results: dict[str, Status] = Field(default_factory=dict)
    restores: list[str] = Field(default_factory=list)
    valid_failing: int | None = None
    required: bool = False


class Ablations(BaseModel):
    """`ablation.json`: the adapter writes it, and the corpus's `ablations/*.json` share it."""
    considered: bool
    skipped: Literal["hidden-regression", "too-many-hunks", "no-hunks"] | None = None
    regressions: list[str] = Field(default_factory=list)
    valid_failing_full: int | None = None
    hunks: list[HunkAblation] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)


def _regressed(checks: list[Check]) -> list[Check]:
    """Existing checks broken by the diff that no deleted symbol reaches: decide()'s regressions."""
    return [c for c in checks
            if c.kind == "existing" and not c.obsoleted and c.before == "pass" and c.after != "pass"]


def _valid_hidden(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.kind == "hidden" and c.before == "fail"]


def _attribute(checks: list[Check], run: HunkAblation) -> HunkAblation:
    """What one hunk's revert shows: the regressions it restores, and whether the request needs it.

    A check absent from the results did not pass: a valid hidden check counts as failing (as in
    `to_checks`), and a regression as not restored.
    """
    if not run.applied or run.infra_error:
        return run.model_copy(update={"restores": [], "valid_failing": None, "required": False})
    restores = [c.name for c in _regressed(checks) if run.results.get(c.name) == "pass"]
    valid_failing = sum(run.results.get(c.name, "fail") != "pass" for c in _valid_hidden(checks))
    full = sum(c.after != "pass" for c in _valid_hidden(checks))
    return run.model_copy(update={"restores": restores, "valid_failing": valid_failing,
                                  "required": valid_failing > full})


def mark_stale(checks: list[Check], ablations: Ablations) -> list[Check]:
    """Mark a regression `required-hunk` when some one hunk both causes it and is required. Pure.

    Causes: reverting it makes the regression pass. Required: reverting it makes strictly more
    valid hidden checks fail than the full diff does. Hidden checks, checks that did not
    regress, and checks a deleted symbol already obsoleted are returned unchanged.
    """
    stale: set[str] = set()
    for run in ablations.hunks:
        judged = _attribute(checks, run)
        if judged.required:
            stale |= set(judged.restores)
    return [c.model_copy(update={"obsoleted": True, "obsoleted_by": "required-hunk"}) if c.name in stale else c
            for c in checks]


async def ablate(task: SafeTask, diff: str, hidden_root: Path, checks: list[Check], log_dir: Path,
                 canaries: list[str] = (), sealed: seal.Expectations | None = None) -> Ablations:
    """Revert the diff's non-test hunks one at a time and re-run what they could explain.

    Called on a `regressed` route only. Skipped, with no container, when a hidden check that
    passed on base fails after (the guard: the diff broke something the request keeps), or
    when there are no hunks or more than MAX_ABLATION_HUNKS. Otherwise one run_after per hunk,
    one at a time, over the unmarked regressions' files and the valid hidden checks' files.
    A run that fails is recorded on its hunk and gives no information; CancelledError (a
    BaseException) propagates. run_after is looked up through `runner` so tests can replace it.
    Spec 18: `canaries` run in every hunk's run, and a run in which one passes is `tampered`:
    recorded as an infra error, so it forgives nothing. Spec 19: `sealed` reaches every hunk's
    run_after, only when set, so a fixed-signature run_after (a test's recorder) still fits.
    """
    regressions, valid = _regressed(checks), _valid_hidden(checks)
    out = Ablations(considered=True, regressions=[c.name for c in regressions],
                    valid_failing_full=sum(c.after != "pass" for c in valid))
    found = hunks(diff)
    if any(c.kind == "hidden" and c.before == "pass" and c.after != "pass" for c in checks):
        return out.model_copy(update={"skipped": "hidden-regression"})
    if not found:
        return out.model_copy(update={"skipped": "no-hunks"})
    if len(found) > MAX_ABLATION_HUNKS:
        return out.model_copy(update={"skipped": "too-many-hunks"})
    watched = {c.name for c in [*regressions, *valid]}
    files = sorted({name.split("::", 1)[0] for name in watched})
    runs = []
    kw = {} if sealed is None else {"sealed": sealed}
    for i, h in enumerate(found):
        run = HunkAblation(id=h.id, header=h.header)
        try:
            results = await runner.run_after(task, without(diff, h.id), hidden_root, files, log_dir, f"hunk{i}",
                                             canaries=canaries, **kw)
            passed = sorted(n for n, st in results.items() if n.split("::", 1)[0] in canaries and st == "pass")
            if passed:
                run.infra_error = f"tampered: {len(passed)} planted checks that must fail passed: {passed[0]}"
            run.results = {name: status for name, status in sorted(results.items()) if name in watched}
        except NotApplied:  # the rest of the diff does not apply without it
            run.applied = False
        except Exception as e:  # InfraError, a timeout, anything: this hunk tells us nothing
            run.infra_error = f"{type(e).__name__}: {e}"
        runs.append(_attribute(checks, run))
    ablations = out.model_copy(update={"hunks": runs})
    stale = [c.name for c in mark_stale(checks, ablations) if c.obsoleted_by == "required-hunk"]
    return ablations.model_copy(update={"stale": stale})


class CodingAdapter:
    def __init__(self, run_dir: str | Path, oracle: str | Path | None = None, *,
                 route: object | None = None):
        self.run_dir = Path(run_dir)
        self.oracle = Path(oracle) if oracle else None
        # the model a fresh oracle would be drawn by; the public engine draws none
        self.route = route

    async def _tracked_files(self, task: SafeTask, c: Sandbox | None = None) -> list[str]:
        """With `c`, list files in a sandbox the caller already reset to base.

        The `c is None` branch opens its own: it is what
        tests/test_replay_equivalence.py calls. Production always goes through
        _existing_tests, which needs the same container to read the files it selects.
        """
        if c is None:
            async with task_sandbox(task.image, run_id=self.run_dir.name) as own:
                await reset_to_base(own, task.base_commit)
                return await self._tracked_files(task, own)
        # thousands of lines on a repo like ansible: output_limit must stay off
        return (await c.exec("git ls-files", timeout=300))[1].splitlines()

    async def _existing_tests(self, task: SafeTask, artifact: str,
                              tests: list[str] = ()) -> tuple[list[str], dict[str, str]]:
        """Select the nearby existing test files and read their base-commit sources.

        One sandbox and one exec for both: the selection needs `git ls-files` and
        mark_obsoleted needs the bodies, and opening a second container to read files we
        were already standing next to would double the slowest step in the run.
        Spec 18: `tests`, the base test modules whose edits were set aside, are selected as
        touched ("strong"), and each tracked one always, past the cap: they run at base.
        """
        async with task_sandbox(task.image, run_id=self.run_dir.name) as c:
            await reset_to_base(c, task.base_commit)
            tracked = await self._tracked_files(task, c)
            existing = nearby_existing_tests(sorted({*touched_files(artifact), *tests}), tracked)
            tracked_set = set(tracked)
            existing += [t for t in sorted(tests) if t in tracked_set and t not in existing]
            if not existing:
                return [], {}
            # random per call, so no file body can forge a delimiter and inject a source
            marker = f"OUTCOME_FILE_{uuid4().hex}"
            script = "\n".join(
                f"printf '\\n%s %s\\n' {shlex.quote(marker)} {shlex.quote(p)}; cat -- {shlex.quote(p)}"
                for p in existing)
            # no output_limit: ~400KB of qutebrowser tests, against a 230KB transcript precedent
            out = (await c.exec(script, timeout=300))[1]
        sources = {}
        for chunk in out.split(f"\n{marker} ")[1:]:
            path, _, body = chunk.partition("\n")
            sources[path] = body
        return existing, sources

    def _sealed(self, hidden_root: Path, hidden_rel: list[str]) -> seal.Expectations | None:
        """Spec 19: the expectations to judge with, or None for a reported oracle.

        Sealed when a pinned manifest says so (its expected/ is copied into the run dir, as the
        tree is) or when a fresh draw left run_dir/expected/. The leaked keys are dropped, never
        judged, and `seal.json` names them. expected/ is hidden_root's sibling, never its child,
        and nothing here hands it to a sandbox. A SealError is an InfraError: `unverified`.
        """
        expected_root = self.run_dir / EXPECTED
        if self.oracle:
            stored = load_expectations(self.oracle)
            if stored is None:
                return None
            for rel in seal.expectation_files(self.oracle / EXPECTED):
                (expected_root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.oracle / EXPECTED / rel, expected_root / rel)
        elif not expected_root.is_dir():
            return None
        exp = seal.load(expected_root, hidden_rel)
        # every file that will be copied into the box, not only the observers: a fresh draw's
        # whole hidden_tests/ goes in, stray notes and data files included
        sources = {p.relative_to(hidden_root).as_posix(): p.read_text(errors="replace")
                   for p in sorted(hidden_root.rglob("*")) if p.is_file()}
        leaked = seal.lint(exp, sources)
        strength = {f"{rel}::{k}": seal.strength(v) for rel, keys in exp.items() for k, v in keys.items()}
        (self.run_dir / "seal.json").write_text(json.dumps({
            "form": "sealed",
            "keys": sorted(strength),
            "strong": sorted(n for n, st in strength.items() if st == "strong"),
            "weak": sorted(n for n, st in strength.items() if st == "weak"),
            "leaked": leaked,
        }, indent=2) + "\n")
        return seal.without(exp, leaked)

    async def gather_evidence(self, request: Request, artifact: str) -> Evidence:
        # set before the try, so a load_oracle failure still records which oracle was asked for
        evidence = Evidence(artifacts={"agent.diff": str(self.run_dir / "agent.diff"),
                                       "oracle": str(self.oracle) if self.oracle else "fresh"})
        ablations = Ablations(considered=False)  # rewritten below only on a `regressed` route
        try:
            task = load_safe(request.task_id)
            # spec 17: first, before any draw or container -- from here on only `graded` is judged
            split = await asyncio.to_thread(infra.split_for, task, artifact)
            graded = split.graded
            evidence.ignored = split.paths
            evidence.base_tests = split.tests  # spec 18: set aside, so graded at base
            if split.paths:
                (self.run_dir / "ignored.diff").write_text(split.ignored)
                evidence.artifacts["ignored"] = str(self.run_dir / "ignored.diff")
            hidden_root = self.run_dir / "hidden_tests"
            if self.oracle:
                # copy the pinned tree into the run dir, so artifacts and run_checks below
                # see exactly what a fresh draw would have left there
                stored_root, hidden_rel = load_oracle(task, self.oracle)
                for rel in hidden_rel:
                    out = hidden_root / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(stored_root / rel, out)
            else:
                hidden = await write_hidden_tests(task, self.run_dir, route=self.route)
                hidden_rel = [p.relative_to(hidden_root).as_posix() for p in hidden]
            evidence.artifacts["hidden_tests"] = str(hidden_root)
            # spec 19, before the canaries exist: the lint reads only the oracle's own sources
            sealed = self._sealed(hidden_root, hidden_rel)
            kw = {} if sealed is None else {"sealed": sealed}
            evidence.sealed = sealed is not None
            # spec 18: planted beside the hidden checks, in the run dir's copy only (invariant 1)
            planted = canary.plant(hidden_rel)
            for rel, source in planted.items():
                (hidden_root / rel).parent.mkdir(parents=True, exist_ok=True)
                (hidden_root / rel).write_text(source)
            try:
                existing, sources = await self._existing_tests(task, graded, split.tests)
                checks = await run_checks(task, graded, hidden_root, hidden_rel, existing,
                                          self.run_dir / "checks_logs", canaries=sorted(planted), **kw)
                evidence.canaries = [c for c in checks if c.kind == "canary"]
                checks = [c for c in checks if c.kind != "canary"]
                # a test broken because the diff deleted what it covers is the change working
                evidence.checks = mark_obsoleted(checks, graded, sources)
                evidence.artifacts["checks_logs"] = str(self.run_dir / "checks_logs")
                # spec 13: or because a hunk the request needs changed what it asserted
                if verdict_route(evidence) == "regressed":
                    ablations = await ablate(task, graded, hidden_root, evidence.checks,
                                             self.run_dir / "ablation_logs", canaries=sorted(planted), **kw)
                    evidence.checks = mark_stale(evidence.checks, ablations)
                    evidence.artifacts["ablation"] = str(self.run_dir / "ablation.json")
            finally:
                # invariant 1: gone once graded, so no later `oracle pin` of this run dir takes one
                for rel in planted:
                    (hidden_root / rel).unlink(missing_ok=True)
        except InfraError as e:
            evidence.infra_errors.append(str(e))
        except Exception as e:  # still write checks.json; any failure here is UNKNOWN, not a crash
            evidence.infra_errors.append(f"{type(e).__name__}: {e}")
        (self.run_dir / "ablation.json").write_text(ablations.model_dump_json(indent=2))
        (self.run_dir / "checks.json").write_text(evidence.model_dump_json(indent=2))
        return evidence


if __name__ == "__main__":
    instance_id, run_dir = sys.argv[1], Path(sys.argv[2])
    oracle = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    safe = load_safe(instance_id)
    diff = (run_dir / "agent.diff").read_text()
    ev = asyncio.run(
        CodingAdapter(run_dir, oracle).gather_evidence(Request(task_id=instance_id, text=safe.request_text()), diff))
    print(json.dumps([c.model_dump() for c in ev.checks], indent=2))
    print(f"hidden checks: {sum(c.kind == 'hidden' for c in ev.checks)}  infra_errors: {ev.infra_errors}")
