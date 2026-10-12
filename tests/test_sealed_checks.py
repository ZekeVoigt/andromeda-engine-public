"""Spec 19's proof: a hidden check's result is computed on the host, from values the box hands
back, against expected values that never enter a box that runs the diff's code.

The Docker-free part covers the seal's pure parts and the judge (ACs 1-2), forgeries over the
bytes a real grading's box handed back (AC3), the runner's and the adapter's wiring with every
container replaced by a recorder (ACs 4, 7), the oracle store and the writer's prompt (ACs 5-6),
the three pinned sealed oracles' soundness (AC9) and the measurement's shape (AC12). The `real`
tests grade in the task images: gold PASS / resolved on all three sealed oracles with no strong
expected value in the box (AC10), and 17's force-pass, with 17's and 18's defences bypassed,
graded FAIL on a sealed oracle where it grades PASS on the reported one (AC11).
"""

import ast
import asyncio
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import andromeda.outcome.adapters.coding.adapter as adapter_mod
import andromeda.outcome.adapters.coding.oracle as oracle
from andromeda.outcome.adapters.coding import runner as runner_mod
from andromeda.outcome.adapters.coding import seal, test_writer
from andromeda.outcome.adapters.coding.adapter import Ablations, CodingAdapter
from andromeda.outcome.adapters.coding.docker import InfraError
from andromeda.outcome.adapters.coding.oracle import OracleMismatch, load_expectations, load_oracle, pin
from andromeda.outcome.adapters.coding.seal import SealError, canonical, judge, lint, strength
from andromeda.outcome.adapters.coding.task import SafeTask
from andromeda.outcome.core.types import Check, Request
from andromeda.outcome.core.verdict import decide, route
from test_gate_integrity import ANSIBLE, C580EBF0, F91ACE96, FIXTURES

TASKS = FIXTURES / "variance" / "tasks"
PINNED = (ANSIBLE, C580EBF0, F91ACE96)


def task_of(instance_id: str) -> SafeTask:
    return SafeTask(**json.loads((TASKS / f"{instance_id}.json").read_text()))


@pytest.fixture(autouse=True)
def _pinned_claude(monkeypatch):
    """load_oracle re-reads OUTCOME_CLAUDE_VERSION: an exported one would be drift here."""
    monkeypatch.delenv("OUTCOME_CLAUDE_VERSION", raising=False)


# --- AC1: the seal's pure parts ---------------------------------------------------------------

OBS = "tests/unit/utils/test_outcome_widened.py"
OBS2 = "tests/unit/components/test_outcome_blocked.py"
LIST = ["a.b.c", "b.c", "c"]
HOST = "www.example.org"


def test_ac1_canonical_orders_keys_and_separates_numbers_and_bools():
    assert canonical({"b": 1, "a": [2, {"d": 3, "c": 4}]}) == '{"a":[2,{"c":4,"d":3}],"b":1}'
    assert canonical(1) != canonical(1.0)
    assert canonical(True) != canonical(1)
    assert canonical("é") == '"é"'  # ensure_ascii=False
    with pytest.raises(ValueError):
        canonical(float("nan"))


@pytest.mark.parametrize("value", [True, False, None, "c", [1], 1234567, "abcde"])
def test_ac1_weak(value):
    assert strength(value) == "weak"


@pytest.mark.parametrize("value", [LIST, {"raised": "ValueError"}, HOST, 123456789, [True, False, True]])
def test_ac1_strong(value):
    assert strength(value) == "strong"


def expected_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


GOOD = {"tests/unit/utils/test_outcome_widened.json": json.dumps({"widened/a.b.c": LIST}),
        "tests/unit/components/test_outcome_blocked.json": json.dumps({"blocked/8": [True, False]})}


def test_ac1_load_pairs_observers_with_their_expectations(tmp_path):
    exp = seal.load(expected_tree(tmp_path, GOOD), [OBS, OBS2])
    assert exp == {OBS: {"widened/a.b.c": LIST}, OBS2: {"blocked/8": [True, False]}}
    assert seal.names(exp) == [f"{OBS2}::blocked/8", f"{OBS}::widened/a.b.c"]
    # a cache the writer's own runs left is nobody's expectations
    expected_tree(tmp_path, {"tests/__pycache__/x.json": "{}"})
    assert seal.load(tmp_path, [OBS, OBS2]) == exp


@pytest.mark.parametrize("files,hidden,why", [
    (GOOD, [OBS, OBS2, "tests/test_outcome_orphan.py"], "has no expectations file"),
    ({**GOOD, "tests/test_outcome_orphan.json": '{"k": 1}'}, [OBS, OBS2], "has no observer"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": "{}"}, [OBS, OBS2], "not a non-empty JSON object"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": "[1, 2]"}, [OBS, OBS2], "not a non-empty JSON object"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": '{"a key": 1}'}, [OBS, OBS2], "breaks the grammar"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": json.dumps({"k" * 161: 1})}, [OBS, OBS2],
     "breaks the grammar"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": '{"blocked/8": 1}'}, [OBS, OBS2], "is in both"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": '{"k": 1, "k": 2}'}, [OBS, OBS2], "repeated"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": '{"k": NaN}'}, [OBS, OBS2], "not JSON data"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": '{"k": 1e400}'}, [OBS, OBS2], "not JSON data"),
    ({**GOOD, "tests/unit/utils/test_outcome_widened.json": '{"k": '}, [OBS, OBS2], "not JSON data"),
])
def test_ac1_load_refuses(tmp_path, files, hidden, why):
    with pytest.raises(SealError, match=why):
        seal.load(expected_tree(tmp_path, files), hidden)


def test_ac1_a_seal_error_is_an_infra_error():
    assert issubclass(SealError, InfraError)


CLEAN = f'''from outcome_seal import observe
from qutebrowser.utils import urlutils


def test_widened():
    for host in ["a" + ".b.c", "x.y"]:
        observe("widened/" + host, list(urlutils.widened_hostnames(host)))
'''


@pytest.mark.parametrize("leak", [
    canonical(LIST),                        # its canonical form
    json.dumps(LIST),                       # its default json.dumps
    repr(LIST),                             # its repr
    f"    # expect {HOST} back\n",          # a bare string, in a comment
    f'@pytest.mark.parametrize("h", [0], ids=["{HOST}"])\n',  # a parametrize id
])
def test_ac1_lint_flags_each_form(leak):
    exp = {OBS: {"widened/a.b.c": LIST, "host": HOST, "weak": True, "short": "c"}}
    flagged = lint(exp, {OBS: CLEAN + leak})
    assert flagged and set(flagged) <= {f"{OBS}::widened/a.b.c", f"{OBS}::host"}
    assert f"{OBS}::weak" not in flagged and f"{OBS}::short" not in flagged


def test_ac1_lint_flags_a_value_in_a_key_and_nothing_when_clean():
    exp = {OBS: {"widened/a.b.c": LIST, "host": HOST, "weak": True, "short": "c"},
           OBS2: {f"seen/{HOST}": [1, 2, 3, 4]}}
    assert lint(exp, {OBS: CLEAN, OBS2: CLEAN}) == [f"{OBS}::host"]
    exp[OBS].pop("host")
    assert lint(exp, {OBS: CLEAN, OBS2: CLEAN}) == []
    # a weak value is everywhere and never flagged: `True`, "c" and `true` are all in the sources
    assert lint({OBS: {"w": True, "x": "c", "n": None}}, {OBS: "True true c None null"}) == []


def test_ac1_without_drops_leaked_keys_and_keeps_an_emptied_observer():
    exp = {OBS: {"a": LIST}, OBS2: {"b": HOST, "c": [9, 9, 9, 9]}}
    assert seal.without(exp, [f"{OBS}::a", f"{OBS2}::b"]) == {OBS: {}, OBS2: {"c": [9, 9, 9, 9]}}


def test_ac1_the_source_reads_no_env_clock_or_randomness():
    src = inspect.getsource(seal)
    assert "environ" not in src and "getenv" not in src
    # and no clock or randomness: the import set is closed
    imported = {n.names[0].name if isinstance(n, ast.Import) else n.module
                for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert imported == {"json", "re", "pathlib", "typing", "andromeda.outcome.adapters.coding.docker",
                        "andromeda.outcome.core.types"}


def test_ac1_the_helper_imports_only_the_stdlib_and_holds_no_expectation():
    tree = ast.parse(seal.HELPER_SOURCE)
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported == {"json", "os"} and not any(isinstance(n, ast.ImportFrom) for n in ast.walk(tree))
    assert seal.OBSERVATIONS in seal.HELPER_SOURCE


def run_helper(tmp_path: Path, body: str) -> str:
    """HELPER_SOURCE for real, in a subprocess, with its file pointed into tmp_path."""
    out = tmp_path / "observations.jsonl"
    (tmp_path / f"{seal.HELPER_MODULE}.py").write_text(
        seal.HELPER_SOURCE.replace(repr(seal.OBSERVATIONS), repr(str(out))))
    subprocess.run([sys.executable, "-c", f"from {seal.HELPER_MODULE} import observe, observe_call\n{body}"],
                   cwd=tmp_path, check=True, env={"PATH": os.environ["PATH"]})
    return out.read_text()


def test_ac1_the_helper_writes_json_data_or_a_type_name(tmp_path):
    text = run_helper(tmp_path, "\n".join([
        "observe('tuple', (1, 'a'))",
        "observe('set', {1})",
        "observe('nan', float('nan'))",
        "observe('obj', object())",
        "observe_call('raised', int, 'x')",
        "observe_call('returned', sorted, (3, 1, 2))",
        "observe_call('unjson', set, [1])",
    ]))
    assert [json.loads(line) for line in text.splitlines()] == [
        {"key": "tuple", "value": [1, "a"]},
        {"key": "set", "error": "set"},
        {"key": "nan", "error": "float"},
        {"key": "obj", "error": "object"},
        {"key": "raised", "value": {"raised": "ValueError"}},
        {"key": "returned", "value": {"returned": [1, 2, 3]}},
        {"key": "unjson", "error": "set"},
    ]
    assert text.endswith("\n")


# --- AC2: the judge ---------------------------------------------------------------------------

EXP = {OBS: {"k": LIST}}
K = f"{OBS}::k"


def v(key, value):
    return json.dumps({"key": key, "value": value}) + "\n"


def e(key, name="set"):
    return json.dumps({"key": key, "error": name}) + "\n"


@pytest.mark.parametrize("text,status", [
    ("", "fail"),                                           # no line
    (v("k", LIST), "pass"),                                 # one equal value
    (v("k", LIST) + v("k", ["c"]), "fail"),                 # equal, then unequal
    (v("k", ["c"]) + v("k", LIST), "fail"),                 # unequal, then equal
    (v("k", LIST) + v("k", LIST), "pass"),                  # two equal
    (e("k"), "error"),                                      # an error line alone
    (e("k") + v("k", ["c"]), "fail"),                       # an error beside an unequal value
    (e("k") + v("k", LIST), "error"),                       # an error beside an equal value
    (v("k", [1.0]), "fail"),                                # canonical: [1.0] is not LIST
    (v("other", LIST), "fail"),                             # an unexpected key: ignored
])
def test_ac2_the_judge(text, status):
    assert judge(EXP, text) == {K: status}


def test_ac2_equality_is_canonical():
    exp = {OBS: {"k": {"a": 1, "b": [True, None]}, "n": 1}}
    text = '{"key":"k","value":{"b":[true,null],"a":1}}\n{"key": "n", "value": 1.0}\n'
    assert judge(exp, text) == {K: "pass", f"{OBS}::n": "fail"}


def test_ac2_junk_and_a_truncated_last_line_are_ignored_and_counted():
    junk = ["not json", "[1, 2]", '{"key": 1, "value": 2}', '{"key": "k"}', '{"key": "k", "value": 1, "x": 2}',
            '{"key": "k", "error": 3}', '{"key": "k", "value": NaN}', '{"key": "k", "value": 1e400}', "",
            "[" * 100000]
    text = v("other", 1) + "\n".join(junk) + "\n" + v("k", LIST) + '{"key": "k", "val'
    summary = seal.judge_summary(EXP, text)
    assert summary == {"statuses": {K: "pass"}, "lines": 1 + len(junk) + 2, "junk": len(junk) + 1,
                       "unexpected": 1}


def test_ac2_judge_returns_exactly_the_expected_keys():
    exp = {OBS: {"a": LIST, "b": HOST}, OBS2: {"c": [1, 2, 3, 4]}, "t/test_outcome_x.py": {}}
    got = judge(exp, v("a", LIST) + v("zzz", 1))
    assert list(got) == [f"{OBS2}::c", f"{OBS}::a", f"{OBS}::b"]
    assert got == {f"{OBS2}::c": "fail", f"{OBS}::a": "pass", f"{OBS}::b": "fail"}


# --- AC4: the runner's wiring, with the box a recorder -----------------------------------------

EXI = "tests/unit/utils/test_urlutils.py"
CAN = "tests/unit/utils/test_outcome_0123456789abcdef.py"
SEALED = {OBS: {"widened/a.b.c": LIST, "host": HOST, "weak": True}}
STRONG_FORMS = sorted({f for value in (LIST, HOST) for f in (canonical(value), json.dumps(value), repr(value))}
                      | {HOST})


class Recorder:
    """A sandbox whose run script leaves scripted logs and observations, recording everything
    that goes in: every copy_in source's bytes, every exec string, and task_sandbox's kwargs."""

    def __init__(self, logs: dict[str, str], stat: str | None = None):
        self.logs, self.stat = logs, stat
        self.execs, self.copied, self.sources, self.kwargs, self.copied_out = [], {}, [], [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def exec(self, cmd, **kw):
        self.execs.append(cmd)
        if cmd == runner_mod.PLANT_SEAL:
            return 0, "/usr/local/lib/python3.9/site-packages\n", ""
        if cmd.startswith("stat -c %s"):
            name = Path(cmd.split()[-1]).name
            if self.stat is not None:
                return 0, self.stat, ""
            return (0, str(len(self.logs[name].encode())), "") if name in self.logs else (1, "", "no such file")
        return 0, "", ""

    async def copy_in(self, src, dst):
        self.sources.append(str(src))
        host = Path(str(src)[:-2] if str(src).endswith("/.") else src)
        if host.is_file():
            self.copied[dst] = host.read_bytes()
        for p in ([] if host.is_file() else sorted(q for q in host.rglob("*") if q.is_file())):
            self.copied[f"{dst.rstrip('/')}/{p.relative_to(host).as_posix()}"] = p.read_bytes()

    async def copy_out(self, src, dst):
        self.copied_out.append(src)
        name = Path(src).name
        if name not in self.logs:
            raise InfraError(f"no {name}")
        Path(dst).write_text(self.logs[name])


def a_task():
    return type("T", (), {"instance_id": C580EBF0, "image": "img", "base_commit": "abc"})()


@pytest.fixture
def box(monkeypatch, tmp_path):
    """Install a Recorder as the runner's task_sandbox, and an oracle beside it: observers in
    hidden_tests/, expectations in expected/ (a sibling, as the adapter lays them out)."""
    boxes = []

    def install(logs, stat=None, cls=None):
        rec = (cls or Recorder)(logs, stat)
        boxes.append(rec)

        def sandbox(image, **kw):
            rec.kwargs.append({"image": image, **kw})
            return rec
        monkeypatch.setattr(runner_mod, "task_sandbox", sandbox)
        return rec

    monkeypatch.setattr(runner_mod, "reset_to_base", lambda c, commit: asyncio.sleep(0))
    hidden = tmp_path / "run" / "hidden_tests"
    (hidden / OBS).parent.mkdir(parents=True)
    (hidden / OBS).write_text(CLEAN)
    (hidden / CAN).write_text("def test_fedcba9876543210():\n    assert 1 == 2\n")
    expected_tree(tmp_path / "run" / "expected", {seal.expectations_path(OBS): json.dumps(SEALED[OBS])})
    return {"install": install, "boxes": boxes, "hidden": hidden, "logs": tmp_path / "run" / "checks_logs"}


def report(before: bool) -> str:
    """A pytest -v report: the observer module 'passes' (it asserts nothing), an existing check,
    a canary that fails as it must."""
    return "\n".join([f"{OBS}::test_widened PASSED", f"{EXI}::test_e PASSED", f"{EXI}::test_f PASSED",
                      f"{CAN}::test_fedcba9876543210 FAILED", ""])


OBSERVED_BEFORE = v("widened/a.b.c", ["a.b.c"]) + v("host", "example.org") + v("weak", False)
OBSERVED_AFTER = v("widened/a.b.c", LIST) + v("host", HOST) + e("weak", "set")


def files_under(d: Path) -> dict[str, bytes]:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_ac4_with_sealed_none_everything_is_18s(box, tmp_path):
    logs = {"stdout.log": report(True), "stderr.log": ""}
    runs = []
    for kw in ({}, {"sealed": None}):
        rec = box["install"](dict(logs))
        log_dir = tmp_path / f"logs{len(runs)}"
        got = asyncio.run(runner_mod.run_checks(a_task(), "diff --git a/x b/x\n", box["hidden"], [OBS], [EXI],
                                                log_dir, canaries=[CAN], **kw))
        runs.append((got, files_under(log_dir), rec.execs, rec.copied, rec.copied_out))
    assert runs[0] == runs[1]
    got, files, execs, copied, copied_out = runs[0]
    # 18's files, and none of 19's: no observations copied out, no seal.json, no helper planted
    assert sorted(files) == ["after_output.json", "after_stderr.log", "after_stdout.log",
                             "before_output.json", "before_stderr.log", "before_stdout.log"]
    assert not any(seal.HELPER_MODULE in cmd or seal.OBSERVATIONS in cmd for cmd in execs)
    assert set(copied_out) == {"/workspace/stdout.log", "/workspace/stderr.log"}
    # the observer's own report is a hidden check here, exactly as 18 reads it
    assert Check(name=f"{OBS}::test_widened", kind="hidden", before="pass", after="pass") in got


def test_ac4_sealed_drops_the_observers_report_and_judges_on_the_host(box):
    box["install"]({"stdout.log": report(True), "stderr.log": "", "outcome_observations.jsonl": OBSERVED_BEFORE})
    box["logs"].mkdir(parents=True)
    before = asyncio.run(runner_mod._run_side(a_task(), None, box["hidden"], sorted([OBS, EXI, CAN]),
                                              box["logs"], "before", sealed=SEALED))
    assert f"{OBS}::test_widened" not in before
    assert before == {f"{EXI}::test_e": "pass", f"{EXI}::test_f": "pass",
                      f"{CAN}::test_fedcba9876543210": "fail",
                      f"{OBS}::host": "fail", f"{OBS}::weak": "fail", f"{OBS}::widened/a.b.c": "fail"}
    assert (box["logs"] / "before_observations.jsonl").read_text() == OBSERVED_BEFORE
    assert json.loads((box["logs"] / "before_seal.json").read_text()) == {
        "statuses": {f"{OBS}::host": "fail", f"{OBS}::weak": "fail", f"{OBS}::widened/a.b.c": "fail"},
        "lines": 3, "junk": 0, "unexpected": 0}
    # the helper is planted, and a stale observations file removed, before the run script
    execs = box["boxes"][0].execs
    run_at = next(i for i, cmd in enumerate(execs) if "run_script.sh" in cmd and "bash" in cmd)
    assert execs.index(runner_mod.PLANT_SEAL) < execs.index(f"rm -f {seal.OBSERVATIONS}") < run_at
    assert box["boxes"][0].copied[f"/workspace/{seal.HELPER_MODULE}.py"] == seal.HELPER_SOURCE.encode()


def test_ac4_run_checks_makes_sealed_keys_hidden_checks(box):
    logs = {"stdout.log": report(True), "stderr.log": ""}
    seq = iter([OBSERVED_BEFORE, OBSERVED_AFTER])

    class Two(Recorder):
        async def copy_out(self, src, dst):
            if Path(src).name == "outcome_observations.jsonl":
                self.logs["outcome_observations.jsonl"] = next(seq)
            await super().copy_out(src, dst)

        async def exec(self, cmd, **kw):
            if cmd.startswith("stat -c %s"):
                return 0, "10", ""
            return await super().exec(cmd, **kw)

    box["install"](logs, cls=Two)
    got = asyncio.run(runner_mod.run_checks(a_task(), "diff --git a/x b/x\n", box["hidden"], [OBS], [EXI],
                                            box["logs"], canaries=[CAN], sealed=SEALED))
    hidden = {c.name: c for c in got if c.kind == "hidden"}
    assert hidden == {
        f"{OBS}::host": Check(name=f"{OBS}::host", kind="hidden", before="fail", after="pass"),
        f"{OBS}::weak": Check(name=f"{OBS}::weak", kind="hidden", before="fail", after="error"),
        f"{OBS}::widened/a.b.c": Check(name=f"{OBS}::widened/a.b.c", kind="hidden", before="fail", after="pass"),
    }
    assert [c.kind for c in got if c.name.startswith(EXI)] == ["existing", "existing"]
    assert [c.kind for c in got if c.name.startswith(CAN)] == ["canary"]
    assert {p for p in files_under(box["logs"])} >= {"before_observations.jsonl", "after_observations.jsonl",
                                                     "before_seal.json", "after_seal.json"}


def test_ac4_a_missing_observations_file_is_every_key_failing(box):
    box["install"]({"stdout.log": report(True), "stderr.log": ""})
    box["logs"].mkdir(parents=True)
    got = asyncio.run(runner_mod._run_side(a_task(), None, box["hidden"], [OBS], box["logs"], "after",
                                           sealed=SEALED))
    assert {n: s for n, s in got.items() if n.startswith(OBS)} == {
        f"{OBS}::host": "fail", f"{OBS}::weak": "fail", f"{OBS}::widened/a.b.c": "fail"}
    assert (box["logs"] / "after_observations.jsonl").read_bytes() == b""


@pytest.mark.parametrize("stat", [None, "5"])  # the box tells the truth, or lies about the size
def test_ac4_an_oversized_observations_file_is_an_infra_error(box, stat):
    big = v("host", HOST) * (seal.MAX_OBSERVATION_BYTES // len(v("host", HOST)) + 1)
    rec = box["install"]({"stdout.log": report(True), "stderr.log": "", "outcome_observations.jsonl": big}, stat)
    box["logs"].mkdir(parents=True)
    with pytest.raises(InfraError, match="over 16777216"):
        asyncio.run(runner_mod._run_side(a_task(), None, box["hidden"], [OBS], box["logs"], "after",
                                         sealed=SEALED))
    # checked in the box first: a truthful size is never transferred
    assert ("/workspace/outcome_observations.jsonl" in rec.copied_out) == (stat is not None)


def test_ac4_no_expectation_enters_the_box(box):
    """Invariant 1: no copy_in source under expected/, and no strong value in anything copied
    in, any command, or the sandbox's arguments -- with a diff and on both sides."""
    rec = box["install"]({"stdout.log": report(True), "stderr.log": "", "outcome_observations.jsonl": ""})
    diff = "diff --git a/qutebrowser/utils/urlutils.py b/qutebrowser/utils/urlutils.py\n"
    asyncio.run(runner_mod.run_checks(a_task(), diff, box["hidden"], [OBS], [EXI], box["logs"],
                                      canaries=[CAN], sealed=SEALED))
    asyncio.run(runner_mod.run_after(a_task(), diff, box["hidden"], [OBS], box["logs"], "hunk0",
                                     canaries=[CAN], sealed=SEALED))
    assert rec.copied and rec.execs
    assert not [p for p in rec.sources if "expected" in Path(p).parts]
    carried = [*(b.decode(errors="replace") for b in rec.copied.values()), *rec.execs, json.dumps(rec.kwargs)]
    assert not [(form, text[:80]) for form in STRONG_FORMS for text in carried if form in text]


def test_ac4_run_after_judges_the_same_keys_in_a_hunk_run(box):
    box["install"]({"stdout.log": report(True), "stderr.log": "", "outcome_observations.jsonl": OBSERVED_AFTER})
    got = asyncio.run(runner_mod.run_after(a_task(), "d", box["hidden"], [OBS, EXI], box["logs"], "hunk3",
                                           canaries=[CAN], sealed=SEALED))
    assert {n: s for n, s in got.items() if n.startswith(OBS)} == {
        f"{OBS}::host": "pass", f"{OBS}::weak": "error", f"{OBS}::widened/a.b.c": "pass"}
    assert (box["logs"] / "hunk3_seal.json").is_file() and (box["logs"] / "hunk3_observations.jsonl").is_file()


def test_ac4_only_the_observers_a_side_runs_are_judged_on_it(box):
    box["install"]({"stdout.log": f"{EXI}::test_e PASSED\n", "stderr.log": "", "outcome_observations.jsonl": ""})
    box["logs"].mkdir(parents=True)
    got = asyncio.run(runner_mod._run_side(a_task(), "d", box["hidden"], [EXI], box["logs"], "hunk0",
                                           sealed=SEALED))
    assert got == {f"{EXI}::test_e": "pass"}


def test_ac4_an_observer_with_every_key_leaked_is_still_never_a_check(box):
    box["install"]({"stdout.log": report(True), "stderr.log": "", "outcome_observations.jsonl": ""})
    box["logs"].mkdir(parents=True)
    got = asyncio.run(runner_mod._run_side(a_task(), "d", box["hidden"], [OBS, EXI], box["logs"], "after",
                                           sealed={OBS: {}}))
    assert not [n for n in got if n.startswith(OBS)]


# --- AC5: the oracle store ----------------------------------------------------------------------

def stored_oracles() -> list[tuple[str, Path]]:
    out = [(p.name, p) for p in sorted((FIXTURES / "oracles").iterdir()) if p.is_dir()]
    for task_dir in sorted((FIXTURES / "variance").iterdir()):
        if task_dir.is_dir() and task_dir.name != "tasks":
            out += [(task_dir.name, d) for d in sorted(task_dir.iterdir()) if d.is_dir()]
    return out


def test_ac5_every_stored_oracle_loads_unedited_as_reported():
    seen = stored_oracles()
    assert len(seen) == 18  # 3 pinned, 15 variance draws
    for instance_id, d in seen:
        manifest = oracle.OracleManifest.model_validate_json((d / "manifest.json").read_text())
        assert manifest.form == "reported" and manifest.expected == {}
        assert "form" not in json.loads((d / "manifest.json").read_text())  # unedited
        tree, rel = load_oracle(task_of(instance_id), d)
        assert rel and tree == d / "hidden_tests"
        assert load_expectations(d) is None


def test_ac5_the_reported_prompt_is_todays_bytes():
    task = task_of(C580EBF0)
    assert test_writer.build_prompt(task, "reported") == test_writer.INSTRUCTIONS + task.request_text()
    # the bytes every stored manifest hashed: one of them, re-derived
    manifest = json.loads((FIXTURES / "oracles" / C580EBF0 / "manifest.json").read_text())
    import hashlib
    assert hashlib.sha256(test_writer.build_prompt(task, "reported").encode()).hexdigest() == manifest["prompt_sha256"]
    assert test_writer.build_prompt(task) == test_writer.build_prompt(task, "sealed") != \
        test_writer.build_prompt(task, "reported")


@pytest.fixture
def sealed_store(monkeypatch, tmp_path):
    """A constructed sealed draw of c580ebf0 (observers + expected/) and a store for it."""
    task = task_of(C580EBF0)
    monkeypatch.setattr(oracle, "STORE", tmp_path / "oracles")
    monkeypatch.setattr(oracle, "load_safe", lambda _id: task)
    run_dir = tmp_path / "run"
    expected_tree(run_dir / "hidden_tests", {OBS: CLEAN, OBS2: "from outcome_seal import observe\n",
                                             "tests/__pycache__/test_outcome_x.cpython-39.pyc": "junk"})
    expected_tree(run_dir / "expected", {**GOOD, "tests/__pycache__/cache.json": "{}"})
    return {"task": task, "run": run_dir, "store": tmp_path / "oracles"}


def test_ac5_a_sealed_draw_pins_and_round_trips(sealed_store):
    import hashlib
    task = sealed_store["task"]
    dest = pin(task.instance_id, sealed_store["run"])
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["form"] == "sealed"
    assert manifest["expected"] == {rel: hashlib.sha256(text.encode()).hexdigest() for rel, text in GOOD.items()}
    assert manifest["prompt_sha256"] == hashlib.sha256(test_writer.build_prompt(task, "sealed").encode()).hexdigest()
    assert sorted(manifest["files"]) == [OBS2, OBS]
    # expected/ beside the tree, never inside it
    assert (dest / "expected").is_dir() and not (dest / "hidden_tests" / "expected").exists()
    assert load_oracle(task, dest) == (dest / "hidden_tests", [OBS2, OBS])
    assert load_expectations(dest) == {OBS: {"widened/a.b.c": LIST}, OBS2: {"blocked/8": [True, False]}}
    # a reported re-pin over it takes expected/ away and writes 18's manifest
    shutil.rmtree(sealed_store["run"] / "expected")
    pin(task.instance_id, sealed_store["run"])
    assert not (dest / "expected").exists()
    assert "form" not in json.loads((dest / "manifest.json").read_text())
    assert load_expectations(dest) is None


@pytest.mark.parametrize("change,why", [
    (lambda d: (d / "expected" / seal.expectations_path(OBS)).write_text('{"widened/a.b.c": ["x"]}'),
     f"expected\\[{seal.expectations_path(OBS)}\\]: sha256"),
    (lambda d: (d / "expected" / seal.expectations_path(OBS)).unlink(),
     f"expected\\[{seal.expectations_path(OBS)}\\]: missing"),
    (lambda d: (d / "expected" / "tests" / "extra.json").write_text("{}"),
     "expected\\[tests/extra.json\\]: on disk but not in the manifest"),
])
def test_ac5_a_moved_expectation_is_drift_named(sealed_store, change, why):
    dest = pin(sealed_store["task"].instance_id, sealed_store["run"])
    change(dest)
    with pytest.raises(OracleMismatch, match=why):
        load_oracle(sealed_store["task"], dest)


def test_ac5_an_expected_dir_beside_a_reported_oracle_is_drift(tmp_path):
    d = tmp_path / "o"
    shutil.copytree(FIXTURES / "oracles" / C580EBF0, d)
    expected_tree(d / "expected", {"tests/x.json": "{}"})
    with pytest.raises(OracleMismatch, match="expected\\[tests/x.json\\]: on disk but not in the manifest"):
        load_oracle(task_of(C580EBF0), d)


@pytest.mark.parametrize("spoil,why", [
    (lambda run: (run / "hidden_tests" / OBS).write_text(CLEAN + f"# {json.dumps(LIST)}\n"),
     f"leaks 1 expected value.*{OBS}::widened/a.b.c"),
    (lambda run: (run / "expected" / "tests" / "test_outcome_orphan.json").write_text('{"k": 1}'),
     "has no observer"),
])
def test_ac5_pin_refuses_a_leak_or_a_seal_error_and_leaves_the_store(sealed_store, spoil, why):
    task = sealed_store["task"]
    dest = pin(task.instance_id, sealed_store["run"])
    before = files_under(sealed_store["store"])
    spoil(sealed_store["run"])
    with pytest.raises(SealError, match=why):
        pin(task.instance_id, sealed_store["run"])
    assert files_under(sealed_store["store"]) == before
    # and a first pin that is refused leaves nothing at all
    with pytest.raises(SealError):
        pin(task.instance_id, sealed_store["run"], dest.parent / "elsewhere")
    assert not (dest.parent / "elsewhere").exists()


# --- AC6: the writer ------------------------------------------------------------------------------

def test_ac6_the_sealed_prompt_names_the_seal():
    prompt = test_writer.build_prompt(task_of(C580EBF0))
    for needle in ("/expected", "observe(", "observe_call(", "NEVER write an expected value anywhere under /hidden",
                   f"from {seal.HELPER_MODULE} import observe, observe_call", "1 is not 1.0"):
        assert needle in prompt, needle
    # every rule of today's prompt is kept, word for word
    for line in test_writer.INSTRUCTIONS.splitlines():
        assert line in test_writer.SEALED_INSTRUCTIONS, line
    assert test_writer.ORACLE_FORM == "sealed"


# --- AC7: the adapter's wiring ---------------------------------------------------------------------

@pytest.fixture
def adapter(monkeypatch, tmp_path):
    """gather_evidence with run_checks, ablate and _existing_tests replaced by recorders, the
    oracle store real, and a regression in the checks so that ablate is reached."""
    task = task_of(C580EBF0)
    monkeypatch.setattr(oracle, "load_safe", lambda _id: task)
    monkeypatch.setattr(adapter_mod, "load_safe", lambda _id: task)
    st = {"run_checks": [], "ablate": [], "task": task}

    async def existing_tests(self, task_, artifact, tests=()):
        return [EXI], {}

    async def run_checks(task_, diff, hidden_dir, hidden_files, existing_, log_dir, canaries=(), sealed=None):
        st["run_checks"].append({"sealed": sealed, "hidden_dir": Path(hidden_dir), "canaries": list(canaries),
                                 "hidden": list(hidden_files),
                                 "planted": {c: (Path(hidden_dir) / c).exists() for c in canaries}})
        names = seal.names(sealed) if sealed is not None else [f"{h}::test_x" for h in hidden_files]
        return [*(Check(name=n, kind="hidden", before="fail", after="pass") for n in names),
                Check(name=f"{EXI}::test_e", kind="existing", before="pass", after="fail"),
                *(Check(name=f"{c}::test_x", kind="canary", before="fail", after="fail") for c in canaries)]

    async def ablate(task_, diff, hidden_root, checks, log_dir, canaries=(), sealed=None):
        st["ablate"].append({"sealed": sealed})
        return Ablations(considered=True, skipped="no-hunks")

    monkeypatch.setattr(CodingAdapter, "_existing_tests", existing_tests)
    monkeypatch.setattr(adapter_mod, "run_checks", run_checks)
    monkeypatch.setattr(adapter_mod, "ablate", ablate)

    def go(oracle_dir: Path | None):
        run_dir = tmp_path / f"run{len(st['run_checks'])}-{time_ns()}"
        run_dir.mkdir()
        diff = "diff --git a/qutebrowser/utils/urlutils.py b/qutebrowser/utils/urlutils.py\n"
        ev = asyncio.run(CodingAdapter(run_dir, oracle_dir).gather_evidence(Request(task_id=C580EBF0, text=""), diff))
        return ev, run_dir
    st["go"] = go
    return st


def time_ns() -> int:
    import time
    return time.time_ns()


def sealed_oracle(tmp_path: Path, leak: bool = False) -> Path:
    """A sealed oracle pinned through the real `pin`; `leak` edits one observer afterwards and
    re-pins the manifest's hash, as a stored oracle that leaks would look."""
    run = tmp_path / f"draw-{time_ns()}"
    expected_tree(run / "hidden_tests", {OBS: CLEAN, OBS2: "from outcome_seal import observe\n"})
    expected_tree(run / "expected", {
        seal.expectations_path(OBS): json.dumps({"widened/a.b.c": LIST, "host": HOST}),
        seal.expectations_path(OBS2): json.dumps({"blocked/8": [True, False], "flag": True})})
    dest = pin(C580EBF0, run, tmp_path / f"oracle-{time_ns()}")
    if leak:
        (dest / "hidden_tests" / OBS).write_text(CLEAN + f"# {HOST}\n")
        m = json.loads((dest / "manifest.json").read_text())
        m["files"][OBS] = oracle.sha256_file(dest / "hidden_tests" / OBS)
        (dest / "manifest.json").write_text(json.dumps(m))
    return dest


def test_ac7_a_pinned_sealed_oracle_reaches_run_checks_and_ablate(adapter, tmp_path):
    ev, run_dir = adapter["go"](sealed_oracle(tmp_path))
    assert ev.infra_errors == [] and ev.sealed is True
    want = {OBS: {"widened/a.b.c": LIST, "host": HOST}, OBS2: {"blocked/8": [True, False], "flag": True}}
    assert adapter["run_checks"][0]["sealed"] == want
    assert adapter["ablate"] == [{"sealed": want}]
    # the expectations were copied into the run dir, beside hidden_tests/ and never under it
    call = adapter["run_checks"][0]
    assert call["hidden_dir"] == run_dir / "hidden_tests"
    assert seal.load(run_dir / "expected", sorted(want)) == want
    assert not list((run_dir / "hidden_tests").rglob("*.json"))
    assert json.loads((run_dir / "seal.json").read_text()) == {
        "form": "sealed",
        "keys": [f"{OBS2}::blocked/8", f"{OBS2}::flag", f"{OBS}::host", f"{OBS}::widened/a.b.c"],
        "strong": [f"{OBS2}::blocked/8", f"{OBS}::host", f"{OBS}::widened/a.b.c"],
        "weak": [f"{OBS2}::flag"],
        "leaked": []}
    assert json.loads((run_dir / "checks.json").read_text())["sealed"] is True


def test_ac7_a_leaked_key_is_named_and_never_judged(adapter, tmp_path):
    ev, run_dir = adapter["go"](sealed_oracle(tmp_path, leak=True))
    assert ev.infra_errors == []
    assert json.loads((run_dir / "seal.json").read_text())["leaked"] == [f"{OBS}::host"]
    assert "host" not in adapter["run_checks"][0]["sealed"][OBS]
    assert "host" not in adapter["ablate"][0]["sealed"][OBS]
    assert f"{OBS}::host" not in {c.name for c in ev.checks}


def test_ac7_a_fresh_sealed_draw(adapter, monkeypatch):
    async def draw(task, run_dir, *, route=None):
        expected_tree(run_dir / "hidden_tests", {OBS: CLEAN})
        expected_tree(run_dir / "expected", {seal.expectations_path(OBS): json.dumps({"widened/a.b.c": LIST})})
        return [run_dir / "hidden_tests" / OBS]

    monkeypatch.setattr(adapter_mod, "write_hidden_tests", draw)
    ev, run_dir = adapter["go"](None)
    assert ev.sealed is True and ev.infra_errors == []
    assert adapter["run_checks"][0]["sealed"] == adapter["ablate"][0]["sealed"] == {OBS: {"widened/a.b.c": LIST}}
    assert json.loads((run_dir / "seal.json").read_text())["keys"] == [f"{OBS}::widened/a.b.c"]


def test_ac7_a_stray_file_a_fresh_draw_copies_in_is_linted_too(adapter, monkeypatch):
    async def draw(task, run_dir, *, route=None):
        expected_tree(run_dir / "hidden_tests", {OBS: CLEAN, "tests/unit/utils/notes.json": json.dumps(LIST)})
        expected_tree(run_dir / "expected", {seal.expectations_path(OBS): json.dumps({"widened/a.b.c": LIST})})
        return [run_dir / "hidden_tests" / OBS]

    monkeypatch.setattr(adapter_mod, "write_hidden_tests", draw)
    ev, run_dir = adapter["go"](None)
    assert json.loads((run_dir / "seal.json").read_text())["leaked"] == [f"{OBS}::widened/a.b.c"]
    assert adapter["run_checks"][0]["sealed"] == {OBS: {}}


def test_ac7_canaries_are_planted_beside_the_observers_and_removed(adapter, tmp_path):
    _, run_dir = adapter["go"](sealed_oracle(tmp_path))
    call = adapter["run_checks"][0]
    assert sorted(str(Path(c).parent) for c in call["canaries"]) == sorted({str(Path(OBS).parent),
                                                                            str(Path(OBS2).parent)})
    assert all(call["planted"].values()) and not set(call["canaries"]) & set(call["hidden"])
    assert not [c for c in call["canaries"] if (run_dir / "hidden_tests" / c).exists()]


def test_ac7_a_seal_error_is_unknown_unverified_with_checks_json(adapter, tmp_path):
    d = sealed_oracle(tmp_path)
    (d / "expected" / seal.expectations_path(OBS)).write_text('{"widened/a.b.c": NaN}')
    m = json.loads((d / "manifest.json").read_text())
    m["expected"][seal.expectations_path(OBS)] = oracle.sha256_file(d / "expected" / seal.expectations_path(OBS))
    (d / "manifest.json").write_text(json.dumps(m))
    ev, run_dir = adapter["go"](d)
    assert ev.infra_errors and "not JSON data" in ev.infra_errors[0]
    assert decide(ev)[0] == "UNKNOWN" and route(ev) == "unverified"
    assert adapter["run_checks"] == [] and ev.sealed is False
    assert json.loads((run_dir / "checks.json").read_text())["infra_errors"] == ev.infra_errors


def test_ac7_a_reported_oracle_hands_on_sealed_none(adapter):
    ev, run_dir = adapter["go"](FIXTURES / "oracles" / C580EBF0)
    assert ev.infra_errors == [] and ev.sealed is False
    assert [c["sealed"] for c in adapter["run_checks"]] == [None]
    assert adapter["ablate"] == [{"sealed": None}]
    assert not (run_dir / "seal.json").exists() and not (run_dir / "expected").exists()


def test_ac5_pin_check_false_keeps_a_leaking_draw_as_drawn(sealed_store, tmp_path):
    """scripts/sealed_variance.py's one exception: every variance draw is kept, leaks and all."""
    (sealed_store["run"] / "hidden_tests" / OBS).write_text(CLEAN + f"# {json.dumps(LIST)}\n")
    dest = pin(C580EBF0, sealed_store["run"], tmp_path / "kept", check=False)
    assert json.loads((dest / "manifest.json").read_text())["form"] == "sealed"
    load_oracle(sealed_store["task"], dest)
    tree = dest / "hidden_tests"
    exp = load_expectations(dest)
    assert lint(exp, {r: (tree / r).read_text() for r in exp}) == [f"{OBS}::widened/a.b.c"]


# --- AC9: the pinned sealed oracles are sound (the Docker-free half) -----------------------------

SEALED_ORACLES = FIXTURES / "sealed-oracles"
STRONG_SHARE = 0.8


def test_ac9_one_sealed_oracle_per_pinned_task():
    assert sorted(p.name for p in SEALED_ORACLES.iterdir() if p.is_dir()) == sorted(PINNED)


@pytest.mark.parametrize("instance_id", PINNED, ids=["ansible", "c580ebf0", "f91ace96"])
def test_ac9_each_pinned_sealed_oracle_is_sound(instance_id):
    d = SEALED_ORACLES / instance_id
    tree, rel = load_oracle(task_of(instance_id), d)
    assert json.loads((d / "manifest.json").read_text())["form"] == "sealed"
    exp = load_expectations(d)
    assert sorted(exp) == rel and all(exp.values())
    assert lint(exp, {r: (tree / r).read_text() for r in rel}) == []
    values = [value for keys in exp.values() for value in keys.values()]
    strong = sum(strength(value) == "strong" for value in values)
    assert strong / len(values) >= STRONG_SHARE, f"{strong}/{len(values)} strong"


# --- AC12: the measurement's shape (quick tier) ---------------------------------------------------

def sealed_variance():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sealed_variance", FIXTURES.parent / "scripts" / "sealed_variance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ac12_the_table_has_every_cell_and_no_false_verdict():
    sv = sealed_variance()
    rows = sv.read_results()
    assert sv.COLUMNS == [*sv.ov.COLUMNS, "n_keys", "n_strong", "n_leaked"]
    cells = sorted((r["task"], r["oracle"], r["patch"]) for r in rows)
    assert cells == sorted((sv.ov.short_task(t), o, p) for t in sv.TASKS for o in sv.ORACLES for p in sv.PATCHES)
    assert len(rows) == 18
    assert not [r for r in rows if r["patch"] == "gold" and r["verdict"] == "FAIL"]
    assert not [r for r in rows if r["patch"] == "half" and r["verdict"] == "PASS"]
    for r in rows:
        assert r["verdict"] in ("PASS", "FAIL", "UNKNOWN") and r["reason"]  # an UNKNOWN names its cause
        assert all(r[c] == "-" or r[c].isdigit() for c in ("n_keys", "n_strong", "n_leaked"))
    # every draw is kept: each draw dir the table grades is on disk, pinned
    for t in sv.TASKS:
        for o in sv.ORACLES:
            assert (FIXTURES.parent / sv.oracle_dir(t, o) / "manifest.json").is_file()


def test_ac12_the_script_draws_two_and_never_redraws():
    sv = sealed_variance()
    assert sv.SEALED_DRAWS == 2 and sv.ORACLES == ["pinned", "draw-01", "draw-02"]
    src = inspect.getsource(sv)
    assert "check=False" in src and "grading" not in {n.module for n in ast.walk(ast.parse(src))
                                                      if isinstance(n, ast.ImportFrom) and n.module}


# --- AC3: forgeries over what the box handed back --------------------------------------------------

RECORDED = FIXTURES / "sealed" / C580EBF0


def recorded(name: str) -> str:
    return (RECORDED / "logs" / name).read_text()


def oracle_c580():
    d = SEALED_ORACLES / C580EBF0
    tree, rel = load_oracle(task_of(C580EBF0), d)
    return tree, rel, load_expectations(d)


def side(monkeypatch, tmp_path, label, stdout, stderr, observations, sealed, files):
    """The runner's real host half over bytes the box handed back (or forged ones)."""
    logs = {"stdout.log": stdout, "stderr.log": stderr}
    if observations is not None:
        logs["outcome_observations.jsonl"] = observations
    rec = Recorder(logs)
    monkeypatch.setattr(runner_mod, "task_sandbox", lambda image, **kw: rec)
    monkeypatch.setattr(runner_mod, "reset_to_base", lambda c, commit: asyncio.sleep(0))
    log_dir = tmp_path / f"{label}-{time_ns()}"
    log_dir.mkdir()
    kw = {} if sealed is None else {"sealed": sealed}
    got = asyncio.run(runner_mod._run_side(a_task(), None, tmp_path / "none", files, log_dir, label, **kw))
    return got, log_dir


def graded(monkeypatch, tmp_path, after_bytes, sealed=True):
    """(evidence, after-side log dir) for the recorded before side and `after_bytes`."""
    _, rel, exp = oracle_c580()
    canaries = json.loads((RECORDED / "canaries.json").read_text())
    files = sorted({*rel, *canaries})
    sealed_exp = exp if sealed else None
    before, _ = side(monkeypatch, tmp_path, "before", recorded("before_stdout.log"), recorded("before_stderr.log"),
                     recorded("before_observations.jsonl"), sealed_exp, files)
    after, log_dir = side(monkeypatch, tmp_path, "after", *after_bytes, sealed_exp, files)
    checks = runner_mod.to_checks(before, after, files, rel, set(), canaries=canaries)
    from andromeda.outcome.core.types import Evidence
    return Evidence(checks=[c for c in checks if c.kind != "canary"],
                    canaries=[c for c in checks if c.kind == "canary"]), log_dir


def unforged():
    return recorded("after_stdout.log"), recorded("after_stderr.log"), recorded("after_observations.jsonl")


def forge_a(stdout: str) -> str:
    """Every FAILED/ERROR report line made PASSED, and the canaries' lines deleted."""
    canaries = json.loads((RECORDED / "canaries.json").read_text())
    lines = [ln for ln in stdout.splitlines(keepends=True) if not any(c in ln for c in canaries)]
    return "".join(ln.replace(" FAILED", " PASSED").replace(" ERROR", " PASSED") for ln in lines)


def everything_the_box_knows(exp) -> str:
    """(c): for every key, one observation of every literal in the observers and in the task's
    in-box test modules they import (recorded at base, under base/)."""
    tree, rel, _ = oracle_c580()
    sources = [(tree / r).read_text() for r in rel]
    sources += [p.read_text() for p in sorted((RECORDED / "base").rglob("*.py"))]
    constants = []
    for src in sources:
        for node in ast.walk(ast.parse(src)):
            try:
                value = ast.literal_eval(node)
                json.dumps(value, allow_nan=False)
            except (ValueError, TypeError, SyntaxError, RecursionError):
                continue
            if value not in constants:
                constants.append(value)
    assert len(constants) > 20, "the box knows more than this: the scan found too little"
    return "".join(v(key, c) for keys in exp.values() for key in keys for c in constants)


def forgeries():
    stdout, stderr, observations = unforged()
    before_obs = recorded("before_observations.jsonl")
    cut = observations[: len(observations) // 2]
    return {
        "a-reports-rewritten": (forge_a(stdout), stderr, observations),
        "b-before-replayed": (stdout, stderr, before_obs),
        "d-deleted": (stdout, stderr, None),
        "d-truncated": (stdout, stderr, cut if not cut.endswith("\n") else cut[:-1]),
        "d-padded": (stdout, stderr, "junk\n" + observations + observations + '{"key": 1}\n[\n'),
        "a+b": (forge_a(stdout), stderr, before_obs),
    }


def test_ac3_the_recording_is_empty_diffs_on_the_sealed_oracle(monkeypatch, tmp_path):
    source = (RECORDED / "SOURCE").read_text()
    assert "empty.diff" in source and "sealed-oracles" in source
    ev, _ = graded(monkeypatch, tmp_path, unforged())
    assert decide(ev)[0] == "FAIL"


@pytest.mark.parametrize("name", ["a-reports-rewritten", "b-before-replayed", "d-deleted", "d-truncated",
                                  "d-padded", "a+b", "c-everything-the-box-knows"])
def test_ac3_a_forgery_moves_no_sealed_check(monkeypatch, tmp_path, name):
    truth, truth_dir = graded(monkeypatch, tmp_path, unforged())
    assert decide(truth)[0] == "FAIL"
    if name.startswith("c-"):
        stdout, stderr, _ = unforged()
        bytes_ = (forge_a(stdout), stderr, everything_the_box_knows(oracle_c580()[2]))
    else:
        bytes_ = forgeries()[name]
    forged, forged_dir = graded(monkeypatch, tmp_path, bytes_)
    valid = [c for c in forged.checks if c.kind == "hidden" and c.before == "fail"]
    assert valid and all(c.after != "pass" for c in valid)
    assert decide(forged)[0] == decide(truth)[0] == "FAIL"
    if name == "a-reports-rewritten":
        # invariant 2: the sealed statuses are a function of the observations alone
        assert json.loads((forged_dir / "after_seal.json").read_bytes()) == \
            json.loads((truth_dir / "after_seal.json").read_bytes())
        assert (forged_dir / "after_seal.json").read_bytes() == (truth_dir / "after_seal.json").read_bytes()


def test_ac3_the_control_arm_the_reported_path_credits_forgery_a(monkeypatch, tmp_path):
    """The hole this spec closes: read as a reported oracle (sealed=None), the same forged
    report makes every hidden check pass after."""
    stdout, stderr, observations = unforged()
    honest, _ = graded(monkeypatch, tmp_path, unforged(), sealed=False)
    forged, _ = graded(monkeypatch, tmp_path, (forge_a(stdout), stderr, observations), sealed=False)
    hidden = [c for c in forged.checks if c.kind == "hidden"]
    assert hidden and all(c.after == "pass" for c in hidden)
    assert [c for c in honest.checks if c.kind == "hidden" and c.after != "pass"]
    assert decide(forged)[0] == "PASS" != decide(honest)[0]


# --- ACs 10-11: graded for real, on the sealed oracles -------------------------------------------

from test_gate_integrity import bad_patch, bypassed, gate_fixture, triples  # noqa: E402,F401 (a fixture)
from test_in_process import assert_canaries_held  # noqa: E402

# Each gold grading's reason, measured in the build by grading gold.diff on its sealed oracle.
GOLD_REASONS: dict[str, str] = {
    ANSIBLE: "all 11 valid hidden checks pass, no regressions",
    C580EBF0: ("all 5 valid hidden checks pass, no regressions; 1 existing checks stale, forgiven by "
               "required-hunk: tests/unit/components/test_hostblock.py::test_disabled_blocking_per_url"),
    F91ACE96: "all 6 valid hidden checks pass, no regressions",
}

# /app's untracked, modified and ignored files, /workspace and /tmp, as one tar at /: what a
# grading box holds besides the unchanged repo. Read on the host, so no needle enters the box.
SNAPSHOT = ("cd /app && git ls-files -z --others --modified > /outcome_spy.list; "
            "tar -cf /outcome_spy.tar --ignore-failed-read --null -T /outcome_spy.list -C / workspace tmp "
            ">/dev/null 2>&1; test -s /outcome_spy.tar")


def spy_on_boxes(monkeypatch, out_dir: Path) -> list[Path]:
    """Wrap the runner's task_sandbox: before each box is torn down, its snapshot comes out."""
    real, grabbed = runner_mod.task_sandbox, []
    out_dir.mkdir(parents=True, exist_ok=True)

    class Spy:
        def __init__(self, cm):
            self.cm = cm

        async def __aenter__(self):
            self.c = await self.cm.__aenter__()
            return self.c

        async def __aexit__(self, *exc):
            try:
                if exc[0] is None:
                    code, _, err = await self.c.exec(SNAPSHOT, timeout=900, workdir="/", check=False)
                    assert code == 0, err
                    dest = out_dir / f"box{len(grabbed)}.tar"
                    await self.c.copy_out("/outcome_spy.tar", dest)
                    grabbed.append(dest)
            finally:
                return await self.cm.__aexit__(*exc)

    monkeypatch.setattr(runner_mod, "task_sandbox", lambda image, **kw: Spy(real(image, **kw)))
    return grabbed


def needles(exp) -> set[bytes]:
    return {form.encode() for keys in exp.values() for value in keys.values() if strength(value) == "strong"
            for form in (canonical(value), json.dumps(value), repr(value))}


def found_in(tar_path: Path, wanted: set[bytes], observations: bool = False) -> list[tuple[str, str]]:
    """(member, needle) for each strong form in the snapshot, the observations file aside: it
    holds what the box's own code produced, which is the channel, not a leak."""
    import tarfile
    hits = []
    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            if not member.isfile() or (member.name.endswith(Path(seal.OBSERVATIONS).name) and not observations):
                continue
            data = tar.extractfile(member).read()
            hits += [(member.name, n.decode()[:60]) for n in wanted if n in data]
    return hits


async def grade_sealed(run_dir: Path, task: str, diff: str, oracle_dir: Path | None = None):
    """grade_both's shape, with the oracle a parameter: the real gather_evidence and the real
    harness at once, each in its own run dir."""
    from andromeda.outcome.core.types import Evidence
    import andromeda.outcome.grading.ground_truth as ground_truth

    engine_dir, truth_dir = run_dir / "engine", run_dir / "truth"
    engine_dir.mkdir()
    truth_dir.mkdir()
    evidence, truth = await asyncio.gather(
        CodingAdapter(engine_dir, oracle_dir or SEALED_ORACLES / task).gather_evidence(
            Request(task_id=task, text=""), diff),
        asyncio.to_thread(ground_truth.grade, task, diff, truth_dir))
    assert evidence.infra_errors == [], evidence.infra_errors
    assert Evidence.model_validate_json((engine_dir / "checks.json").read_text()) == evidence
    return evidence, truth


def observed_keys(log: Path) -> set[str]:
    return {json.loads(line)["key"] for line in log.read_text().splitlines() if line.strip()}


@pytest.mark.task_image  # grades in a SWE-bench task image
@pytest.mark.parametrize("instance_id", PINNED, ids=["ansible", "c580ebf0", "f91ace96"])
async def test_real_ac10_gold_passes_on_each_sealed_oracle(test_run_dir, monkeypatch, instance_id):
    spied = spy_on_boxes(monkeypatch, test_run_dir / "spy") if instance_id == C580EBF0 else []
    diff = bad_patch(instance_id, "gold")
    evidence, truth = await grade_sealed(test_run_dir, instance_id, diff)
    verdict, reason = decide(evidence)
    assert (verdict, truth) == ("PASS", "resolved"), reason
    assert reason == GOLD_REASONS[instance_id]
    assert evidence.sealed and json.loads((test_run_dir / "engine" / "seal.json").read_text())["leaked"] == []
    # AC9's other half: every expected key observed on the after side, so outcome_seal
    # imported under this task's runner (qutebrowser's pytest, ansible-test units)
    exp = load_expectations(SEALED_ORACLES / instance_id)
    logs = test_run_dir / "engine" / "checks_logs"
    assert {k for keys in exp.values() for k in keys} <= observed_keys(logs / "after_observations.jsonl")
    assert_canaries_held(evidence, test_run_dir)
    if instance_id == C580EBF0:
        # invariant 1, for real: no strong expected value in either side's box. A needle the
        # diff itself carries is the diff's, not the engine's, and only the after side has one.
        wanted = needles(exp)
        assert len(spied) >= 2, spied  # before, after, and any of spec 13's hunk runs
        assert found_in(spied[0], wanted) == []
        for box in spied[1:]:  # each ran gold.diff, or gold.diff less one hunk
            assert found_in(box, {n for n in wanted if n.decode() not in diff}) == []
        # the positive control: the search finds a needle where one is -- the after side's own
        # observations, which gold's code produced in the box
        assert found_in(spied[1], wanted, observations=True)


@pytest.mark.task_image  # grades in a SWE-bench task image
@pytest.mark.parametrize("patch", ["empty", "half"])
async def test_real_ac11_empty_and_half_fail_on_the_sealed_oracle(test_run_dir, patch):
    evidence, truth = await grade_sealed(test_run_dir, C580EBF0, bad_patch(C580EBF0, patch))
    verdict, reason = decide(evidence)
    assert verdict == "FAIL", reason
    assert evidence.sealed and truth == "unresolved"


@pytest.mark.task_image  # grades in a SWE-bench task image
async def test_real_ac11_the_force_pass_grades_fail_on_the_sealed_oracle(test_run_dir, bypassed):
    """17's force-pass with 17's rule and 18's canaries both bypassed: PASS on the reported
    oracle (the hole, as 17 and 18 reproduced it), FAIL on the sealed one with empty.diff's
    hidden checks -- it rewrote every report and moved no sealed check."""
    force = gate_fixture(C580EBF0, "force-pass.diff")
    runs = {name: test_run_dir / name for name in ("reported", "sealed", "empty")}
    for d in runs.values():
        d.mkdir()
    reported, truth = await grade_sealed(runs["reported"], C580EBF0, force, FIXTURES / "oracles" / C580EBF0)
    assert decide(reported)[0] == "PASS" and not reported.sealed
    assert truth == "resolved"  # the harness residual: "Settled" names it
    sealed_ev, _ = await grade_sealed(runs["sealed"], C580EBF0, force)
    empty_ev, _ = await grade_sealed(runs["empty"], C580EBF0, bad_patch(C580EBF0, "empty"))
    verdict, reason = decide(sealed_ev)
    assert verdict == "FAIL", reason
    hidden = lambda ev: [t for t in triples(ev) if t[0] in {c.name for c in ev.checks if c.kind == "hidden"}]
    assert hidden(sealed_ev) and hidden(sealed_ev) == hidden(empty_ev)
