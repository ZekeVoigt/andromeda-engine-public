"""Docker-backed tests for the merged Sandbox. These really start containers."""

import asyncio
import json
import subprocess

import pytest

import andromeda.runtime.sandbox as sandbox_mod
from andromeda.runtime.sandbox import LABEL, InfraError, Sandbox
from conftest import NATIVE_PLATFORM, SANDBOX_IMAGE, container_named, run_containers

pytestmark = pytest.mark.usefixtures("sandbox_image")

# A tag that cannot resolve anywhere. Not SANDBOX_IMAGE + a suffix: that image is a digest
# reference, and appending to it yields a MALFORMED reference, which fails before the daemon
# ever looks for an image -- a different failure from the one the test means to force.
NO_SUCH_IMAGE = "andromeda-no-such-image:v0"


def sandbox(**kw) -> Sandbox:
    """The unit-test image is pulled for the host arch; production pins linux/amd64.

    Passing it explicitly is also the only coverage the `platform=` parameter gets.
    """
    return Sandbox(SANDBOX_IMAGE, platform=NATIVE_PLATFORM, **kw)


@pytest.fixture
async def sb(test_run_dir):
    """A started sandbox labelled with this test's run id, always removed."""
    async with sandbox(run_id=test_run_dir.name) as box:
        yield box


# --- AC11: split streams, stdin scripts, per-call workdir, check ---------------------


async def test_exec_returns_code_and_split_streams(sb):
    code, out, err = await sb.exec("echo to-stdout; echo to-stderr >&2", timeout=30)
    assert code == 0
    assert out == "to-stdout\n"
    # stderr must NOT be merged into stdout: _run_side and run_claude read them separately
    assert err.strip() == "to-stderr"


async def test_nonzero_exit_code_is_returned_when_not_checking(sb):
    assert (await sb.exec("exit 3", timeout=30, check=False))[0] == 3
    assert (await sb.exec("true", timeout=30, check=False))[0] == 0


async def test_check_true_raises_infra_error(sb):
    with pytest.raises(InfraError) as e:
        await sb.exec("echo boom >&2; exit 7", timeout=30)
    assert "(7)" in str(e.value) and "boom" in str(e.value)


async def test_multiline_script_goes_through_stdin(sb):
    """isolate_repo sends a 7-line script with quoting; argv would mangle or reject it."""
    script = (
        "set -e\n"
        "cd /tmp\n"
        "mkdir -p stdin_probe && cd stdin_probe\n"
        "cat > note.txt <<'PROBE_EOF'\n"
        "line with 'single' and \"double\" quotes\n"
        "PROBE_EOF\n"
        "echo \"lines=$(wc -l < note.txt)\"\n"
    )
    code, out, _ = await sb.exec(script, timeout=60)
    assert code == 0
    assert out.strip() == "lines=1"
    _, body, _ = await sb.exec("cat /tmp/stdin_probe/note.txt", timeout=30)
    assert body.strip() == "line with 'single' and \"double\" quotes"


async def test_workdir_defaults_to_app_and_is_overridable(sb):
    assert (await sb.exec("pwd", timeout=30))[1].strip() == "/app"
    assert (await sb.exec("pwd", timeout=30, workdir="/tmp"))[1].strip() == "/tmp"


async def test_timeout_kills(sb):
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    code, _, err = await sb.exec("sleep 30", timeout=2, check=False)
    elapsed = loop.time() - t0
    assert code in (124, 137)
    assert "timed out" in err
    assert elapsed < 10


# --- AC12: output is not truncated by default ---------------------------------------


async def test_large_output_returned_in_full(sb):
    """agent-world capped every exec at 8KB. `git ls-files` on ansible and a stream-json
    agent transcript both blow past that, and a truncated result is silently wrong."""
    code, out, _ = await sb.exec("head -c 20000 /dev/zero | tr '\\0' a", timeout=60)
    assert code == 0
    assert out == "a" * 20000
    assert "[truncated]" not in out


async def test_output_limit_still_truncates_when_asked(test_run_dir):
    async with sandbox(output_limit=8192, run_id=test_run_dir.name) as box:
        code, out, _ = await box.exec("head -c 20000 /dev/zero | tr '\\0' a", timeout=60)
    assert code == 0
    assert out == "a" * 8192 + "\n[truncated]"


# --- AC9 / AC10: environment ---------------------------------------------------------


async def test_default_sandbox_forwards_no_env(sb, monkeypatch):
    """A Sandbox with no env_allow passes no -e at all: nothing from the host gets in."""
    sentinel = "sk-or-sentinel-sandbox-test"
    monkeypatch.setenv("OPENROUTER_API_KEY", sentinel)

    assert "-e" not in sb._run_args()

    inspect = subprocess.run(["docker", "inspect", sb.name], capture_output=True, text=True, check=True)
    env = json.loads(inspect.stdout)[0]["Config"]["Env"]
    assert not any("OPENROUTER" in e or sentinel in e for e in env)

    code, out, _ = await sb.exec("env", timeout=30)
    assert code == 0 and sentinel not in out and "OPENROUTER" not in out


async def test_env_allow_forwards_only_the_named_var(test_run_dir, monkeypatch):
    secret = "sk-ant-sentinel-env-allow"
    other = "sk-or-sentinel-not-allowed"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("OPENROUTER_API_KEY", other)

    box = sandbox(env_allow=("ANTHROPIC_API_KEY",), run_id=test_run_dir.name)

    # The flag is the bare NAME. The VALUE must never reach argv, where `ps` on the host
    # would show it to every other user on the machine.
    args = box._run_args()
    assert ["-e", "ANTHROPIC_API_KEY"] == [args[i] for i in (args.index("-e"), args.index("-e") + 1)]
    assert args.count("-e") == 1
    assert not any(secret in a for a in args), args

    async with box:
        code, out, _ = await box.exec("env", timeout=30)
    assert code == 0
    assert f"ANTHROPIC_API_KEY={secret}" in out   # the allowed one arrives, by value
    assert other not in out and "OPENROUTER" not in out  # nothing else does


async def test_env_allow_rejects_a_name_that_is_not_set(test_run_dir, monkeypatch):
    """`docker run -e NAME` with NAME unset forwards nothing, silently. Fail loudly instead."""
    monkeypatch.delenv("ANDROMEDA_NO_SUCH_VAR", raising=False)
    with pytest.raises(InfraError) as e:
        async with sandbox(env_allow=("ANDROMEDA_NO_SUCH_VAR",), run_id=test_run_dir.name):
            pass
    assert "ANDROMEDA_NO_SUCH_VAR" in str(e.value)


# --- E02 T1: --add-host -----------------------------------------------------------------

# Deliberately NOT "host.docker.internal": under Colima the container's resolver
# (192.168.5.1) answers that name on its own, so a test using it would pass with the flag
# removed. An alias docker's DNS has never heard of is what makes --add-host load-bearing.
PROBE_ALIAS = "andromeda-add-host-probe"


def test_add_host_lands_beside_network_and_before_the_tail():
    """--add-host is network topology, so it sits with --network -- and, like every other
    flag, ahead of the `-w`/image/command tail, which docker treats as positional."""
    args = sandbox(add_host=(f"{PROBE_ALIAS}:host-gateway",))._run_args()

    assert args.count("--add-host") == 1
    at = args.index("--add-host")
    assert args[at + 1] == f"{PROBE_ALIAS}:host-gateway"
    assert args.index("--network") < at < args.index("-w")


def test_default_sandbox_adds_no_host_entry():
    assert "--add-host" not in sandbox()._run_args()


async def host_probe(test_run_dir, *, add_host: tuple[str, ...]):
    """Run one urllib GET at PROBE_ALIAS from a bridge container against a host listener
    bound to 127.0.0.1. Returns (exec result, what the listener actually received)."""
    seen: list[bytes] = []

    async def handle(reader, writer):
        seen.append(await reader.read(64))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nonhost")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        box = sandbox(network="bridge",  # a host listener is unreachable from "none"
                      add_host=add_host, run_id=test_run_dir.name)
        async with box:
            result = await box.exec(
                'python3 -c "import urllib.request as u;'
                f"print(u.urlopen('http://{PROBE_ALIAS}:{port}/ping', timeout=10)"
                '.read().decode())"',
                timeout=60, check=False)
    return result, seen


async def test_add_host_reaches_a_127_0_0_1_listener_on_the_host(test_run_dir):
    """The claim E02 is built on: a bridge container reaches a host listener bound to
    LOOPBACK, not 0.0.0.0. If this ever fails, binding 0.0.0.0 is a security regression,
    not a fix -- the spec says stop and respec.
    """
    (code, out, err), seen = await host_probe(
        test_run_dir, add_host=(f"{PROBE_ALIAS}:host-gateway",))

    assert code == 0, (code, out, err)
    assert "onhost" in out
    assert seen and b"/ping" in seen[0]   # the host listener really was the one that answered


async def test_the_probe_alias_is_unreachable_without_add_host(test_run_dir):
    """Not-vacuous companion: without the flag the very same probe cannot even resolve,
    so the test above is measuring --add-host and not docker's own DNS."""
    (code, _, err), seen = await host_probe(test_run_dir, add_host=())

    assert code != 0
    assert "Name or service not known" in err or "NameResolutionError" in err, err
    assert seen == []


# --- AC13: copy_in / copy_out / mounts ------------------------------------------------


async def test_copy_in_copy_out_round_trip(sb, tmp_path):
    """docker cp, so pytest's tmp_path works even though Colima cannot mount it."""
    src = tmp_path / "in.txt"
    src.write_text("payload from host\n")
    await sb.copy_in(src, "/app/in.txt")

    code, out, _ = await sb.exec("cat /app/in.txt && tr a-z A-Z < /app/in.txt > /app/out.txt", timeout=30)
    assert code == 0 and out == "payload from host\n"

    dest = tmp_path / "out.txt"
    await sb.copy_out("/app/out.txt", dest)
    assert dest.read_text() == "PAYLOAD FROM HOST\n"


async def test_copy_out_of_a_missing_path_raises(sb, tmp_path):
    with pytest.raises(InfraError) as e:
        await sb.copy_out("/app/no_such_file.txt", tmp_path / "x")
    assert "cp out failed" in str(e.value)


async def test_mount_is_visible_both_ways(test_run_dir):
    """Bind mounts need a path under $HOME -- Colima mounts nothing else."""
    shared = test_run_dir / "shared"
    shared.mkdir()
    # Writable by the container's root, which holds no DAC_OVERRIDE since spec 14: on a Linux
    # host it is not the owner of a host dir (Colima's file sharing hid that on the Mac).
    shared.chmod(0o777)
    (shared / "from_host.txt").write_text("hello from host")

    async with sandbox(mounts={str(shared): "/hidden"}, run_id=test_run_dir.name) as box:
        code, out, _ = await box.exec("cat /hidden/from_host.txt && echo hi > /hidden/from_container.txt",
                                      timeout=30)
        assert code == 0 and out == "hello from host"

    assert (shared / "from_container.txt").read_text() == "hi\n"


# --- AC14: labels and no leaks --------------------------------------------------------


async def test_containers_carry_the_andromeda_labels(sb, test_run_dir):
    inspect = subprocess.run(["docker", "inspect", sb.name], capture_output=True, text=True, check=True)
    labels = json.loads(inspect.stdout)[0]["Config"]["Labels"]
    assert labels[LABEL] == "1"
    assert labels[f"{LABEL}-run"] == test_run_dir.name
    assert f"{LABEL}-suite" in labels  # conftest sets SUITE_ENV at import time
    # the run label is what the leak fixtures filter on, so it has to actually select this container
    assert len(run_containers(test_run_dir.name)) == 1


async def test_extra_labels_are_applied(test_run_dir):
    async with sandbox(labels={"phase": "probe"}, run_id=test_run_dir.name) as box:
        inspect = subprocess.run(["docker", "inspect", box.name], capture_output=True, text=True, check=True)
        assert json.loads(inspect.stdout)[0]["Config"]["Labels"]["phase"] == "probe"


async def test_stop_leaves_no_container(test_run_dir):
    box = sandbox(run_id=test_run_dir.name)
    await box.start()
    try:
        assert len(run_containers(test_run_dir.name)) == 1
    finally:
        await box.stop()
    await box.stop()  # idempotent


async def test_failed_start_leaves_no_container(test_run_dir):
    box = sandbox(run_id=test_run_dir.name)
    box.image = NO_SUCH_IMAGE
    with pytest.raises(InfraError):
        await box.start()
    assert container_named(box.name) == []


# --- diagnostics ----------------------------------------------------------------------


def test_sandbox_image_is_digest_pinned():
    """The tests assert on the image's CONTENTS, so a mutable tag could break them with no
    commit here. Softening the pin back to `python:3.12-slim` has to fail loudly."""
    assert "@sha256:" in SANDBOX_IMAGE, SANDBOX_IMAGE


async def test_missing_docker_cli_raises_infra_error(monkeypatch):
    """InfraError is the one type callers catch; a bare FileNotFoundError escapes all of them."""
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(InfraError) as e:
        await sandbox().start()
    assert "docker CLI not usable" in str(e.value)


async def test_copy_timeout_raises_infra_error(sb, tmp_path, monkeypatch):
    """Every docker call must translate its timeout; only exec and start do so themselves."""
    monkeypatch.setattr(sandbox_mod, "COPY_TIMEOUT_S", 0.001)
    with pytest.raises(InfraError) as e:
        await sb.copy_in(tmp_path, "/app/whatever")
    assert "timed out" in str(e.value)


async def test_unusable_docker_cli_raises_infra_error(tmp_path, monkeypatch):
    """Not just ENOENT: a `docker` on PATH that is not executable is equally unusable."""
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o644)  # present, not executable -> PermissionError, not FileNotFoundError
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(InfraError) as e:
        await sandbox().start()
    assert "docker CLI not usable" in str(e.value)


def test_stop_sync_missing_docker_cli_raises_infra_error(monkeypatch):
    """stop_sync is the one sync path out of the module, and it must obey the same rule.

    No container is started here: with no docker CLI there could not be one, and the point
    is only that the failure leaves as InfraError rather than a bare FileNotFoundError.
    """
    box = sandbox(run_id="stop-sync-probe")
    box._started = True
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(InfraError) as e:
        box.stop_sync()
    assert "docker CLI not usable" in str(e.value)


async def test_daemon_down_error_points_at_colima(monkeypatch):
    # A dead socket is exactly what a stopped Colima VM looks like to the docker CLI.
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/andromeda-no-such-docker.sock")
    with pytest.raises(InfraError) as e:
        await sandbox().start()
    assert "/tmp/andromeda-no-such-docker.sock" in str(e.value)  # the real docker CLI error is kept
    assert "colima start" in str(e.value)
