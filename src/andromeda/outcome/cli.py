"""`outcome`: grade one diff, and say whether it does what was asked.

    outcome grade INSTANCE --patch FILE --oracle DIR   a SWE-bench Pro task, by a pinned oracle
    outcome check BUNDLE --patch FILE                  a task defined by an operator's checks
    outcome checks templates | add ...                 the starter check templates
    outcome bootstrap                                  fetch SWE-bench Pro + the dataset

Every grading writes `runs/<id>/<ts>/` -- the judged diff, each side's logs, checks.json and
record.json -- and prints one line. `grade` also runs SWE-bench Pro's own harness on the same
diff (`ground_truth`), so every verdict is set beside an independent one.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from andromeda.outcome.core.types import Evidence, OutcomeRecord, Request
from andromeda.runtime import isolation as isolation_mod
from andromeda.runtime.log import RunLog
from andromeda.runtime.sandbox import InfraError, stop_all_sync

RUNS_DIR = Path("runs")


def new_run_dir(task_id: str) -> Path:
    """A fresh `runs/<task_id>/<ts>/`, never one that exists."""
    n = 0
    while True:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir = RUNS_DIR / task_id / (f"{ts}-{n}" if n else ts)
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            return run_dir
        except FileExistsError:
            n += 1


def agreement(verdict: str, ground_truth: str) -> bool | None:
    if verdict == "UNKNOWN" or ground_truth in ("error", "none"):
        return None
    return (verdict == "PASS") == (ground_truth == "resolved")


async def _judge(task_id: str, patch: Path, oracle_source: str, prepare) -> OutcomeRecord:
    """The shared body of `grade` and `check`: refusals above the run dir, then one judged
    diff whose every failure is an UNKNOWN record, never a crash. `prepare(run_dir)` returns
    (SafeTask, adapter, grade_fn or None)."""
    from andromeda.outcome.core.engine import verify

    posture = await isolation_mod.require()
    diff = patch.read_text()
    started = datetime.now(timezone.utc)
    run_dir = new_run_dir(task_id)
    log = RunLog(run_dir)
    log.event("run_started", task_id=task_id, patch_source=str(patch),
              oracle_source=oracle_source, isolation=posture.as_event())
    request = Request(task_id=task_id, text="")
    verdict, reason, evidence, ground_truth = "UNKNOWN", "", Evidence(), "none"
    try:
        task, adapter, grade = prepare(run_dir)
        log.event("task_loaded", image=task.image)
        request = Request(task_id=task_id, text=task.request_text())
        (run_dir / "request.txt").write_text(request.text)
        (run_dir / "agent.diff").write_text(diff)  # the adapters read this name
        verdict, reason, evidence = await verify(request, diff, adapter)
        log.event("verdict", verdict=verdict, reason=reason, checks=len(evidence.checks),
                  infra_errors=len(evidence.infra_errors))
        if grade is not None:
            ground_truth = grade(task_id, diff, run_dir)
            log.event("graded", ground_truth=ground_truth)
    except Exception as e:
        log.event("run_failed", error_type=type(e).__name__, message=str(e))
        verdict, ground_truth = "UNKNOWN", ("none" if ground_truth == "none" else "error")
        reason = f"run failed before a verdict: {type(e).__name__}: {e}"
        evidence.infra_errors.append(reason)
    record = OutcomeRecord(
        request=request, verdict=verdict, reason=reason, evidence=evidence,
        ground_truth=ground_truth, agrees=agreement(verdict, ground_truth),
        started_at=started, finished_at=datetime.now(timezone.utc),
        patch_source=str(patch), oracle_source=oracle_source)
    (run_dir / "record.json").write_text(record.model_dump_json(indent=2))
    log.event("run_finished", verdict=record.verdict, ground_truth=record.ground_truth,
              agrees=record.agrees)
    print(f"{run_dir}\n  {reason}", file=sys.stderr)
    return record


async def grade(instance_id: str, patch: Path, oracle: Path) -> OutcomeRecord:
    """A SWE-bench Pro instance, judged by a pinned oracle and graded by the benchmark's own
    harness beside it."""
    from andromeda.outcome.adapters.coding import task as task_mod
    from andromeda.outcome.adapters.coding.adapter import CodingAdapter
    from andromeda.outcome.adapters.coding.docker import require_swepro
    from andromeda.outcome.grading.ground_truth import grade as ground_truth

    require_swepro()
    if not oracle.is_dir():
        raise InfraError(f"--oracle {oracle} is not a directory")

    def prepare(run_dir: Path):
        return task_mod.load_safe(instance_id), CodingAdapter(run_dir, oracle), ground_truth

    return await _judge(instance_id, patch, str(oracle), prepare)


async def check(bundle_dir: Path, patch: Path, ask: str | None) -> OutcomeRecord:
    """A task whose definition of done is a bundle of checks: sealed into the run dir first,
    so what judged the diff is byte-equal to what was given, and judged before and after."""
    from andromeda.outcome.adapters.checks import bundle as bundle_mod
    from andromeda.outcome.adapters.checks.adapter import ChecksAdapter
    from andromeda.outcome.adapters.coding.docker import require_swepro

    if not (bundle_dir / "checks.json").is_file():
        raise InfraError(f"{bundle_dir} has no checks.json")
    if bundle_mod._parse(bundle_dir / "checks.json").on != "inputs":
        require_swepro()  # an instance's environment comes from the dataset
    # a bundle kept as <task>/checks/ beside <task>/ask.txt is named for its task
    here = bundle_dir.resolve()
    task_id = here.parent.name if here.name == "checks" else here.name
    if ask is None:
        ask_file = bundle_dir.parent / "ask.txt"
        ask = ask_file.read_text().strip() if ask_file.is_file() else ""

    def prepare(run_dir: Path):
        dest = run_dir / "acceptance"
        ref = bundle_mod.seal(bundle_dir, dest)
        return (bundle_mod.environment(dest, ref, task_id, ask),
                ChecksAdapter(run_dir, dest, ref), None)

    return await _judge(task_id, patch, str(bundle_dir), prepare)


def summary_line(record: OutcomeRecord) -> str:
    agrees = "n/a" if record.agrees is None else str(record.agrees).lower()
    return (f"{record.request.task_id}  engine={record.verdict}  "
            f"truth={record.ground_truth}  agrees={agrees}")


def _execute(coro) -> int:
    """The engine's single `asyncio.run()`. 0 with the summary line, 1 on a refusal, 130 on
    Ctrl-C; `stop_all_sync()` removes any container a second Ctrl-C left behind."""
    interrupted = False
    try:
        record = asyncio.run(coro)
    except KeyboardInterrupt:
        interrupted = True
    except InfraError as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        stop_all_sync()
    if interrupted:
        print("interrupted: run stopped, sandbox container removed", file=sys.stderr)
        return 130
    print(summary_line(record))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="outcome", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("grade", help="judge a diff on a SWE-bench Pro instance by a pinned oracle, "
                                     "and grade it with the benchmark's own harness beside it")
    g.add_argument("instance_id")
    g.add_argument("--patch", type=Path, required=True, help="the diff to judge")
    g.add_argument("--oracle", type=Path, required=True,
                   help="a pinned oracle dir, e.g. fixtures/sealed-oracles/<instance_id>")
    c = sub.add_parser("check", help="judge a diff by a bundle of checks, before and after")
    c.add_argument("bundle", type=Path, help="a dir holding checks.json (and files/, inputs/)")
    c.add_argument("--patch", type=Path, required=True, help="the diff to judge")
    c.add_argument("--ask", help="what was asked (default: ask.txt beside the bundle)")
    sub.add_parser("checks", help="list the starter check templates, or add one to a bundle",
                   add_help=False)
    sub.add_parser("bootstrap", help="fetch the pinned SWE-bench Pro tree and dataset revision")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["checks"]:
        from andromeda.outcome.adapters.checks import templates

        return templates.main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.command == "bootstrap":
        from andromeda.outcome import bootstrap

        return bootstrap.main([])
    if not args.patch.is_file():
        print(f"--patch {args.patch} is not a file", file=sys.stderr)
        return 1
    if args.command == "grade":
        return _execute(grade(args.instance_id, args.patch, args.oracle))
    return _execute(check(args.bundle, args.patch, args.ask))


if __name__ == "__main__":
    sys.exit(main())
