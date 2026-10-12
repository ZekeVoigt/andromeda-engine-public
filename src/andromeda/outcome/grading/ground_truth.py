"""Official SWE-bench Pro grading. The only module allowed to read GradingData."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from andromeda.outcome.adapters.coding import canary, infra
from andromeda.outcome.adapters.coding.docker import PLATFORM, swepro_dir, timeout_from_env
from andromeda.outcome.adapters.coding.task import (
    GRADER_REPO,
    IMAGE_DIGESTS,
    IMAGE_REPO,
    GradingData,
    SafeTask,
    load,
)

HARNESS_DEPS = ["pandas", "tqdm", "docker"]


def _docker_host() -> str | None:
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    proc = subprocess.run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                          capture_output=True, text=True)
    return proc.stdout.strip() or None


def _pin_image(safe: SafeTask, log, env: dict[str, str]) -> str:
    """Put the pinned digest where the harness will find it, and say who to tell it we are.

    The harness rebuilds its own reference as `<username>/sweap-images:<tag>` and pulls it, so
    the only way to make it run a digest is to pull that digest, retag it under a registry that
    cannot resolve, and hand it that username: its pull fails on DNS and its `except` falls back
    to the local image -- which is the retag. Returns the username for `--dockerhub_username=`.

    `log` is the open ground_truth.log, written to before the `$ ...` command line so a miss is
    the file's first line. `env` is the same env the harness gets, so both talk to one daemon.
    """
    digest = IMAGE_DIGESTS.get(safe.dockerhub_tag)
    if digest is None:
        log.write(f"WARNING: {safe.instance_id} is not in IMAGE_DIGESTS -- grading against the mutable tag\n")
        log.flush()
        return "jefzda"
    ref = f"{IMAGE_REPO}@{digest}"
    # check=True on purpose: a failed pull must reach grade's outer except and become "error".
    # Silently falling back to `:<tag>` here would reintroduce exactly the hole this closes.
    subprocess.run(["docker", "pull", "--platform", PLATFORM, ref], check=True, env=env)
    subprocess.run(["docker", "tag", ref, f"{GRADER_REPO}:{safe.dockerhub_tag}"], check=True, env=env)
    return GRADER_REPO.split("/")[0]


def _with_canaries(grading: GradingData) -> tuple[dict, dict[str, str]]:
    """Spec 18: the raw row with canaries planted beside `selected_test_files_to_run`'s
    directories and appended to it, re-encoded in the row's own list style (the harness
    `eval`s it). No other field changes; with nothing selected, nothing is planted."""
    raw_row = grading.raw_row
    selected = ast.literal_eval(raw_row.get("selected_test_files_to_run", grading.selected_test_files_to_run))
    planted = canary.plant([entry.split("::", 1)[0] for entry in selected])
    if not planted:
        return raw_row, {}
    return {**raw_row, "selected_test_files_to_run": json.dumps([*selected, *sorted(planted)])}, planted


def _tampered(output: Path, planted: dict[str, str]) -> list[str]:
    """The planted checks the harness's own report calls PASSED, by name."""
    tests = json.loads(output.read_text()).get("tests", [])
    return sorted({t["name"] for t in tests if t.get("status") == "PASSED"
                   and str(t.get("name", "")).split("::", 1)[0] in planted})


def grade(task_id: str, diff: str, run_dir: str | Path) -> Literal["resolved", "unresolved", "error"]:
    run_dir = Path(run_dir)
    log = run_dir / "ground_truth.log"
    grader_dir = run_dir / "grader"
    try:
        safe, grading = load(task_id)
        grading: GradingData
        # spec 17: the harness's only input is the patch, so the rule reaches it there
        try:
            split = infra.split_for(safe, diff)
        except (infra.NoInfraRule, infra.Unreadable) as e:
            with log.open("w") as f:
                f.write(f"[outcome grader refused] {type(e).__name__}: {e}\n")
            return "error"
        grader_dir.mkdir(parents=True, exist_ok=False)
        # spec 18: canaries reach the harness through its two inputs, as 17's rule does
        row, planted = _with_canaries(grading)
        (grader_dir / "canaries.json").write_text(json.dumps(sorted(planted), indent=2) + "\n")
        samples = grader_dir / "raw_sample.jsonl"
        samples.write_text(json.dumps(row) + "\n")
        patches = grader_dir / "patches.json"
        patches.write_text(json.dumps([{"instance_id": task_id, "patch": split.graded + canary.as_diff(planted),
                                        "prefix": ""}]))
        if split.paths:
            (grader_dir / "ignored.diff").write_text(split.ignored)
        out_dir = grader_dir / "out"
        env = {**os.environ}
        if host := _docker_host():
            env["DOCKER_HOST"] = host
        with log.open("w") as f:
            if split.paths:
                f.write(f"ignored as test infrastructure: {', '.join(split.paths)}\n")
                f.flush()
            username = _pin_image(safe, f, env)
            cmd = [
                "uv", "run", "--no-project", *[a for d in HARNESS_DEPS for a in ("--with", d)],
                "python", "swe_bench_pro_eval.py",
                f"--raw_sample_path={samples.resolve()}", f"--patch_path={patches.resolve()}",
                f"--output_dir={out_dir.resolve()}", "--scripts_dir=run_scripts",
                f"--dockerhub_username={username}",
                "--use_local_docker", "--num_workers=1", f"--docker_platform={PLATFORM}",
                # spec 14: the graded diff's code runs in these containers, and they need none
                "--block_network",
            ]
            f.write("$ " + " ".join(cmd) + "\n")
            f.flush()
            proc = subprocess.run(cmd, cwd=swepro_dir(), stdout=f, stderr=subprocess.STDOUT, env=env,
                                  timeout=timeout_from_env("OUTCOME_GRADER_TIMEOUT", 3600))
        with log.open("a") as f:
            f.write(f"\n[grader exit {proc.returncode}]\n")
        results_path = out_dir / "eval_results.json"
        # the harness records False when the container never produced output; that is an error, not unresolved
        if proc.returncode != 0 or not results_path.exists() or not (out_dir / task_id / "_output.json").exists():
            return "error"
        result = json.loads(results_path.read_text()).get(task_id)
        if result is None:
            return "error"
        # spec 18: a planted check that must fail, reported PASSED, vetoes the harness's verdict
        if tampered := _tampered(out_dir / task_id / "_output.json", planted):
            with log.open("a") as f:
                f.write(f"[outcome grader] tampered: {', '.join(tampered)}\n")
            return "unresolved"
        return "resolved" if result else "unresolved"
    except Exception as e:
        with log.open("a") as f:
            f.write(f"\n[outcome grader error] {type(e).__name__}: {e}\n")
        return "error"


if __name__ == "__main__":
    instance_id, diff_path, run_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    run_dir.mkdir(parents=True, exist_ok=True)
    print(grade(instance_id, diff_path.read_text(), run_dir))
