"""The pinned-oracle store: pin, the stale check, and an adapter that reuses instead of drawing.

Hermetic by construction -- the SafeTask comes from the replay fixture's task.json and
oracle.load_safe is stubbed, so nothing here touches HuggingFace or Docker.
"""

import json
from pathlib import Path

import pytest

import andromeda.outcome.adapters.coding.adapter as adapter_mod
import andromeda.outcome.adapters.coding.oracle as oracle
from andromeda.outcome.adapters.coding.adapter import CodingAdapter
from andromeda.outcome.adapters.coding.docker import InfraError
from andromeda.outcome.adapters.coding.oracle import OracleMismatch, load_oracle, pin
from andromeda.outcome.adapters.coding.task import SafeTask, UnpinnedImage
from andromeda.outcome.core.types import Check, Request
from andromeda.outcome.core.verdict import decide

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "ansible-gold"
REL = "test/units/utils/collection_loader/test_outcome_fqcn.py"
BODY = "def test_outcome_rejects_keywords():\n    assert True\n"


@pytest.fixture
def task() -> SafeTask:
    return SafeTask(**json.loads((FIXTURE / "task.json").read_text()))


@pytest.fixture
def store(monkeypatch, tmp_path, task) -> Path:
    """An empty store, and a pin that never reaches the network."""
    monkeypatch.setattr(oracle, "STORE", tmp_path / "oracles")
    monkeypatch.setattr(oracle, "load_safe", lambda _id: task)
    return tmp_path / "oracles"


@pytest.fixture
def draw(tmp_path) -> Path:
    """A run dir shaped like a real write_hidden_tests draw, junk included."""
    run_dir = tmp_path / "run"
    hidden = run_dir / "hidden_tests"
    (hidden / Path(REL).parent).mkdir(parents=True)
    (hidden / REL).write_text(BODY)
    (hidden / "notes.txt").write_text("scratch notes from the writer")
    (hidden / "__pycache__").mkdir()
    (hidden / "__pycache__" / "test_outcome_fqcn.cpython-312.pyc").write_bytes(b"\x00compiled")
    return run_dir


@pytest.fixture
def pinned(store, draw, task) -> Path:
    return pin(task.instance_id, draw)


def read_manifest(oracle_dir: Path) -> dict:
    return json.loads((oracle_dir / "manifest.json").read_text())


def rewrite_manifest(oracle_dir: Path, **changes) -> None:
    m = read_manifest(oracle_dir)
    m.update(changes)
    (oracle_dir / "manifest.json").write_text(json.dumps(m, indent=2))


# --- pin (AC1) ---------------------------------------------------------------------------

def test_pin_round_trips_and_stores_only_prefixed_files(pinned, task, draw):
    tree = pinned / "hidden_tests"

    assert (pinned / "manifest.json").is_file()
    # the manifest must sit BESIDE the tree: _run_side copies the tree wholesale into /app
    assert not (tree / "manifest.json").exists()

    stored = sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file())
    assert stored == [REL], "only test_outcome_* files belong in the store"
    assert (tree / REL).read_text() == BODY

    m = read_manifest(pinned)
    assert m["instance_id"] == task.instance_id
    assert m["drawn_from"] == str(draw)
    assert list(m["files"]) == [REL]
    assert m["image"].startswith("jefzda/sweap-images@sha256:"), "AC5: pinned tasks use a digest ref"
    assert m["dataset_revision"] and m["claude_version"] and m["prompt_sha256"]

    # and it validates against the very task it was drawn for
    root, rel = load_oracle(task, pinned)
    assert (root, rel) == (tree, [REL])
    assert all(Path(r).name.startswith("test_outcome_") for r in rel)


def test_pin_writes_to_dest_and_leaves_the_store_alone(store, draw, task, tmp_path):
    """Spec 02 draws five extra oracles per task without disturbing the pinned one.

    The store must stay empty: a dest= that still wrote STORE/<id> would silently repin
    the oracle every run of oracle_variance.py, which AC7 forbids.
    """
    elsewhere = tmp_path / "elsewhere" / "draw-01"

    written = pin(task.instance_id, draw, dest=elsewhere)

    assert written == elsewhere
    assert (elsewhere / "hidden_tests" / REL).read_text() == BODY
    assert load_oracle(task, elsewhere) == (elsewhere / "hidden_tests", [REL])
    assert not (store / task.instance_id).exists(), "dest= must not also write the store"
    assert not list(elsewhere.glob(".*incoming*")), "staging dir left behind"


def test_pin_reads_store_at_call_time_not_as_a_default(store, draw, task, monkeypatch, tmp_path):
    """STORE bound as a default argument would freeze at import and defeat every monkeypatch.

    `store` already repoints it; moving it again mid-test is what distinguishes a call-time
    read from a value captured when the module was first imported.
    """
    moved = tmp_path / "moved-store"
    monkeypatch.setattr(oracle, "STORE", moved)

    assert pin(task.instance_id, draw) == moved / task.instance_id
    assert not (store / task.instance_id).exists()


# --- the module CLI ----------------------------------------------------------------------

def test_cli_pin_accepts_an_explicit_dest(store, draw, task, tmp_path):
    """The 4-arg form's only non-human caller. Without this it is shipped code that nothing
    but a typed command reaches, and USAGE could drift from what the match actually accepts.
    """
    elsewhere = tmp_path / "cli-dest"

    out = oracle.main(["pin", task.instance_id, str(draw), str(elsewhere)])

    assert out == f"pinned: {elsewhere}"
    assert load_oracle(task, elsewhere) == (elsewhere / "hidden_tests", [REL])
    assert not (store / task.instance_id).exists()


def test_cli_pin_without_dest_still_writes_the_store(store, draw, task):
    out = oracle.main(["pin", task.instance_id, str(draw)])

    assert out == f"pinned: {store / task.instance_id}"
    assert (store / task.instance_id / "manifest.json").is_file()


def test_cli_check_reports_the_loaded_tree(pinned, task):
    assert oracle.main(["check", task.instance_id]) == f"ok: 1 file(s) under {pinned / 'hidden_tests'}"


@pytest.mark.parametrize("argv", [[], ["pin"], ["pin", "a"], ["pin", "a", "b", "c", "d"], ["nope"]])
def test_cli_rejects_a_shape_it_cannot_serve(argv):
    """USAGE must name every accepted form, or the fallback tells the user the wrong thing."""
    with pytest.raises(SystemExit) as e:
        oracle.main(argv)
    assert str(e.value) == oracle.USAGE
    assert "pin <id> <run_dir> [dest]" in oracle.USAGE


def test_pin_refuses_a_draw_with_no_hidden_tests(store, tmp_path, task):
    empty = tmp_path / "empty-run"
    (empty / "hidden_tests").mkdir(parents=True)
    (empty / "hidden_tests" / "notes.txt").write_text("nothing usable")

    with pytest.raises(FileNotFoundError):
        pin(task.instance_id, empty)

    with pytest.raises(FileNotFoundError):
        pin(task.instance_id, tmp_path / "no-such-run")


def test_pin_warns_about_dropped_support_files(store, draw, task, capsys):
    """F5: a fresh draw copies the whole dir into /app; a pinned one copies only the manifest.

    So a conftest.py the writer's tests import is silently dropped here. AC1 forbids storing
    it, which makes the warning the only thing standing between that and a mystery import
    error 40 minutes into step 9.
    """
    (draw / "hidden_tests" / Path(REL).parent / "conftest.py").write_text("import pytest\n")
    (draw / "hidden_tests" / Path(REL).parent / "sample_data.json").write_text("{}\n")

    pin(task.instance_id, draw)

    warning = capsys.readouterr().err.split("WARNING")[1]
    # not .py-only: a test that reads a sibling .json breaks the same way as one that
    # imports a missing conftest
    assert "conftest.py" in warning and "sample_data.json" in warning
    # a cache file is not a support file: warning about it would train the reader to skip this line
    assert ".pyc" not in warning and "__pycache__" not in warning


def test_pin_refuses_when_the_image_is_not_digest_pinned(store, draw, task, monkeypatch):
    """F6: an unpinned tag means image drift can never be detected for that oracle.

    A warning let the operator store one anyway; a refusal does not. The store dir must not
    exist afterwards -- the raise is above dest.mkdir, so a refused pin writes nothing at all.
    """
    unpinned = task.model_copy(update={"dockerhub_tag": "some-task-not-in-IMAGE_DIGESTS"})
    assert "@sha256:" not in unpinned.image
    monkeypatch.setattr(oracle, "load_safe", lambda _id: unpinned)

    with pytest.raises(UnpinnedImage) as e:
        pin(unpinned.instance_id, draw)

    # the operator's next step is in the error, not in the docs
    assert "task digest" in str(e.value)
    assert not (store / unpinned.instance_id).exists()


def test_a_digest_pinned_task_draws_no_warning(store, draw, task, capsys):
    assert "@sha256:" in task.image

    pin(task.instance_id, draw)

    assert "IMAGE_DIGESTS" not in capsys.readouterr().err


def test_a_failed_repin_leaves_the_stored_oracle_intact(store, draw, task, tmp_path):
    """The plan budgets one redraw, so "the second draw was unusable" is a live case.

    pin stages into a sibling dir and swaps last; copying straight into the store would
    delete a good oracle on the way to failing.
    """
    pinned = pin(task.instance_id, draw)
    good = (pinned / "hidden_tests" / REL).read_text()

    bad_draw = tmp_path / "redraw"
    (bad_draw / "hidden_tests").mkdir(parents=True)
    (bad_draw / "hidden_tests" / "notes.txt").write_text("the writer produced nothing usable")
    with pytest.raises(FileNotFoundError):
        pin(task.instance_id, bad_draw)

    assert (pinned / "hidden_tests" / REL).read_text() == good
    assert load_oracle(task, pinned) == (pinned / "hidden_tests", [REL])
    assert not list(pinned.glob(".*incoming*")), "staging dir left behind"


# --- the pinned inputs (AC5) ---

def test_the_dataset_is_loaded_at_the_pinned_revision():
    """AC5's first half. Nothing else holds it: drop the kwarg and every other test stays green."""
    import sys
    import types

    import andromeda.outcome.adapters.coding.task as task_mod

    seen = {}
    fake = types.ModuleType("datasets")
    fake.load_dataset = lambda name, split=None, revision=None: seen.update(
        name=name, split=split, revision=revision) or []

    real = sys.modules.get("datasets")
    task_mod._dataset.cache_clear()
    try:
        sys.modules["datasets"] = fake
        task_mod._dataset()
    finally:
        # the lru_cache would otherwise hand the fake's [] to every later caller
        task_mod._dataset.cache_clear()
        sys.modules["datasets"] = real if real is not None else sys.modules.pop("datasets", None)

    assert seen["revision"] == task_mod.DATASET_REVISION
    assert seen["revision"] and len(seen["revision"]) == 40
    assert (seen["name"], seen["split"]) == (task_mod.DATASET, task_mod.SPLIT)


# --- the stale check (AC4) ---------------------------------------------------------------

@pytest.mark.parametrize("field, bad", [
    ("base_commit", "0" * 40),
    ("image", "jefzda/sweap-images@sha256:" + "d" * 64),
    ("dataset_revision", "1" * 40),
    ("claude_version", "0.0.1"),
    ("prompt_sha256", "e" * 64),
    ("instance_id", "instance_someone_else"),
])
def test_each_drifted_manifest_field_is_named(pinned, task, field, bad):
    rewrite_manifest(pinned, **{field: bad})

    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, pinned)
    assert field in str(e.value)


def test_live_claude_version_is_re_read_not_self_compared(pinned, task, monkeypatch):
    """The manifest is untouched here: only the environment moves.

    Comparing the manifest to itself would pass, and every pinned oracle would go on being
    reused after the test writer's Claude version changed under it.
    """
    monkeypatch.setenv("OUTCOME_CLAUDE_VERSION", "9.9.9")

    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, pinned)
    assert "claude_version" in str(e.value) and "9.9.9" in str(e.value)


def test_edited_missing_and_unlisted_files_are_named(pinned, task):
    tree = pinned / "hidden_tests"

    (tree / REL).write_text(BODY + "# one more line\n")
    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, pinned)
    assert f"files[{REL}]" in str(e.value) and "sha256" in str(e.value)

    (tree / REL).unlink()
    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, pinned)
    assert f"files[{REL}]" in str(e.value) and "missing" in str(e.value)

    # an unlisted file is not inert: the whole tree is copied into /app
    (tree / REL).write_text(BODY)
    (tree / "test_outcome_smuggled.py").write_text("import os; os.environ['x'] = '1'\n")
    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, pinned)
    assert "files[test_outcome_smuggled.py]" in str(e.value) and "not in the manifest" in str(e.value)


def test_all_drift_is_reported_in_one_message(pinned, task):
    rewrite_manifest(pinned, base_commit="0" * 40, claude_version="0.0.1")
    (pinned / "hidden_tests" / REL).write_text("changed\n")

    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, pinned)
    message = str(e.value)
    assert all(f in message for f in ("base_commit", "claude_version", f"files[{REL}]"))


def test_a_missing_store_is_a_mismatch_not_a_crash(store, task, tmp_path):
    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, tmp_path / "oracles" / task.instance_id)
    assert "manifest.json" in str(e.value)


@pytest.mark.parametrize("text, why", [
    ("{not json at all", "unparseable"),
    ('{"instance_id": "x"}', "missing every other field"),
    ('{"instance_id": "x", "files": "not a dict"}', "wrong type"),
])
def test_an_unreadable_manifest_is_a_mismatch_not_a_validation_error(pinned, task, text, why):
    """F1: every way a stored oracle can be unusable must be one exception type.

    Otherwise bad_patch_check's `except (OracleMismatch, KeyError)` misses it and --check-only
    dies with a raw traceback instead of a FIXTURE PROBLEM line.
    """
    (pinned / "manifest.json").write_text(text)

    with pytest.raises(OracleMismatch) as e:
        load_oracle(task, pinned)
    assert "manifest" in str(e.value), why


def test_oracle_mismatch_is_not_an_infra_error():
    """Settled decision: a stale fixture is not a docker failure.

    It still reaches Evidence.infra_errors through gather_evidence's generic except, but
    typing it as InfraError would let it be caught as one anywhere else.
    """
    assert not issubclass(OracleMismatch, InfraError)


# --- the adapter reuses instead of drawing (AC3) -----------------------------------------

CHECKS = [Check(name=f"{REL}::test_outcome_rejects_keywords", kind="hidden", before="fail", after="pass")]


@pytest.fixture
def no_draw(monkeypatch, task):
    """Stub everything below the adapter that would need Docker or the network.

    write_hidden_tests is patched on the ADAPTER module, not on test_writer: adapter.py
    binds the name at import time, so patching test_writer would not be seen.
    """
    seen = {"checks_args": None}

    async def never_draw(*a, **kw):
        pytest.fail("write_hidden_tests called even though an oracle was given")

    async def fake_run_checks(task_, diff, hidden_dir, hidden_files, existing, log_dir, canaries=()):
        seen["checks_args"] = (hidden_dir, hidden_files, existing)
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        return list(CHECKS)

    async def no_existing(self, task_, artifact, tests=()):
        # the sandbox now opens inside _existing_tests, so that is the Docker seam
        return [], {}

    monkeypatch.setattr(adapter_mod, "write_hidden_tests", never_draw)
    monkeypatch.setattr(adapter_mod, "run_checks", fake_run_checks)
    monkeypatch.setattr(adapter_mod, "load_safe", lambda _id: task)
    monkeypatch.setattr(CodingAdapter, "_existing_tests", no_existing)
    return seen


async def test_adapter_with_an_oracle_never_draws(pinned, task, no_draw, tmp_path):
    run_dir = tmp_path / "verify-run"
    run_dir.mkdir()

    evidence = await CodingAdapter(run_dir, pinned).gather_evidence(
        Request(task_id=task.instance_id, text=task.request_text()), "diff --git a/x.py b/x.py\n")

    assert evidence.infra_errors == []
    assert evidence.checks == CHECKS
    assert evidence.artifacts["oracle"] == str(pinned)
    # the pinned tree was copied into the run dir, byte for byte
    assert (run_dir / "hidden_tests" / REL).read_text() == BODY
    hidden_dir, hidden_files, _ = no_draw["checks_args"]
    assert (hidden_dir, hidden_files) == (run_dir / "hidden_tests", [REL])
    assert decide(evidence)[0] == "PASS"


async def test_adapter_without_an_oracle_still_draws(pinned, task, monkeypatch, tmp_path):
    """The other half of AC3: no --oracle means today's behavior, unchanged."""
    drew = []

    async def fake_draw(task_, run_dir, **_):
        hidden = Path(run_dir) / "hidden_tests"
        (hidden / Path(REL).parent).mkdir(parents=True, exist_ok=True)
        (hidden / REL).write_text(BODY)
        drew.append(task_.instance_id)
        return [hidden / REL]

    async def fake_run_checks(*a, **kw):
        Path(a[5]).mkdir(parents=True, exist_ok=True)
        return list(CHECKS)

    async def no_existing(self, task_, artifact, tests=()):
        # the sandbox now opens inside _existing_tests, so that is the Docker seam
        return [], {}

    monkeypatch.setattr(adapter_mod, "write_hidden_tests", fake_draw)
    monkeypatch.setattr(adapter_mod, "run_checks", fake_run_checks)
    monkeypatch.setattr(adapter_mod, "load_safe", lambda _id: task)
    monkeypatch.setattr(CodingAdapter, "_existing_tests", no_existing)

    run_dir = tmp_path / "fresh-run"
    run_dir.mkdir()
    evidence = await CodingAdapter(run_dir).gather_evidence(
        Request(task_id=task.instance_id, text=""), "diff --git a/x.py b/x.py\n")

    assert drew == [task.instance_id]
    assert evidence.artifacts["oracle"] == "fresh"
    assert evidence.infra_errors == []


async def test_stale_oracle_is_unknown_not_a_crash(pinned, task, no_draw, tmp_path):
    """AC4 end to end, without Docker: the run still writes checks.json and lands UNKNOWN."""
    rewrite_manifest(pinned, base_commit="0" * 40)
    run_dir = tmp_path / "stale-run"
    run_dir.mkdir()

    evidence = await CodingAdapter(run_dir, pinned).gather_evidence(
        Request(task_id=task.instance_id, text=""), "diff --git a/x.py b/x.py\n")

    assert len(evidence.infra_errors) == 1
    assert "OracleMismatch" in evidence.infra_errors[0]
    assert "base_commit" in evidence.infra_errors[0]
    assert evidence.checks == []
    assert evidence.artifacts["oracle"] == str(pinned), "the failed oracle is still on the record"

    written = json.loads((run_dir / "checks.json").read_text())
    assert written["infra_errors"] == evidence.infra_errors

    verdict, reason = decide(evidence)
    assert verdict == "UNKNOWN" and "base_commit" in reason
