"""Replay spec 02's 36 measured cells through today's verdict rules.

No Docker, no network: fixtures/verdict-corpus/ holds the checks each cell produced, the
existing test sources the diff's file selection reads, and the verdict spec 02 recorded.
Regenerate with `uv run python scripts/build_verdict_corpus.py`.

This is the spec 03 gate. Spec 02 measured 24 of 36 cells agreeing with ground truth; the
tolerance and the obsolescence rule take that to 30. Spec 13 takes it to 36: obsolescence per
test, and required-hunk attribution for a cell that still routes `regressed`, replayed from the
committed ablations/<oracle>-<patch>.json that the builder's last step measured in Docker.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from andromeda.outcome.adapters.coding.adapter import Ablations, mark_obsoleted, mark_stale
from andromeda.outcome.core.types import Check, Evidence
from andromeda.outcome.core.verdict import decide, route

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures" / "verdict-corpus"

EXPECTED_CELLS = 36
EXPECTED_SOURCE_SETS = 6

TRUTH_TO_VERDICT = {"resolved": "PASS", "unresolved": "FAIL"}

C580EBF0 = ("instance_qutebrowser__qutebrowser-c580ebf0801e5a3ecabc54f327498bb753c6d5f2"
            "-v2ef375ac784985212b1805e1d0431dc8f1b3c171")
ORACLES = ["pinned"] + [f"draw-{n:02d}" for n in range(1, 6)]

# Empty since spec 13. Spec 03 left the six c580ebf0 gold cells here: past the tolerance,
# tests/unit/components/test_hostblock.py::test_disabled_blocking_per_url regressed, naming no
# deleted symbol -- the gold *test patch* repairs it, and the engine never sees that. Spec 13
# forgives it causally instead: reverting `_is_blocked`'s hunk (hostblock.py@1) restores it and
# fails more valid hidden checks, so the request needs the very hunk that broke it. A cell
# that cannot be reached goes back in here, with its reason, as spec 03's did.
KNOWN_DIVERGENT: set[tuple[str, str, str]] = set()


def cell_files() -> list[Path]:
    return sorted(p for p in CORPUS.rglob("*.json") if p.parent.name not in ("sources", "ablations"))


def load_cell(path: Path) -> dict:
    with path.open() as fh:
        return json.load(fh)


def cell_id(cell: dict) -> tuple[str, str, str]:
    return (cell["task"], cell["oracle"], cell["patch"])


def short_id(cell: dict) -> str:
    """instance_<org>__<repo>-<sha>-v... -> <repo>-<sha8>-<oracle>-<patch>"""
    repo, _, sha = cell["task"].split("__", 1)[-1].partition("-")
    return f"{repo}-{sha[:8]}-{cell['oracle']}-{cell['patch']}"


def ablation_file(cell: dict) -> Path:
    return CORPUS / cell["task"] / "ablations" / f"{cell['oracle']}-{cell['patch']}.json"


def replayed_evidence(cell: dict) -> Evidence:
    """mark_obsoleted, then mark_stale on a `regressed` route, as gather_evidence does."""
    with (CORPUS / cell["task"] / "sources" / f"{cell['patch']}.json").open() as fh:
        sources = json.load(fh)
    diff = (ROOT / cell["diff_path"]).read_text()
    checks = [Check.model_validate(c) for c in cell["checks"]]
    evidence = Evidence(checks=mark_obsoleted(checks, diff, sources))
    if route(evidence) == "regressed":
        path = ablation_file(cell)
        if not path.is_file():
            pytest.fail(f"{short_id(cell)} routes regressed but {path.relative_to(ROOT)} is missing: "
                        "run `uv run python scripts/build_verdict_corpus.py --ablations-only`")
        evidence.checks = mark_stale(evidence.checks, Ablations.model_validate_json(path.read_text()))
    return evidence


def replay(cell: dict) -> tuple[str, str]:
    """replayed_evidence() then decide(), exactly as gather_evidence and engine.verify do."""
    return decide(replayed_evidence(cell))


ALL_CELLS = [load_cell(p) for p in cell_files()]


def test_the_corpus_is_whole():
    """AC7. Every assertion below iterates the corpus, so a truncated one passes vacuously."""
    assert len(ALL_CELLS) == EXPECTED_CELLS, f"corpus holds {len(ALL_CELLS)} cells, not {EXPECTED_CELLS}"
    assert len({cell_id(c) for c in ALL_CELLS}) == EXPECTED_CELLS, "duplicate cell ids in the corpus"

    source_sets = sorted(p for p in CORPUS.rglob("sources/*.json"))
    assert len(source_sets) == EXPECTED_SOURCE_SETS, f"{len(source_sets)} source sets, not {EXPECTED_SOURCE_SETS}"
    for path in source_sets:
        with path.open() as fh:
            sources = json.load(fh)
        assert sources, f"empty source set: {path.relative_to(ROOT)}"

    for cell in ALL_CELLS:
        assert cell["checks"], f"{cell_id(cell)} recorded no checks"
        assert cell["ground_truth"] in TRUTH_TO_VERDICT, f"{cell_id(cell)}: {cell['ground_truth']}"
        assert (ROOT / cell["diff_path"]).is_file(), f"{cell_id(cell)}: missing {cell['diff_path']}"

    # the divergent set names real cells, so a rename cannot make the exemption silently empty
    assert KNOWN_DIVERGENT <= {cell_id(c) for c in ALL_CELLS}


@pytest.mark.parametrize("cell", ALL_CELLS, ids=short_id)
def test_cell_agrees_with_ground_truth(cell):
    """AC8: PASS <-> resolved, FAIL <-> unresolved, for every cell not in KNOWN_DIVERGENT."""
    expected = TRUTH_TO_VERDICT[cell["ground_truth"]]
    verdict, reason = replay(cell)
    where = f"{cell['task']} {cell['oracle']} {cell['patch']}"
    moved = f"{cell['verdict_before']} -> {verdict}"

    if cell_id(cell) in KNOWN_DIVERGENT:
        assert verdict != expected, (
            f"{where}: {moved} now AGREES with {cell['ground_truth']!r} -- it is listed in "
            f"KNOWN_DIVERGENT, so remove it from that set. reason: {reason}")
        return

    assert verdict == expected, (
        f"{where}: {moved}, but ground truth is {cell['ground_truth']!r} "
        f"(expected {expected}). reason: {reason}")


def test_all_thirty_six_agree():
    """The headline number, asserted as a whole so a shift in either direction is visible."""
    agree, diverge = [], []
    for cell in ALL_CELLS:
        verdict, _ = replay(cell)
        (agree if verdict == TRUTH_TO_VERDICT[cell["ground_truth"]] else diverge).append(cell_id(cell))

    assert {tuple(d) for d in diverge} == KNOWN_DIVERGENT, (
        "the divergent set changed.\n"
        f"  unexpected: {sorted(set(map(tuple, diverge)) - KNOWN_DIVERGENT)}\n"
        f"  newly agreeing: {sorted(KNOWN_DIVERGENT - set(map(tuple, diverge)))}")
    assert KNOWN_DIVERGENT == set()
    assert len(agree) == EXPECTED_CELLS - len(KNOWN_DIVERGENT) == 36


def test_both_obsolescence_kinds_actually_fire_somewhere():
    """Without this, the 36/36 above could be reached by the tolerance alone."""
    kinds: dict[str | None, int] = {}
    for cell in ALL_CELLS:
        for c in replayed_evidence(cell).checks:
            if c.obsoleted:
                kinds[c.obsoleted_by] = kinds.get(c.obsoleted_by, 0) + 1
    assert set(kinds) == {"deleted-symbol", "required-hunk"}, kinds


def test_the_six_c580ebf0_gold_cells_pass_with_one_required_hunk_check_each():
    gold = [c for c in ALL_CELLS if c["task"] == C580EBF0 and c["patch"] == "gold"]
    assert sorted(c["oracle"] for c in gold) == sorted(ORACLES)
    for cell in gold:
        evidence = replayed_evidence(cell)
        stale = [c.name for c in evidence.checks if c.obsoleted_by == "required-hunk"]
        assert stale == [DISABLED_PER_URL], f"{short_id(cell)}: {stale}"
        assert decide(evidence)[0] == "PASS"


DISABLED_PER_URL = "tests/unit/components/test_hostblock.py::test_disabled_blocking_per_url"


def test_the_ablations_are_the_six_c580ebf0_gold_cells_and_nothing_else():
    """AC9: one file per cell that routes regressed, and hostblock.py@1 is the cause."""
    files = sorted(CORPUS.rglob("ablations/*.json"))
    assert [p.relative_to(CORPUS).as_posix() for p in files] == [
        f"{C580EBF0}/ablations/{oracle}-gold.json" for oracle in sorted(ORACLES)]
    for cell in ALL_CELLS:
        with (CORPUS / cell["task"] / "sources" / f"{cell['patch']}.json").open() as fh:
            sources = json.load(fh)
        checks = [Check.model_validate(c) for c in cell["checks"]]
        regressed = route(Evidence(checks=mark_obsoleted(checks, (ROOT / cell["diff_path"]).read_text(), sources))) == "regressed"
        assert ablation_file(cell).is_file() == regressed, short_id(cell)
    for path in files:
        ablations = Ablations.model_validate_json(path.read_text())
        assert ablations.considered and ablations.skipped is None
        both = [h.id for h in ablations.hunks if DISABLED_PER_URL in h.restores and h.required]
        assert both == ["qutebrowser/components/hostblock.py@1"], f"{path.name}: {both}"
        assert ablations.stale == [DISABLED_PER_URL]


def test_a_regressed_cell_with_no_ablation_file_fails_by_name():
    cell = next(c for c in ALL_CELLS if c["task"] == C580EBF0 and c["patch"] == "gold")
    orphan = {**cell, "oracle": "draw-99"}
    with pytest.raises(pytest.fail.Exception, match="c580ebf0-draw-99-gold routes regressed"):
        replay(orphan)


# --- the builder's dedup ---------------------------------------------------------------
#
# A cell can legitimately be re-run: spec 03's own end-to-end verification re-ran one of
# spec 02's pinned cells. `verdict_before` must stay the verdict spec 02 measured, so the
# earliest record wins. Getting this backwards writes the post-spec-03 verdict into the
# column the corpus exists to preserve -- silently, with the corpus still 36 cells.

BUILDER = ROOT / "scripts" / "build_verdict_corpus.py"


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("build_verdict_corpus", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_record(runs: Path, task: str, oracle_src: str, patch: str, verdict: str, started: str) -> None:
    d = runs / task / started.replace("-", "").replace(":", "")
    d.mkdir(parents=True, exist_ok=True)
    (d / "record.json").write_text(json.dumps({
        "request": {"task_id": task, "text": ""},
        "verdict": verdict, "reason": "", "ground_truth": "resolved", "agrees": None,
        "started_at": started, "finished_at": started,
        "patch_source": f"fixtures/bad_patches/{task}/{patch}.diff",
        "oracle_source": oracle_src,
        "evidence": {"checks": [{"name": "t.py::a", "kind": "hidden", "before": "fail", "after": "pass"}],
                     "infra_errors": [], "artifacts": {}},
    }))


def test_a_rerun_cell_keeps_the_earliest_verdict_and_is_reported(builder, tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(builder, "RUNS", runs)
    write_record(runs, "instance_x__repo-" + "a" * 40 + "-v1", "fixtures/oracles/t", "gold",
                 "UNKNOWN", "2026-09-18T22:04:32Z")
    write_record(runs, "instance_x__repo-" + "a" * 40 + "-v1", "fixtures/oracles/t", "gold",
                 "PASS", "2026-09-19T04:43:14Z")

    cells, ignored = builder.read_cells()

    assert len(cells) == 1, "the re-run became a second cell instead of being deduped"
    assert cells[0]["verdict_before"] == "UNKNOWN", \
        "the later re-run won: verdict_before now holds the post-spec-03 verdict"
    assert cells[0]["recorded_at"] == "2026-09-18T22:04:32Z"
    # dropping a record silently is the other failure mode: it must be named
    assert [i[-1] for i in ignored] == ["2026-09-19T04:43:14Z"]


def test_earliest_wins_regardless_of_directory_order(builder, tmp_path, monkeypatch):
    """read_cells walks sorted paths, so the later record could arrive first or second."""
    runs = tmp_path / "runs"
    monkeypatch.setattr(builder, "RUNS", runs)
    task = "instance_y__repo-" + "b" * 40 + "-v1"
    # timestamps deliberately out of order relative to the dir names pytest will sort
    write_record(runs, task, "fixtures/variance/t/draw-01", "half", "PASS", "2026-01-02T00:00:00Z")
    write_record(runs, task, "fixtures/variance/t/draw-01", "half", "FAIL", "2026-01-01T00:00:00Z")

    cells, ignored = builder.read_cells()
    assert len(cells) == 1 and cells[0]["verdict_before"] == "FAIL"
    assert [i[-1] for i in ignored] == ["2026-01-02T00:00:00Z"]


def test_the_guard_counts_distinct_cells_not_records(builder, tmp_path, monkeypatch):
    """The bug this replaces: a legitimate re-run made the count 37 and refused to build."""
    runs = tmp_path / "runs"
    monkeypatch.setattr(builder, "RUNS", runs)
    task = "instance_z__repo-" + "c" * 40 + "-v1"
    write_record(runs, task, "fixtures/oracles/t", "gold", "FAIL", "2026-01-01T00:00:00Z")
    write_record(runs, task, "fixtures/oracles/t", "gold", "PASS", "2026-02-01T00:00:00Z")

    cells, _ = builder.read_cells()
    assert len({(c["task"], c["oracle"], c["patch"]) for c in cells}) == len(cells)


def test_ad_hoc_runs_are_not_cells(builder, tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(builder, "RUNS", runs)
    task = "instance_w__repo-" + "d" * 40 + "-v1"
    write_record(runs, task, "fresh", "gold", "PASS", "2026-01-01T00:00:00Z")
    d = runs / task / "agentrun"
    d.mkdir(parents=True, exist_ok=True)
    (d / "record.json").write_text(json.dumps({
        "request": {"task_id": task, "text": ""}, "verdict": "PASS", "reason": "",
        "ground_truth": "resolved", "agrees": None,
        "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:00Z",
        "patch_source": "agent", "oracle_source": "fixtures/oracles/t",
        "evidence": {"checks": [], "infra_errors": [], "artifacts": {}},
    }))

    cells, ignored = builder.read_cells()
    assert cells == [] and ignored == []
