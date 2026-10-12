# Spec 14 — REPORT

Measured on a gate VM (n2-standard-4, x86_64, Ubuntu 24.04.5, kernel 7.0.0-1011-gcp,
Docker 29.8.1) on 2026-09-27, under `flock ~/suite/lock`, before the code that depends on each
measurement was finished. The Mac figures are Colima 29.2.1 on arm64.

## runsc: release, pins, platform

| | |
|---|---|
| Release | `release-20260914.0` (`runsc --version`), OCI spec 1.2.1 |
| Artifact | `https://storage.googleapis.com/gvisor/releases/release/20260914.0/x86_64/gvisor.tar.bz2` |
| Tarball sha256 | `94a1b9716797efcb0b348fc3283ddaa1ee5c7898b61eeccb08ee9ac7dc903c15` (its published sha512 matched) |
| `runsc` sha256 | `c0f4ec0ac1198975d5cf919a78f2302426de096f69eebd33e50125c3ca42d699` |
| `gvisor-bin/gvisor_sentry` sha256 | `aff3ed7dfac54b04aab14de2dde53e021402f0ba238bc7ece3ac7d4b6604b055` |
| Platform | **systrap** (the default). `kvm` was measured, because `/dev/kvm` is present |

**The release layout changed, and it cost one measurement pass.** Current gVisor releases ship
no bare `runsc` at `.../x86_64/runsc` (404). They ship a tarball holding `runsc`,
`containerd-shim-runsc-v1` and a `gvisor-bin/` directory of sidecars. The first pass
installed `runsc` alone, and every runsc container failed to start:
`sidecar "gvisor_sentry" not usable (stat /usr/local/bin/gvisor-bin/gvisor_sentry: no such
file or directory) and --sidecar-usage-policy is set to STRICT`. `scripts/isolate-host` installs
`gvisor-bin/` beside `runsc` and pins the sentry's sha256 as well. The VM has no `bzip2`, so the
script extracts with `python3`'s `tarfile`.

**Why systrap, not kvm.** On nested virtualization, kvm was faster only on the syscall-heavy
file loop (19.5 s vs 23.1 s). It was slower to start and to import Python (see the overhead
table). The suite is dominated by container starts, `exec`s and test runs, not by one tight
syscall loop, and systrap needs no device.

## Risk 1: `docker cp` under runsc, both ways

Measured with `--cap-drop ALL --security-opt no-new-privileges`, on `python:3.12-slim`:

| | runc | runsc |
|---|---|---|
| `docker cp` in, 1 MiB | exit 0; bytes arrive, owned **1001:1002** (the host uid) | exit 0, and **the file is not there**: `sha256sum: /tmp/blob: No such file or directory` |
| `docker cp` out, of a file an `exec` wrote | exit 0, sha256-equal | exit 1: `Could not find the file /tmp/out in container` |
| exec copy in (tar through `docker exec -i -u 0`) | 0:0, modes kept, sha256-equal, `mkdir /app/test/results` OK | same |
| exec copy out (tar through `docker exec -u 0`) | sha256-equal | sha256-equal |

**The change was forced, not only chosen.** Under runsc, `docker cp` in exits 0 and silently
loses the file, because `dockerd` writes beneath the sandbox's root-filesystem overlay. Nothing
would have caught that but a later "no such file". On runc it also shows, on Linux, the
host-uid rewrite that 07 recorded on the Mac (`501:20` there, `1001:1002` here).

## Risk 2: the enforced egress topology

**(i) A per-draw `docker network create --internal`, listeners on its gateway: dropped.** The
gateway does get a host address (`172.18.0.1/16` on its `br-*`), and a sibling network's gateway
is unroutable (`Errno 101`). But the host accepts on its gateway address anything bound to
`0.0.0.0`. From a container on the internal network, under both runtimes:

```
OPEN   172.18.0.1 18004 b'hello-ANY\n'          # a 0.0.0.0 listener on the host
OPEN   172.18.0.1 22 b'SSH-2.0-OpenSSH_9.6p'   # the VM's sshd
```

That breaks invariant 3. Passing it would take per-draw `iptables` INPUT rules keyed to
ephemeral ports, which the plan ruled out.

**(ii) `--network none`, per-draw Unix sockets, and an in-container forwarder: kept.**

| check | runc | runsc (`--host-uds=open`) |
|---|---|---|
| `--add-host host.docker.internal:127.0.0.1` with `--network none` | accepted | accepted |
| connect to a 0775 host socket as the container's root (no caps) | **EACCES**: no DAC_OVERRIDE | (not measured at 0775) |
| connect to a 0666 socket on a `:ro` bind mount | OPEN | OPEN, as root and as uid 1000 |
| the forwarder (`sandbox._FORWARDER`, fed to `python3 -`) | ready in 0.16 s, relays | ready in 0.19 s, relays |
| from that container: 1.1.1.1:443, 169.254.169.254:80 (GCE metadata), 10.128.0.1:22, 172.17.0.1:22 | all `Errno 101` | all `Errno 101` |
| interfaces | `lo` | `lo` |
| can the container `bind()` a socket in a **rw** host mount? | — | the bind returns OK, but no socket appears on the host: it lives only in the sandbox's view |

So the sockets are 0666 (`egress._enforce`), the mount is read-only (`Sandbox._run_args`), and
`isolate-host` sets `--host-uds=open`. No new baseline binary is needed: `python3` is an
asserted row on every task image E11 measured, alpine included. The proxy stays the decision
point. Its host-alias rule is **narrowed to the paired broker's port** under enforcement,
because through the proxy "the alias, any port" reached every loopback listener on the host.

**Found on the way: the advisory topology reaches the GCE metadata server.** Measured only as
a negative under enforcement (`Errno 101`). A `bridge` container on the VM has a default route,
so on a shared-kernel posture an agent that ignores `HTTP_PROXY` could ask
`169.254.169.254` for the VM's service-account token. That is exactly the residual the advisory
posture already admits (E03: "the route is not blocked"), and the isolated posture removes it.

## Risk 3: 07's OOM tests under runsc

`--memory 64m`, a 512 MiB `bytearray`:

| | runc | runsc |
|---|---|---|
| the OOMing exec | exit **137** in 0.14 s; container still running, `OOMKilled=true` | exit **128** in 0.17 s, `waiting on pid 2 ... urpc method "containerManager.WaitPID" failed: EOF`; container **exited**, `OOMKilled=true` |
| a later `timeout -s KILL 3 sleep 10` in the same container | 137 | impossible: `container ... is not running` |
| a swallowed OOM (`python3 ...; echo swallowed; exit 0`) | exit 0, `swallowed` | exit 128: the child's OOM ends the sandbox |
| a plain timeout, no OOM | 137, container running | 137, container running |

gVisor accounts memory for the whole sandbox, so an OOM ends the container. `Sandbox.exec()`
therefore gained a runsc branch: a code ≥ 128 (other than 124/137) is checked against the
**dead** container's `.State.OOMKilled`, and reported as out of memory. 07's two objections to
the flag, stickiness and a swallowed OOM, are about a container that keeps running, and a dead
one cannot. The wall-clock discriminator for 137 is unchanged. The three OOM tests in
`tests/test_container_hardening.py` branch on `HostConfig.Runtime` and cite this section. On
runsc, "a later timeout" and "a swallowed OOM" cannot happen, so those branches assert that the
sandbox is gone.

## Risk 4 and kernel-surface drift

**No verdict moved.** Under `OUTCOME_ISOLATION=required` on runsc,
`test_replay_equivalence.py`, `test_verdict_corpus.py`, `test_oracle_determinism.py` and
`test_grader_pinning.py` passed **unmodified**, with no fixture re-recorded.
`test_agent_replay.py` skipped as it did before this spec ("draw run dirs not on this machine",
3/3: `runs/` is gitignored and `scripts/suite` does not copy it). That skip is in C20's count
of 9 as well. On the Mac, with its recorded draws present, it passed with `CAP_ADD=()` and the
exec copy.

What moved, all found before the full run and each changed with a reason:

| test | why | change |
|---|---|---|
| `test_container_hardening.py::test_default_sandbox_has_no_network` | gVisor has no `/sys/class/net` | reads `/proc/net/dev`, which lists `lo` on both runtimes (measured) |
| `…::test_memory_cap_reports_out_of_memory_not_a_timeout` | runsc: exit 128, not 137 (risk 3) | expects 128 on runsc; the "out of memory" message is asserted on both |
| `…::test_a_later_timeout_is_not_reported_as_an_oom` | runsc: no later exec exists | asserts that the sandbox is gone |
| `…::test_a_swallowed_oom_does_not_poison_a_later_timeout` | runsc cannot swallow an OOM | asserts it surfaces as an OOM |
| `test_runtime_sandbox.py::test_mount_is_visible_both_ways` | not gVisor: **`CAP_ADD=()` on Linux**. Root without DAC_OVERRIDE cannot write into a host-owned 0755 dir | its host dir is 0777, with the reason. The one production writer into a mount, the oracle, now uses `copy_out` |
| `test_egress.py`'s four `bridge` assertions | the posture, by design (AC11) | pinned to the advisory posture; the enforced case added; the E03 real test accepts either topology |

Nothing else in the suite noticed the kernel underneath it.

## Overhead, runc vs runsc (VM)

`python:3.12-slim`, averages:

| | runc | runsc (systrap) | runsc (kvm) |
|---|---|---|---|
| `docker run -d` + one `exec` + `rm -f` | 0.397 s | 0.425 s | 0.439 s |
| one `docker exec true` | 0.065 s | 0.054 s | 0.057 s |
| 2000 × (write, read, delete a file) in one exec | 4.09 s | 23.07 s (**5.6×**) | 19.54 s (4.8×) |
| `python3 -c "import json,email,asyncio,unittest"` | 0.50 s | 0.56 s | 0.70 s |

Container lifecycle costs almost nothing extra. Syscall-heavy file I/O is where gVisor costs
more, as the spec expected.

## The suite, before and after

| | tree | runtime | result | wall time |
|---|---|---|---|---|
| before | main at C20 (`28caeac`) | runc | 1441 passed, 9 skipped | 1991.81 s (33:11) |
| after | this branch (`spec-14-vm-isolation`) | **runsc**, `OUTCOME_ISOLATION=required` | **1475 passed, 9 skipped** | **3156.18 s (52:36)** |
| merged | main + 14 (`52c6faa`: specs 13, 15's SPEC, C22 included) | **runsc**, `OUTCOME_ISOLATION=required` | **1555 passed, 9 skipped** | 3840.23 s (1:04:00) |

That is **+59 %** wall time, and the direction the spec predicted. The per-operation table above
says where it goes: container lifecycle costs about the same, and file-heavy test runs cost
several times more. The 9 skips are the same 9 as before, and none names `scripts/isolate-host`:
every (VM) test ran and passed. `tests/test_isolation.py` alone, on the VM: 33 passed.

## `scripts/isolate-host` (AC14), on the VM

| run | output | exit | `daemon.json` | dockerd `ActiveEnterTimestamp` |
|---|---|---|---|---|
| first | `installing runsc 20260914.0` / `isolated: runsc 20260914.0, default runtime runsc` | 0 | written | unchanged (a reload) |
| second | `already isolated` | 0 | sha256 unchanged | unchanged, 20:10:22 UTC |
| `RUNSC_SHA256` tampered | `runsc sha256 c0f4… != pinned 0000… -- refusing, nothing changed` | 1 | sha256 unchanged | unchanged |

**`runtimes` and `default-runtime` are reloadable** (`systemctl reload docker`, SIGHUP), so the
script never restarts dockerd, and a running suite's containers survive it. **Removing the key
is not a reset:** the first measurement pass removed `default-runtime` and reloaded, and dockerd
still reported `runsc` two seconds later. The measurement then restored `runc` explicitly, so
`--shared` writes `"default-runtime": "runc"` rather than omitting it.

**The loopback DNAT is kept, and now written by the script.** `route_localnet=1` on `docker0`
plus the `PREROUTING -d 172.17.0.1 -j DNAT --to-destination 127.0.0.1` rule are what let a
`bridge` container reach a 127.0.0.1 listener through `host-gateway` on Linux. Its users are
`tests/test_broker.py`'s real tests and E03's advisory topology. No production container is on
`docker0` on an isolated host.

**Transitional while 14 was built (the user's decision), and removed at the merge.** Other
sessions' suites on main's code share this VM, so until the merge `scripts/suite` ran
`isolate-host` inside the lock and `isolate-host --shared` on exit (runc again). From the merge
on, runsc stays the default. A branch that has not merged main since then will find
`test_default_sandbox_has_no_network` and 07's three OOM tests moved under it, as recorded
above.

## What changed in the plan while building

- **`copy_in`/`copy_out` exposed a Linux-only regression that the Mac hid.** With `CAP_ADD=()`,
  the container's root cannot write into a bind-mounted host dir owned by the host user (0755,
  uid 1001). Colima's file sharing maps that away on the Mac. DAC_OVERRIDE had been covering it
  on the VM. The oracle (`test_writer.py`) was the one production writer into a mount, `/hidden`,
  so a live oracle draw on the VM would have failed. It now writes `/hidden` inside its box and
  `copy_out` brings it back: no mount at all, and the files arrive owned by the host user
  instead of root.
- **The spec said "neither call site changes" for `sandbox_kwargs()`, and it holds.** `egress`
  finds its paired broker through `broker.live(run_dir, role)`, a registry keyed the way both
  call sites already enter them. Every fake in the suite keeps its `lambda run_dir, role:` shape.
