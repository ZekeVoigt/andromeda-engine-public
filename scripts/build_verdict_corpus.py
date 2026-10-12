"""Freeze spec 02's 36 recorded cells into a committed corpus the verdict rules can be tested against.

    uv run python scripts/build_verdict_corpus.py
    uv run python scripts/build_verdict_corpus.py --ablations-only   # spec 13's step alone

Spec 02 ran 3 tasks x 6 oracles x 2 patches and wrote one record.json per cell under runs/.
runs/ is gitignored and local, so those 36 measurements cannot be a regression test on their
own. This lifts the part that matters -- the checks, the ground truth, and the verdict spec 02
measured -- into fixtures/verdict-corpus/, which is committed and replayable with no Docker.

Needs Docker and vendor/ exactly once per (task, patch): the existing test *sources* were never
recorded, and mark_obsoleted needs them. Everything else is read off disk.

Sources are deduped per (task, patch) rather than inlined per cell. Only 6 distinct source sets
exist across the 36 cells; inlining would commit the same qutebrowser test files six times over.
Each cell names its patch, and the test resolves sources/<patch>.json.

Spec 13 adds a last step: every cell whose replay routes `regressed` under today's
mark_obsoleted has its hunks ablated once, in Docker, against that cell's oracle tree, and the
result is committed as ablations/<oracle>-<patch>.json -- the same shape as a run's
ablation.json, which tests/test_verdict_corpus.py replays through mark_stale.

One constraint this file lives under: tests/test_boundaries.py sweeps scripts/ for leaks of the
answer-bearing task loader. Never import the bare loader name from anything, never call it
unqualified, and never spell the answer field names. `json.load(` and `load_safe` are both fine --
the sweep's regexes are defeated by the dot and by the underscore respectively. Same rule
scripts/oracle_variance.py documents.
"""

import asyncio
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from andromeda.outcome.adapters.coding.adapter import CodingAdapter, ablate, mark_obsoleted
from andromeda.outcome.adapters.coding.oracle import load_oracle
from andromeda.outcome.adapters.coding.task import load_safe
from andromeda.outcome.core.types import Check, Evidence
from andromeda.outcome.core.verdict import route

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
CORPUS = ROOT / "fixtures" / "verdict-corpus"
# Colima only mounts $HOME (tests/conftest.py), so the scratch run dir stays inside the repo.
RUN_DIR = RUNS / "_verdict_corpus"

PINNED = "pinned"
ORACLE_STORE = "fixtures/oracles"  # spec 01's store; every other oracle is a spec 02 draw
VARIANCE = "fixtures/variance"  # spec 02's draws: <task>/draw-NN

EXPECTED_CELLS = 36
EXPECTED_SOURCE_SETS = 6


def short_task(instance_id: str) -> str:
    """instance_<org>__<repo>-<sha>-v... -> <repo>-<sha[:8]>. Same form oracle_variance.py prints."""
    m = re.search(r"__(.+?)-([0-9a-f]{40})", instance_id)
    return f"{m.group(1)}-{m.group(2)[:8]}" if m else instance_id


def oracle_name(oracle_source: str) -> str:
    """`fixtures/oracles/<task>` is the pinned draw; everything else ends in `draw-NN`."""
    return PINNED if oracle_source.startswith(ORACLE_STORE) else oracle_source.rstrip("/").rsplit("/", 1)[-1]


def patch_name(patch_source: str) -> str:
    """`fixtures/bad_patches/<task>/gold.diff` -> `gold`."""
    return Path(patch_source).stem


def read_cells() -> tuple[list[dict], list[tuple[str, str, str, str]]]:
    """One dict per distinct (task, oracle, patch), plus the later re-runs that were ignored.

    A cell can legitimately be re-run against the same pinned oracle and the same committed
    patch -- spec 03's own end-to-end verification did exactly that, on a task this corpus
    measures. `verdict_before` must stay the verdict *spec 02* measured, so the EARLIEST
    record for an id is the one that counts. A later re-run carries the post-spec-03 verdict
    and would silently corrupt the column the corpus exists to preserve, so it is dropped and
    reported rather than allowed to win.
    """
    by_id: dict[tuple[str, str, str], dict] = {}
    ignored: list[tuple[str, str, str, str]] = []
    for record in sorted(RUNS.rglob("record.json")):
        with record.open() as fh:
            d = json.load(fh)
        if d["patch_source"] == "agent" or d["oracle_source"] == "fresh":
            continue  # an ad-hoc run, not one of spec 02's cells
        cell = {
            "task": d["request"]["task_id"],
            "oracle": oracle_name(d["oracle_source"]),
            "patch": patch_name(d["patch_source"]),
            "diff_path": d["patch_source"],
            "checks": d["evidence"]["checks"],
            "ground_truth": d["ground_truth"],
            "verdict_before": d["verdict"],
            "recorded_at": d["started_at"],
        }
        key = (cell["task"], cell["oracle"], cell["patch"])
        kept = by_id.get(key)
        if kept is None:
            by_id[key] = cell
        elif cell["recorded_at"] < kept["recorded_at"]:
            ignored.append((*key, kept["recorded_at"]))
            by_id[key] = cell
        else:
            ignored.append((*key, cell["recorded_at"]))
    cells = sorted(by_id.values(), key=lambda c: (c["task"], c["oracle"], c["patch"]))
    return cells, ignored


async def read_sources(task_id: str, diff_path: str) -> dict[str, str]:
    """The existing test files the adapter would select, as they stand on the base commit."""
    task = load_safe(task_id)
    diff = (ROOT / diff_path).read_text()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    existing, sources = await CodingAdapter(RUN_DIR)._existing_tests(task, diff)
    missing = [p for p in existing if p not in sources]
    if missing:
        raise SystemExit(f"selected {len(existing)} existing files but read none of {missing}")
    return sources


def oracle_dir(task_id: str, oracle: str) -> Path:
    return ROOT / (f"{ORACLE_STORE}/{task_id}" if oracle == PINNED else f"{VARIANCE}/{task_id}/{oracle}")


def ablation_path(cell: dict) -> Path:
    return CORPUS / cell["task"] / "ablations" / f"{cell['oracle']}-{cell['patch']}.json"


def written_cells() -> list[dict]:
    """The cells as committed, read back: what the replay reads, so what the step judges."""
    paths = sorted(p for p in CORPUS.rglob("*.json") if p.parent.name not in ("sources", "ablations"))
    return [json.loads(p.read_text()) for p in paths]


async def write_ablations() -> int:
    """Spec 13: ablate every cell that routes `regressed` after per-test mark_obsoleted."""
    for stale in CORPUS.rglob("ablations"):
        shutil.rmtree(stale)
    written = 0
    for cell in written_cells():
        sources = json.loads((CORPUS / cell["task"] / "sources" / f"{cell['patch']}.json").read_text())
        diff = (ROOT / cell["diff_path"]).read_text()
        checks = mark_obsoleted([Check.model_validate(c) for c in cell["checks"]], diff, sources)
        if route(Evidence(checks=checks)) != "regressed":
            continue
        where = f"{short_task(cell['task'])} {cell['oracle']} {cell['patch']}"
        print(f"ablating {where}", flush=True)
        hidden_root, _ = load_oracle(load_safe(cell["task"]), oracle_dir(cell["task"], cell["oracle"]))
        log_dir = RUN_DIR / f"ablate-{short_task(cell['task'])}-{cell['oracle']}-{cell['patch']}"
        ablations = await ablate(load_safe(cell["task"]), diff, hidden_root, checks, log_dir)
        dump(ablation_path(cell), ablations.model_dump(mode="json"))
        print(f"          skipped={ablations.skipped} stale={ablations.stale}", flush=True)
        written += 1
    print(f"\nwrote {written} ablation files")
    return 0


def dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


async def main(argv: list[str]) -> int:
    if argv == ["--ablations-only"]:
        return await write_ablations()
    if argv:
        print(f"usage: build_verdict_corpus.py [--ablations-only], not {argv}", file=sys.stderr)
        return 2
    cells, ignored = read_cells()
    # before the guard, not after: if two records merged into one cell the count comes up
    # short, and these lines are the only thing that says which merge caused it
    for task, oracle, patch, at in sorted(ignored):
        print(f"ignoring a later re-run of {short_task(task)} {oracle} {patch} ({at}): "
              f"verdict_before must stay the verdict spec 02 measured", flush=True)
    if len(cells) != EXPECTED_CELLS:
        print(f"expected {EXPECTED_CELLS} distinct cells under {RUNS}, found {len(cells)} "
              f"-- refusing to write a partial corpus", file=sys.stderr)
        return 1

    # one source set per (task, patch): the diff decides which tests get selected, the oracle does not
    by_patch: dict[tuple[str, str], str] = {}
    for c in cells:
        by_patch[(c["task"], c["patch"])] = c["diff_path"]
    if len(by_patch) != EXPECTED_SOURCE_SETS:
        print(f"expected {EXPECTED_SOURCE_SETS} (task, patch) source sets, found {len(by_patch)}",
              file=sys.stderr)
        return 1

    # rebuild from empty: a cell left behind by a deleted run would otherwise live forever
    if CORPUS.exists():
        shutil.rmtree(CORPUS)

    for i, ((task, patch), diff_path) in enumerate(sorted(by_patch.items()), 1):
        print(f"[{i}/{len(by_patch)}] reading sources in a container: {task} {patch}", flush=True)
        sources = await read_sources(task, diff_path)
        dump(CORPUS / task / "sources" / f"{patch}.json", sources)
        print(f"          {len(sources)} files, {sum(len(v) for v in sources.values())} bytes", flush=True)

    per_task: dict[str, int] = defaultdict(int)
    for c in sorted(cells, key=lambda c: (c["task"], c["oracle"], c["patch"])):
        dump(CORPUS / c["task"] / c["oracle"] / f"{c['patch']}.json", c)
        per_task[c["task"]] += 1

    print(f"\nwrote {len(cells)} cells and {len(by_patch)} source sets to {CORPUS.relative_to(ROOT)}")
    for task, n in sorted(per_task.items()):
        print(f"  {n:2d}  {task}")
    return await write_ablations()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
