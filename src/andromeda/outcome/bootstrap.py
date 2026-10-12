"""Refill the two things grading a SWE-bench Pro task needs and the repo does not carry.

    outcome bootstrap

fetches the pinned SWE-bench Pro tree into `vendor/` (each task's run script and parser) and
the pinned HuggingFace dataset revision into the HF cache. Both steps are idempotent and print
whether they fetched or found what they needed. A folder task (`"on": "inputs"`) needs neither.

**Not in `runtime/`.** It calls `adapters.coding.docker` and `adapters.coding.task`, and
`docker.py` already imports `runtime.sandbox`; a `runtime/` home would be a circular import.
The matching rule at the other end: `docker.require_swepro()` must never import this module.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from andromeda.outcome.adapters.coding import docker, task
from andromeda.runtime.sandbox import InfraError

SWEPRO_REMOTE = "https://github.com/scaleapi/SWE-bench_Pro-os.git"
# Measured, not chosen by recency: at this commit all 4,586 blobs are byte-identical to the
# tree every committed fixture was recorded against, including
# run_scripts/instance_ansible__ansible-f327e65d.../run_script.sh, whose sha256 is
# c8fd7c51c5cff523aaaca6461149100258b23133daec4f6467e4bd8d5401459b (spec 09 AC3).
SWEPRO_COMMIT = "ca10a60a5fcae51e6948ffe1485d4153d421e6c5"

# The tree is 57 MB over the network; a slow link must not look like a hang forever.
GIT_TIMEOUT_S = 1800


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """One git command. InfraError is the only type that leaves this module."""
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_S)
    except OSError as e:
        raise InfraError(f"git not usable: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise InfraError(f"git {args[0]} timed out after {GIT_TIMEOUT_S}s") from e


def _git_checked(*args: str, cwd: Path | None = None) -> str:
    result = _git(*args, cwd=cwd)
    if result.returncode != 0:
        raise InfraError(f"git {' '.join(args)} failed ({result.returncode}): "
                         f"{(result.stderr or result.stdout).strip()[-800:]}")
    return result.stdout.strip()


def vendor_state() -> str | None:
    """The vendored tree's HEAD SHA, or None if it is absent or not a checkout.

    The `.git` guard is not defensive noise, it is the state this repo is actually in:
    `vendor/SWE-bench_Pro-os` ships as plain files with no `.git` of its own, so a bare
    `git -C <dir> rev-parse HEAD` **walks up to andromeda's own repository** and returns
    andromeda's HEAD. Without the guard a present-but-unversioned tree reads as "a checkout
    at some other commit", and fetch_swepro() would print a refusal naming the wrong reason.
    """
    directory = docker.swepro_dir()
    # .exists(), not .is_dir(): a linked worktree or submodule stores `.git` as a FILE, and
    # that is still a real checkout. Only a missing `.git` triggers the walk-up above.
    if not (directory / ".git").exists():
        return None
    result = _git("rev-parse", "HEAD", cwd=directory)
    return result.stdout.strip() if result.returncode == 0 else None


def fetch_swepro() -> bool:
    """Put the pinned SWE-bench Pro tree at swepro_dir(). True if it fetched, False if present.

    Three states, and the third is a refusal rather than an overwrite: a tree that is not
    the pin may be someone's deliberate local edit, and 57 MB of silent `rm -rf` is not a
    bootstrap's call to make.
    """
    directory = docker.swepro_dir()
    state = vendor_state()
    if state == SWEPRO_COMMIT and (directory / "run_scripts").is_dir():
        return False
    if directory.exists():
        if state == SWEPRO_COMMIT:
            # At the pin, but missing the one directory `require_swepro()` checks for. Round-1
            # gap-check found this falls between the two: bootstrap said "already present" and
            # exited 0 while every run refused and told the user to run bootstrap -- a loop with
            # nothing the user can do. It gets its own message, because "is a checkout at
            # ca10a60a, not ca10a60a" below would be nonsense.
            raise InfraError(
                f"{directory} is a checkout at {SWEPRO_COMMIT} but has no run_scripts/, so a "
                f"run would refuse it. The checkout is damaged: "
                f"rm -rf {directory} && {docker.BOOTSTRAP_COMMAND}")
        found = f"a checkout at {state}" if state else "present but not a git checkout"
        raise InfraError(
            f"{directory} is {found}, not {SWEPRO_COMMIT}. Bootstrap will not overwrite it. "
            f"If it is not work you need: rm -rf {directory} && {docker.BOOTSTRAP_COMMAND}")

    directory.mkdir(parents=True)
    try:
        _git_checked("init", "-q", cwd=directory)
        _git_checked("remote", "add", "origin", SWEPRO_REMOTE, cwd=directory)
        # Fetch by SHA, which GitHub permits, so --depth 1 lands exactly the pin and nothing
        # else. No --recurse-submodules: upstream's SWE-agent and mini-swe-agent stay empty,
        # as they are in the tree every fixture was recorded against, and nothing reads them.
        _git_checked("fetch", "--depth", "1", "origin", SWEPRO_COMMIT, cwd=directory)
        _git_checked("checkout", "-q", "FETCH_HEAD", cwd=directory)
        landed = vendor_state()
        if landed != SWEPRO_COMMIT:
            raise InfraError(f"fetched {SWEPRO_REMOTE} but {directory} is at {landed}, "
                             f"not {SWEPRO_COMMIT}")
    except BaseException:
        # Only ever the directory this call created. Without this a network drop mid-fetch
        # leaves a `.git` at no commit, and the NEXT run hits the refusal above and tells the
        # user to delete a directory bootstrap itself made.
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return True


def main(argv: list[str] | None = None) -> int:
    del argv  # no options: the two steps are the whole contract
    try:
        directory = docker.swepro_dir()
        fetched = fetch_swepro()
        print(f"SWE-bench Pro {SWEPRO_COMMIT[:12]}: "
              f"{'fetched' if fetched else 'already present'} -> {directory}")

        from huggingface_hub.constants import HF_HUB_CACHE

        # Measured layout: the snapshot directory is named by the revision SHA itself, so this
        # answers "was it already in the cache" without downloading to find out.
        cached = (Path(HF_HUB_CACHE) / f"datasets--{task.DATASET.replace('/', '--')}"
                  / "snapshots" / task.DATASET_REVISION).exists()
        try:
            rows = task._dataset()
        except InfraError:
            raise
        except Exception as e:  # load_dataset raises its own types; InfraError is our contract
            raise InfraError(f"cannot load {task.DATASET} at revision "
                             f"{task.DATASET_REVISION}: {e}") from e
        print(f"{task.DATASET}@{task.DATASET_REVISION[:12]}: "
              f"{'already present' if cached else 'downloaded'} -> {len(rows)} rows in the HF cache")
    except InfraError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
