"""The Docker container everything executes inside. Driven through the docker CLI, stdlib only.

Merged from two implementations: agent-world's async `DockerSandbox` (daemon diagnostics,
labels, in-container kill timeout, `stop_sync`) and the outcome engine's sync `Container`
(platform pin, bare `-e NAME` env flags, per-exec workdir, split streams).

Every permissive option defaults to off: no env reaches the container unless named in
`env_allow`, no host path is mounted unless named in `mounts`, output is never truncated
unless `output_limit` is set, and the container starts with no network, a memory and CPU
cap, every Linux capability dropped, no-new-privileges, and the runtime the host's isolation
posture names (`runtime/isolation.py`).
"""

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

LABEL = "andromeda"
# E10: who owns a container. `reap()` removes an `andromeda=1` container only when BOTH say it
# is ours and dead: the host label is this host (a pid means nothing on another machine's
# daemon) and the pid is not alive. A container with no pid label predates E10 and is skipped.
PID_LABEL = f"{LABEL}-pid"
HOST_LABEL = f"{LABEL}-host"
REAP_LIST_TIMEOUT_S = 60

# Every Sandbox that has reached `docker run`, so a handler with no event loop left can still
# remove the containers. Identity-hashed -- Sandbox defines no __eq__ -- and quoted because the
# class is defined below. start() adds, stop() and stop_sync() discard, stop_all_sync() drains.
_LIVE: "set[Sandbox]" = set()

# `docker run -w` CREATES a missing directory; `docker exec -w` does not (measured: exec into a
# stock python:3.12-slim started without it dies "chdir to cwd (\"/app\") ... no such file or
# directory"). Setting it on `run` is what lets a stock image satisfy exec()'s workdir default.
# No-op on the SWE-bench task images, whose repo already lives at /app.
WORKDIR_DEFAULT = "/app"

# A test suite sets this so its containers are distinguishable from a real run happening at the
# same time in another terminal; unset in normal use. When set, `reap()` collects only what this
# suite left: a parallel suite (one pytest worker per core, each its own SUITE_ENV) must not reap
# a sibling worker's killed-owner container out from under the test that is asserting on it.
SUITE_ENV = "ANDROMEDA_SUITE"

PLATFORM = os.environ.get("OUTCOME_DOCKER_PLATFORM", "linux/amd64")

# On a shared kernel (runc, the Mac) the threat model is spec 07's: a mistaken agent. On an
# isolated host (spec 14: runsc as the daemon's default, `runtime/isolation.py`) it holds
# against a deliberate one. Either way a container gets no network unless its role asks.
NETWORK_DEFAULT = "none"
MEMORY_DEFAULT = "2g"
CPUS_DEFAULT = "2"
# Empty since spec 14 moved copy_in/copy_out off `docker cp`, whose host uid forced DAC_OVERRIDE.
CAP_ADD: tuple[str, ...] = ()

# Spec 14's enforced egress: an agent role gets `--network none`, and each host endpoint it may
# reach is a Unix socket in one per-draw dir under $HOME (Colima mounts nothing else), mounted
# read-only at FORWARD_MOUNT. A forwarder inside the container listens on 127.0.0.1:<port> and
# relays to the socket, so what the container is told (host.docker.internal:<port>) does not
# change. The dir is named `<owner pid>-<hex>`, which is how `reap()` finds a dead owner's.
FORWARD_ROOT = Path.home() / ".andromeda" / "forward"
FORWARD_MOUNT = "/run/andromeda-forward"
FORWARD_START_TIMEOUT_S = 60

# The forwarder, fed to the container's `python3` (an asserted baseline row on every image)
# on stdin. It binds every port first and only then forks away and exits 0, so the exec
# returning 0 means the ports are listening. Threads, not asyncio: images ship python 3.8+.
_FORWARDER = """\
import os, socket, sys, threading
pairs = [(int(sys.argv[i]), sys.argv[i + 1]) for i in range(1, len(sys.argv), 2)]
listeners = []
for port, path in pairs:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(64)
    listeners.append((s, path))
if os.fork():
    os._exit(0)
os.setsid()
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
def pump(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        try:
            b.shutdown(socket.SHUT_WR)
        except OSError:
            pass
def serve(listener, path):
    while True:
        client, _ = listener.accept()
        upstream = socket.socket(socket.AF_UNIX)
        try:
            upstream.connect(path)
        except OSError:
            client.close()
            continue
        for a, b in ((client, upstream), (upstream, client)):
            threading.Thread(target=pump, args=(a, b), daemon=True).start()
threads = [threading.Thread(target=serve, args=pair) for pair in listeners]
for t in threads:
    t.start()
for t in threads:
    t.join()
"""


def new_forward_dir() -> Path:
    """A fresh per-draw socket dir, `<FORWARD_ROOT>/<pid>-<hex>` (`<pid>-<suite>-<hex>` under a
    SUITE_ENV), traversable by the container (0755). Its owner removes it; `reap()` removes a
    dead owner's."""
    FORWARD_ROOT.mkdir(parents=True, exist_ok=True)
    suite = os.environ.get(SUITE_ENV)
    tag = f"{suite}-" if suite else ""
    path = FORWARD_ROOT / f"{os.getpid()}-{tag}{uuid.uuid4().hex[:12]}"
    path.mkdir(mode=0o755)
    path.chmod(0o755)
    return path


TIMEOUT_EXIT_CODES = (124, 137)
# A `timeout -s KILL` and an OOM kill both land here; exec() has to tell them apart.
OOM_EXIT_CODE = 137
# Grace on top of the in-container timeout, in case `docker exec` itself hangs.
EXEC_GRACE_S = 15
COPY_TIMEOUT_S = 600
REMOVE_TIMEOUT_S = 120


def start_timeout_s() -> int:
    """Read per call, like every other OUTCOME_* timeout.

    OUTCOME_DOCKER_PLATFORM above is the one that stays import-time, because PLATFORM is a
    module constant that docker.py re-exports and grading/ground_truth.py imports by name.
    """
    return int(os.environ.get("OUTCOME_CONTAINER_START_TIMEOUT", 1800))


def memory_limit() -> str:
    """Read per call, like start_timeout_s. See Sandbox.memory for why it is not frozen."""
    return os.environ.get("OUTCOME_CONTAINER_MEMORY", MEMORY_DEFAULT)


def cpu_limit() -> str:
    return os.environ.get("OUTCOME_CONTAINER_CPUS", CPUS_DEFAULT)


DAEMON_HINT = "is the Docker daemon running? try: colima start"

# The docker CLI words an unreachable daemon differently per subcommand.
DAEMON_DOWN_MARKERS = ("connect to the docker daemon", "connect to the docker api",
                       "docker daemon is not running", "is the docker daemon running")


class InfraError(RuntimeError):
    """Docker / Claude / harness failure: not a verdict about the work.

    The single failure type for the whole runtime layer. `adapter.gather_evidence`
    catches this by name to route failures into `Evidence.infra_errors` -> UNKNOWN,
    so a second exception type here would silently lose that classification.
    """


def _explain(output: str) -> str:
    text = output.strip()
    if any(m in text.lower() for m in DAEMON_DOWN_MARKERS):
        return f"{text}\n{DAEMON_HINT}"
    return text


async def _docker(*args: str, timeout: float | None = None,
                  stdin: str | None = None) -> tuple[int, str, str]:
    """Run one docker command. stdout and stderr stay separate.

    Writing stdin and reading both pipes happens in a single `communicate()` call:
    reading them in sequence deadlocks as soon as a command writes more than a pipe
    buffer, and the recorded `claude -p` run emits 230KB on stdout.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        # InfraError is the one type callers catch; a bare OSError from a machine with no
        # usable docker CLI would escape every `except InfraError` in the repo. OSError,
        # not FileNotFoundError: a `docker` on PATH that is not executable raises
        # PermissionError, and is exactly as unusable.
        raise InfraError(f"docker CLI not usable: {e}") from e
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout)
    except BaseException:
        # Covers cancellation as well as the timeout: without this an orphaned
        # `docker exec` keeps running after we have stopped waiting for it.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def _docker_checked(*args: str, what: str, timeout: float,
                          stdin: str | None = None) -> tuple[int, str, str]:
    """`_docker`, with the timeout converted to InfraError.

    `exec` and `start` translate their own timeouts because they have more to say about
    them. Everything else routes through here so no docker call can raise a bare
    TimeoutError past a caller that only catches InfraError.
    """
    try:
        return await _docker(*args, timeout=timeout, stdin=stdin)
    except asyncio.TimeoutError as e:
        raise InfraError(f"docker {what} timed out after {timeout}s") from e


async def _docker_io(*args: str, what: str, stdin=None, stdout=None,
                     timeout: float | None = None) -> tuple[int, str, str]:
    """One docker command whose stdin or stdout is a FILE, not a pipe: copy_in / copy_out
    move whole trees, and `_docker` would hold them in memory. Returns (code, stdout, stderr);
    stdout is "" when it went to the file. The timeout is COPY_TIMEOUT_S, read per call."""
    timeout = COPY_TIMEOUT_S if timeout is None else timeout
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdin=stdin if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=stdout if stdout is not None else asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        raise InfraError(f"docker CLI not usable: {e}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as e:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if isinstance(e, asyncio.TimeoutError):
            raise InfraError(f"docker {what} timed out after {timeout}s") from e
        raise
    return (proc.returncode, (out or b"").decode(errors="replace"),
            (err or b"").decode(errors="replace"))


def _root_owned(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def _write_tar(archive, members: list[tuple[Path, str]]) -> None:
    """Each (host path, name in the archive), recursively, every entry 0:0, modes kept."""
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for path, name in members:
            tar.add(path, arcname=name, filter=_root_owned)


def _read_tar(archive, name: str, host: Path) -> None:
    """The one entry `name` (and, for a directory, what is under it) to `host`."""
    with tarfile.open(fileobj=archive, mode="r:") as tar:
        members = tar.getmembers()
        top = next((m for m in members if m.name.rstrip("/") == name), None)
        if top is None:
            raise ValueError(f"the archive holds no {name!r}")
        if top.isfile():
            src = tar.extractfile(top)
            data = src.read() if src is not None else b""
            if len(data) != top.size:
                raise ValueError(f"{name}: read {len(data)} of {top.size} bytes")
            host.write_bytes(data)
            os.chmod(host, top.mode & 0o777)
            return
        if not top.isdir():
            raise ValueError(f"{name} is neither a file nor a directory")
        host.mkdir(parents=True, exist_ok=True)
        prefix = name.rstrip("/") + "/"
        for m in members:
            if m.name.startswith(prefix):
                m.name = m.name[len(prefix):]
                tar.extract(m, host, filter="data")


def _truncate(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


class Sandbox:
    """A long-lived container we exec into; always removed on exit.

    Args:
        image:        image to run.
        platform:     `--platform` value; defaults to PLATFORM (linux/amd64).
        env_allow:    environment variable NAMES to forward. Emitted as bare `-e NAME`
                      so the value travels through the docker CLI's own environment and
                      never appears in argv, where `docker inspect` and `ps` would show it.
        mounts:       {host_path: container_path} bind mounts.
        labels:       extra {key: value} labels, on top of the andromeda ones.
        entrypoint:   `--entrypoint` override. With one set the keep-alive is
                      `-c "sleep infinity"`; without, it is `sleep infinity` as argv.
        output_limit: bytes to keep per stream, or None (the default) for no limit.
                      `git ls-files` on ansible and a stream-json agent transcript both
                      exceed any small cap, and a truncated result is silently wrong.
        run_id:       recorded as a label so a leaked container is traceable to its run.
        network:      `--network` value. Defaults to "none". Only `egress.sandbox_kwargs()`
                      asks for more, and only on a shared-kernel posture ("bridge"); on an
                      isolated host an agent role stays "none" and uses `forward`.
        add_host:     `--add-host` entries, each "NAME:ADDR". `egress.sandbox_kwargs()` passes
                      "host.docker.internal:host-gateway" (bridge: a host listener bound to
                      127.0.0.1) or "host.docker.internal:127.0.0.1" (enforced: the forwarder).
        memory:       `--memory`, and `--memory-swap` set equal to it so swap is not an
                      escape hatch from the cap. None -> memory_limit() at run time.
        cpus:         `--cpus`. None -> cpu_limit() at run time.
        cap_add:      capabilities handed back after `--cap-drop ALL`. Every entry must
                      carry an inline comment naming the gate failure that forced it; a
                      guessed capability is worse than none.
        runtime:      `--runtime`. None -> the isolation posture's, resolved in start().
                      Under OUTCOME_ISOLATION=required anything but runsc is refused there.
        forward:      (port, host socket path) pairs, all in one dir: the enforced egress
                      topology (spec 14). The dir is mounted read-only and start() runs a
                      forwarder from 127.0.0.1:<port> inside to each socket. Set by
                      `egress.sandbox_kwargs()`, never by hand.
    """

    def __init__(self, image: str, *, platform: str | None = None,
                 env_allow: tuple[str, ...] | list[str] = (),
                 mounts: dict[str, str] | None = None,
                 labels: dict[str, str] | None = None,
                 entrypoint: str | None = None,
                 output_limit: int | None = None,
                 run_id: str | None = None,
                 network: str = NETWORK_DEFAULT,
                 add_host: tuple[str, ...] = (),
                 memory: str | None = None,
                 cpus: str | None = None,
                 cap_add: tuple[str, ...] = CAP_ADD,
                 runtime: str | None = None,
                 forward: tuple[tuple[int, str], ...] = ()) -> None:
        self.image = image
        self.platform = platform or PLATFORM
        self.env_allow = tuple(env_allow)
        self.mounts = dict(mounts or {})
        self.labels = dict(labels or {})
        self.entrypoint = entrypoint
        self.output_limit = output_limit
        self.run_id = run_id
        self.network = network
        self.add_host = tuple(add_host)
        # Stored verbatim, resolved in _run_args(): every OUTCOME_* value except PLATFORM is
        # read per call, and freezing these here would ignore an env var set after __init__.
        self.memory = memory
        self.cpus = cpus
        self.cap_add = tuple(cap_add)
        self.runtime = runtime
        self.forward = tuple((int(port), str(path)) for port, path in forward)
        if len({Path(path).parent for _, path in self.forward}) > 1:
            raise InfraError(f"forward sockets must share one dir: {self.forward}")
        self.name = f"outcome-{uuid.uuid4().hex[:12]}"
        self._started = False

    def _memory(self) -> str:
        """The cap that is actually in force, so the OOM message can name it."""
        return self.memory if self.memory is not None else memory_limit()

    def _cpus(self) -> str:
        return self.cpus if self.cpus is not None else cpu_limit()

    def _runtime(self) -> str | None:
        """The explicit runtime, else the cached posture's. None only before any posture is
        resolved, which `start()` never is: then `--runtime` is left out."""
        if self.runtime is not None:
            return self.runtime
        from andromeda.runtime import isolation

        found = isolation.current()
        return found.runtime if found is not None else None

    def _run_args(self) -> list[str]:
        args = ["run", "-d", "--name", self.name, "--platform", self.platform]
        # One contiguous hardening block, so the whole posture reads in one place.
        memory = self._memory()
        args += ["--network", self.network]
        for entry in self.add_host:
            args += ["--add-host", entry]  # network topology: belongs beside --network
        args += ["--memory", memory, "--memory-swap", memory]  # equal: swap is not an escape hatch
        args += ["--cpus", self._cpus()]
        args += ["--cap-drop", "ALL"]
        for cap in self.cap_add:
            args += ["--cap-add", cap]
        args += ["--security-opt", "no-new-privileges"]
        runtime = self._runtime()
        if runtime is not None:
            args += ["--runtime", runtime]  # spec 14: the posture, after the block 07 pins
        args += ["--label", f"{LABEL}=1"]
        # Before self.labels, never after: docker keeps the LAST value of a repeated label, so
        # an explicit `labels={PID_LABEL: ...}` wins -- which is how a test plants a dead owner.
        args += ["--label", f"{PID_LABEL}={os.getpid()}"]
        args += ["--label", f"{HOST_LABEL}={socket.gethostname()}"]
        if self.run_id:
            args += ["--label", f"{LABEL}-run={self.run_id}"]
        suite = os.environ.get(SUITE_ENV)
        if suite:
            args += ["--label", f"{LABEL}-suite={suite}"]
        for key, value in self.labels.items():
            args += ["--label", f"{key}={value}"]
        for host, target in self.mounts.items():
            args += ["-v", f"{Path(host).resolve()}:{target}"]
        if self.forward:
            args += ["-v", f"{Path(self.forward[0][1]).parent.resolve()}:{FORWARD_MOUNT}:ro"]
        for name in self.env_allow:
            if name not in os.environ:
                raise InfraError(f"env_allow names {name!r}, which is not set on the host; "
                                 "`-e NAME` would silently forward nothing")
            args += ["-e", name]  # value comes from the docker CLI's environment, never argv
        args += ["-w", WORKDIR_DEFAULT]
        if self.entrypoint:
            args += ["--entrypoint", self.entrypoint, self.image, "-c", "sleep infinity"]
        else:
            args += [self.image, "sleep", "infinity"]
        return args

    async def start(self) -> "Sandbox":
        from andromeda.runtime import isolation

        timeout = start_timeout_s()
        # Marked started before `docker run` so stop() cleans up even a half-started container,
        # and registered on the same pair of lines so the backstop sees it just as early.
        self._started = True
        _LIVE.add(self)
        # All three failure exits below call stop(), which discards on success. If stop()
        # ITSELF raises -- an unusable docker CLI fails `run` and `rm` alike -- the sandbox
        # stays in _LIVE on purpose: a removal that did not confirm is not a removal, and the
        # registry is the only thing left pointing at a container that may exist. stop_all_sync()
        # drains it, and discards whatever the second attempt does, so the entry is bounded.
        try:
            # Spec 14, invariant 4: every start re-checks `required`, so a container that
            # does not come from cli.run() (a wave merge, bad_patch_check) is covered too.
            found = await isolation.posture()
            runtime = self.runtime or found.runtime
            if found.mode == "required" and runtime != isolation.ISOLATED_RUNTIME:
                # Nothing was created, so there is nothing for stop() to remove.
                self._started = False
                _LIVE.discard(self)
                raise InfraError(isolation.refusal(found) if not found.isolated else
                                 f"{isolation.MODE_ENV}=required refuses a container on "
                                 f"runtime {runtime!r}: only {isolation.ISOLATED_RUNTIME!r}")
            code, out, err = await _docker(*self._run_args(), timeout=timeout)
        except asyncio.TimeoutError as e:
            await self.stop()  # a timed-out start may still have created the container
            raise InfraError(f"container start timed out after {timeout}s for {self.image}") from e
        except BaseException:
            await self.stop()  # a no-op after a refusal, which already unregistered
            raise
        if code != 0:
            await self.stop()  # `run -d` can leave a created-but-not-started container
            raise InfraError(f"container start failed for {self.image}: {_explain((out + err)[-500:])}")
        if self.forward:
            try:
                await self._start_forwarder()
            except BaseException:
                await self.stop()
                raise
        return self

    async def _start_forwarder(self) -> None:
        """Returns once every forwarded port is listening inside the container."""
        args = [a for port, path in self.forward
                for a in (str(port), f"{FORWARD_MOUNT}/{Path(path).name}")]
        code, out, err = await _docker_checked(
            "exec", "-i", "-u", "0", self.name, "python3", "-", *args,
            what="exec (forwarder)", timeout=FORWARD_START_TIMEOUT_S, stdin=_FORWARDER)
        if code != 0:
            raise InfraError(f"the egress forwarder did not start in {self.name} ({code}): "
                             f"{_explain(out + err)[-500:]}")

    async def __aenter__(self) -> "Sandbox":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def exec(self, script: str, *, timeout: int, workdir: str = WORKDIR_DEFAULT,
                   check: bool = True) -> tuple[int, str, str]:
        """Run `script` through `bash -s` on stdin. Returns (code, stdout, stderr).

        The script goes on stdin rather than into argv: callers send multi-line scripts
        with quoting and heredocs that argv would mangle and, past a point, reject.
        """
        t = max(1, int(timeout))
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            code, out, err = await _docker(
                "exec", "-i", "-w", workdir, self.name,
                "timeout", "-s", "KILL", str(t), "bash", "-s",
                timeout=t + EXEC_GRACE_S, stdin=script,
            )
        except asyncio.TimeoutError as e:
            # The in-container kill did not take: `docker exec` itself is wedged. Infra, always.
            raise InfraError(f"docker exec hung past {t + EXEC_GRACE_S}s "
                             f"(in-container timeout {t}s did not fire): {script.strip()[:120]!r}") from e
        elapsed = loop.time() - started
        out, err = _truncate(out, self.output_limit), _truncate(err, self.output_limit)
        if code in TIMEOUT_EXIT_CODES:
            if code == OOM_EXIT_CODE and self._was_oom_killed(elapsed, t):
                err += (f"\n[out of memory: killed at {elapsed:.1f}s, under the "
                        f"{self._memory()} cap, before the {t}s limit]")
            else:
                err += f"\n[timed out after {t}s]"
        elif code >= 128 and await self._sandbox_died_of_oom():
            err += (f"\n[out of memory: the container was killed at {elapsed:.1f}s under the "
                    f"{self._memory()} cap -- on runsc an OOM ends the whole sandbox]")
        if check and code != 0:
            raise InfraError(f"exec failed ({code}): {script.strip()[:120]!r}: {err.strip()[-800:]}")
        return code, out, err

    def _was_oom_killed(self, elapsed: float, t: int) -> bool:
        """Did exit 137 come from the memory cap rather than from `timeout -s KILL`?

        **The wall clock decides, and `.State.OOMKilled` is not consulted at all.** Spec 07
        named that flag as the primary discriminator and named one risk -- that it would not
        flip for an *exec'd* process. Measured, it does flip. It was retired anyway, because
        two later measurements showed it cannot be trusted to mean "this exec":

        1. **It is sticky.** Once anything in the container has been OOM-killed it reads
           `true` for the rest of that container's life, so the next genuine
           `timeout -s KILL` also read as an OOM -- with a message contradicting itself,
           "killed at 3.1s ... before the 3s limit".
        2. **It can already be `true` before the first 137.** A swallowed OOM -- a child
           killed while the exec itself exits 0 -- sets it with no 137 to observe, so
           remembering "has it fired on a previous 137" does not close (1) either.

        Guarding the flag with the clock instead (`... and elapsed < t`) would have left a
        `docker inspect` on every 137 that could no longer change the answer, so it is gone.
        AC6 provides for exactly this: "if the test shows that, use the fallback".

        The fallback is sound on its own terms: a SIGKILL that arrived *before*
        `timeout -s KILL t` could possibly have fired did not come from timeout, so it came
        from the OOM killer. It is also the only branch that needs no daemon, which a
        diagnostic should not -- it must never be able to replace the real error.

        The one case this gives up is an OOM that takes longer than its own exec timeout;
        that is reported as a timeout, which is what the code did before this spec.
        """
        return elapsed < t

    async def _sandbox_died_of_oom(self) -> bool:
        """Spec 14, measured: on `runsc` the memory cap covers the whole sandbox, so an OOM
        kills the container itself and `docker exec` exits 128 ("waiting on pid ...: EOF"),
        never 137 -- the wall clock above never sees it. Here `.State.OOMKilled` IS
        trustworthy, because the container is dead: 07's two objections were about a flag
        read in a container that keeps running. On runc it is never asked. A failed inspect
        says no, because a diagnostic must never replace the real error."""
        if self._runtime() != "runsc":
            return False
        try:
            code, out, _ = await _docker_checked(
                "inspect", "--format", "{{.State.Running}} {{.State.OOMKilled}}", self.name,
                what="inspect", timeout=REAP_LIST_TIMEOUT_S)
        except InfraError:
            return False
        return code == 0 and out.split() == ["false", "true"]

    async def copy_in(self, host_path: str | Path, container_path: str) -> None:
        """Copy a host file or tree in, through `docker exec ... tar -x`, not `docker cp`.

        Spec 14. `docker cp` wrote the HOST uid into the container (07's `/app/test` went
        from 0:1001 to 501:20), which is what CAP_ADD's DAC_OVERRIDE was for, and it writes
        from outside the container, beneath `runsc`'s root-filesystem overlay. The tar is
        built here with every entry 0:0 and its mode kept, and the container's own root
        unpacks it. The forms the repo uses:

            a file   -> `container_path` is the file's full target path
            `dir/.`  -> the tree's CONTENTS land in `container_path` (created if missing)
            a dir    -> the same, into `container_path` itself
        """
        src = str(host_path)
        contents = src.endswith("/.")
        host = Path(src[:-2] if contents else src)
        if host.is_dir():
            dest, members = container_path, [(p, p.name) for p in sorted(host.iterdir())]
        else:
            dest, _, name = container_path.rstrip("/").rpartition("/")
            dest, members = dest or "/", [(host, name)]
        with tempfile.TemporaryFile() as archive:
            try:
                _write_tar(archive, members)
            except OSError as e:
                raise InfraError(f"cp in failed: cannot read {host}: {e}") from e
            archive.seek(0)
            code, out, err = await _docker_io(
                "exec", "-i", "-u", "0", self.name, "sh", "-c",
                'mkdir -p "$1" && exec tar -x -p -o -f - -C "$1"', "sh", dest,
                what="cp in", stdin=archive)
        if code != 0:
            raise InfraError(f"cp in failed ({code}) to {container_path}: {_explain(out + err)}")

    async def copy_out(self, container_path: str, host_path: str | Path) -> None:
        """Copy one file or tree out, through `docker exec ... tar -c`, not `docker cp`.

        The archive is read here, so a truncated one is an `InfraError`, never a partial
        file: a partial copy is never success.
        """
        path = container_path.rstrip("/") or "/"
        parent, _, name = path.rpartition("/")
        host = Path(host_path)
        with tempfile.TemporaryFile() as archive:
            code, _, err = await _docker_io(
                "exec", "-u", "0", self.name, "tar", "-c", "-f", "-", "-C", parent or "/", name,
                what="cp out", stdout=archive)
            if code != 0:
                raise InfraError(f"cp out failed ({code}) for {container_path}: {_explain(err)}")
            archive.seek(0)
            try:
                _read_tar(archive, name, host)
            except (tarfile.TarError, EOFError, OSError, ValueError) as e:
                raise InfraError(f"cp out failed for {container_path}: {e}") from e

    async def stop(self) -> None:
        """Remove the container. Idempotent; safe to call on a sandbox that never started.

        **The flag is cleared after the await, not before.** A second Ctrl-C lands inside that
        await, and with the flag already false `stop_sync()` -- the backstop that runs when the
        loop is gone -- would decline and the container would survive. Clearing it afterwards
        costs nothing: `_docker_checked` ignores `rm`'s exit code, so a repeat `rm -f` on an
        already-removed container is a no-op and this stays idempotent.

        For the same reason a raising `rm` leaves BOTH the flag and the registry entry alone:
        the container may still be there, and dropping it here would silently retire the only
        handle anything has on it. `stop_all_sync()` is what resolves that state.
        """
        if not self._started:
            return
        await _docker_checked("rm", "-f", self.name, what="rm", timeout=REMOVE_TIMEOUT_S)
        self._started = False
        _LIVE.discard(self)

    def stop_sync(self) -> None:
        """Backstop for when the event loop is gone (e.g. a second Ctrl-C interrupted stop()).

        **Main thread only.** `signal.signal` raises `ValueError` anywhere else. Spec 10
        owns the Ctrl-C/atexit handler that will call this; it must run there.

        **A removal it cannot confirm raises** (ledger L30, closed by spec C07): `docker rm -f`
        exiting non-zero is an `InfraError` naming the container. "No such container" is the
        one non-zero exit that IS a confirmation -- the container is gone either way. Only
        `stop_all_sync()` calls this, and it catches, names and moves on, so the Ctrl-C path
        still never raises: it just stops counting what it did not see removed.
        """
        if not self._started:
            return
        self._started = False
        # Further Ctrl-C presses must not abort the removal; its own session keeps the
        # terminal's SIGINT away from the docker child too.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            done = subprocess.run(["docker", "rm", "-f", self.name], capture_output=True,
                                  text=True, start_new_session=True)
        except OSError as e:
            # Same rule as the async path: InfraError is the only type that leaves this module.
            raise InfraError(f"docker CLI not usable: {e}") from e
        finally:
            signal.signal(signal.SIGINT, previous)
            _LIVE.discard(self)
        if done.returncode != 0 and "no such container" not in done.stderr.lower():
            raise InfraError(f"docker rm -f {self.name} exited {done.returncode}: "
                             f"{done.stderr.strip() or 'no output'}")


def stop_all_sync() -> int:
    """Remove every still-live container synchronously. Returns how many removals it
    CONFIRMED: an `rm` that exited non-zero is not counted, and is named on stderr (ledger
    L30, closed by spec C07).

    **Main thread only**, for the same reason `stop_sync()` is: `signal.signal`. This is the
    caller ledger L12 records as missing -- `cli._execute()`'s `finally` -- and it is the
    backstop, not the normal path. `asyncio.run` turns the FIRST Ctrl-C into cancellation, so
    `async with task_sandbox(...)` unwinds and `stop()` removes the container on its own; on a
    clean run and on a single-press interrupt this finds an empty set and returns 0.

    A copy of the set is iterated because `stop_sync()` mutates it. One sandbox's failure must
    not strand the others, so an `InfraError` is printed and the loop continues -- and the
    sandbox is discarded either way, so a wedged docker CLI cannot make this loop forever if
    it is ever called twice.
    """
    removed = 0
    for box in list(_LIVE):
        try:
            box.stop_sync()
            removed += 1
        except InfraError as e:
            print(f"could not remove container {box.name}: {e}", file=sys.stderr)
        finally:
            _LIVE.discard(box)
    return removed


def _alive(pid: int) -> bool:
    """`os.kill(pid, 0)` sends nothing; it only asks. Anything but "no such process" --
    including a pid we may not signal -- counts as alive: a missed reap, never a wrong kill."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _reap_forward_dirs() -> None:
    """Spec 14: a dead owner's per-draw socket dirs, by the pid in their name."""
    try:
        entries = list(FORWARD_ROOT.iterdir())
    except OSError:
        return
    suite = os.environ.get(SUITE_ENV)
    for path in entries:
        pid_text, _, rest = path.name.partition("-")
        if suite and not rest.startswith(f"{suite}-"):
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid <= 1 or _alive(pid):
            continue
        shutil.rmtree(path, ignore_errors=True)
        print(f"reaped {path}: owner pid {pid} is gone", file=sys.stderr)


async def reap() -> list[str]:
    """Remove every `andromeda=1` container on THIS host whose owner pid is dead (E10), and
    every per-draw forward dir (spec 14) whose owner pid is dead.

    `stop()` and the Ctrl-C backstop only ever reach containers a LIVE process still holds.
    A process that was SIGKILLed, OOM-killed or lost its machine mid-draw leaves its container
    running forever, and nothing else would notice. `cli.run()` calls this in its pre-flight,
    above `new_run_dir()`, so every run collects what the dead ones left.

    Skipped, never removed: another host's container, a missing or non-integer pid label
    (pre-E10), and pid <= 1. Under a SUITE_ENV, everything another suite (or no suite) owns. A pid reused by an unrelated process reads as alive and is
    skipped this time. A failed `rm -f` is named on stderr and not raised -- one wedged
    container must not stop a run -- but an unreachable daemon raises `InfraError`, because
    then the run could not start a container either.
    """
    suite = os.environ.get(SUITE_ENV)
    only = ["--filter", f"label={LABEL}-suite={suite}"] if suite else []
    code, out, err = await _docker_checked(
        "ps", "-a", "--filter", f"label={LABEL}=1", *only, "--format",
        f'{{{{.Names}}}}\t{{{{.Label "{PID_LABEL}"}}}}\t{{{{.Label "{HOST_LABEL}"}}}}',
        what="ps", timeout=REAP_LIST_TIMEOUT_S)
    if code != 0:
        raise InfraError(f"docker ps failed while reaping: {_explain(out + err)}")
    _reap_forward_dirs()
    host = socket.gethostname()
    removed: list[str] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, pid_label, owner = parts
        if owner != host:
            continue
        try:
            pid = int(pid_label)
        except ValueError:
            continue
        if pid <= 1 or _alive(pid):
            continue
        try:
            code, out, err = await _docker_checked("rm", "-f", name, what="rm",
                                                   timeout=REMOVE_TIMEOUT_S)
        except InfraError as e:
            code, out, err = 1, "", str(e)
        if code != 0:
            print(f"could not reap {name}: {_explain(out + err)}", file=sys.stderr)
            continue
        print(f"reaped {name}: owner pid {pid} is gone", file=sys.stderr)
        removed.append(name)
    return removed
