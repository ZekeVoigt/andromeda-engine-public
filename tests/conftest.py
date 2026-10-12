import os
import subprocess
import time
from pathlib import Path

import pytest

from andromeda.runtime.sandbox import LABEL, SUITE_ENV

# Spec 15: the report `scripts/green` reads, and the quick tier's `task_image` guard.

# The sandbox unit-test image is pulled and run for the host's own architecture. The
# PLATFORM default (linux/amd64) is what production uses, because the SWE-bench Pro
# images are amd64-only; emulating it here would turn a fast native container into
# minutes of QEMU for no extra coverage. The replay test exercises linux/amd64 against
# a real task image.
def _native_platform() -> str:
    arch = subprocess.run(["docker", "version", "--format", "{{.Server.Arch}}"],
                          capture_output=True, text=True, check=True).stdout.strip()
    return f"linux/{arch}"

# Tag every container this suite starts, so a real `outcome run` in another terminal is
# never mistaken for a leak (and ours are never mistaken for part of the real run).
# This runs at IMPORT time, before any fixture, because Sandbox.__init__ reads SUITE_ENV
# per container -- a fixture would be too late for anything constructed during collection.
SUITE_ID = f"pytest-{os.getpid()}"
os.environ[SUITE_ENV] = SUITE_ID

REPO = Path(__file__).resolve().parent.parent
# Colima only mounts $HOME, so pytest's tmp_path is invisible to containers; anything that
# bind-MOUNTS a host path has to live here. copy_in/copy_out go through `docker cp` and
# work with tmp_path fine.
TEST_RUNS = REPO / "runs" / "_test"

NATIVE_PLATFORM = _native_platform()

# The image the sandbox unit tests run on. Pinned by DIGEST, not by tag: the tests assert on
# what is inside the image (`/sys/class/net`, python3, coreutils), and a mutable tag could
# change those under us with no commit here. This index digest covers linux/amd64 and
# linux/arm64, so NATIVE_PLATFORM resolves on this host and on amd64 CI alike.
SANDBOX_IMAGE = ("python:3.12-slim@sha256:"
                 "2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9")


def _ids(*filters: str) -> list[str]:
    args = ["docker", "ps", "-aq"]
    for f in filters:
        args += ["--filter", f]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.split()


def suite_containers() -> list[str]:
    """Only containers this pytest session started."""
    return _ids(f"label={LABEL}-suite={SUITE_ID}")


def run_containers(run_id: str) -> list[str]:
    return _ids(f"label={LABEL}-run={run_id}")


def container_named(name: str) -> list[str]:
    return _ids(f"name=^{name}$")


def assert_no_suite_containers() -> None:
    assert suite_containers() == [], f"containers left behind by this test session: {suite_containers()}"


@pytest.fixture(autouse=True)
def no_container_leak(request):
    """Fail the test that leaks, not whatever runs next, and never leave the daemon dirty."""
    before = set(suite_containers())
    yield
    leaked = [c for c in suite_containers() if c not in before]
    if leaked:
        subprocess.run(["docker", "rm", "-f", *leaked], capture_output=True)
        raise AssertionError(f"{request.node.name} left containers behind: {leaked}")


@pytest.fixture(scope="session", autouse=True)
def no_leftover_containers():
    yield
    assert_no_suite_containers()


@pytest.fixture(scope="session")
def sandbox_image():
    """Pull once per session, so no per-test timeout ever sees a cold registry fetch.

    Plain subprocess on purpose: a `pull()` helper in src/ would be reached only from here,
    which is the caller-less surface this spec exists to delete (ledger L13).
    """
    subprocess.run(["docker", "pull", "--platform", NATIVE_PLATFORM, SANDBOX_IMAGE], check=True)


@pytest.fixture
def test_run_dir(request):
    path = TEST_RUNS / f"{request.node.name}-{time.time_ns()}"
    path.mkdir(parents=True)
    yield path


