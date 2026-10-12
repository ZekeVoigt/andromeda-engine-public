"""Oracle variance: draw 5 extra oracles per task, run gold and half against all 6, tabulate the spread.

    uv run python scripts/oracle_variance.py [--check-only]

Spec 01 made a run repeatable by pinning one drawn oracle per task. It said nothing about
how lucky that draw was. This measures it: 3 tasks x 6 oracles x 2 patches = 36 cells,
appended to fixtures/variance/results.tsv one line at a time so an interrupt costs one cell.

Exit 0 means every cell produced a clean record. The verdict spread is the finding, not the
pass condition -- a task whose gold patch lands PASS=4 FAIL=2 is exactly what spec 03 needs
to know, and this script still exits 0 for it.

Two constraints this file lives under:

- tests/test_boundaries.py sweeps scripts/ for grading leaks: the engine-side loader is
  load_safe, and the answer-bearing one must never be reachable from here. So never import
  the bare name `load` from anything, and never call it unqualified. `json.loads` and
  `load_safe`/`load_oracle` are all fine -- the sweep's regexes are defeated by the dot and
  by the underscore respectively. (This paragraph deliberately does not spell out the
  offending token, because the sweep reads this file too.)
- Colima only mounts $HOME (tests/conftest.py). Draw dirs go under runs/_variance/, inside
  the repo and therefore inside $HOME; a runs dir relocated outside it would fail as a
  confusing empty mount rather than an error.
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from andromeda.outcome.adapters.coding.docker import InfraError, timeout_from_env
from andromeda.outcome.adapters.coding.oracle import MANIFEST, STORE, OracleMismatch, load_oracle, pin
from andromeda.outcome.adapters.coding.task import load_safe
from andromeda.outcome.adapters.coding.test_writer import HIDDEN_PREFIX, write_hidden_tests
from andromeda.outcome.core.types import OutcomeRecord

ROOT = Path(__file__).resolve().parents[1]
# ROOT-relative, so patch_source/oracle_source stay readable in record.json. Every in-process
# call resolves with ROOT / ...; only the subprocess argv uses the bare relative form.
VARIANCE = Path("fixtures") / "variance"
PATCH_FIXTURES = Path("fixtures") / "bad_patches"
ORACLE_STORE = Path("fixtures") / "oracles"  # spec 01's store; read only, never written here
# The gate's SafeTasks. Deliberately a sibling of the per-task draw trees rather than a child
# of one: every dir directly under VARIANCE except this one is a task's draw tree, which is
# what lets the gate assert the set of draw trees exactly.
TASK_FILES = VARIANCE / "tasks"
RUNS = Path("runs")

TASKS = [
    "instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111-v059c6fdc75567943479b23ebca7c07b5e9a7f34c",
    "instance_qutebrowser__qutebrowser-c580ebf0801e5a3ecabc54f327498bb753c6d5f2-v2ef375ac784985212b1805e1d0431dc8f1b3c171",
    "instance_ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5",
]
PATCHES = ["gold", "half"]
DRAWS = 5
PINNED = "pinned"
ORACLES = [PINNED] + [f"draw-{n:02d}" for n in range(1, DRAWS + 1)]

COLUMNS = ["task", "oracle", "patch", "verdict", "truth", "n_hidden", "n_valid", "reason"]
DIST_COLUMNS = ["task", "patch", "PASS", "FAIL", "UNKNOWN", "pinned"]
VERDICTS = ["PASS", "FAIL", "UNKNOWN"]
REASON_WIDTH = 80

# decide() turns both of these into UNKNOWN. Only the first means the cell is worthless:
# "no valid hidden checks" is genuine oracle variance and must stay in the table.
INFRA_REASONS = ("infra error: ", "run failed before a verdict: ")


def short_task(instance_id: str) -> str:
    """instance_<org>__<repo>-<sha>-v... -> <repo>-<sha[:8]>"""
    m = re.search(r"__(.+?)-([0-9a-f]{40})", instance_id)
    return f"{m.group(1)}-{m.group(2)[:8]}" if m else instance_id


def oracle_dir(instance_id: str, oracle: str) -> Path:
    """ROOT-relative dir for one (task, oracle) pair. `pinned` is spec 01's store, untouched."""
    return ORACLE_STORE / instance_id if oracle == PINNED else VARIANCE / instance_id / oracle


def results_path() -> Path:
    """Named by SPEC.md and imported by the gate, so script and gate cannot disagree on it."""
    return ROOT / VARIANCE / "results.tsv"


def rel(path: Path) -> str:
    """A path for a progress line, relative to ROOT where it can be. Total by construction:
    Path.relative_to raises, and a progress print is no place to end a multi-hour run."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def timestamp() -> str:
    """Microseconds, matching cli.new_run_dir: second resolution collides on a fast retry."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


# --- the table ----------------------------------------------------------------------------

def tsv_line(row: dict[str, str], columns: list[str]) -> str:
    """The single writer behind both format_tsv and append_row, so header and rows cannot drift."""
    return "\t".join(row[c] for c in columns) + "\n"


def format_tsv(rows: list[dict[str, str]], columns: list[str]) -> str:
    return "".join(tsv_line(r, columns) for r in [dict(zip(columns, columns)), *rows])


def format_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    widths = {c: max(len(c), *(len(r[c]) for r in rows)) for c in columns}
    lines = ["  ".join(c.ljust(widths[c]) for c in columns)]
    lines += ["  ".join(r[c].ljust(widths[c]) for c in columns) for r in rows]
    return "\n".join(line.rstrip() for line in lines)


def format_markdown(rows: list[dict[str, str]], columns: list[str]) -> str:
    """A GitHub pipe table. REPORT.md holds this verbatim and the gate parses it back."""
    out = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    out += ["| " + " | ".join(r[c] for c in columns) + " |" for r in rows]
    return "\n".join(out)


def row(instance_id: str, oracle: str, patch: str, record: OutcomeRecord) -> dict[str, str]:
    """One results.tsv row. n_valid mirrors verdict.decide's own `valid` filter exactly."""
    hidden = [c for c in record.evidence.checks if c.kind == "hidden"]
    valid = [c for c in hidden if c.before == "fail"]
    reason = " ".join(record.reason.split())  # no tabs or newlines can reach a TSV cell
    if len(reason) > REASON_WIDTH:
        reason = reason[:REASON_WIDTH - 3] + "..."
    return dict(zip(COLUMNS, [short_task(instance_id), oracle, patch, record.verdict,
                              record.ground_truth, str(len(hidden)), str(len(valid)), reason]))


def distribution(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Pure: per-cell rows -> the 6-row (task, patch) verdict distribution.

    Counts include the pinned oracle, so every row sums to 6. The `pinned` column carries
    that oracle's own verdict, which is where spec 01's draw sits in its own distribution.
    """
    known = {short_task(t) for t in TASKS}
    unknown = sorted({r["task"] for r in rows} - known)
    if unknown:
        # dropping these silently would let a renamed task shrink the table and still pass
        raise ValueError(f"rows for unknown task(s): {', '.join(unknown)}")

    out = []
    for instance_id in TASKS:
        task = short_task(instance_id)
        for patch in PATCHES:
            cell = [r for r in rows if r["task"] == task and r["patch"] == patch]
            pinned = [r["verdict"] for r in cell if r["oracle"] == PINNED]
            counts = [str(sum(r["verdict"] == v for r in cell)) for v in VERDICTS]
            out.append(dict(zip(DIST_COLUMNS, [task, patch, *counts, pinned[0] if pinned else "-"])))
    return out


# --- results.tsv --------------------------------------------------------------------------

def read_results() -> list[dict[str, str]]:
    path = results_path()
    if not path.is_file():
        return []
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    if header != COLUMNS:
        raise ValueError(f"{path}: header {header} != {COLUMNS}")
    return [dict(zip(header, ln.split("\t"))) for ln in lines[1:]]


def append_row(new: dict[str, str]) -> None:
    """One line per successful cell, written immediately. This is what makes resume real."""
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.is_file() or not path.read_text().strip()
    with path.open("a") as fh:
        if fresh:
            fh.write(tsv_line(dict(zip(COLUMNS, COLUMNS)), COLUMNS))
        fh.write(tsv_line(new, COLUMNS))


def done_cells() -> set[tuple[str, str, str]]:
    return {(r["task"], r["oracle"], r["patch"]) for r in read_results()}


def remaining_cells() -> list[tuple[str, str, str]]:
    """Every (task, oracle, patch) not already in results.tsv, in a stable order."""
    done = done_cells()
    return [(t, o, p) for t in TASKS for o in ORACLES for p in PATCHES
            if (short_task(t), o, p) not in done]


def drawn(instance_id: str, oracle: str) -> bool:
    """True when that draw dir already loads through the real oracle check."""
    try:
        load_oracle(load_safe(instance_id), ROOT / oracle_dir(instance_id, oracle))
    except (OracleMismatch, KeyError):
        return False
    return True


def remaining_draws() -> list[tuple[str, str]]:
    return [(t, o) for t in TASKS for o in ORACLES if o != PINNED and not drawn(t, o)]


# --- drawing ------------------------------------------------------------------------------

def pinned_sources(instance_id: str) -> set[str]:
    """The run dirs this task's existing draws were pinned from, read off `drawn_from`.

    Without this, orphaned_draw would hand draw-01's own run dir back when draw-02 is asked
    for -- a successful draw leaves its tests on disk exactly like an interrupted one does.
    Two byte-identical oracles would then report zero variance between them, silently.
    """
    out = set()
    parent = ROOT / VARIANCE / instance_id
    if not parent.is_dir():
        return out
    for manifest in sorted(parent.glob(f"*/{MANIFEST}")):
        try:
            out.add(json.loads(manifest.read_text())["drawn_from"])
        except (ValueError, KeyError) as e:
            print(f"unreadable manifest {manifest}: {e}", flush=True)
    return out


def orphaned_draw(instance_id: str) -> Path | None:
    """The newest finished-but-unpinned draw under runs/_variance/<id>/, if there is one.

    A draw costs minutes to tens of minutes. If the process dies between write_hidden_tests
    returning and pin completing, the tests are on disk and a naive resume would redraw them.
    "Unpinned" is decided by drawn_from, not by the dir's name: a run dir whose name says
    draw-01 may legitimately be pinned as draw-02 after an earlier slot failed.
    """
    parent = ROOT / RUNS / "_variance" / instance_id
    if not parent.is_dir():
        return None
    already = pinned_sources(instance_id)
    finished = [d for d in sorted(parent.iterdir(), reverse=True)
                if d.is_dir() and str(d) not in already
                and any(p.name.startswith(HIDDEN_PREFIX)
                        for p in (d / "hidden_tests").rglob("*") if p.is_file())]
    return finished[0] if finished else None


async def draw(instance_id: str, oracle: str) -> Path:
    """Produce one loadable oracle dir, doing as little work as the disk allows."""
    dest = ROOT / oracle_dir(instance_id, oracle)
    if drawn(instance_id, oracle):
        print(f"already drawn: {short_task(instance_id)} {oracle}", flush=True)
        return dest

    orphan = orphaned_draw(instance_id)
    if orphan is not None:
        print(f"pinning orphaned draw: {rel(orphan)} -> {oracle}", flush=True)
        return pin(instance_id, orphan, dest=dest)

    run_dir = ROOT / RUNS / "_variance" / instance_id / f"{oracle}-{timestamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)  # the writer requires hidden_tests/ to start empty
    print(f"drawing: {short_task(instance_id)} {oracle} -> {rel(run_dir)}", flush=True)
    await write_hidden_tests(load_safe(instance_id), run_dir)
    return pin(instance_id, run_dir, dest=dest)


async def draw_all() -> int:
    """Every missing draw. One expired token on draw 7 must not cost draws 8-15."""
    todo = remaining_draws()
    failed = 0
    for i, (instance_id, oracle) in enumerate(todo, 1):
        print(f"[draw {i}/{len(todo)}] {short_task(instance_id)} {oracle}", flush=True)
        try:
            await draw(instance_id, oracle)
        except (InfraError, OracleMismatch, FileNotFoundError, KeyError) as e:
            failed += 1
            print(f"DRAW FAILED: {short_task(instance_id)} {oracle}: {type(e).__name__}: {e}", flush=True)
    return failed


# --- cells --------------------------------------------------------------------------------

def run_cell(instance_id: str, oracle: str, patch: str) -> OutcomeRecord | None:
    """One `outcome run`. None means the cell is worthless and must be retried, not recorded."""
    runs = ROOT / RUNS / instance_id
    before = set(runs.iterdir()) if runs.is_dir() else set()
    fixture = PATCH_FIXTURES / instance_id / f"{patch}.diff"
    argv = ["uv", "run", "outcome", "run", instance_id,
            "--patch", str(fixture), "--oracle", str(oracle_dir(instance_id, oracle))]
    try:
        proc = subprocess.run(argv, cwd=ROOT,
                              timeout=timeout_from_env("OUTCOME_VARIANCE_CELL_TIMEOUT", 5400))
        code = proc.returncode
    except subprocess.TimeoutExpired:
        print(f"CELL TIMEOUT: {short_task(instance_id)} {oracle} {patch}", flush=True)
        return None

    # microsecond run-dir names cannot be predicted, so identify the new one by difference
    new = sorted(set(runs.iterdir()) - before) if runs.is_dir() else []
    if len(new) != 1:
        print(f"CELL CRASHED: expected 1 new run dir, found {len(new)} (exit {code})", flush=True)
        return None
    record_path = new[0] / "record.json"
    if not record_path.is_file():
        print(f"CELL CRASHED: no record.json in {rel(new[0])} (exit {code})", flush=True)
        return None
    try:
        record = OutcomeRecord.model_validate_json(record_path.read_text())
    except ValueError as e:
        print(f"CELL CRASHED: invalid record.json in {rel(new[0])}: {e}", flush=True)
        return None

    if record.evidence.infra_errors:
        # Hazard 1: an infra flake is indistinguishable from real variance by verdict alone --
        # decide() returns UNKNOWN for both. infra_errors is the only discriminator, so a
        # flaked cell stays undone and resume retries it rather than polluting the table.
        print(f"CELL INFRA ERROR: {short_task(instance_id)} {oracle} {patch}: "
              f"{record.evidence.infra_errors[0][:200]}", flush=True)
        return None
    return record


def run_cells() -> int:
    todo = remaining_cells()
    failed = 0
    for i, (instance_id, oracle, patch) in enumerate(todo, 1):
        print(f"[cell {i}/{len(todo)}] {short_task(instance_id)} {oracle} {patch}", flush=True)
        record = run_cell(instance_id, oracle, patch)
        if record is None:
            failed += 1
            continue
        append_row(row(instance_id, oracle, patch, record))  # immediately, so resume is real
    return failed


# --- main ---------------------------------------------------------------------------------

def task_problems() -> list[str]:
    """TASKS must match fixtures/oracles/ exactly, or the pinned column measures nothing."""
    problems = []
    if len({short_task(t) for t in TASKS}) != len(TASKS):
        problems.append("short_task collides across TASKS -- the resume key would merge two tasks")
    if ROOT / ORACLE_STORE != STORE:
        # oracle_dir hands the relative form to the subprocess; if the store moved, every
        # `pinned` cell would silently run against the wrong directory
        problems.append(f"ORACLE_STORE {ROOT / ORACLE_STORE} != oracle.STORE {STORE}")
    pinned = {p.name for p in STORE.iterdir() if p.is_dir()} if STORE.is_dir() else set()
    if pinned != set(TASKS):
        problems.append(f"TASKS != fixtures/oracles/: missing {sorted(set(TASKS) - pinned)}, "
                        f"extra {sorted(pinned - set(TASKS))}")
    for instance_id in TASKS:
        for patch in PATCHES:
            fixture = ROOT / PATCH_FIXTURES / instance_id / f"{patch}.diff"
            if not fixture.is_file():
                problems.append(f"missing patch fixture: {rel(fixture)}")
    return problems


def write_task_files() -> list[str]:
    """The full SafeTask per task, for the gate to load_oracle against without the network.

    model_dump, not the `task` module's CLI dump: that one drops problem_statement,
    requirements and interface, which feed prompt_sha256.

    Never overwrites. A resumed run that quietly rewrote these would move the committed
    ansible copy out of step with fixtures/replay/ansible-gold/task.json, and would hide the
    dataset drift behind a fixture update instead of reporting it.
    """
    drifted = []
    for instance_id in TASKS:
        out = ROOT / TASK_FILES / f"{instance_id}.json"
        text = json.dumps(load_safe(instance_id).model_dump(), indent=2, default=str) + "\n"
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text)
            print(f"task file written: {rel(out)}", flush=True)
        elif out.read_text() != text:
            drifted.append(f"{rel(out)} differs from the live dataset row")
    return drifted


def report(draws_failed: int, cells_failed: int) -> int:
    rows = read_results()
    if rows:
        print()
        print(format_table(rows, COLUMNS))
        print()
        print(format_markdown(distribution(rows), DIST_COLUMNS))
    print()
    print(f"results: {rel(results_path())} ({len(rows)} rows)")
    if draws_failed:
        print(f"{draws_failed} draw(s) failed -- re-run to retry them")
    if cells_failed:
        print(f"{cells_failed} cell(s) produced no clean record -- re-run to retry them")
    return 1 if (draws_failed or cells_failed) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-only", action="store_true",
                        help="report the remaining work, draw nothing and run nothing")
    args = parser.parse_args(argv)

    problems = task_problems()
    for p in problems:  # all of them: fixing one at a time costs a round trip each
        print(f"SETUP PROBLEM: {p}")
    if problems:
        return 1

    draws, cells = remaining_draws(), remaining_cells()
    print(f"{len(draws)} draws remaining, {len(cells)} cells remaining")
    if args.check_only:
        # returns BEFORE asyncio.run and before the credential pre-flight, so inspecting a long run's
        # remaining work never needs a credential or a container
        for instance_id, oracle in draws:
            print(f"  draw  {short_task(instance_id)} {oracle}")
        for instance_id, oracle, patch in cells:
            print(f"  cell  {short_task(instance_id)} {oracle} {patch}")
        return 0

    # Drawing needs a model credential held outside the box, which the public engine does
    # not carry: write_hidden_tests refuses, so a draw here stops before any box opens.
    for d in write_task_files():
        # not fatal: the run can proceed, but the gate and the report must say so
        print(f"TASK FILE DRIFT: {d}")

    draws_failed = asyncio.run(draw_all())  # the one and only asyncio.run in this script
    return report(draws_failed, run_cells())


if __name__ == "__main__":
    sys.exit(main())
