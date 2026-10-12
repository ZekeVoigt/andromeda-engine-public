"""Spec 07: every container starts hardened. Docker-backed -- these really start
containers, really force an OOM, and really make an outbound connection.

A fast finish means Docker never ran and the result proves nothing.
"""

import ast
import json
import socket
import subprocess
from pathlib import Path

import pytest

import andromeda.runtime.sandbox as sandbox_mod
from andromeda.runtime.sandbox import Sandbox
from conftest import NATIVE_PLATFORM, SANDBOX_IMAGE

pytestmark = pytest.mark.usefixtures("sandbox_image")

SRC = Path(__file__).resolve().parents[1] / "src"

# A literal IP, not a hostname: DNS is a second failure mode, and under `--network none`
# the failure would land at resolution rather than at connect, which is not the claim.
PROBE_HOST, PROBE_PORT = "1.1.1.1", 443

# Heredoc on stdin, like the other sandbox tests -- `bash -s` buffers it and hands it to
# python, so the quoting survives intact. python3 is in the image and gives a typed error.
CONNECT_PROBE = """\
python3 - <<'PROBE_EOF'
import socket, sys
try:
    socket.create_connection(("1.1.1.1", 443), timeout=10).close()
except OSError as e:
    print(f"UNREACHABLE: {type(e).__name__}: {e}")
    sys.exit(1)
print("REACHED")
PROBE_EOF
"""

# ~512 MB, zero-filled, so every page is really touched. Under a 64m cap the OOM killer
# arrives in seconds; under no cap this simply succeeds.
OOM_PROBE = """\
python3 - <<'OOM_EOF'
big = bytearray(512 * 1024 * 1024)
print(len(big))
OOM_EOF
"""


def sandbox(**kw) -> Sandbox:
    """Same helper as tests/test_runtime_sandbox.py: the unit-test image is host-arch."""
    return Sandbox(SANDBOX_IMAGE, platform=NATIVE_PLATFORM, **kw)


def on_runsc(box: Sandbox) -> bool:
    """Spec 14: the VM's suite runs every sandbox on gVisor. Where a claim's EVIDENCE differs
    by runtime (never the claim), the branch cites specs/14-vm-isolation/REPORT.md."""
    return inspect(box.name)["HostConfig"]["Runtime"] == "runsc"


def inspect(name: str) -> dict:
    out = subprocess.run(["docker", "inspect", name], capture_output=True, text=True,
                         check=True).stdout
    return json.loads(out)[0]


def flag(args: list[str], name: str) -> list[str]:
    """Every value `name` was given, in order."""
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]


def host_reaches_internet() -> bool:
    try:
        socket.create_connection((PROBE_HOST, PROBE_PORT), timeout=10).close()
    except OSError:
        return False
    return True


# --- AC2: the flags ------------------------------------------------------------------


def test_run_args_carries_every_hardening_flag():
    args = sandbox()._run_args()
    assert flag(args, "--network") == ["none"]
    assert flag(args, "--memory") == ["2g"]
    assert flag(args, "--memory-swap") == ["2g"]   # equal: swap is not an escape hatch
    assert flag(args, "--cpus") == ["2"]
    assert flag(args, "--cap-drop") == ["ALL"]
    assert flag(args, "--cap-add") == list(getattr(sandbox_mod, "CAP_ADD", ()))
    assert "no-new-privileges" in flag(args, "--security-opt")

    # One contiguous block right after --platform: a reader sees the whole posture in one
    # place, and a second default added elsewhere in _run_args() shows up here as a gap.
    expected = ["--network", "none", "--memory", "2g", "--memory-swap", "2g",
                "--cpus", "2", "--cap-drop", "ALL"]
    for cap in getattr(sandbox_mod, "CAP_ADD", ()):
        expected += ["--cap-add", cap]
    expected += ["--security-opt", "no-new-privileges"]
    start = args.index("--platform") + 2
    assert args[start:start + len(expected)] == expected


async def test_default_container_inspects_as_hardened(test_run_dir):
    async with sandbox(run_id=test_run_dir.name) as box:
        host = inspect(box.name)["HostConfig"]
    assert host["NetworkMode"] == "none"
    assert host["Memory"] == 2147483648          # 2g
    assert host["MemorySwap"] == 2147483648      # equal to Memory
    assert host["NanoCpus"] == 2000000000        # 2 cpus
    assert host["CapDrop"] == ["ALL"]
    # docker inspect reports capabilities in canonical CAP_-prefixed form, whichever form
    # was passed on the command line, so compare on the unprefixed names.
    assert [c.removeprefix("CAP_") for c in host["CapAdd"] or []] == \
        [c.removeprefix("CAP_") for c in getattr(sandbox_mod, "CAP_ADD", ())]
    assert "no-new-privileges" in host["SecurityOpt"]


# --- AC3 / AC4: behaviour, not flag strings -------------------------------------------


async def test_default_sandbox_has_no_network(test_run_dir):
    """The security claim itself, and hermetic by construction.

    `--network none` fails a connect on an offline host too, so this test has no reason
    to skip and must never be allowed to acquire one.
    """
    async with sandbox(run_id=test_run_dir.name) as box:
        # /proc/net/dev, not /sys/class/net: gVisor has no /sys/class/net at all, and both
        # runtimes list their interfaces here (spec 14 REPORT, kernel-surface drift).
        code, ifaces, _ = await box.exec(
            "tail -n +3 /proc/net/dev | cut -d: -f1", timeout=30)
        assert code == 0
        assert ifaces.split() == ["lo"], ifaces
        code, out, err = await box.exec(CONNECT_PROBE, timeout=60, check=False)
    assert code != 0, out
    assert "UNREACHABLE" in out, (out, err)


async def test_bridge_reaches_the_internet_and_the_default_does_not(test_run_dir):
    """One test, both sandboxes, same probe: the difference is the whole point of AC4."""
    if not host_reaches_internet():
        pytest.skip(f"host cannot reach {PROBE_HOST}:{PROBE_PORT} either, so `bridge` "
                    "would prove nothing here. The no-network claim is "
                    "test_default_sandbox_has_no_network, which never skips.")

    async with sandbox(network="bridge", run_id=test_run_dir.name) as box:
        code, out, err = await box.exec(CONNECT_PROBE, timeout=60, check=False)
    assert code == 0 and "REACHED" in out, (code, out, err)

    async with sandbox(run_id=test_run_dir.name) as box:
        code, out, err = await box.exec(CONNECT_PROBE, timeout=60, check=False)
    assert code != 0 and "UNREACHABLE" in out, (code, out, err)


# --- AC4: exactly two opt-in sites in src/ --------------------------------------------


def bridge_sites(text: str) -> list[int]:
    """Line number of every call that passes network="bridge"."""
    hits = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call):
            hits += [node.lineno for kw in node.keywords
                     if kw.arg == "network" and isinstance(kw.value, ast.Constant)
                     and kw.value.value == "bridge"]
    return sorted(hits)


def test_the_bridge_sweep_is_not_vacuous():
    """The assertion below is a set equality, which passes while checking nothing if the
    root is wrong or the detector never detects. Pin both."""
    assert SRC.is_dir(), f"sweep root missing: {SRC}"
    assert any(SRC.rglob("*.py")), f"sweep root has no .py files: {SRC}"
    assert bridge_sites('task_sandbox(img, network="bridge")') == [1]
    assert bridge_sites('task_sandbox(img, network="none")') == []
    assert bridge_sites("task_sandbox(img)") == []


def test_no_bridge_literals_left_in_src():
    """Spec E03 inverted this: the two opt-in sites are gone, and the set is empty.

    A call site that names its own network is the shape EL2 exists to kill -- the decision
    is invisible at runtime and unaudited. The topology now comes from `egress.network_for`,
    off a per-role policy, so a new `network="bridge"` anywhere under src/ is a regression
    to the thing this spec deleted. scripts/ is deliberately outside the sweep: the claim is
    scoped to src/.
    """
    found = {p.relative_to(SRC).as_posix()
             for p in sorted(SRC.rglob("*.py")) if bridge_sites(p.read_text())}
    assert found == set()


def bridge_literals(text: str) -> list[int]:
    """Line of every bare `"bridge"` string in the source -- keyword or not.

    AST, not `in text`, so prose is not a hit: a docstring that MENTIONS bridging is one
    string constant whose value is the whole paragraph, and it never equals "bridge".
    """
    return sorted(node.lineno for node in ast.walk(ast.parse(text))
                  if isinstance(node, ast.Constant) and node.value == "bridge")


def test_the_bridge_literal_sweep_is_not_vacuous():
    assert bridge_literals('return "bridge"') == [1]
    assert bridge_literals('"""a docstring about bridge networking"""') == []
    assert bridge_literals('return "none"') == []


def test_no_bridge_literal_is_left_in_src():
    """AC1's exception clause, which the AST sweep above cannot see.

    `bridge_sites` matches `network=` KEYWORDS; this catches a call site that spelled its
    network some other way. No grading box has a network, so no `"bridge"` appears at all.
    """
    found = {p.relative_to(SRC).as_posix()
             for p in sorted(SRC.rglob("*.py")) if bridge_literals(p.read_text())}
    assert found == set()


# --- AC5: the env overrides -----------------------------------------------------------


async def test_env_vars_change_the_memory_and_cpu_limits(test_run_dir, monkeypatch):
    """Constructed *before* the env vars are set.

    Every OUTCOME_* value except PLATFORM is read per call, so these have to resolve in
    `_run_args()`; resolving them in `__init__` would freeze the defaults in here.
    """
    box = sandbox(run_id=test_run_dir.name)
    monkeypatch.setenv("OUTCOME_CONTAINER_MEMORY", "512m")
    monkeypatch.setenv("OUTCOME_CONTAINER_CPUS", "1")

    args = box._run_args()
    assert flag(args, "--memory") == ["512m"]
    assert flag(args, "--memory-swap") == ["512m"]
    assert flag(args, "--cpus") == ["1"]

    async with box:
        host = inspect(box.name)["HostConfig"]
    assert host["Memory"] == 536870912
    assert host["MemorySwap"] == 536870912
    assert host["NanoCpus"] == 1000000000


# --- AC6: OOM is not a timeout --------------------------------------------------------


async def test_memory_cap_reports_out_of_memory_not_a_timeout(test_run_dir):
    """A `timeout -s KILL` and an OOM kill both exit 137, so calling every 137 a timeout
    became wrong the moment a memory cap existed.

    64m is safely above Docker's 6m floor and small enough that the kill takes seconds.
    """
    async with sandbox(memory="64m", run_id=test_run_dir.name) as box:
        runsc = on_runsc(box)
        code, out, err = await box.exec(OOM_PROBE, timeout=60, check=False)
    # spec 14 REPORT, risk 3: on runsc the OOM ends the whole sandbox and `docker exec` exits
    # 128; the message is the claim, and it holds on both
    assert code == (128 if runsc else 137), (code, out, err)
    assert "out of memory" in err, err
    assert "timed out" not in err, err


async def test_a_later_timeout_is_not_reported_as_an_oom(test_run_dir):
    """`.State.OOMKilled` is sticky: once anything in the container has been OOM-killed it
    reads `true` for that container's whole life. Round-1 gap-check caught the primary
    discriminator lying on the *next* 137, printing a self-contradicting
    "killed at 3.1s ... before the 3s limit". The flag only answers when it flips.
    """
    async with sandbox(memory="64m", run_id=test_run_dir.name) as box:
        oom_code, _, oom_err = await box.exec(OOM_PROBE, timeout=60, check=False)
        if on_runsc(box):
            # spec 14 REPORT, risk 3: there is no later exec on runsc -- the OOM ended the
            # container, so a sticky flag has nothing left to mislead
            assert oom_code == 128 and "out of memory" in oom_err, oom_err
            assert inspect(box.name)["State"]["Running"] is False
            return
        # same container, same 137, but this one really is `timeout -s KILL`
        to_code, _, to_err = await box.exec("sleep 60", timeout=3, check=False)

    assert oom_code == 137 and "out of memory" in oom_err, oom_err
    assert to_code == 137, (to_code, to_err)
    assert "timed out" in to_err, to_err
    assert "out of memory" not in to_err, to_err


async def test_a_swallowed_oom_does_not_poison_a_later_timeout(test_run_dir):
    """Round-2 gap-check: a child can be OOM-killed while the exec itself exits 0, which sets
    `.State.OOMKilled` with no 137 to observe. Remembering "has it fired on a previous 137"
    did not close that; the wall clock does, which is why the flag is not consulted at all.
    """
    async with sandbox(memory="64m", run_id=test_run_dir.name) as box:
        # the OOM is swallowed: the child dies, the exec reports success
        code, out, err = await box.exec(
            "bash -c 'python3 -c \"b=bytearray(512*1024*1024)\"' ; echo swallowed; exit 0",
            timeout=60, check=False)
        if on_runsc(box):
            # spec 14 REPORT, risk 3: runsc cannot swallow an OOM -- the child's OOM ends the
            # sandbox, so it surfaces, and no later exec exists to be poisoned
            assert code == 128 and "out of memory" in err, (code, out, err)
            return
        assert code == 0 and "swallowed" in out, (code, out)
        assert inspect(box.name)["State"]["OOMKilled"] is True, "probe no longer sets the flag"

        to_code, _, to_err = await box.exec("sleep 60", timeout=3, check=False)

    assert to_code == 137, (to_code, to_err)
    assert "timed out" in to_err, to_err
    assert "out of memory" not in to_err, to_err


# --- AC7: no guessed capabilities -----------------------------------------------------


def test_cap_add_is_empty_or_every_entry_names_the_failure_that_forced_it():
    """A guessed capability is a failed criterion, so each one must cite its gate failure."""
    cap_add = getattr(sandbox_mod, "CAP_ADD", None)
    assert cap_add is not None, "sandbox.py defines no CAP_ADD"
    source = Path(sandbox_mod.__file__).read_text().splitlines()
    for cap in cap_add:
        cited = [line for line in source
                 if cap in line and "#" in line.split(cap, 1)[1]]
        assert cited, f"CAP_ADD entry {cap!r} carries no inline comment naming the gate failure"
