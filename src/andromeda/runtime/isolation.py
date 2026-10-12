"""The host's isolation posture (spec 14): which runtime every container gets, and whether an
agent role's egress is enforced or only advisory.

Spec 07's threat model is a mistaken agent, because a shared host kernel backs nothing more.
An **isolated host** is a Linux Docker host whose daemon has gVisor's `runsc` registered as its
default runtime. On one, every container the engine starts names `runsc`, and so does every
container the vendored grader starts, because the grader names no runtime and gets the
default. There, the engine holds against a deliberate adversary. `scripts/isolate-host` is what
makes a host isolated. A Linux VM with runsc installed is one. A Mac is not, and cannot be: its task
images run amd64 by emulation, which `runsc` does not implement.

`OUTCOME_ISOLATION` picks the mode:

    mode       host has runsc as its default         host without
    auto       runsc, enforced                       runc, advisory
    required   runsc, enforced                       refused, naming SETUP_COMMAND
    off        runc, advisory (the grader keeps      runc, advisory
               the daemon's default, recorded)

The posture comes from one `docker info` per process and is cached. It is per process, not per
run, and that is correct: the mode is read when the posture is resolved, and a swarm's draws
share one process. Two coroutines that resolve it at once may both query, but the first result
is the one kept, so a process never holds two postures.

Stdlib plus `runtime.sandbox` (`InfraError`, `_docker_checked`, `_explain`) only, and nothing
from `andromeda.outcome`. This is infrastructure next to `sandbox.py`, the rule `baseline.py`,
`broker.py` and `egress.py` keep. `sandbox.py` imports this module inside `start()`, not at
the top, because this module imports it.
"""

import json
import os
from dataclasses import asdict, dataclass

from andromeda.runtime.sandbox import InfraError, _docker_checked, _explain

__all__ = ["ISOLATED_RUNTIME", "MODES", "MODE_ENV", "SETUP_COMMAND", "Posture", "current",
           "mode", "posture", "require", "resolve"]

ISOLATED_RUNTIME = "runsc"
# The runtime every daemon ships, and the only one the Mac has.
SHARED_KERNEL_RUNTIME = "runc"
MODE_ENV = "OUTCOME_ISOLATION"
MODES = ("auto", "required", "off")
SETUP_COMMAND = "scripts/isolate-host"  # the one string every refusal names
INFO_TIMEOUT_S = 60


@dataclass(frozen=True)
class Posture:
    mode: str             # auto | required | off, as resolved
    runtime: str          # what Sandbox passes to --runtime: "runsc" or "runc"
    default_runtime: str  # the daemon's, i.e. what the grader's containers get
    egress: str           # "enforced" | "advisory"

    @property
    def isolated(self) -> bool:
        return (self.runtime == self.default_runtime == ISOLATED_RUNTIME
                and self.egress == "enforced")

    def as_event(self) -> dict[str, str]:
        """`run_started`'s `isolation` field."""
        return asdict(self)


def mode() -> str:
    """`OUTCOME_ISOLATION`, read now. An unknown value is a ValueError, not an InfraError: it
    is the operator's typo, not a broken host."""
    value = os.environ.get(MODE_ENV, "").strip() or "auto"
    if value not in MODES:
        raise ValueError(f"{MODE_ENV}={value!r} is not one of {', '.join(MODES)}")
    return value


def resolve(mode: str, default_runtime: str) -> Posture:
    """The mode table, pure. `required` on a host without `runsc` resolves to what that host
    can do (`runc`, advisory). The refusal is `require()`'s and `Sandbox.start()`'s, which
    keeps this function total."""
    if mode not in MODES:
        raise ValueError(f"{MODE_ENV}={mode!r} is not one of {', '.join(MODES)}")
    if mode != "off" and default_runtime == ISOLATED_RUNTIME:
        return Posture(mode, ISOLATED_RUNTIME, default_runtime, "enforced")
    return Posture(mode, SHARED_KERNEL_RUNTIME, default_runtime, "advisory")


def refusal(p: Posture) -> str:
    """One line, naming the fix."""
    return (f"{MODE_ENV}=required, but this host is not isolated: the daemon's default "
            f"runtime is {p.default_runtime!r}, not {ISOLATED_RUNTIME!r}. "
            f"Run {SETUP_COMMAND} on it")


_POSTURE: Posture | None = None


def current() -> Posture | None:
    """The cached posture, or None before the first `posture()`. For sync readers."""
    return _POSTURE


def _reset() -> None:
    """Tests only: forget the cached posture."""
    global _POSTURE
    _POSTURE = None


async def _default_runtime() -> str:
    code, out, err = await _docker_checked("info", "--format", "{{json .DefaultRuntime}}",
                                           what="info", timeout=INFO_TIMEOUT_S)
    if code != 0:
        raise InfraError(f"docker info failed while resolving the isolation posture: "
                         f"{_explain(out + err)}")
    try:
        value = json.loads(out)
    except ValueError as e:
        raise InfraError(f"docker info gave no default runtime: {out.strip()[:200]!r}") from e
    if not isinstance(value, str) or not value:
        raise InfraError(f"docker info gave no default runtime: {out.strip()[:200]!r}")
    return value


async def posture() -> Posture:
    """Resolved once per process, then cached. The mode is read before the daemon is asked,
    so a typo in `OUTCOME_ISOLATION` costs no docker call. A failure caches nothing."""
    global _POSTURE
    if _POSTURE is None:
        wanted = mode()
        found = resolve(wanted, await _default_runtime())
        # First result wins: a second coroutine that resolved at the same time keeps it.
        _POSTURE = _POSTURE or found
    return _POSTURE


async def require() -> Posture:
    """`posture()`, and under `required` an `InfraError` unless the host is isolated.
    `cli.run()` calls this above `new_run_dir()`, so a refused host leaves no `runs/`."""
    p = await posture()
    if p.mode == "required" and not p.isolated:
        raise InfraError(refusal(p))
    return p
