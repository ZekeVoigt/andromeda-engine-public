# Spec 07 — Container hardening: results

Measured 2026-09-19 on macOS + Colima, `Server.Arch` `aarch64`, against real containers. Every
number below was read back off a real `docker inspect`, a real `docker run`, or a real gate run —
none of it from the code. Reproduce with:

```bash
uv run pytest -q tests/test_container_hardening.py   # 11 passed, real containers, real OOMs
uv run pytest -q                                     # the whole gate, 336 passed, ~2m55s
```

## AC6 — which OOM discriminator actually fired

The SPEC named a risk: `.State.OOMKilled` might flip only for the container's **main** process
and not for one we `exec`'d into it, in which case `exec()` would have to fall back to wall
clock. Measured, on a `memory="64m"` sandbox allocating 512 MB:

<!-- oom:start -->
| What | Value |
|---|---|
| exit code | `137` |
| elapsed, as `exec()` measured it (the number in the message) | `0.1s` |
| elapsed, as the probe measured it (a superset interval, outside `exec()`) | `0.16s` → renders `0.2s` |
| `.State.OOMKilled` | **`true`** |
| `.State.Status` | `running` — the container survived; only the exec'd process died |
| primary discriminator (`OOMKilled`) fired | **yes** |
| fallback (`elapsed < timeout`) would also have fired | yes |
| message produced | `[out of memory: killed at 0.1s, under the 64m cap, before the 60s limit]` |
<!-- oom:end -->

The two elapsed rows are one exec, not two runs. `exec()` times only the `_docker` call; the
probe times the whole `box.exec()` around it, so the probe's interval is a superset and reads
~0.02s longer. Both are stated because `{:.1f}` rounds them to different tenths, and an earlier
draft of this table printed them as a single number — round-1 gap-check caught it.

**The named risk did not materialise — but the flag failed the other way.** `.State.OOMKilled`
does flip for an exec'd process, even though the container itself stays `running`, so the
primary discriminator answers. What the SPEC did not anticipate is that **the flag is sticky**:
once anything in a container has been OOM-killed it reads `true` for the rest of that
container's life. Round-1 gap-check measured the consequence — a later, genuine
`timeout -s KILL` in the same sandbox was also reported as an OOM, with a message that
contradicts itself:

```
[out of memory: killed at 3.1s, under the 64m cap, before the 3s limit]
                          ^^^^                              ^^         killed *after* the cap
```

The first fix only let the flag answer when it **flipped** — `_oom_seen` recording that it had
already fired. Round-2 gap-check then found that insufficient, and measured why: the flag can
already read `true` *before* the first 137, when a child is OOM-killed while the exec itself
exits 0. Remembering "has it fired on a previous 137" cannot see that, so the same
self-contradicting message came back through a different door:

```
1) swallowed OOM : exit=0 ('swallowed') -> OOMKilled=true  (no 137 observed)
2) real timeout  : exit=137  msg='[out of memory: killed at 3.1s, ... before the 3s limit]'   ← wrong
```

**So `.State.OOMKilled` was retired outright; the wall clock decides alone.** Guarding the flag
with the clock instead (`... and elapsed < t`) would have left a `docker inspect` on every 137
that could no longer change the answer — the fallback already returns `True` whenever
`elapsed < t` — so it is gone rather than kept as decoration. AC6 provides for exactly this:
*"if the test shows that, use the fallback"*. It named the risk as the flag being **under**-eager;
measurement found it **over**-eager, and prescribed the same remedy.

<!-- discriminator:start -->
| Attempt | Mechanism | Outcome |
|---|---|---|
| as specced | `.State.OOMKilled`, primary | wrong on the 2nd 137 — flag is sticky |
| round-1 fix | flag, but only when it flips (`_oom_seen`) | wrong when the 1st OOM is swallowed |
| **final** | **wall clock alone: `elapsed < t`** | **correct in all four measured cases** |
<!-- discriminator:end -->

The clock is sound on its own terms: a SIGKILL that arrived *before* `timeout -s KILL t` could
possibly have fired did not come from timeout. It also needs no daemon, which a diagnostic
should not — it must never be able to replace the real error. The one case given up is an OOM
slower than its own exec timeout, reported as a timeout, which is what the code did before this
spec.

Pinned by two regression tests, each verified red against the code it fixes:
`test_a_later_timeout_is_not_reported_as_an_oom` and
`test_a_swallowed_oom_does_not_poison_a_later_timeout`.

Both readings AC6 asks for hold: the message contains `out of memory` and does **not** contain
`timed out`.

## AC7 — `CAP_ADD`, and the gate failure that forced each entry

<!-- capadd:start -->
| Capability | Forced by | Evidence |
|---|---|---|
| `DAC_OVERRIDE` | `ansible-test` died on `mkdir /app/test/results` under `--cap-drop ALL`, so the run produced no test results at all and `to_checks()` raised `InfraError("no test results on base or after change for 4 files")` | 3 gate failures: `test_agent_replay`, `test_oracle_determinism`, `test_replay_equivalence` |
<!-- capadd:end -->

The root cause, measured rather than reasoned about — `copy_in` is `docker cp`, which writes the
**host** uid into the container:

<!-- ownership:start -->
| Step | `/app/test` owner | mode |
|---|---|---|
| container start | `0:1001` | `drwxr-sr-x` |
| after `docker cp hidden_tests/. → /app/` | **`501:20`** | `drwxr-xr-x` |
<!-- ownership:end -->

`501:20` is the macOS host user. Root inside the container no longer owns the directory, and
without `CAP_DAC_OVERRIDE` root does not get to bypass the permission check:

```
$ docker exec <c> bash -c 'mkdir -p /app/test/results'
/usr/bin/mkdir: cannot create directory '/app/test/results': Permission denied
```

Which flag was responsible, bisected one at a time against `tests/test_replay_equivalence.py`:

<!-- bisect:start -->
| Configuration | Result |
|---|---|
| all hardening stashed (HEAD) | 3 passed, 23.18s |
| full hardening | **1 failed**, 2 passed |
| full hardening, `--cap-drop ALL` removed | 3 passed, 25.51s |
| full hardening, `--security-opt no-new-privileges` removed | **1 failed**, 2 passed |
<!-- bisect:end -->

Minimality — AC7 says a *guessed* capability is a failed criterion, so every plausible
neighbour was measured, not assumed:

<!-- minimality:start -->
| `--cap-add` on top of `--cap-drop ALL` | `mkdir /app/test/results` |
|---|---|
| *(none)* | FAILED |
| `FOWNER` | FAILED |
| `CHOWN` | FAILED |
| `DAC_READ_SEARCH` | FAILED |
| **`DAC_OVERRIDE`** | **OK** |
<!-- minimality:end -->

`DAC_OVERRIDE` is the only one of the four that lets the gate run, so it is the one entry, and
`sandbox.py` carries the comment naming this failure inline beside it.

**Two limits of this entry, stated rather than implied.** First, it is in the module-level
default, so every container gets it, including the two agent sandboxes — a narrower fix exists
(have `copy_in` restore `0:0` after `docker cp`, leaving `CAP_ADD` empty), but that changes
`copy_in` for all callers and is a different mechanism from the one this spec scoped. Second,
the trigger is **host-uid-specific**: `docker cp` wrote `501:20` because that is this macOS
user. On Linux CI running as uid 0 the ownership may not change at all and the capability may
be unnecessary there. This has not been measured on Linux.

## What the gate does *not* cover

AC1 leans on the gate to prove the hardening did not break the task path. It proves that for
**four** of the six `task_sandbox` sites. The two that opt into `bridge` —
`agent.py:78` and `test_writer.py:51` — are monkeypatched out of every test that reaches them
(spec 05 pinned both halves precisely so a gate run costs no live API draw), so `claude -p` has
**never run under the new caps**. Round-1 gap-check surfaced this.

<!-- uncovered:start -->
| Site | Reached by the gate? | New constraint it has never run under |
|---|---|---|
| `runner.py:41` | yes — the replay and determinism tests | `none`, 2g, `cap-drop ALL`, `DAC_OVERRIDE` |
| `adapter.py:105`, `adapter.py:118` | yes | same |
| `scripts/bad_patch_check.py:154` | yes | same |
| `agent.py:78` | **no** — monkeypatched | `bridge`, **2g memory cap**, `cap-drop ALL` |
| `test_writer.py:51` | **no** — monkeypatched | same |
<!-- uncovered:end -->

The capability is not the risk on that path — `DAC_OVERRIDE` is granted, not withheld. The
unmeasured knob is the **2g memory cap**, which those two sandboxes did not have before and
which now applies to a `node` process running `claude -p`. Nothing here shows 2g is enough; the
only way to find out is a live agent draw, which the gate deliberately does not do. If a future
draw dies at exit 137, `OUTCOME_CONTAINER_MEMORY` is the knob, and the measured requirement
belongs in this file.

## AC1 — the full gate, before and after

<!-- gate:start -->
| | tests | wall clock | result |
|---|---|---|---|
| before (HEAD, no hardening, hardening test file held aside) | 325 | `157.18s` (2:37) | passed |
| after (hardening on, `DAC_OVERRIDE`) | 334 | `169.23s` (2:49) | passed |
| after round-1 gap-check (+ sticky-flag regression test) | 335 | `180.78s` (3:00) | passed |
| after round-2 gap-check (+ swallowed-OOM regression test) | 336 | `175.43s` (2:55) | passed |
| delta, start to finish | **+11** | **+18.25s** (+12%) | — |
<!-- gate:end -->

The +11 are this spec's own tests. The time is what real containers cost: seven of the eleven
start one, three force a real OOM, and one makes two outbound connections. Every run is in **minutes**,
which is the standing bar — a fast finish would mean Docker never ran.

## AC2–AC5 — what a default container actually looks like

<!-- inspect:start -->
| `docker inspect .HostConfig` | Value | Asserted by |
|---|---|---|
| `NetworkMode` | `none` | AC2 |
| `Memory` | `2147483648` (2g) | AC2 |
| `MemorySwap` | `2147483648` — equal, so swap is not an escape hatch | AC2 |
| `NanoCpus` | `2000000000` (2 cpus) | AC2 |
| `CapDrop` | `["ALL"]` | AC2 |
| `CapAdd` | `["CAP_DAC_OVERRIDE"]` | AC7 |
| `SecurityOpt` | contains `no-new-privileges` | AC2 |
<!-- inspect:end -->

`docker inspect` reports capabilities in canonical `CAP_`-prefixed form whichever form was
passed on the command line, so the test compares on the unprefixed names.

Behaviour, not flag strings — AC3 and AC4 are asserted on what the container can actually do:

<!-- behaviour:start -->
| Sandbox | `/sys/class/net` | connect to `1.1.1.1:443` |
|---|---|---|
| default (`network="none"`) | `lo` only | **UNREACHABLE** |
| `network="bridge"` | `lo`, `eth0` | **REACHED** |
<!-- behaviour:end -->

The no-network test is hermetic and never skips: `--network none` fails a connect on an offline
host too, so it has no reason to acquire a skip. The bridge test is the only one that can skip,
and only when a host-side probe shows the machine itself is offline, in which case `bridge`
would prove nothing.

## The two facts a passing test cannot show

Read back by hand, as the plan requires.

No second default crept in outside `_run_args()` — every knob resolves in exactly one place:

```
237:        args += ["--network", self.network]
238:        args += ["--memory", memory, "--memory-swap", memory]  # equal: swap is not an escape hatch
239:        args += ["--cpus", self._cpus()]
240:        args += ["--cap-drop", "ALL"]
241:        for cap in self.cap_add:
243:        args += ["--security-opt", "no-new-privileges"]
```

Exactly two `bridge` sites in `src/`:

```
$ grep -rn 'network="bridge"' src/
src/andromeda/outcome/adapters/coding/test_writer.py:51:                            network="bridge") as c:
src/andromeda/outcome/adapters/coding/agent.py:78:                            network="bridge") as c:
```

The other four `task_sandbox` sites — `runner.py:41`, `adapter.py:105`, `adapter.py:118` and
`scripts/bad_patch_check.py:154` — were not edited and inherit `none`. `scripts/` is deliberately
outside the AST sweep: AC4 scopes the claim to `src/`.

## What this does not contain

Recorded as ledger **L28**, owned by the new stub **14**. The threat model this spec commits to
is *a mistaken agent, not a hostile one*. The caps contain runaway memory and CPU, accidental
writes, and casual egress from the four non-agent sandboxes. They do not contain a kernel
exploit, a container escape, or an adversary with CVE knowledge — every container still shares
the host kernel, and the two agent sandboxes hold plain `bridge` with no egress filter. That is
what Docker can actually back; the rest is staged, not claimed.
