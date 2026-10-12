"""Spec 16: a `checks` task's definition of done -- the operator's bundle, sealed at `task new`.

A bundle is a directory with exactly two entries::

    checks.json    {"on": "<pinned SWE-bench instance id>", "checks": [CheckSpec, ...]}
    files/         optional helper files, copied to /outcome_checks/ in the check container

or, since spec 21, a folder task's three: `"on": "inputs"`, `files/` as above, and
`inputs/`, the job's material, laid down as /app in `FOLDER_IMAGE` at a base commit that is
a pure function of it (`docker.folder_commit`), so the worker's result is a diff against it.

`seal()` validates it and copies it byte for byte to `tasks/<tid>/acceptance/`, and the task
stores only `ref`, the digest below. `unseal()` re-hashes the sealed copy against `ref` before
anything reads it, so the definition of done cannot move under a task: a changed, added or
removed file is `AcceptanceDrift`, never a quietly different verdict.

`ref` is `"sha256:" + sha256(manifest)`, where the manifest is one `"<posix relpath>\\0<sha256
of the file>\\n"` line per file, sorted by relpath -- and for a file under `inputs/` that its
owner may execute, `<sha256>+x`: the executable bit is part of a folder task's base commit, so
a `chmod` after sealing is drift too. It depends on the bundle's bytes and paths,
never on mtimes or on where the source directory was. The manifest itself is kept beside the
sealed copy (`acceptance.manifest`), only so a drift can name the path that moved; `ref`, in
`task.json`, is what decides.

Spec 20: a `change` check may carry an `expected_stdout`. It is held on the host, never copied
into a box, and the adapter judges the check from the stdout `docker exec` hands back. `seal()`
refuses one that could not seal anything: on a `keep` check (its value is base behaviour, which
every diff holds), empty, oversized, weak, or leaked into a `run`, a `name` or a file under
`files/` (spec 19's lint, at seal time only: `ref` fixes the bytes after).

`load_safe` is read off `task_mod` at call time, never bound by name, so a test can fake it.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from andromeda.outcome.adapters.coding import task as task_mod
from andromeda.outcome.adapters.coding.docker import folder_commit
from andromeda.outcome.adapters.coding.runner import harness_env_exports
from andromeda.outcome.adapters.coding.seal import lint, strength
from andromeda.outcome.adapters.coding.task import FolderTask, SafeTask
from andromeda.runtime.sandbox import InfraError

__all__ = ["AcceptanceDrift", "Bundle", "CheckSpec", "CHECKS_JSON", "FILES", "INPUTS",
           "INPUTS_ON", "MAX_BYTES", "MAX_EXPECTED_BYTES", "box", "environment", "pinned",
           "seal", "unseal"]

CHECKS_JSON = "checks.json"
FILES = "files"
INPUTS = INPUTS_ON = "inputs"    # spec 21: the dir, and the `on` that asks for it
MAX_BYTES = 10 * 1024 * 1024
MAX_EXPECTED_BYTES = 64 * 1024  # spec 20: one sealed check's expected_stdout, utf-8
MANIFEST_SUFFIX = ".manifest"


class AcceptanceDrift(InfraError):
    """The sealed bundle no longer hashes to the task's `ref`."""


# strict: a sealed definition of done takes its types exactly -- "300" or true is not a timeout
class CheckSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    expect: Literal["change", "keep"]
    run: str = Field(min_length=1)   # one bash command line, run with cwd /app
    timeout_s: int = Field(default=300, ge=1, le=1800)
    expected_stdout: str | None = None  # spec 20: judged on the host; never enters a box


class Bundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    on: str                          # a SWE-bench instance id, pinned in IMAGE_DIGESTS,
                                     # or INPUTS_ON: a folder task (spec 21)
    checks: list[CheckSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _a_change_check_and_unique_names(self) -> "Bundle":
        if not any(c.expect == "change" for c in self.checks):
            raise ValueError("no check has expect='change', so nothing could prove the change")
        seen, dup = set(), []
        for c in self.checks:
            if c.name in seen:
                dup.append(c.name)
            seen.add(c.name)
        if dup:
            raise ValueError(f"check names repeat: {', '.join(sorted(set(dup)))}")
        return self


def _files(root: Path) -> list[tuple[str, Path]]:
    """Every file under `root`, and every symlink -- to a file or a dir, never followed -- as
    (posix relpath, path), sorted by relpath. `seal()` refuses a symlink, so one here was
    added after sealing, and must count as drift even when it points at a directory."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*filenames, *(d for d in dirnames if (Path(dirpath) / d).is_symlink())]:
            path = Path(dirpath) / name
            out.append((path.relative_to(root).as_posix(), path))
    return sorted(out)


def _sha256(path: Path) -> str:
    if path.is_symlink():
        return hashlib.sha256(b"symlink\0" + os.fsencode(os.readlink(path))).hexdigest()
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _digest(rel: str, path: Path) -> str:
    """A manifest line's digest: the file's sha256, and for an `inputs/` file its owner may
    execute, `+x` after it (git's `fileMode` rule, as `folder_commit` reads it). Every other
    line is spec 16's, so a bundle with no `inputs/` keeps its `ref` byte for byte."""
    digest = _sha256(path)
    if (rel.startswith(INPUTS + "/") and not path.is_symlink()
            and path.stat().st_mode & 0o100):
        return digest + "+x"
    return digest


def _manifest(root: Path) -> dict[str, str]:
    return {rel: _digest(rel, path) for rel, path in _files(root)}


def _encode(manifest: dict[str, str]) -> bytes:
    return b"".join(os.fsencode(rel) + b"\0" + digest.encode() + b"\n"
                    for rel, digest in sorted(manifest.items()))


def _ref(manifest: dict[str, str]) -> str:
    return "sha256:" + hashlib.sha256(_encode(manifest)).hexdigest()


def _layout(src: Path) -> None:
    """Exactly `checks.json`, an optional `files/` dir and -- read against `on` once the bundle
    parses (`_inputs`) -- an `inputs/` dir; no symlink and nothing but regular files and dirs
    anywhere; at most MAX_BYTES in all, `inputs/` included. An unreadable dir is an
    InfraError."""
    try:
        _walk_layout(src)
    except OSError as e:
        raise InfraError(f"--checks {src}: cannot read the bundle: {e}") from e


def _walk_layout(src: Path) -> None:
    if src.is_symlink() or not src.is_dir():
        raise InfraError(f"--checks {src}: not a directory")
    stray = sorted(p.name for p in src.iterdir() if p.name not in (CHECKS_JSON, FILES, INPUTS))
    if stray:
        raise InfraError(f"--checks {src}: stray top-level entry {', '.join(stray)} "
                         f"(a bundle holds only {CHECKS_JSON} and {FILES}/)")
    for sub in (FILES, INPUTS):
        if (src / sub).exists() and ((src / sub).is_symlink() or not (src / sub).is_dir()):
            raise InfraError(f"--checks {src}: {sub} must be a directory, not a symlink or file")
    total = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath) / name
            rel = path.relative_to(src).as_posix()
            if path.is_symlink():
                raise InfraError(f"--checks {src}: {rel} is a symlink; a bundle holds none")
            if name in filenames and not path.is_file():
                raise InfraError(f"--checks {src}: {rel} is not a regular file")
            if name in filenames:
                total += path.stat().st_size
    if total > MAX_BYTES:
        raise InfraError(f"--checks {src}: {total} bytes, over the {MAX_BYTES}-byte (10 MiB) "
                         "limit")


def _inputs(src: Path, bundle: Bundle) -> None:
    """Spec 21: `inputs/` exactly when `on` is INPUTS_ON, holding at least one file, no empty
    directory (git holds none, so the base would silently lose it) and no path component
    starting `.git` (it would change what the base commit holds, or what `git add -A` sees)."""
    inputs = src / INPUTS
    if bundle.on != INPUTS_ON:
        if inputs.exists():
            raise InfraError(f"--checks {src}: stray top-level entry {INPUTS} (a bundle holds "
                             f"{INPUTS}/ only when checks.json's 'on' is {INPUTS_ON!r})")
        return
    if not inputs.is_dir():
        raise InfraError(f"--checks {src}: 'on' is {INPUTS_ON!r} but there is no {INPUTS}/ "
                         "directory: a folder task's inputs are its base")
    try:
        found = False
        for dirpath, dirnames, filenames in os.walk(inputs, followlinks=False):
            here = Path(dirpath)
            for name in [*dirnames, *filenames]:
                if name.startswith(".git"):
                    rel = (here / name).relative_to(src).as_posix()
                    raise InfraError(f"--checks {src}: {rel}: a path under {INPUTS}/ may not "
                                     "start with .git, which would change what the base "
                                     "commit holds")
            if not dirnames and not filenames and here != inputs:
                raise InfraError(f"--checks {src}: {here.relative_to(src).as_posix()} is an "
                                 "empty directory; git holds none, so the base would lose it")
            found = found or bool(filenames)
    except OSError as e:
        raise InfraError(f"--checks {src}: cannot read the bundle: {e}") from e
    if not found:
        raise InfraError(f"--checks {src}: {INPUTS}/ holds no file: a folder task's inputs "
                         "are its base")


def _parse(path: Path) -> Bundle:
    try:
        raw = path.read_text()
    except FileNotFoundError:
        raise InfraError(f"{path}: {CHECKS_JSON} is missing") from None
    except (OSError, UnicodeDecodeError) as e:
        raise InfraError(f"{path}: cannot read {CHECKS_JSON}: {e}") from e
    try:
        json.loads(raw)
    except ValueError as e:
        raise InfraError(f"{path}: {CHECKS_JSON} is not JSON: {e}") from None
    try:
        # JSON mode, so strict still reads a JSON object as a model
        return Bundle.model_validate_json(raw)
    except ValidationError as e:
        raise InfraError(f"{path}: invalid {CHECKS_JSON}: {e}") from None


def _sealable(src: Path, bundle: Bundle) -> None:
    """Spec 20: refuse an `expected_stdout` that would seal nothing, naming the check."""
    sealed = [c for c in bundle.checks if c.expected_stdout is not None]
    if not sealed:
        return
    for c in sealed:
        v = c.expected_stdout
        why = ("is on a keep check: a keep check's expected value is base behaviour, which every "
               "diff holds, so it cannot be sealed" if c.expect == "keep"
               else "is empty" if v == ""
               else f"is over {MAX_EXPECTED_BYTES} bytes" if len(v.encode("utf-8", "surrogateescape")) > MAX_EXPECTED_BYTES
               else "is weak (guessable): under 8 bytes as canonical JSON" if strength(v) == "weak"
               else None)
        if why:
            raise InfraError(f"--checks {src}: check {c.name!r}: expected_stdout {why}")
    sources = {f"{CHECKS_JSON}:{i}:{field}": text for i, c in enumerate(bundle.checks)
               for field, text in (("name", c.name), ("run", c.run))}
    try:
        # spec 21: the worker reads inputs/, so a value found there is readable by a box too
        for sub in (FILES, INPUTS):
            for rel, path in _files(src / sub) if (src / sub).is_dir() else []:
                sources[f"{sub}/{rel}"] = path.read_bytes().decode("utf-8", "surrogateescape")
    except OSError as e:
        raise InfraError(f"--checks {src}: cannot read the bundle: {e}") from e
    leaked = lint({CHECKS_JSON: {c.name: c.expected_stdout for c in sealed}}, sources)
    if leaked:
        names = ", ".join(repr(k.split("::", 1)[1]) for k in leaked)
        raise InfraError(f"--checks {src}: check {names}: expected_stdout leaks: it occurs in a "
                         "check's run or name, or in a file under files/ or inputs/, so a box "
                         "could read it")


def pinned(on: str) -> SafeTask:
    """`on`'s SafeTask, refused unless its image is digest-pinned (spec 06's rule: a mutable
    tag could change the environment under a sealed definition of done)."""
    try:
        safe = task_mod.load_safe(on)
    except KeyError:
        raise InfraError(f"checks.json: 'on' names unknown instance {on!r}") from None
    if safe.dockerhub_tag not in task_mod.IMAGE_DIGESTS:
        raise InfraError(f"checks.json: 'on' instance {on!r} is not in IMAGE_DIGESTS, so its "
                         "image is a mutable tag; pin it first (spec 06)")
    return safe


def seal(src: Path, dest: Path) -> str:
    """Validate the bundle at `src`, copy it byte for byte to `dest`, and return its ref.
    Every refusal is an `InfraError` naming the cause; `dest` is not created unless it
    passes, and is removed again if the copy does not match what was validated."""
    src, dest = Path(src), Path(dest)
    _layout(src)
    bundle = _parse(src / CHECKS_JSON)
    _inputs(src, bundle)
    if bundle.on != INPUTS_ON:
        pinned(bundle.on)
    _sealable(src, bundle)
    try:
        want = _manifest(src)
        shutil.copytree(src, dest, symlinks=True)
        got = _manifest(dest)
        if got != want:
            raise InfraError(f"--checks {src} changed while it was being sealed")
        dest.with_name(dest.name + MANIFEST_SUFFIX).write_bytes(_encode(got))
    except (OSError, shutil.Error) as e:
        shutil.rmtree(dest, ignore_errors=True)
        dest.with_name(dest.name + MANIFEST_SUFFIX).unlink(missing_ok=True)
        raise InfraError(f"cannot seal --checks {src} into {dest}: {e}") from e
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        dest.with_name(dest.name + MANIFEST_SUFFIX).unlink(missing_ok=True)
        raise
    return _ref(got)


def _drifted(dest: Path, now: dict[str, str], ref: str) -> str:
    """The first path that differs from what was sealed, when the manifest beside `dest` can
    still be trusted (it hashes to `ref`); else the dir itself."""
    try:
        kept = dest.with_name(dest.name + MANIFEST_SUFFIX).read_bytes()
    except OSError:
        kept = None
    if kept is None or "sha256:" + hashlib.sha256(kept).hexdigest() != ref:
        return f"{dest} no longer hashes to {ref}"
    sealed = {}
    for line in kept.splitlines():
        rel, _, digest = line.partition(b"\0")
        sealed[os.fsdecode(rel)] = digest.decode()
    for rel in sorted(set(sealed) | set(now)):
        if rel not in now:
            return f"{dest / rel} was removed"
        if rel not in sealed:
            return f"{dest / rel} was added"
        if sealed[rel] != now[rel]:
            return f"{dest / rel} changed"
    return f"{dest} no longer hashes to {ref}"


def unseal(dest: Path, ref: str) -> Bundle:
    """The sealed bundle at `dest`, once it re-hashes to `ref`; `AcceptanceDrift` naming the
    changed, added or removed path otherwise."""
    dest = Path(dest)
    if not dest.is_dir():
        raise AcceptanceDrift(f"acceptance drift: {dest} is missing")
    now = _manifest(dest)
    if _ref(now) != ref:
        raise AcceptanceDrift(f"acceptance drift: {_drifted(dest, now, ref)}")
    return _parse(dest / CHECKS_JSON)


def box(bundle: Bundle, dest: Path) -> tuple[str, str, str, SafeTask]:
    """The one place an unsealed bundle at `dest` resolves to where its checks run: (image,
    base commit, harness exports, task). An instance `on` is its pinned SafeTask -- still
    digest-pinned, as it was at `new` -- and its harness ENV; an `inputs` bundle (spec 21) is
    a `FolderTask` in `FOLDER_IMAGE` at `folder_commit(dest/inputs)`, with no exports. Call it
    only on what `unseal` returned, so the inputs hashed are the sealed ones."""
    dest = Path(dest)
    if bundle.on == INPUTS_ON:
        tid = dest.parent.name
        env = FolderTask(instance_id=tid, repo=f"folder/{tid}",
                         base_commit=folder_commit(dest / INPUTS), dockerhub_tag="",
                         repo_language="folder", problem_statement="", requirements="",
                         interface="", inputs=str(dest / INPUTS))
        return env.image, env.base_commit, "", env
    env = pinned(bundle.on)
    return env.image, env.base_commit, harness_env_exports(bundle.on), env


def environment(dest: Path, ref: str, task_id: str, ask: str) -> SafeTask:
    """Where a `checks` task is drawn and judged: its `on` instance's SafeTask -- image, base
    commit, repo -- or, for an `inputs` bundle, its `FolderTask` (spec 21), with the task's own
    id and ask. `request_text()` is then the ask alone, and nothing of `on`'s problem
    statement, requirements or interface reaches the worker. `dest` is the sealed bundle and
    `ref` its `sha256:` reference from `seal()`; a drifted bundle raises `AcceptanceDrift`."""
    *_, env = box(unseal(dest, ref), dest)
    return env.model_copy(update={
        "instance_id": task_id, "problem_statement": ask,
        "requirements": "", "interface": ""})
