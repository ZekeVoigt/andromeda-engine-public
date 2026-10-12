"""The gate on spec 02's measurement: the committed draws, results.tsv and REPORT.md. No Docker.

Two jobs. It keeps scripts/oracle_variance.py's analysis code from rotting after the
by-hand run, and it keeps the committed artifacts honest -- an edited REPORT.md or a stale
results.tsv fails pytest.

Hermetic by construction: every SafeTask comes from fixtures/variance/tasks/<id>.json and is
handed straight to load_oracle, which takes a SafeTask rather than an id and so needs no
network. Only `pin` ever needs load_safe monkeypatched.
"""

import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest

import andromeda.outcome.adapters.coding.oracle as oracle_mod
from andromeda.outcome.adapters.coding.oracle import MANIFEST, TREE, OracleMismatch, load_oracle
from andromeda.outcome.adapters.coding.task import SafeTask
from andromeda.outcome.core.types import Check, Evidence, GroundTruth, OutcomeRecord, Request, Verdict

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "oracle_variance.py"
REPORT = ROOT / "specs" / "02-oracle-variance" / "REPORT.md"
FENCE = ("<!-- distribution:start -->", "<!-- distribution:end -->")


@pytest.fixture(scope="module")
def ov():
    spec = importlib.util.spec_from_file_location("oracle_variance", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module", autouse=True)
def _no_claude_version_override():
    """load_oracle re-reads OUTCOME_CLAUDE_VERSION; a developer with it exported would get
    15 unrelated OracleMismatch failures that have nothing to do with the draws."""
    import os
    saved = os.environ.pop("OUTCOME_CLAUDE_VERSION", None)
    yield
    if saved is not None:
        os.environ["OUTCOME_CLAUDE_VERSION"] = saved


@pytest.fixture(scope="module")
def tasks(ov) -> dict[str, SafeTask]:
    out = {}
    for instance_id in ov.TASKS:
        path = ROOT / ov.TASK_FILES / f"{instance_id}.json"
        assert path.is_file(), f"missing task file: {path.relative_to(ROOT)}"
        out[instance_id] = SafeTask(**json.loads(path.read_text()))
    return out


def rows_or_skip(ov) -> list[dict[str, str]]:
    if not ov.results_path().is_file():
        pytest.fail(f"{ov.results_path().relative_to(ROOT)} does not exist -- the measurement has not run")
    return ov.read_results()


def synthetic(ov, task: str, patch: str, verdicts: list[str]) -> list[dict[str, str]]:
    """One cell's worth of rows, oracle order matching ov.ORACLES."""
    return [dict(zip(ov.COLUMNS, [task, o, patch, v, "resolved", "3", "3", "r"]))
            for o, v in zip(ov.ORACLES, verdicts)]


# --- the committed draws (AC2) ------------------------------------------------------------

def test_the_task_files_are_the_full_safetask(ov, tasks):
    """model_dump, not the task module's CLI dump: that one drops the three fields that
    feed prompt_sha256, and load_oracle compares prompt_sha256 on every load."""
    for task in tasks.values():
        assert task.problem_statement and task.requirements
        assert task.request_text().strip()


def test_the_ansible_task_file_matches_the_replay_fixture(ov):
    """Two committed copies of one SafeTask. They must not drift apart silently."""
    ansible = [t for t in ov.TASKS if "ansible" in t]
    assert len(ansible) == 1
    mine = (ROOT / ov.TASK_FILES / f"{ansible[0]}.json").read_bytes()
    theirs = (ROOT / "fixtures" / "replay" / "ansible-gold" / "task.json").read_bytes()
    assert mine == theirs


def test_every_draw_dir_loads_through_the_real_oracle_check(ov, tasks):
    """AC2. 15 dirs, each drawn by a real write_hidden_tests run and pinned."""
    checked = 0
    for instance_id, task in tasks.items():
        for oracle in ov.ORACLES:
            if oracle == ov.PINNED:
                continue  # spec 01's, covered by its own tests
            directory = ROOT / ov.oracle_dir(instance_id, oracle)
            assert directory.is_dir(), f"missing draw: {directory.relative_to(ROOT)}"
            tree, listed = load_oracle(task, directory)
            assert listed, f"empty oracle: {directory.relative_to(ROOT)}"
            checked += 1
    assert checked == len(ov.TASKS) * ov.DRAWS == 15


def test_every_file_in_every_draw_is_a_hidden_test(ov, tasks):
    """Asserted off the disk, not off the manifest: a manifest listing only the good files
    would satisfy load_oracle's file check and still ship a stray notes.txt into /app."""
    offenders, seen = [], 0
    for instance_id in tasks:
        for oracle in ov.ORACLES:
            if oracle == ov.PINNED:
                continue
            tree = ROOT / ov.oracle_dir(instance_id, oracle) / TREE
            for p in tree.rglob("*"):
                if not p.is_file():
                    continue
                seen += 1
                if not p.name.startswith("test_outcome_"):
                    offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []
    # `offenders == []` is also true when the trees are missing entirely
    assert seen >= len(ov.TASKS) * ov.DRAWS, f"only {seen} file(s) across 15 draws"


def test_the_set_of_draw_trees_is_exactly_the_tasks(ov):
    """A stray or missing tree is caught here rather than skipped by an iterdir loop.

    TASK_FILES is the one non-task dir under VARIANCE; results.tsv is a file, so is_dir
    already excludes it.
    """
    directories = {p.name for p in (ROOT / ov.VARIANCE).iterdir() if p.is_dir()}
    assert directories == set(ov.TASKS) | {(ROOT / ov.TASK_FILES).name}


def test_a_stray_file_in_a_draw_fails_the_oracle_check(ov, tasks, tmp_path):
    """The falsifier for the two tests above: prove load_oracle really would object."""
    instance_id, task = next(iter(tasks.items()))
    source = ROOT / ov.oracle_dir(instance_id, "draw-01")
    if not source.is_dir():
        source = ROOT / ov.oracle_dir(instance_id, ov.PINNED)  # pre-measurement fallback
    copy = tmp_path / "draw"
    shutil.copytree(source, copy)
    load_oracle(task, copy)  # clean before

    (copy / TREE / "notes.txt").write_text("scratch\n")

    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, copy)
    assert "notes.txt" in str(e.value) and "not in the manifest" in str(e.value)


# --- results.tsv (AC3) --------------------------------------------------------------------

def test_results_tsv_is_the_full_grid_exactly_once(ov):
    """AC3. Set equality, not a row count: a double-append after an interrupted resume
    would keep 36 from being 36, but two of the same cell would pass a bare len()."""
    rows = rows_or_skip(ov)
    seen = [(r["task"], r["oracle"], r["patch"]) for r in rows]
    expected = {(ov.short_task(t), o, p) for t in ov.TASKS for o in ov.ORACLES for p in ov.PATCHES}
    assert len(expected) == 36
    assert len(seen) == len(set(seen)), f"duplicate cells: {sorted({c for c in seen if seen.count(c) > 1})}"
    assert set(seen) == expected


def test_every_row_is_well_formed(ov):
    rows = rows_or_skip(ov)
    for r in rows:
        where = f"{r['task']} {r['oracle']} {r['patch']}"
        assert r["verdict"] in Verdict.__args__, where
        assert r["truth"] in GroundTruth.__args__, where
        assert int(r["n_valid"]) <= int(r["n_hidden"]), where
        assert "\t" not in r["reason"] and "\n" not in r["reason"], where
        assert len(r["reason"]) <= ov.REASON_WIDTH, where


def test_no_committed_row_is_an_infra_flake(ov):
    """Hazard 1. decide() returns UNKNOWN both for a dead container and for an oracle whose
    hidden tests all passed on base. The second is the variance this spec exists to measure;
    the first is noise. Only the reason tells them apart, so no flaked cell may be recorded.
    """
    offenders = [f"{r['task']} {r['oracle']} {r['patch']}: {r['reason']}"
                 for r in rows_or_skip(ov) if r["reason"].startswith(ov.INFRA_REASONS)]
    assert offenders == []


# --- distribution (AC6) -------------------------------------------------------------------

def test_distribution_counts_and_marks_the_pinned_oracle(ov):
    task = ov.short_task(ov.TASKS[0])
    rows = synthetic(ov, task, "gold", ["PASS", "PASS", "FAIL", "PASS", "UNKNOWN", "PASS"])
    rows += synthetic(ov, task, "half", ["FAIL"] * 6)

    dist = {(d["task"], d["patch"]): d for d in ov.distribution(rows)}

    gold = dist[(task, "gold")]
    assert (gold["PASS"], gold["FAIL"], gold["UNKNOWN"]) == ("4", "1", "1")
    assert gold["pinned"] == "PASS", "the pinned oracle's own verdict is the first ORACLES entry"
    half = dist[(task, "half")]
    assert (half["PASS"], half["FAIL"], half["UNKNOWN"]) == ("0", "6", "0")


def test_distribution_is_six_rows_in_task_then_patch_order(ov):
    rows = [r for t in ov.TASKS for p in ov.PATCHES
            for r in synthetic(ov, ov.short_task(t), p, ["PASS"] * 6)]

    dist = ov.distribution(rows)

    assert [(d["task"], d["patch"]) for d in dist] == \
        [(ov.short_task(t), p) for t in ov.TASKS for p in ov.PATCHES]
    assert list(dist[0]) == ov.DIST_COLUMNS
    for d in dist:
        assert sum(int(d[v]) for v in ov.VERDICTS) == 6, "counts must include the pinned oracle"


def test_distribution_refuses_a_row_for_an_unknown_task(ov):
    """Silently dropping it would let a renamed task shrink the table and still pass AC6."""
    rows = synthetic(ov, "renamed-deadbeef", "gold", ["PASS"] * 6)

    with pytest.raises(ValueError) as e:
        ov.distribution(rows)
    assert "renamed-deadbeef" in str(e.value)


def test_short_task_does_not_collide_across_tasks(ov):
    """short_task is half the resume key. A collision would merge two tasks' cells."""
    assert len({ov.short_task(t) for t in ov.TASKS}) == len(ov.TASKS)


# --- REPORT.md round trip (AC6) -----------------------------------------------------------

def parse_report_table(ov) -> list[dict[str, str]]:
    assert REPORT.is_file(), f"missing {REPORT.relative_to(ROOT)}"
    text = REPORT.read_text()
    start, end = FENCE
    assert start in text and end in text, f"{REPORT.relative_to(ROOT)}: missing {start}/{end}"
    body = text.split(start, 1)[1].split(end, 1)[0]
    lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    assert lines, "no pipe table between the fences"
    parsed = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
    parsed = [cells for cells in parsed if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells)]
    assert parsed[0] == ov.DIST_COLUMNS, f"header {parsed[0]} != {ov.DIST_COLUMNS}"
    return [dict(zip(ov.DIST_COLUMNS, cells)) for cells in parsed[1:]]


def test_report_table_is_derivable_from_the_committed_tsv(ov):
    """AC6. An edited report or a stale TSV fails here."""
    assert parse_report_table(ov) == ov.distribution(rows_or_skip(ov))


def test_format_markdown_is_what_the_parser_reads_back(ov):
    """The falsifier for the test above: the writer and the parser must be inverses, or
    a REPORT.md generated by this script could fail its own gate."""
    rows = ov.distribution([r for t in ov.TASKS for p in ov.PATCHES
                            for r in synthetic(ov, ov.short_task(t), p, ["PASS"] * 6)])

    round_tripped = ov.format_markdown(rows, ov.DIST_COLUMNS)

    lines = [ln for ln in round_tripped.splitlines() if ln.startswith("|")]
    parsed = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
    parsed = [c for c in parsed if not all(re.fullmatch(r":?-{3,}:?", x) for x in c)]
    assert parsed[0] == ov.DIST_COLUMNS
    assert [dict(zip(ov.DIST_COLUMNS, c)) for c in parsed[1:]] == rows


# --- resume (AC4) -------------------------------------------------------------------------

def test_remaining_cells_names_exactly_the_missing_ones(ov, monkeypatch, tmp_path):
    """AC4. VARIANCE is joined onto ROOT, and an absolute right-hand side wins, so
    pointing it at tmp_path redirects results_path() without touching the repo."""
    monkeypatch.setattr(ov, "VARIANCE", tmp_path)
    full = [(t, o, p) for t in ov.TASKS for o in ov.ORACLES for p in ov.PATCHES]
    assert ov.remaining_cells() == full, "nothing done yet means everything remains"

    for instance_id, oracle, patch in full[:-2]:
        ov.append_row(dict(zip(ov.COLUMNS, [ov.short_task(instance_id), oracle, patch,
                                            "PASS", "resolved", "3", "3", "ok"])))

    assert ov.remaining_cells() == full[-2:]
    assert ov.read_results()[0]["verdict"] == "PASS"


def test_the_appended_header_matches_format_tsv(ov, monkeypatch, tmp_path):
    """One writer behind both, so a column added to COLUMNS cannot desync them."""
    monkeypatch.setattr(ov, "VARIANCE", tmp_path)
    row = dict(zip(ov.COLUMNS, [ov.short_task(ov.TASKS[0]), "draw-01", "gold",
                                "FAIL", "unresolved", "3", "2", "r"]))

    ov.append_row(row)
    ov.append_row(row)

    assert ov.results_path().read_text() == ov.format_tsv([row, row], ov.COLUMNS)
    assert ov.results_path().read_text().splitlines()[0].split("\t") == ov.COLUMNS


def test_run_cell_argv_carries_the_patch_and_the_draw_oracle(ov, monkeypatch):
    """AC4. The oracle must be the DRAW's dir -- passing the store would measure spec 01's
    oracle six times and report zero variance."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        raise AssertionError("stop before the run dir diff")

    monkeypatch.setattr(ov.subprocess, "run", fake_run)
    instance_id = ov.TASKS[0]

    with pytest.raises(AssertionError):
        ov.run_cell(instance_id, "draw-03", "gold")

    argv = seen["argv"]
    assert argv[:5] == ["uv", "run", "outcome", "run", instance_id]
    assert argv[argv.index("--patch") + 1] == str(ov.PATCH_FIXTURES / instance_id / "gold.diff")
    assert argv[argv.index("--oracle") + 1] == str(ov.VARIANCE / instance_id / "draw-03")
    assert seen["kw"]["cwd"] == ov.ROOT and seen["kw"]["timeout"] > 0


def fake_record(instance_id, verdict="PASS", reason="ok", checks=(), infra=()):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return OutcomeRecord(request=Request(task_id=instance_id, text=""), verdict=verdict,
                         reason=reason, evidence=Evidence(checks=list(checks), infra_errors=list(infra)),
                         ground_truth="resolved", agrees=None, started_at=now, finished_at=now)


def stub_outcome_run(ov, monkeypatch, tmp_path, record: OutcomeRecord | None):
    """Point ROOT at tmp_path and make `outcome run` create one run dir holding `record`."""
    instance_id = ov.TASKS[0]
    runs = tmp_path / "runs" / instance_id
    runs.mkdir(parents=True)
    monkeypatch.setattr(ov, "ROOT", tmp_path)

    def fake_run(argv, **kw):
        made = runs / "20260101T000000000000Z"
        made.mkdir()
        if record is not None:
            (made / "record.json").write_text(record.model_dump_json())
        import subprocess as sp
        return sp.CompletedProcess(argv, 0)

    monkeypatch.setattr(ov.subprocess, "run", fake_run)
    return instance_id


def test_run_cell_drops_a_cell_whose_record_carries_an_infra_error(ov, monkeypatch, tmp_path):
    """Hazard 1, at the only place it is enforced.

    cli.run writes a perfectly valid record.json when the run dies: verdict UNKNOWN,
    infra_errors populated. decide() also returns UNKNOWN for "no valid hidden checks",
    which IS the variance this spec measures. The verdict cannot tell them apart, so the
    record's infra_errors is the discriminator -- and a flaked cell must stay undone so
    resume retries it rather than being counted as an UNKNOWN draw.
    """
    flake = "InfraError: docker daemon went away"
    record = fake_record(ov.TASKS[0], verdict="UNKNOWN",
                         reason=f"infra error: {flake}", infra=[flake])
    instance_id = stub_outcome_run(ov, monkeypatch, tmp_path, record)

    assert ov.run_cell(instance_id, "draw-01", "gold") is None

    # and it really is infra_errors doing the work, not the reason string: the same record
    # with the message but an empty infra_errors list is a normal cell
    passthrough = fake_record(ov.TASKS[0], verdict="UNKNOWN", reason=f"infra error: {flake}")
    stub_outcome_run(ov, monkeypatch, tmp_path / "second", passthrough)
    assert ov.run_cell(instance_id, "draw-01", "gold") is not None


def test_run_cell_keeps_a_genuine_unknown_with_no_infra_error(ov, monkeypatch, tmp_path):
    """The other half, and the one that makes the test above meaningful: an oracle whose
    hidden tests all passed on base is real variance and must reach the table."""
    record = fake_record(ov.TASKS[0], verdict="UNKNOWN",
                         reason="no valid hidden checks (none failed on base)",
                         checks=[Check(name="t::a", kind="hidden", before="pass", after="pass")])
    instance_id = stub_outcome_run(ov, monkeypatch, tmp_path, record)

    kept = ov.run_cell(instance_id, "draw-01", "gold")

    assert kept is not None and kept.verdict == "UNKNOWN"
    assert not ov.row(instance_id, "draw-01", "gold", kept)["reason"].startswith(ov.INFRA_REASONS)


def test_run_cell_returns_none_when_no_record_was_written(ov, monkeypatch, tmp_path):
    instance_id = stub_outcome_run(ov, monkeypatch, tmp_path, None)
    assert ov.run_cell(instance_id, "draw-01", "gold") is None


def test_row_counts_hidden_and_valid_the_way_decide_does(ov):
    """n_valid must mirror verdict.decide's own filter, or the table's denominators are
    not the denominators the verdict was computed from."""
    checks = [
        Check(name="t::a", kind="hidden", before="fail", after="pass"),
        Check(name="t::b", kind="hidden", before="fail", after="fail"),
        Check(name="t::c", kind="hidden", before="pass", after="pass"),   # hidden, not valid
        Check(name="t::d", kind="existing", before="pass", after="pass"),  # not hidden
    ]
    record = fake_record(ov.TASKS[0], verdict="FAIL", reason="x\ty\n" + "z" * 200, checks=checks)

    r = ov.row(ov.TASKS[0], "draw-02", "half", record)

    assert list(r) == ov.COLUMNS
    assert (r["n_hidden"], r["n_valid"]) == ("3", "2")
    assert (r["task"], r["oracle"], r["patch"]) == (ov.short_task(ov.TASKS[0]), "draw-02", "half")
    assert len(r["reason"]) == ov.REASON_WIDTH
    assert "\t" not in r["reason"] and "\n" not in r["reason"]


def test_draw_skips_a_dir_that_already_loads(ov, tasks, monkeypatch, tmp_path):
    """AC4. A redrawn oracle costs ~40 minutes and would silently replace a committed one."""
    instance_id, task = next(iter(tasks.items()))
    dest = tmp_path / instance_id / "draw-01"
    dest.parent.mkdir(parents=True)
    shutil.copytree(ROOT / ov.oracle_dir(instance_id, ov.PINNED), dest)

    async def never_draw(*a, **kw):
        pytest.fail("write_hidden_tests called for a draw that already loads")

    monkeypatch.setattr(ov, "VARIANCE", tmp_path)
    monkeypatch.setattr(ov, "load_safe", lambda _id: task)
    monkeypatch.setattr(ov, "write_hidden_tests", never_draw)

    import asyncio
    assert asyncio.run(ov.draw(instance_id, "draw-01")) == dest
    assert ov.remaining_draws() == [(t, o) for t in ov.TASKS for o in ov.ORACLES
                                    if o != ov.PINNED and (t, o) != (instance_id, "draw-01")]


def test_draw_pins_an_orphaned_tree_instead_of_redrawing(ov, tasks, monkeypatch, tmp_path):
    """Hazard 2. A process killed between write_hidden_tests returning and pin completing
    leaves a finished 40-minute draw on disk; a naive resume would pay for it twice."""
    instance_id, task = next(iter(tasks.items()))
    orphan = tmp_path / "runs" / "_variance" / instance_id / "draw-01-20260101T000000000000Z"
    (orphan / TREE).mkdir(parents=True)
    (orphan / TREE / "test_outcome_orphan.py").write_text("def test_outcome_x():\n    assert True\n")

    async def never_draw(*a, **kw):
        pytest.fail("write_hidden_tests called although a finished draw was on disk")

    monkeypatch.setattr(ov, "ROOT", tmp_path)
    monkeypatch.setattr(ov, "VARIANCE", tmp_path / "fixtures" / "variance")
    monkeypatch.setattr(ov, "load_safe", lambda _id: task)
    monkeypatch.setattr(ov, "write_hidden_tests", never_draw)
    # pin() resolves load_safe in ITS OWN module, so ov.load_safe above does not cover it.
    # Without this the test reaches HuggingFace and inherits whatever dataset state another
    # test left in the lru_cache.
    monkeypatch.setattr(oracle_mod, "load_safe", lambda _id: task)

    import asyncio
    pinned = asyncio.run(ov.draw(instance_id, "draw-01"))

    assert (pinned / TREE / "test_outcome_orphan.py").is_file()
    assert json.loads((pinned / MANIFEST).read_text())["drawn_from"] == str(orphan)
    assert load_oracle(task, pinned)[1] == ["test_outcome_orphan.py"]


def test_a_successful_draws_run_dir_is_not_offered_as_an_orphan(ov, tasks, monkeypatch, tmp_path):
    """The bug this guards: a draw that pinned cleanly leaves its tests on disk exactly like
    an interrupted one does. Offering it again for the next slot would pin two byte-identical
    oracles and report zero variance between them -- the one failure mode that looks like a
    finding rather than an error.
    """
    instance_id, task = next(iter(tasks.items()))
    monkeypatch.setattr(ov, "ROOT", tmp_path)
    monkeypatch.setattr(ov, "VARIANCE", tmp_path / "fixtures" / "variance")
    monkeypatch.setattr(ov, "load_safe", lambda _id: task)
    monkeypatch.setattr(oracle_mod, "load_safe", lambda _id: task)

    run_dir = tmp_path / "runs" / "_variance" / instance_id / "draw-01-20260101T000000000000Z"
    (run_dir / TREE).mkdir(parents=True)
    (run_dir / TREE / "test_outcome_a.py").write_text("def test_outcome_a():\n    assert True\n")

    assert ov.orphaned_draw(instance_id) == run_dir  # unpinned: it is an orphan

    import asyncio
    asyncio.run(ov.draw(instance_id, "draw-01"))

    assert ov.pinned_sources(instance_id) == {str(run_dir)}
    assert ov.orphaned_draw(instance_id) is None, "a pinned run dir is not an orphan"


def test_draw_redraws_rather_than_reusing_a_pinned_run_dir(ov, tasks, monkeypatch, tmp_path):
    """The consequence of the test above, at the level draw_all actually works on."""
    instance_id, task = next(iter(tasks.items()))
    monkeypatch.setattr(ov, "ROOT", tmp_path)
    monkeypatch.setattr(ov, "VARIANCE", tmp_path / "fixtures" / "variance")
    monkeypatch.setattr(ov, "load_safe", lambda _id: task)
    monkeypatch.setattr(oracle_mod, "load_safe", lambda _id: task)

    first = tmp_path / "runs" / "_variance" / instance_id / "draw-01-20260101T000000000000Z"
    (first / TREE).mkdir(parents=True)
    (first / TREE / "test_outcome_a.py").write_text("def test_outcome_a():\n    assert True\n")

    drew = []

    async def fake_writer(task_, run_dir):
        run_dir = Path(run_dir)
        (run_dir / TREE).mkdir(parents=True, exist_ok=True)
        (run_dir / TREE / "test_outcome_b.py").write_text("def test_outcome_b():\n    assert True\n")
        drew.append(run_dir)
        return [run_dir / TREE / "test_outcome_b.py"]

    monkeypatch.setattr(ov, "write_hidden_tests", fake_writer)

    import asyncio
    one = asyncio.run(ov.draw(instance_id, "draw-01"))   # pins the orphan, no writer call
    two = asyncio.run(ov.draw(instance_id, "draw-02"))   # must call the writer

    assert drew, "draw-02 reused draw-01's run dir instead of drawing"
    sources = {json.loads((d / MANIFEST).read_text())["drawn_from"] for d in (one, two)}
    assert len(sources) == 2, "two draws pinned from the same run dir"
    assert load_oracle(task, one)[1] == ["test_outcome_a.py"]
    assert load_oracle(task, two)[1] == ["test_outcome_b.py"]


def test_no_two_committed_draws_share_a_source_run(ov, tasks):
    """The same invariant, asserted on what actually shipped."""
    for instance_id in tasks:
        sources = []
        for oracle in ov.ORACLES:
            if oracle == ov.PINNED:
                continue
            manifest = ROOT / ov.oracle_dir(instance_id, oracle) / MANIFEST
            if manifest.is_file():
                sources.append(json.loads(manifest.read_text())["drawn_from"])
        assert len(sources) == len(set(sources)), \
            f"{ov.short_task(instance_id)}: duplicate drawn_from across draws"
        assert len(sources) == ov.DRAWS, f"{ov.short_task(instance_id)}: {len(sources)} draws, want {ov.DRAWS}"


def test_an_unfinished_tree_is_not_mistaken_for_an_orphan(ov, monkeypatch, tmp_path):
    """The falsifier: a draw killed mid-write has hidden_tests/ but no test file in it.
    Pinning that would commit an empty oracle."""
    instance_id = ov.TASKS[0]
    started = tmp_path / "runs" / "_variance" / instance_id / "draw-01-20260101T000000000000Z"
    (started / TREE).mkdir(parents=True)
    (started / "test_writer.log").write_text("claude was still running\n")

    monkeypatch.setattr(ov, "ROOT", tmp_path)
    assert ov.orphaned_draw(instance_id) is None


# --- setup prechecks ----------------------------------------------------------------------

def test_task_problems_is_clean_for_the_real_repo(ov):
    """TASKS must match fixtures/oracles/ exactly, or the `pinned` column measures nothing."""
    assert ov.task_problems() == []


def test_write_task_files_reports_drift_instead_of_overwriting(ov, tasks, monkeypatch, tmp_path):
    """A resumed run that rewrote these would move the committed ansible copy out of step
    with the replay fixture, and would hide dataset drift behind a fixture update."""
    monkeypatch.setattr(ov, "VARIANCE", tmp_path)
    monkeypatch.setattr(ov, "TASK_FILES", tmp_path / "tasks")
    monkeypatch.setattr(ov, "load_safe", lambda instance_id: tasks[instance_id])

    assert ov.write_task_files() == [], "first run writes them and reports no drift"
    written = {f: (tmp_path / "tasks" / f"{f}.json").read_text() for f in ov.TASKS}
    assert len(written) == len(ov.TASKS)

    assert ov.write_task_files() == [], "a second run with no drift is a no-op"

    edited = tmp_path / "tasks" / f"{ov.TASKS[0]}.json"
    edited.write_text(json.dumps({"instance_id": "moved"}, indent=2) + "\n")

    drift = ov.write_task_files()

    assert len(drift) == 1 and ov.TASKS[0] in drift[0]
    assert json.loads(edited.read_text()) == {"instance_id": "moved"}, "must not overwrite"


def test_oracle_dir_separates_the_store_from_the_draws(ov):
    instance_id = ov.TASKS[0]
    assert ov.oracle_dir(instance_id, ov.PINNED) == ov.ORACLE_STORE / instance_id
    assert ov.oracle_dir(instance_id, "draw-02") == ov.VARIANCE / instance_id / "draw-02"
    assert ov.ORACLE_STORE not in ov.oracle_dir(instance_id, "draw-02").parents
