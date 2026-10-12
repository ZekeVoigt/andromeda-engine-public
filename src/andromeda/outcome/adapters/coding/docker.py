"""Task-image operations shared by the graders. All run inside a Sandbox."""

import hashlib
import os
from pathlib import Path

from andromeda.outcome.adapters.coding.task import FolderTask
from andromeda.runtime.sandbox import PLATFORM, InfraError, Sandbox

# Re-exported so runner.py, adapter.py, test_writer.py and
# tests/test_evidence_logic.py keep importing InfraError from here. core/ must not import
# from adapters/ or grading/, and runtime/ is neither, so the single definition lives there.
__all__ = [
    "PLATFORM", "PROJECT_ROOT", "CLAUDE_VERSION", "InfraError", "Sandbox",
    "task_sandbox", "swepro_dir", "require_swepro", "timeout_from_env", "reset_to_base",
    "isolate_repo", "folder_commit", "lay_base", "claude_version", "BOOTSTRAP_COMMAND",
]

PROJECT_ROOT = Path(__file__).resolve().parents[5]
# The Claude Code release a recorded oracle was drawn with; an oracle's manifest names it.
CLAUDE_VERSION = "2.1.274"


def swepro_dir() -> Path:
    return Path(os.environ.get("OUTCOME_SWEPRO_DIR", PROJECT_ROOT / "vendor" / "SWE-bench_Pro-os"))


# The command every refusal in this repo names. `outcome` is the console script declared in
# pyproject; `python -m andromeda.outcome.bootstrap` still works and is the fallback for a
# checkout that has not been installed.
BOOTSTRAP_COMMAND = "outcome bootstrap"


def require_swepro() -> None:
    """Refuse a run on a machine that has never bootstrapped, and say what to do about it.

    `vendor/` is gitignored and 307 MB, so this is the state EVERY fresh clone starts in.
    Without the check `_run_side` dies deep inside a container on a missing
    `run_scripts/<id>/run_script.sh`, tens of minutes and one image pull later.

    `run_scripts`, not just the directory: an empty or half-fetched `vendor/SWE-bench_Pro-os`
    must not pass, because that is exactly what a `mkdir` or an interrupted clone leaves.

    Deliberately does NOT import `bootstrap`: bootstrap imports this module, so the naming
    goes one way only, as a string. The command is in a constant so it cannot drift silently
    away from the module that actually exists.
    """
    if not (swepro_dir() / "run_scripts").is_dir():
        raise InfraError(f"{swepro_dir()} has no run_scripts/: this clone has not been "
                         f"bootstrapped. Run: {BOOTSTRAP_COMMAND}")


def timeout_from_env(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def task_sandbox(image: str, **kw) -> Sandbox:
    """A Sandbox for a SWE-bench Pro task image.

    `--entrypoint /bin/bash` with `-c "sleep infinity"` is not optional here: the task
    images carry their own ENTRYPOINT, which would otherwise swallow the keep-alive
    command. Every engine call site goes through this so no one site can forget it.
    """
    return Sandbox(image, entrypoint="/bin/bash", **kw)


async def reset_to_base(sandbox: Sandbox, base_commit: str) -> None:
    await sandbox.exec(f"git reset -q --hard {base_commit} && git clean -fdq", timeout=900)


async def isolate_repo(sandbox: Sandbox, base_commit: str) -> None:
    """Reset /app to base, then replace its git history with one commit of exactly the base tree.

    The image ships the full upstream history, including the fix commit (whose hash is in the
    instance id), so anything Claude runs in must not be able to `git checkout` the answer.
    """
    await reset_to_base(sandbox, base_commit)
    await sandbox.exec(
        "set -e\n"
        "git ls-files -z > /tmp/outcome_tracked\n"
        "rm -rf .git\n"
        "git init -q && git config user.email outcome@local && git config user.name outcome\n"
        "git add -f --pathspec-from-file=/tmp/outcome_tracked --pathspec-file-nul\n"
        "git commit -qm base --no-verify\n"
        f"! git cat-file -e {base_commit} 2>/dev/null\n",
        timeout=900,
    )


# Spec 21: the one identity, date and message of a folder task's base commit, shared by the
# host's `folder_commit` and the box's `git commit` in `lay_base`, so the two cannot drift.
FOLDER_IDENT = b"outcome <outcome@local> 0 +0000"
FOLDER_MESSAGE = b"inputs\n"
FOLDER_GIT = ("git -c core.autocrlf=false -c core.fileMode=true -c core.hooksPath=/dev/null "
              "-c commit.gpgsign=false -c user.name=outcome -c user.email=outcome@local")


def _git_object(kind: bytes, body: bytes) -> bytes:
    return hashlib.sha1(kind + b" " + str(len(body)).encode() + b"\0" + body).digest()


def _folder_tree(path: Path) -> bytes | None:
    """The raw sha-1 of the git tree holding `path`'s files, or None when it holds none (git
    stores no empty directory). Entries sort as git sorts them: by name bytes, with a
    directory compared as `name/`. A file's mode is 100755 when its owner may execute it --
    git's own `fileMode` rule -- else 100644."""
    entries = []
    for child in path.iterdir():
        name = os.fsencode(child.name)
        if child.is_dir() and not child.is_symlink():
            sub = _folder_tree(child)
            if sub is not None:
                entries.append((name + b"/", b"40000 " + name + b"\0" + sub))
        else:
            mode = b"100755" if child.stat().st_mode & 0o100 else b"100644"
            entries.append((name, mode + b" " + name + b"\0"
                            + _git_object(b"blob", child.read_bytes())))
    if not entries:
        return None
    return _git_object(b"tree", b"".join(entry for _, entry in sorted(entries)))


def folder_commit(inputs: str | Path) -> str:
    """Spec 21: the sha git gives one parentless commit of exactly `inputs`' files, under
    FOLDER_IDENT and FOLDER_MESSAGE. Pure: it reads each file's bytes, relative path and
    executable bit, and nothing else -- no mtime, owner or absolute location, no environment,
    no clock -- so the host knows a folder task's base before any box exists."""
    tree = _folder_tree(Path(inputs)) or _git_object(b"tree", b"")
    body = (b"tree " + tree.hex().encode() + b"\nauthor " + FOLDER_IDENT
            + b"\ncommitter " + FOLDER_IDENT + b"\n\n" + FOLDER_MESSAGE)
    return _git_object(b"commit", body).hex()


async def lay_base(sandbox: Sandbox, task) -> None:
    """Spec 21: materialise a folder task's base as /app, before anything resets or isolates.

    A no-op for every other task -- no exec, no copy -- since a SWE-bench image ships /app.
    For a `FolderTask`: an /app that is not empty is refused; `task.inputs` goes in through
    `copy_in` (spec 14's exec tar, modes kept) and is committed under `folder_commit`'s
    identity, date and message; and the box's HEAD must be `task.base_commit`, or the box is
    never used -- it would be working on a tree the host did not hash.
    """
    if not isinstance(task, FolderTask):
        return
    code, out, _ = await sandbox.exec(
        "mkdir -p /app && ls -A /app", timeout=60, workdir="/", check=False)
    if code != 0 or out.strip():
        raise InfraError(f"task {task.instance_id}: /app is not empty in {task.image}, so its "
                         f"inputs cannot be laid down as the base: {out.strip()[:200]!r}")
    await sandbox.copy_in(f"{task.inputs}/.", "/app")
    date = FOLDER_IDENT.split(b"> ", 1)[1].decode()
    await sandbox.exec(
        "set -e\n"
        f"export GIT_AUTHOR_DATE='@{date}' GIT_COMMITTER_DATE='@{date}'\n"
        f"{FOLDER_GIT} init -q\n"
        f"{FOLDER_GIT} add -A -f\n"
        f"{FOLDER_GIT} commit -q --no-verify -m {FOLDER_MESSAGE.decode().strip()}\n",
        timeout=600, workdir="/app")
    _, head, _ = await sandbox.exec("git rev-parse HEAD", timeout=60, workdir="/app")
    if head.strip() != task.base_commit:
        raise InfraError(f"task {task.instance_id}: the box's base commit is {head.strip()}, "
                         f"not {task.base_commit}, so the inputs it holds are not the sealed "
                         "ones")


def claude_version() -> str:
    """Pinned so runs stay comparable; override with OUTCOME_CLAUDE_VERSION."""
    return os.environ.get("OUTCOME_CLAUDE_VERSION", CLAUDE_VERSION)
