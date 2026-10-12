"""Pinned oracles: one drawn hidden-test tree per task, stored in git beside a manifest of what it was drawn against.

Drawing an oracle costs ~40 minutes and never repeats itself, so two runs of the same task
are otherwise judged by two different oracles. A pinned oracle is drawn once by hand
(`pin`), committed, and reused byte-for-byte (`load_oracle`) until something it was drawn
against moves -- at which point the manifest check says which thing moved.

Store layout. The manifest sits *beside* the tree, never inside it: runner._run_side copies
the whole hidden-test dir into /app, and a manifest.json riding along would land in the repo
under test.

    fixtures/oracles/<instance_id>/manifest.json
    fixtures/oracles/<instance_id>/hidden_tests/<repo-relative>/test_outcome_*.py

Spec 19: a sealed oracle adds its expectations beside the tree, never inside it, so nothing
that copies the tree into a box can carry one (fixtures/sealed-oracles/ holds three):

    <oracle>/expected/<repo-relative>/test_outcome_*.json
"""

import hashlib
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from andromeda.outcome.adapters.coding.docker import PROJECT_ROOT, claude_version
from andromeda.outcome.adapters.coding.task import (
    DATASET_REVISION,
    SafeTask,
    load_safe,
    unpinned_image_error,
)
from andromeda.outcome.adapters.coding import seal
from andromeda.outcome.adapters.coding.test_writer import EXPECTED, HIDDEN_PREFIX, build_prompt

STORE = PROJECT_ROOT / "fixtures" / "oracles"  # module global: tests monkeypatch it
SEALED_STORE = PROJECT_ROOT / "fixtures" / "sealed-oracles"  # spec 19; the bare --oracle stays STORE
MANIFEST = "manifest.json"
TREE = "hidden_tests"
# A draw leaves these behind when the writer runs its own tests. write_hidden_tests deletes
# them, but pin reads the dir off disk and must not depend on that having happened: a
# __pycache__/test_outcome_x.cpython-312.pyc passes the name check and is not a test file.
JUNK_DIRS = ("__pycache__", ".pytest_cache")


class OracleMismatch(Exception):
    """A stored oracle no longer matches what it was drawn against.

    Deliberately not an InfraError: nothing failed in docker, the fixture is stale.
    gather_evidence's generic `except` routes it into Evidence.infra_errors, so a run with
    a stale oracle ends UNKNOWN rather than silently reusing tests drawn against other code.
    """


class OracleManifest(BaseModel):
    instance_id: str
    base_commit: str
    image: str
    dataset_revision: str
    claude_version: str
    prompt_sha256: str
    files: dict[str, str]  # tree-relative path -> sha256
    drawn_at: datetime
    drawn_from: str
    # Spec 19. Defaulted, so every stored manifest loads unedited as a reported oracle.
    form: Literal["reported", "sealed"] = "reported"
    expected: dict[str, str] = {}  # tree-relative path under expected/ -> sha256


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def live_fields(task: SafeTask, prompt: str) -> dict[str, str]:
    """The five values re-read at load time and compared against the manifest.

    Everything here can move without anyone touching the stored fixture: the row, the image
    tag, the dataset revision, the pinned Claude version, the prompt the model was given.

    `prompt` is an argument rather than build_prompt(task) computed here, because two stores
    hash two different prompts from this one function: the oracle store hashes the test
    writer's prompt, the agent store hashes PREAMBLE + task.request_text().
    """
    return {
        "base_commit": task.base_commit,
        "image": task.image,
        "dataset_revision": DATASET_REVISION,
        "claude_version": claude_version(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }


def tree_hashes(tree: Path) -> dict[str, str]:
    """Every file actually on disk under `tree`, keyed by tree-relative posix path."""
    if not tree.is_dir():
        return {}
    return {p.relative_to(tree).as_posix(): sha256_file(p)
            for p in sorted(tree.rglob("*")) if p.is_file()}


def is_stored_test(path: Path, rel: Path) -> bool:
    """A file the store should keep: a hidden test, not a cache artifact of one."""
    return path.name.startswith(HIDDEN_PREFIX) and not any(part in JUNK_DIRS for part in rel.parts)


def pin(instance_id: str, run_dir: str | Path, dest: Path | None = None, *, check: bool = True) -> Path:
    """Copy one real draw's hidden tests into the store and record what it was drawn against.

    `dest` writes the oracle somewhere other than the store -- spec 02 draws five extra
    oracles per task into fixtures/variance/ without disturbing the pinned one. STORE is
    read here at call time, never bound as a default argument: tests monkeypatch it.

    Spec 19: `check=False` skips the sealed draw's refusals, and only scripts/sealed_variance.py
    passes it: a variance draw is never redrawn, so a leaking or unloadable one is kept as
    drawn, and its gradings drop the leaked keys or end UNKNOWN naming the SealError.
    """
    run_dir = Path(run_dir)
    source = run_dir / TREE
    if not source.is_dir():
        raise FileNotFoundError(f"no {TREE}/ under {run_dir}")
    task = load_safe(instance_id)
    # Before dest.mkdir and before staging: a refused pin must leave nothing on disk.
    if "@sha256:" not in task.image:
        raise unpinned_image_error(task)

    # Spec 19: expected/ beside the draw's tree means sealed. Checked here too, before
    # anything is written: an unusable or leaking sealed draw leaves the store byte-equal.
    expected_src = run_dir / EXPECTED
    form = "sealed" if expected_src.is_dir() else "reported"
    if form == "sealed" and check:
        _check_sealed(source, expected_src)

    dest = Path(dest) if dest is not None else STORE / instance_id
    tree = dest / TREE
    dest.mkdir(parents=True, exist_ok=True)
    # Staged, then swapped in last. A re-pin whose draw turns out to be unusable must leave
    # the oracle already in the store intact -- the plan budgets a redraw, so this happens.
    staging = dest / f".{TREE}.incoming"
    expected_staging = dest / f".{EXPECTED}.incoming"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(expected_staging, ignore_errors=True)
    try:
        kept, dropped_support = _copy_tests(source, staging)
        if not kept:
            raise FileNotFoundError(f"no {HIDDEN_PREFIX}* files under {source}")
        _warn_about(dropped_support)
        if form == "sealed":
            for rel in seal.expectation_files(expected_src):
                (expected_staging / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(expected_src / rel, expected_staging / rel)
        shutil.rmtree(tree, ignore_errors=True)
        staging.rename(tree)
        # a reported re-pin over a sealed oracle takes its expected/ away with it
        shutil.rmtree(dest / EXPECTED, ignore_errors=True)
        if form == "sealed":
            expected_staging.rename(dest / EXPECTED)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(expected_staging, ignore_errors=True)

    manifest = OracleManifest(
        instance_id=instance_id,
        **live_fields(task, build_prompt(task, form)),
        files=tree_hashes(tree),
        drawn_at=datetime.now(timezone.utc),
        drawn_from=str(run_dir),
        form=form,
        expected=tree_hashes(dest / EXPECTED) if form == "sealed" else {},
    )
    # a reported manifest is written exactly as 18 wrote it: the two new fields at default
    exclude = {"form", "expected"} if form == "reported" else None
    (dest / MANIFEST).write_text(manifest.model_dump_json(indent=2, exclude=exclude) + "\n")
    return dest


def _check_sealed(source: Path, expected_src: Path) -> None:
    """Refuse a sealed draw `seal.load` refuses, or one with a leaked key, naming it."""
    observers = {p.relative_to(source).as_posix(): p.read_text(errors="replace")
                 for p in sorted(source.rglob("*")) if p.is_file() and is_stored_test(p, p.relative_to(source))}
    exp = seal.load(expected_src, sorted(observers))
    leaked = seal.lint(exp, observers)
    if leaked:
        raise seal.SealError(f"sealed oracle leaks {len(leaked)} expected value(s) into its observers: "
                             + ", ".join(leaked))


def _copy_tests(source: Path, into: Path) -> tuple[int, list[str]]:
    """Copy the hidden tests out of a draw, reporting what was left behind."""
    kept, dropped_support = 0, []
    for src in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = src.relative_to(source)
        if not is_stored_test(src, rel):
            # caches and stray notes the writer left behind: they would ride into /app
            print(f"skipped (not a {HIDDEN_PREFIX}* test file): {rel}", file=sys.stderr)
            if not any(part in JUNK_DIRS for part in rel.parts):
                # not .py-only: a drawn test that reads a sibling sample.json or .yaml
                # breaks exactly the same way when the pinned run leaves it behind
                dropped_support.append(rel.as_posix())
            continue
        out = into / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)  # not copy2: mtimes would be noise in git
        kept += 1
    return kept, dropped_support


def _warn_about(dropped_support: list[str]) -> None:
    """Still a warning, not a raise: a dropped support file may or may not matter, and the
    operator is the one who can tell. The unpinned-image case moved to the top of `pin` and
    became a refusal -- that one is never recoverable."""
    if dropped_support:
        # A fresh draw copies the WHOLE hidden dir into /app; a pinned one copies only the
        # manifest's files. So a conftest.py or __init__.py the writer relied on is dropped
        # here, and the pinned tests may error on import where the fresh ones passed.
        print(f"WARNING: dropped {len(dropped_support)} non-test file(s) the pinned tests may need: "
              f"{', '.join(dropped_support)}", file=sys.stderr)


def load_oracle(task: SafeTask, oracle_dir: str | Path) -> tuple[Path, list[str]]:
    """Validate a stored oracle and return it in the shape run_checks takes: (hidden_root, hidden_rel).

    Takes a SafeTask rather than an instance id, so it needs no network. Collects *all*
    drift into one message -- one run tells you everything that moved, not the first thing.
    """
    oracle_dir = Path(oracle_dir)
    manifest_path = oracle_dir / MANIFEST
    if not manifest_path.is_file():
        raise OracleMismatch(f"no {MANIFEST} in {oracle_dir}")
    try:
        manifest = OracleManifest.model_validate_json(manifest_path.read_text())
    except ValidationError as e:
        # truncated, hand-edited, or written by an older OracleManifest. Every way a stored
        # oracle can be unusable is an OracleMismatch, so no caller needs a second except.
        raise OracleMismatch(f"{manifest_path}: unreadable manifest: {e}") from e
    tree = oracle_dir / TREE

    drift = []
    if manifest.instance_id != task.instance_id:
        drift.append(f"instance_id: manifest {manifest.instance_id!r} != live {task.instance_id!r}")
    for field, live in live_fields(task, build_prompt(task, manifest.form)).items():
        stored = getattr(manifest, field)
        if stored != live:
            drift.append(f"{field}: manifest {stored!r} != live {live!r}")

    on_disk = tree_hashes(tree)
    for rel, digest in sorted(manifest.files.items()):
        if rel not in on_disk:
            drift.append(f"files[{rel}]: missing from {tree}")
        elif on_disk[rel] != digest:
            drift.append(f"files[{rel}]: sha256 {on_disk[rel]} != manifest {digest}")
    for rel in sorted(set(on_disk) - set(manifest.files)):
        # unlisted files are not inert: the whole tree is copied into /app
        drift.append(f"files[{rel}]: on disk but not in the manifest")
    # spec 19: the expectations are the verdict's answers; a moved one is drift like a test
    expected = oracle_dir / EXPECTED
    on_disk = tree_hashes(expected)
    for rel, digest in sorted(manifest.expected.items()):
        if rel not in on_disk:
            drift.append(f"expected[{rel}]: missing from {expected}")
        elif on_disk[rel] != digest:
            drift.append(f"expected[{rel}]: sha256 {on_disk[rel]} != manifest {digest}")
    for rel in sorted(set(on_disk) - set(manifest.expected)):
        drift.append(f"expected[{rel}]: on disk but not in the manifest")

    if drift:
        raise OracleMismatch(f"{oracle_dir}: " + "; ".join(drift))
    return tree, sorted(manifest.files)


def load_expectations(oracle_dir: str | Path) -> seal.Expectations | None:
    """A stored oracle's expectations: None for a reported oracle. Call after `load_oracle`,
    which has already checked every expected/ file against the manifest."""
    oracle_dir = Path(oracle_dir)
    try:
        manifest = OracleManifest.model_validate_json((oracle_dir / MANIFEST).read_text())
    except (OSError, ValidationError) as e:
        raise OracleMismatch(f"{oracle_dir / MANIFEST}: unreadable manifest: {e}") from e
    if manifest.form == "reported":
        return None
    return seal.load(oracle_dir / EXPECTED, sorted(manifest.files))


USAGE = "usage: python -m andromeda.outcome.adapters.coding.oracle (pin <id> <run_dir> [dest] | check <id>)"

def main(argv: list[str]) -> str:
    """Dispatch for `python -m ...oracle`, returning the line to print.

    A function rather than a bare __main__ block so the argv shapes have a caller that is
    not a human: `pin <id> <run_dir> <dest>` is otherwise reachable only by typing it.
    """
    match argv:
        case ["pin", instance_id, run_dir]:
            return f"pinned: {pin(instance_id, run_dir)}"
        case ["pin", instance_id, run_dir, dest]:
            return f"pinned: {pin(instance_id, run_dir, Path(dest))}"
        case ["check", instance_id]:
            root, rel = load_oracle(load_safe(instance_id), STORE / instance_id)
            return f"ok: {len(rel)} file(s) under {root}"
        case _:
            raise SystemExit(USAGE)


if __name__ == "__main__":
    print(main(sys.argv[1:]))
