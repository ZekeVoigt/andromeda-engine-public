"""The L19 proof: the grader runs the exact bytes IMAGE_DIGESTS names.

Before this spec, `grade` handed the vendored harness `--dockerhub_username=jefzda`, and the
harness rebuilt its own `jefzda/sweap-images:<tag>` reference from that and pulled it. The
engine could run a pinned digest while the grader ran whatever the mutable tag pointed at
today, and nothing said so. These tests assert the two references are now one.

Real Docker, real grading, no mocks -- a mocked harness would prove nothing about which image
the harness actually chose, which is the whole question.
"""

import subprocess
import sys
import time

import pytest
from conftest import REPO, TEST_RUNS

import andromeda.outcome.adapters.coding.task as task
from andromeda.outcome.adapters.coding.docker import swepro_dir
from andromeda.outcome.adapters.coding.task import (
    GRADER_REPO,
    IMAGE_DIGESTS,
    IMAGE_REPO,
    resolve_digest,
)
from andromeda.outcome.grading import ground_truth
from andromeda.outcome.grading.ground_truth import grade

# No skip guard: vendor/ being on disk is the repo's stance (L8 / spec 09), and a missing
# vendor should fail loudly here exactly as it already does in the run path.
sys.path.insert(0, str(swepro_dir()))
from helper_code.image_uri import get_dockerhub_image_uri  # noqa: E402

GOLD = REPO / "fixtures" / "replay" / "ansible-gold"
ANSIBLE_ID = "instance_ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5"
ANSIBLE_TAG = "ansible.ansible-ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5"


def _run_dir(name: str):
    # NOT tmp_path: conftest's TEST_RUNS exists because pytest's tmp dir is invisible to the
    # Docker daemon, and the harness bind-mounts its workspace into the container. A tmp_path
    # run mounts empty and the entryscript dies with 127 and no output at all.
    path = TEST_RUNS / f"{name}-{time.time_ns()}"
    path.mkdir(parents=True)
    return path


@pytest.fixture(scope="module")
def pinned_rows():
    """(instance_id, repo) for every IMAGE_DIGESTS key, from the pinned dataset revision.

    One reverse scan, module-scoped: IMAGE_DIGESTS is keyed by dockerhub_tag and only the
    dataset holds the other two fields. test_the_dataset_is_loaded_at_the_pinned_revision
    already establishes that a HuggingFace round trip is acceptable in this suite.
    """
    rows = task._dataset().filter(lambda r: r["dockerhub_tag"] in IMAGE_DIGESTS)
    by_tag = {r["dockerhub_tag"]: (r["instance_id"], r["repo"]) for r in rows}
    missing = set(IMAGE_DIGESTS) - set(by_tag)
    assert not missing, f"IMAGE_DIGESTS keys with no row at the pinned revision: {missing}"
    return by_tag


@pytest.fixture(scope="module")
def pinned_grade():
    """One real grade of the pinned ansible task, shared by AC4 and AC5.

    Module-scoped because it is ~20s of real container work and both tests ask about the same
    single run: one asks what the log says, the other what the daemon says.
    """
    # Drop any retag a previous run left behind. _pin_image is what creates this reference,
    # and it is not self-cleaning by design (the harness's fallback has to find it). Without
    # this, AC5 below passes on a stale image even when _pin_image never ran -- verified: on
    # the pre-change source it went green off a leftover tag.
    subprocess.run(["docker", "rmi", "-f", f"{GRADER_REPO}:{ANSIBLE_TAG}"], capture_output=True)

    run_dir = _run_dir("pinned_grade")
    status = grade(ANSIBLE_ID, (GOLD / "gold.diff").read_text(), run_dir)
    return status, (run_dir / "ground_truth.log").read_text()


def test_resolve_digest_returns_the_committed_digests():
    """AC1. Every key, not just one: a wrong digest *form* (manifest-list digest, config .Id)
    would still look plausible on a single tag and go red only on the one it happens to miss."""
    for tag, committed in IMAGE_DIGESTS.items():
        assert resolve_digest(tag) == committed, f"{tag} drifted or was recorded in the wrong form"


def test_the_digest_subcommand_prints_a_pasteable_entry():
    """AC1, AC2. The output is what an operator pastes into IMAGE_DIGESTS, so it is asserted
    at this file's own indentation -- key at 4 spaces, value at 8 -- not merely 'contains'."""
    printed = task.main(["digest", ANSIBLE_ID])

    assert printed == f'    "{ANSIBLE_TAG}":\n        "{IMAGE_DIGESTS[ANSIBLE_TAG]}",'


def test_every_pinned_tag_is_the_tag_the_grader_builds(pinned_rows):
    """AC3. The engine's dict key and the grader's reference are proven the same string.

    get_dockerhub_image_uri is the vendored function the harness itself calls, so this fails
    if vendor/ ever changes how a tag is derived -- which is the drift that would silently
    point the grader somewhere else again.
    """
    for tag, (instance_id, repo) in pinned_rows.items():
        built = get_dockerhub_image_uri(instance_id, "outcome.invalid", repo)
        assert built == f"{GRADER_REPO}:{tag}"


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
def test_the_grader_is_pointed_at_a_registry_that_cannot_resolve(pinned_grade):
    """AC4. The harness must fail its pull and fall back to the local retag, not succeed."""
    status, log = pinned_grade

    assert status == "resolved"
    assert "--dockerhub_username=outcome.invalid" in log
    # positively asserted: if a DNS mirror ever answers for outcome.invalid, the pull would
    # succeed and this line would be absent -- that is exactly the failure worth catching
    assert f"Using locally available image: {GRADER_REPO}:{ANSIBLE_TAG}" in log
    assert f"Using Docker Hub image: {IMAGE_REPO}" not in log


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
def test_the_grader_ran_the_pinned_digest(pinned_grade):
    """AC5. Asked of the daemon, not inferred from the log: what does the reference the
    harness actually ran resolve to?"""
    status, _ = pinned_grade
    assert status == "resolved"

    proc = subprocess.run(
        ["docker", "image", "inspect", f"{GRADER_REPO}:{ANSIBLE_TAG}", "--format", "{{json .RepoDigests}}"],
        capture_output=True, text=True, check=True,
    )

    assert f"{IMAGE_REPO}@{IMAGE_DIGESTS[ANSIBLE_TAG]}" in proc.stdout


@pytest.mark.task_image  # spec 15: the quick-tier guard saw it use a task image
def test_an_unpinned_task_still_grades(monkeypatch):
    """AC6. Pinning is not a gate on grading -- an unpinned task grades against the mutable
    tag as before, but the log says so on its first line rather than leaving it to be guessed."""
    monkeypatch.setattr(ground_truth, "IMAGE_DIGESTS", {})
    run_dir = _run_dir("unpinned_grade")

    status = grade(ANSIBLE_ID, (GOLD / "gold.diff").read_text(), run_dir)

    log = (run_dir / "ground_truth.log").read_text()
    assert status == "resolved"
    assert log.splitlines()[0] == f"WARNING: {ANSIBLE_ID} is not in IMAGE_DIGESTS -- grading against the mutable tag"
    assert "--dockerhub_username=jefzda" in log
