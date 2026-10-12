# Spec 06 — Pinning integrity: results

Measured 2026-09-19 on the ansible gold task, the one row that is in `IMAGE_DIGESTS`, has a
replay fixture, and already graded `resolved` before this spec. Every number below was read
back off a real `docker image inspect` or a real `ground_truth.log`, not from the code.
Reproduce with:

```bash
uv run pytest -q tests/test_grader_pinning.py    # 6 passed, ~30s, two real grades
```

## AC12 — the grader and the engine ran the same bytes

<!-- digests:start -->
| Who | Reference it used | Digest it resolved to |
|---|---|---|
| engine | `jefzda/sweap-images@sha256:a2ea931d…` (`SafeTask.image`) | `sha256:a2ea931d3b4a4ca9d19cf4577f1f97b970553f459fedb13a18ed01c804d88ace` |
| grader | `outcome.invalid/sweap-images:ansible.ansible-…` | `sha256:a2ea931d3b4a4ca9d19cf4577f1f97b970553f459fedb13a18ed01c804d88ace` |
| `IMAGE_DIGESTS[tag]` | — | `sha256:a2ea931d3b4a4ca9d19cf4577f1f97b970553f459fedb13a18ed01c804d88ace` |
<!-- digests:end -->

**They match.** The grader's digest is read from `.RepoDigests` of the reference the harness
actually ran, which carries both names of the one image:

```
$ docker image inspect outcome.invalid/sweap-images:ansible.ansible-… --format '{{json .RepoDigests}}'
["jefzda/sweap-images@sha256:a2ea931d3b4a4ca9d19cf4577f1f97b970553f459fedb13a18ed01c804d88ace",
 "outcome.invalid/sweap-images@sha256:a2ea931d3b4a4ca9d19cf4577f1f97b970553f459fedb13a18ed01c804d88ace"]
```

Before this spec the grader's row would have read `jefzda/sweap-images:<tag>` — a mutable
reference, which is to say no answer at all to "which bytes ran".

## The log, before and after

<!-- log:start -->
| | before (`runs/…/20260919T190131366423Z`) | after (`runs/_test/pinned_grade-…`) |
|---|---|---|
| `--dockerhub_username=` | `jefzda` | `outcome.invalid` |
| harness's intent line | `Using Docker Hub image: jefzda/sweap-images:ansible.…` | `Using Docker Hub image: outcome.invalid/sweap-images:ansible.…` |
| harness's resolution | *(pull succeeded — whatever the tag pointed at that day)* | `Using locally available image: outcome.invalid/sweap-images:ansible.…` |
| verdict | `resolved` | `resolved` |
<!-- log:end -->

The third row is the whole mechanism. `outcome.invalid` is RFC 2606 reserved, so the harness's
`client.images.pull` fails on DNS without reaching Docker Hub, and its own `except` falls back
to `client.images.get` — which finds the local retag `_pin_image` just made from the pinned
digest. The harness was not modified; a byte of `vendor/` was not touched. It was given a name
it cannot resolve and a local image under that name, and it did the rest itself.

The verdict is unchanged, which is the point: pinning the grader was meant to remove an
ambiguity, not to change an answer.

## AC2 — the fourth digest

<!-- fourth:start -->
| Field | Value |
|---|---|
| instance_id | `instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan` |
| repo | `NodeBB/NodeBB` |
| dockerhub_tag | `nodebb.nodebb-NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5` |
| digest | `sha256:e49637ebe82a479ca43b4663525955bc9cdd58c457140ee31c20958d621d3cf7` |
<!-- fourth:end -->

Produced by the subcommand this spec added, and pasted in verbatim:

```bash
uv run python -m andromeda.outcome.adapters.coding.task digest \
  instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan
```

NodeBB rather than navidrome, internetarchive or flipt-io because of what it makes AC3 test.
`get_dockerhub_image_uri` has three interesting branches: the element-web special case, the
128-character truncation, and stripping a `-vnan` suffix off the instance id. Both existing
qutebrowser keys are exactly 128 characters, so truncation was already covered; nothing
covered `-vnan`. This instance id ends in `-vnan` and its tag does not, so AC3 now pins a
second branch of the vendored function instead of a third copy of the first.

No fixture was drawn for it — AC2 adds the digest, and AC11 confirms `fixtures/` is
byte-identical.

## What the tests would catch

<!-- rbg:start -->
| Test | AC | On pre-change `ground_truth.py` |
|---|---|---|
| `test_resolve_digest_returns_the_committed_digests` | 1 | pass |
| `test_the_digest_subcommand_prints_a_pasteable_entry` | 1, 2 | pass |
| `test_every_pinned_tag_is_the_tag_the_grader_builds` | 3 | pass |
| `test_the_grader_is_pointed_at_a_registry_that_cannot_resolve` | 4 | **fail** |
| `test_the_grader_ran_the_pinned_digest` | 5 | **fail** |
| `test_an_unpinned_task_still_grades` | 6 | **fail** |
<!-- rbg:end -->

Run by stashing only `src/andromeda/outcome/grading/ground_truth.py`, so the three failures are
against the real `jefzda` behaviour rather than import errors. They fail in three different ways,
and the differences are the point:

| Test | AC | How it fails on the pre-change tree |
|---|---|---|
| `…registry_that_cannot_resolve` | 4 | `AssertionError` -- the log says `--dockerhub_username=jefzda` |
| `…ran_the_pinned_digest` | 5 | `subprocess.CalledProcessError` -- `docker image inspect` finds no such reference, because nothing retagged one |
| `…unpinned_task_still_grades` | 6 | `AttributeError` -- `monkeypatch.setattr` has no module-level `IMAGE_DIGESTS` to bind to |

AC5's is the strongest of the three: it is not that the digest was wrong, it is that the
retagged reference does not exist at all, so there is nothing for the grader to have run.

The first attempt at this check had AC5 passing on the pre-change tree. `_pin_image` leaves
`outcome.invalid/sweap-images:<tag>` in the local daemon on purpose — it is what the harness's
fallback finds — so a leftover retag from an earlier run satisfied the `docker image inspect`
assertion even though nothing in that run had created it. The `pinned_grade` fixture now
`docker rmi -f`s that reference before grading. The flip from pass to fail in the table above
is that fix being load-bearing.

## Known, not fixed

- **The retag is still left behind.** By design: the harness has to find it. The grader test is
  therefore not self-cleaning, and a stale retag is a way a future test could pass for the
  wrong reason. The `rmi` in `pinned_grade` handles the one test that could; the accumulation
  itself is tracked as **L27** in [OPEN-ITEMS.md](../OPEN-ITEMS.md).
- **`resolve_digest` is single-arch only.** It raises on a manifest list rather than guessing
  which platform's digest `docker pull` would have recorded. Every SWE-bench Pro image checked
  so far is single-arch amd64; the first multi-arch one will fail loudly rather than pin a
  reference the engine would never resolve to.
- **`SafeTask.image` still falls back to a mutable tag** for a task outside `IMAGE_DIGESTS`.
  Running an unpinned task is deliberately still allowed; only *storing a fixture* for one is
  now refused.
