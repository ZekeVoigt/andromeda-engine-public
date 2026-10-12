"""ChecksAdapter (spec 16): a `checks` task's evidence -- the operator's sealed commands, run on
the base tree and on the base plus the judged diff.

Each side is a fresh `task_sandbox` of the `on` instance's pinned image, reset to its base
commit as the hidden-test runner's is -- or, for a folder task (spec 21), of `FOLDER_IMAGE`,
with its sealed `inputs/` laid down as /app first (`lay_base`); `bundle.box` says which. The after side `git apply`s the diff; one that does not
apply is an `InfraError`, so the verdict is UNKNOWN. `files/` goes to /outcome_checks/, never
into /app. /app is snapshotted once the side is set up and restored before every check, so no
check sees what an earlier one wrote. Checks run one at a time, in bundle order, as
`bash -c <run>` with cwd /app, the instance's harness ENV, and their own timeout.

A `change` check is a `hidden` Check and a `keep` check an `existing` one, so `decide()`
reads them unchanged: a `change` check counts only when it failed on base, and a `keep` check
that breaks is a regression. Spec 13's `mark_obsoleted` and hunk ablation do not run here --
they reason about repo test files, and an operator's `keep` check that breaks is a regression,
full stop. Nothing here imports `grading/`.

Spec 20: a `change` check with an `expected_stdout` is sealed. That value never enters a box
(neither `checks.json` nor the value is copied in, put in a script, an env or an argument), and
the host judges the check from the stdout `docker exec` hands back: `_sealed_status`. The exit
status and stderr are logged and read by nothing but the sandbox's own timeout note. A bundle
with no sealed check is graded, and logged, byte for byte as in spec 16.

`task_sandbox` is bound by name, as the coding adapter binds it, so a test patches it on this
module; `load_safe` is read off `task_mod` at call time (in `bundle.pinned`) for the same
reason.
"""

import shlex
import tempfile
from pathlib import Path

from andromeda.outcome.adapters.checks import bundle as bundle_mod
from andromeda.outcome.adapters.coding.docker import (
    InfraError, lay_base, reset_to_base, task_sandbox)
from andromeda.outcome.adapters.coding.task import SafeTask
from andromeda.outcome.core.types import Check, Evidence, Request, Status
from andromeda.runtime.sandbox import TIMEOUT_EXIT_CODES

CHECKS_DIR = "/outcome_checks"
PATCH = "/workspace/patch.diff"
SNAPSHOT = "/workspace/outcome_app.tar"
SETUP_TIMEOUT_S = 900
# a snapshot restore: /app emptied, then its set-up tree laid back byte for byte. Not
# `git reset/clean`: -x would wipe the image's build artifacts, and without it an ignored
# file a check wrote would survive into the next check.
RESTORE = f"find /app -mindepth 1 -delete && tar -C /app --no-same-owner -xf {SNAPSHOT}"


def _status(code: int, err: str, timeout_s: int) -> Status:
    """exit 0 -> pass; the sandbox's own timeout -> error; any other exit -> fail. A check
    that exits 124 or 137 by itself is a fail: only the note `exec()` appends when ITS
    `timeout -s KILL` fired makes an error."""
    if code == 0:
        return "pass"
    if code in TIMEOUT_EXIT_CODES and err.rstrip().endswith(f"[timed out after {timeout_s}s]"):
        return "error"
    return "fail"


def _sealed_status(code: int, out: str, err: str, expected: str, timeout_s: int) -> Status:
    """Spec 20: the sandbox's own timeout -> error (`_status`'s test); else pass exactly when
    stdout, less one trailing newline, is `expected`; else fail. The exit status decides nothing."""
    if code in TIMEOUT_EXIT_CODES and err.rstrip().endswith(f"[timed out after {timeout_s}s]"):
        return "error"
    return "pass" if out.removesuffix("\n") == expected else "fail"


class ChecksAdapter:
    def __init__(self, run_dir: Path, acceptance_dir: Path, ref: str):
        self.run_dir = Path(run_dir)
        self.acceptance_dir = Path(acceptance_dir)
        self.ref = ref

    async def _side(self, bundle: bundle_mod.Bundle, env: SafeTask, exports: str,
                    diff: str | None, label: str, log_dir: Path) -> dict[str, Status]:
        """One side's statuses, by check name, in bundle order, in a box of `env`."""
        statuses: dict[str, Status] = {}
        any_sealed = any(spec.expected_stdout is not None for spec in bundle.checks)
        async with task_sandbox(env.image, run_id=self.run_dir.name) as c:
            await lay_base(c, env)
            await reset_to_base(c, env.base_commit)
            await c.exec(f"mkdir -p /workspace {CHECKS_DIR}", timeout=120, workdir="/")
            # an empty diff changes nothing: `after` is `before`, as the coding runner has it
            if diff is not None and diff.strip():
                with tempfile.TemporaryDirectory() as tmp:
                    patch = Path(tmp) / "patch.diff"
                    patch.write_text(diff)
                    await c.copy_in(patch, PATCH)
                code, _, err = await c.exec(f"git apply -v {PATCH}", timeout=600, check=False)
                if code != 0:
                    raise InfraError(f"{label}: the diff does not apply: {err.strip()[-800:]}")
            files = self.acceptance_dir / bundle_mod.FILES
            if files.is_dir():
                await c.copy_in(f"{files}/.", f"{CHECKS_DIR}/")
            await c.exec(f"tar -C /app -cf {SNAPSHOT} .", timeout=SETUP_TIMEOUT_S, workdir="/")
            for spec in bundle.checks:
                await c.exec(RESTORE, timeout=SETUP_TIMEOUT_S, workdir="/")
                # stdin is /dev/null, not the rest of `exec()`'s `bash -s` script
                script = f"{exports}\nbash -c {shlex.quote(spec.run)} </dev/null\n"
                code, out, err = await c.exec(script, timeout=spec.timeout_s, workdir="/app",
                                              check=False)
                if spec.expected_stdout is None:
                    statuses[spec.name] = _status(code, err, spec.timeout_s)
                else:
                    statuses[spec.name] = _sealed_status(code, out, err, spec.expected_stdout, spec.timeout_s)
                # spec 20's line only in a bundle with a sealed check: 16's logs stay byte-equal
                sealed = f"sealed: {'no' if spec.expected_stdout is None else 'yes'}\n" if any_sealed else ""
                (log_dir / f"{label}_{spec.name}.log").write_text(
                    f"$ {spec.run}\nexit: {code}\nstatus: {statuses[spec.name]}\n{sealed}"
                    f"--- stdout ---\n{out}\n--- stderr ---\n{err}\n")
        return statuses

    async def gather_evidence(self, request: Request, artifact: str) -> Evidence:
        """The coding adapter's contract: any InfraError or unexpected exception lands in
        `infra_errors`, `checks.json` is written either way, and a CancelledError propagates.
        A sealed bundle that no longer hashes to `ref` is one infra error, and no sandbox."""
        evidence = Evidence(artifacts={"agent.diff": str(self.run_dir / "agent.diff"),
                                       "oracle": str(self.acceptance_dir)})
        log_dir = self.run_dir / "checks_logs"
        try:
            bundle = bundle_mod.unseal(self.acceptance_dir, self.ref)
            # an instance's still digest-pinned, as it was at `new`; a folder task's base
            # hashed from the inputs `unseal` just verified
            _, _, exports, env = bundle_mod.box(bundle, self.acceptance_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            evidence.artifacts["checks_logs"] = str(log_dir)
            before = await self._side(bundle, env, exports, None, "before", log_dir)
            after = await self._side(bundle, env, exports, artifact, "after", log_dir)
            evidence.checks = [
                Check(name=spec.name, kind="hidden" if spec.expect == "change" else "existing",
                      before=before[spec.name], after=after[spec.name])
                for spec in bundle.checks]
            evidence.sealed = all(spec.expected_stdout is not None
                                  for spec in bundle.checks if spec.expect == "change")
        except InfraError as e:
            evidence.infra_errors.append(str(e))
        except Exception as e:  # still write checks.json; any failure here is UNKNOWN
            evidence.infra_errors.append(f"{type(e).__name__}: {e}")
        (self.run_dir / "checks.json").write_text(evidence.model_dump_json(indent=2))
        return evidence
