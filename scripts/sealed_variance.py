"""Sealed-oracle variance: draw 2 extra sealed oracles per task, grade gold and half on all 3.

    uv run python scripts/sealed_variance.py [--check-only]

Spec 19's AC12. Spec 01's practice pins a sealed oracle only when it passes AC9, so the pinned
three say how good a SELECTED draw is. This measures unselected ones: 3 tasks x 3 oracles (the
pinned sealed one + SEALED_DRAWS fresh draws) x 2 patches = 18 cells, appended to
fixtures/sealed-variance/results.tsv one line at a time so an interrupt costs one cell. Its
columns are spec 02's plus n_keys n_strong n_leaked, read off each grading's seal.json.

**Every draw is kept.** A variance draw is never redrawn -- that would select for agreement --
so it is pinned with `pin(check=False)`: a leaking draw is stored as drawn and its gradings drop
the leaked keys (n_leaked counts them), and an unloadable one grades UNKNOWN with its SealError
as the named cause, recorded like any other verdict rather than retried as a flake.

It lives under spec 02's two constraints (see scripts/oracle_variance.py's docstring): the
boundary sweep over scripts/, and draw dirs under runs/_sealed_variance/, inside $HOME.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_variance as ov  # noqa: E402  spec 02's helpers: short_task, the TSV and table writers

from andromeda.outcome.adapters.coding.docker import InfraError, timeout_from_env  # noqa: E402
from andromeda.outcome.adapters.coding.oracle import MANIFEST, OracleMismatch, load_oracle, pin  # noqa: E402
from andromeda.outcome.adapters.coding.task import load_safe  # noqa: E402
from andromeda.outcome.adapters.coding.test_writer import HIDDEN_PREFIX, write_hidden_tests  # noqa: E402
from andromeda.outcome.core.types import OutcomeRecord  # noqa: E402

ROOT = ov.ROOT
SEALED_STORE = Path("fixtures") / "sealed-oracles"   # the three pinned by hand; read only here
VARIANCE = Path("fixtures") / "sealed-variance"
RUNS = Path("runs")
TASKS = ov.TASKS
PATCHES = ov.PATCHES
SEALED_DRAWS = 2
PINNED = ov.PINNED
ORACLES = [PINNED] + [f"draw-{n:02d}" for n in range(1, SEALED_DRAWS + 1)]
COLUMNS = [*ov.COLUMNS, "n_keys", "n_strong", "n_leaked"]
# a sealed oracle that seal.load refuses is a finding about the draw, not a flake to retry
SEAL_ERROR = "sealed oracle"


def oracle_dir(instance_id: str, oracle: str) -> Path:
    """ROOT-relative dir for one (task, oracle) pair; `pinned` is fixtures/sealed-oracles/."""
    return SEALED_STORE / instance_id if oracle == PINNED else VARIANCE / instance_id / oracle


def results_path() -> Path:
    return ROOT / VARIANCE / "results.tsv"


def row(instance_id: str, oracle: str, patch: str, record: OutcomeRecord, sealed: dict) -> dict[str, str]:
    """Spec 02's row, plus the grading's own seal.json counts ("-" where it wrote none)."""
    out = ov.row(instance_id, oracle, patch, record)
    for column, field in (("n_keys", "keys"), ("n_strong", "strong"), ("n_leaked", "leaked")):
        out[column] = str(len(sealed[field])) if field in sealed else "-"
    return out


def read_results() -> list[dict[str, str]]:
    path = results_path()
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()] if path.is_file() else []
    if not lines:
        return []
    if lines[0].split("\t") != COLUMNS:
        raise ValueError(f"{path}: header {lines[0].split(chr(9))} != {COLUMNS}")
    return [dict(zip(COLUMNS, ln.split("\t"))) for ln in lines[1:]]


def append_row(new: dict[str, str]) -> None:
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.is_file() or not path.read_text().strip()
    with path.open("a") as fh:
        if fresh:
            fh.write(ov.tsv_line(dict(zip(COLUMNS, COLUMNS)), COLUMNS))
        fh.write(ov.tsv_line(new, COLUMNS))


def remaining_cells() -> list[tuple[str, str, str]]:
    done = {(r["task"], r["oracle"], r["patch"]) for r in read_results()}
    return [(t, o, p) for t in TASKS for o in ORACLES for p in PATCHES if (ov.short_task(t), o, p) not in done]


def drawn(instance_id: str, oracle: str) -> bool:
    """A draw is kept once pinned: its manifest exists, whether or not it loads cleanly."""
    return (ROOT / oracle_dir(instance_id, oracle) / MANIFEST).is_file()


def remaining_draws() -> list[tuple[str, str]]:
    return [(t, o) for t in TASKS for o in ORACLES if o != PINNED and not drawn(t, o)]


def pinned_sources(instance_id: str) -> set[str]:
    out = set()
    parent = ROOT / VARIANCE / instance_id
    for manifest in sorted(parent.glob(f"*/{MANIFEST}")) if parent.is_dir() else []:
        try:
            out.add(json.loads(manifest.read_text())["drawn_from"])
        except (ValueError, KeyError) as e:
            print(f"unreadable manifest {manifest}: {e}", flush=True)
    return out


def orphaned_draw(instance_id: str) -> Path | None:
    """A finished but unpinned draw under runs/_sealed_variance/<id>/: pinned, never redrawn."""
    parent = ROOT / RUNS / "_sealed_variance" / instance_id
    if not parent.is_dir():
        return None
    already = pinned_sources(instance_id)
    finished = [d for d in sorted(parent.iterdir(), reverse=True)
                if d.is_dir() and str(d) not in already and (d / "expected").is_dir()
                and any(p.name.startswith(HIDDEN_PREFIX) for p in (d / "hidden_tests").rglob("*") if p.is_file())]
    return finished[0] if finished else None


async def draw(instance_id: str, oracle: str) -> Path:
    dest = ROOT / oracle_dir(instance_id, oracle)
    orphan = orphaned_draw(instance_id)
    if orphan is not None:
        print(f"pinning orphaned draw: {ov.rel(orphan)} -> {oracle}", flush=True)
        return pin(instance_id, orphan, dest=dest, check=False)
    run_dir = ROOT / RUNS / "_sealed_variance" / instance_id / f"{oracle}-{ov.timestamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    print(f"drawing: {ov.short_task(instance_id)} {oracle} -> {ov.rel(run_dir)}", flush=True)
    await write_hidden_tests(load_safe(instance_id), run_dir)  # sealed: test_writer.ORACLE_FORM
    return pin(instance_id, run_dir, dest=dest, check=False)


async def draw_all() -> int:
    todo, failed = remaining_draws(), 0
    for i, (instance_id, oracle) in enumerate(todo, 1):
        print(f"[draw {i}/{len(todo)}] {ov.short_task(instance_id)} {oracle}", flush=True)
        try:
            await draw(instance_id, oracle)
        except (InfraError, OracleMismatch, FileNotFoundError, KeyError) as e:
            failed += 1
            print(f"DRAW FAILED: {ov.short_task(instance_id)} {oracle}: {type(e).__name__}: {e}", flush=True)
    return failed


def run_cell(instance_id: str, oracle: str, patch: str) -> tuple[OutcomeRecord, dict] | None:
    """One `outcome run --patch F --oracle DIR`, as spec 02's; None means retry, not record."""
    runs = ROOT / RUNS / instance_id
    before = set(runs.iterdir()) if runs.is_dir() else set()
    argv = ["uv", "run", "outcome", "run", instance_id, "--patch",
            str(ov.PATCH_FIXTURES / instance_id / f"{patch}.diff"), "--oracle", str(oracle_dir(instance_id, oracle))]
    try:
        code = subprocess.run(argv, cwd=ROOT, timeout=timeout_from_env("OUTCOME_VARIANCE_CELL_TIMEOUT", 5400)).returncode
    except subprocess.TimeoutExpired:
        print(f"CELL TIMEOUT: {ov.short_task(instance_id)} {oracle} {patch}", flush=True)
        return None
    new = sorted(set(runs.iterdir()) - before) if runs.is_dir() else []
    if len(new) != 1 or not (new[0] / "record.json").is_file():
        print(f"CELL CRASHED: {len(new)} new run dir(s), exit {code}", flush=True)
        return None
    try:
        record = OutcomeRecord.model_validate_json((new[0] / "record.json").read_text())
    except ValueError as e:
        print(f"CELL CRASHED: invalid record.json in {ov.rel(new[0])}: {e}", flush=True)
        return None
    errors = record.evidence.infra_errors
    if errors and not all(SEAL_ERROR in e for e in errors):
        print(f"CELL INFRA ERROR: {ov.short_task(instance_id)} {oracle} {patch}: {errors[0][:200]}", flush=True)
        return None
    sealed_json = new[0] / "seal.json"
    return record, (json.loads(sealed_json.read_text()) if sealed_json.is_file() else {})


def run_cells() -> int:
    todo, failed = remaining_cells(), 0
    for i, (instance_id, oracle, patch) in enumerate(todo, 1):
        print(f"[cell {i}/{len(todo)}] {ov.short_task(instance_id)} {oracle} {patch}", flush=True)
        got = run_cell(instance_id, oracle, patch)
        if got is None:
            failed += 1
            continue
        append_row(row(instance_id, oracle, patch, *got))
    return failed


def task_problems() -> list[str]:
    problems = []
    for instance_id in TASKS:
        d = ROOT / oracle_dir(instance_id, PINNED)
        try:
            load_oracle(load_safe(instance_id), d)
        except (OracleMismatch, KeyError, FileNotFoundError) as e:
            problems.append(f"no loadable pinned sealed oracle at {ov.rel(d)}: {e}")
        for patch in PATCHES:
            if not (ROOT / ov.PATCH_FIXTURES / instance_id / f"{patch}.diff").is_file():
                problems.append(f"missing patch fixture: {instance_id}/{patch}.diff")
    return problems


def report(draws_failed: int, cells_failed: int) -> int:
    rows = read_results()
    if rows:
        print()
        print(ov.format_markdown(rows, COLUMNS))
    print(f"\nresults: {ov.rel(results_path())} ({len(rows)} rows)")
    if draws_failed:
        print(f"{draws_failed} draw(s) failed -- re-run to retry them")
    if cells_failed:
        print(f"{cells_failed} cell(s) produced no clean record -- re-run to retry them")
    return 1 if (draws_failed or cells_failed) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-only", action="store_true",
                        help="report the remaining work, draw nothing and run nothing")
    args = parser.parse_args(argv)
    draws, cells = remaining_draws(), remaining_cells()
    print(f"{len(draws)} draws remaining, {len(cells)} cells remaining")
    if args.check_only:
        for instance_id, oracle in draws:
            print(f"  draw  {ov.short_task(instance_id)} {oracle}")
        for instance_id, oracle, patch in cells:
            print(f"  cell  {ov.short_task(instance_id)} {oracle} {patch}")
        return 0
    problems = task_problems()
    for p in problems:
        print(f"SETUP PROBLEM: {p}")
    if problems:
        return 1
    # Drawing needs a model credential held outside the box, which the public engine does
    # not carry: write_hidden_tests refuses, so a draw here stops before any box opens.
    draws_failed = asyncio.run(draw_all())  # the one and only asyncio.run in this script
    return report(draws_failed, run_cells())


if __name__ == "__main__":
    sys.exit(main())
