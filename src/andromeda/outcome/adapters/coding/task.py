"""Loads one SWE-bench Pro row and splits it into what the engine may see and what only the grader may see."""

import json
import subprocess
import sys
from functools import lru_cache

from pydantic import BaseModel

DATASET = "ScaleAI/SWE-bench_Pro"
SPLIT = "test"
# Pinned: an unpinned dataset can change rows under a stored oracle, which is drift the
# manifest check exists to catch. Bump deliberately, never incidentally.
DATASET_REVISION = "7ab5114912baf22bb098818e604c02fe7ad2c11f"

IMAGE_REPO = "jefzda/sweap-images"
# The grader is pointed here instead of at Docker Hub. `.invalid` is RFC 2606 reserved, so the
# harness's `client.images.pull` fails on DNS with no round trip, and its fallback picks up the
# local retag of the pinned digest -- the exact bytes IMAGE_DIGESTS names.
GRADER_REPO = "outcome.invalid/sweap-images"
# `:<tag>` is mutable on Docker Hub; a digest is not. Keyed by dockerhub_tag, which the
# dataset already truncates to docker's 128-char tag limit, so these match the rows verbatim.
IMAGE_DIGESTS: dict[str, str] = {
    "ansible.ansible-ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5":
        "sha256:a2ea931d3b4a4ca9d19cf4577f1f97b970553f459fedb13a18ed01c804d88ace",
    "qutebrowser.qutebrowser-qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111-v059c6fdc75567943479b23ebca7c07b5e9a7f":
        "sha256:bca2bc90cbe20acca25e9b5e2e96aa751a231a5b6f8f5bc7b7ea3a7b7d449758",
    "qutebrowser.qutebrowser-qutebrowser__qutebrowser-c580ebf0801e5a3ecabc54f327498bb753c6d5f2-v2ef375ac784985212b1805e1d0431dc8f1b3c":
        "sha256:5909cff71973743b14dd7127624a5e527b3c69e33ec18115287ce5c2b6881efd",
    "nodebb.nodebb-NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5":
        "sha256:e49637ebe82a479ca43b4663525955bc9cdd58c457140ee31c20958d621d3cf7",
    "element-hq.element-element-hq__element-web-1077729a19c0ce902e713cf6fab42c91fb7907f1":
        "sha256:d2896e2a4dc6aee6c6ee6a17465e7d443976ac06a8510fd80215a68cda1ff71d",
    "flipt-io.flipt-flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1":
        "sha256:c936fbeff2b65212b5f19ce866d84514cc1a4eb0a4c838bf67bc7eeb14d0e0b4",
    "future-architect.vuls-future-architect__vuls-01441351c3407abfc21c48a38e28828e1b504e0c":
        "sha256:a4e8787da0f8b9ca6c53990b3246834ecbd69e586d18a1d6b0a8b060f7ebc6ee",
    "gravitational.teleport-gravitational__teleport-005dcb16bacc6a5d5890c4cd302ccfd4298e275d-vee9b09fb20c43af7e520f57e9239bbcf46b7113":
        "sha256:5c439b66a8bd5bf43038d5ec42405a408de602619a953f20c0ac95264a74e7ec",
    "internetarchive.openlibrary-internetarchive__openlibrary-0a90f9f0256e4f933523e9842799e39f95ae29ce-v76304ecdb3a5954fcf13feb710e8c":
        "sha256:f23b3bdc3d833a679941e260f07beb8d768ce0e6ae553147088fdea41eb59da6",
    "navidrome.navidrome-navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9":
        "sha256:a26279b8798e6b965c6c5b08e59cfa45bf544d9c3ed6e32d801c1d5d405236f3",
    "protonmail.webclients-protonmail__webclients-01b519cd49e6a24d9a05d2eb97f54e420740072e":
        "sha256:813ef73559b628a1b1a46137f7a4d9f82715f20d7e31e22270017c1be6ce4b1a",
    "tutao.tutanota-tutao__tutanota-12a6cbaa4f8b43c2f85caca0787ab55501539955-vc4e41fd0029957297843cb9dec4a25c7c756f029":
        "sha256:3e132f530ddf10e83a494ded2150fa1bc41cfbe67805aa695d4f92210d4cbd01",
}


# Spec 21: the one box a folder task is drawn and judged in. A full Debian image (buildpack-deps),
# because runtime/baseline.py's ASSERTED rows (git, gcc, ssh, ps, ...) are not in a slim one.
# The index digest, pinned from the registry as tests/conftest.py pins SANDBOX_IMAGE.
FOLDER_IMAGE = ("python:3.12-bookworm@sha256:"
                "dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0")


class UnpinnedImage(Exception):
    """A fixture was asked for against a task with no entry in IMAGE_DIGESTS.

    Both pinners raise this. A fixture pinned against a mutable `:<tag>` can have its image
    swapped underneath it and no drift check can ever see it, so storing one is refused.
    """


class SafeTask(BaseModel):
    instance_id: str
    repo: str
    base_commit: str
    dockerhub_tag: str
    repo_language: str
    problem_statement: str
    requirements: str
    interface: str

    @property
    def image(self) -> str:
        """Digest reference for a pinned task, mutable tag otherwise.

        Nothing parses this: task_sandbox hands it straight to `docker run`, which takes
        either form.
        """
        digest = IMAGE_DIGESTS.get(self.dockerhub_tag)
        return f"{IMAGE_REPO}@{digest}" if digest else f"{IMAGE_REPO}:{self.dockerhub_tag}"

    def request_text(self) -> str:
        parts = [self.problem_statement, self.requirements, self.interface]
        return "\n\n".join(p.strip() for p in parts if p and p.strip())


class FolderTask(SafeTask):
    """Spec 21: a `checks` task whose base is its sealed `inputs/`, not a SWE-bench repo.

    `inputs` is the host path of `tasks/<T>/acceptance/inputs`; `docker.lay_base` lays it
    down as /app and commits it, and `base_commit` is `docker.folder_commit(inputs)`, so the
    box's HEAD is checked against a sha the host computed with no box at all.
    """

    inputs: str

    @property
    def image(self) -> str:
        return FOLDER_IMAGE


class GradingData(BaseModel):
    """Answer-bearing fields. Only grading/ may read these (AC4)."""

    patch: str
    test_patch: str
    fail_to_pass: str
    pass_to_pass: str
    selected_test_files_to_run: str
    # checks out the gold test files from the fix commit, so it is answer-bearing too
    before_repo_set_cmd: str
    raw_row: dict


@lru_cache(maxsize=1)
def _dataset():
    from datasets import load_dataset

    return load_dataset(DATASET, split=SPLIT, revision=DATASET_REVISION)


def _row(instance_id: str) -> dict:
    rows = _dataset().filter(lambda r: r["instance_id"] == instance_id)
    if len(rows) != 1:
        raise KeyError(f"{instance_id} not found in {DATASET}/{SPLIT} (matches: {len(rows)})")
    return dict(rows[0])


def load_safe(instance_id: str) -> SafeTask:
    """Engine-side loader: never builds GradingData."""
    row = _row(instance_id)
    return SafeTask(**{k: row[k] or "" for k in SafeTask.model_fields})


def load(instance_id: str) -> tuple[SafeTask, GradingData]:
    """Grader-side loader."""
    row = _row(instance_id)
    safe = SafeTask(**{k: row[k] or "" for k in SafeTask.model_fields})
    grading = GradingData(**{k: row[k] or "" for k in GradingData.model_fields if k != "raw_row"}, raw_row=row)
    return safe, grading


def resolve_digest(dockerhub_tag: str) -> str:
    """The digest `docker pull {IMAGE_REPO}:{tag}` would record, read from the live registry.

    Registry metadata only -- no layers are pulled.
    """
    ref = f"{IMAGE_REPO}:{dockerhub_tag}"
    proc = subprocess.run(["docker", "manifest", "inspect", "-v", ref], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"docker manifest inspect {ref} failed ({proc.returncode}): {proc.stderr.strip()}")
    manifest = json.loads(proc.stdout)
    # A list means multi-arch: `Descriptor` is then per-platform and none of them is the digest
    # `docker pull <tag>` records (that would be the manifest list's own digest). Refuse rather
    # than pin a reference the engine would never resolve to.
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{ref} is a multi-arch manifest list; resolve_digest handles single-arch only")
    return manifest["Descriptor"]["digest"]


def unpinned_image_error(task: SafeTask) -> UnpinnedImage:
    """The one refusal message, so the two pinners cannot drift apart."""
    return UnpinnedImage(
        f"{task.instance_id} is not in IMAGE_DIGESTS, so its image is the mutable tag "
        f"{IMAGE_REPO}:{task.dockerhub_tag} and a fixture pinned against it can never detect "
        f"image drift. Pin it first:\n"
        f"    python -m andromeda.outcome.adapters.coding.task digest {task.instance_id}\n"
        f"then paste that entry into IMAGE_DIGESTS in {__name__}."
    )


USAGE = "usage: python -m andromeda.outcome.adapters.coding.task (<id> | digest <id>)"


def main(argv: list[str]) -> str:
    """Dispatch for `python -m ...task`, returning the line to print.

    A function rather than a bare __main__ block so `digest` has a caller that is not a human.
    """
    match argv:
        case ["digest", instance_id]:
            task = load_safe(instance_id)
            # paste-ready: this file's own indentation, key at 4 spaces and value at 8
            return f'    "{task.dockerhub_tag}":\n        "{resolve_digest(task.dockerhub_tag)}",'
        case [instance_id]:
            safe = load_safe(instance_id)
            dump = safe.model_dump(exclude={"problem_statement", "requirements", "interface"})
            return json.dumps(dump, indent=2)
        case _:
            raise SystemExit(USAGE)


if __name__ == "__main__":
    print(main(sys.argv[1:]))
