"""Pure verdict rules. No I/O."""

from typing import Literal

from andromeda.outcome.core.types import Check, Evidence, Verdict

# Every oracle draw writes a few hidden tests a correct patch cannot pass, so "any hidden
# failure is FAIL" makes gold patches indistinguishable from broken ones. Spec 02 measured
# where the two populations actually sit (36 cells, 3 tasks x 6 oracles x 2 patches):
#
#   gold patches   worst draw  3 failing of 19 valid  ~= 16%
#   half patches   mildest draw 6 failing of 16 valid ~= 38%
#
# 20% sits in that gap with room on both sides. It is a constant, not an env var: a verdict
# whose threshold varies per machine is not a verdict.
HIDDEN_FAIL_TOLERANCE = 0.20


def decide(evidence: Evidence) -> tuple[Verdict, str]:
    if evidence.infra_errors:
        return "UNKNOWN", f"infra error: {evidence.infra_errors[0]}"

    # Spec 17: a diff's ignored test infrastructure is named on every judged reason, as a
    # forgiven check is; with none, every reason is byte-identical to spec 13's.
    ignored = evidence.ignored
    ign = f"; ignored changes to {len(ignored)} test-infrastructure files: {ignored[0]}" if ignored else ""
    # Spec 18: so are the edited base test modules, graded at base, after 17's suffix.
    base_tests = evidence.base_tests
    bt = f"; {len(base_tests)} edited existing test modules graded at base: {base_tests[0]}" if base_tests else ""

    # Spec 18: a planted check that must fail passed, so the run's report is false; nothing
    # below can be read from it.
    tampered = [c for c in evidence.canaries if c.after == "pass"]
    if tampered:
        return "FAIL", (
            f"tampered: {len(tampered)}/{len(evidence.canaries)} planted checks that must fail passed "
            f"after change: {tampered[0].name}{ign}{bt}"
        )

    # A hidden check only counts if it failed before the change (it tests the new behavior).
    valid = [c for c in evidence.checks if c.kind == "hidden" and c.before == "fail"]
    if not valid:
        return "UNKNOWN", f"no valid hidden checks (none failed on base){ign}{bt}"

    failing = [c for c in valid if c.after != "pass"]
    if len(failing) > HIDDEN_FAIL_TOLERANCE * len(valid):
        return "FAIL", (
            f"{len(failing)}/{len(valid)} valid hidden checks fail after change "
            f"(over the {HIDDEN_FAIL_TOLERANCE:.0%} tolerance): {failing[0].name}{ign}{bt}"
        )

    # An obsoleted check tests something the diff deleted; its breakage is the change working.
    regressions = [
        c for c in evidence.checks
        if c.kind == "existing" and not c.obsoleted and c.before == "pass" and c.after != "pass"
    ]
    if regressions:
        return "UNKNOWN", (
            f"{len(valid) - len(failing)}/{len(valid)} valid hidden checks pass but "
            f"{len(regressions)} existing checks regressed: {regressions[0].name}{ign}{bt}"
        )

    # Spec 13: a regression forgiven by a hunk revert is named, as a tolerated hidden failure is;
    # with none, every reason is byte-identical to spec 03's.
    stale = [c for c in evidence.checks if c.obsoleted_by == "required-hunk"]
    forgiven = f"; {len(stale)} existing checks stale, forgiven by required-hunk: {stale[0].name}" if stale else ""

    # A forgiven hidden failure is still named here: tolerating it must never make it invisible.
    if failing:
        return "PASS", (
            f"{len(valid) - len(failing)}/{len(valid)} valid hidden checks pass, no regressions; "
            f"{len(failing)} failing within the {HIDDEN_FAIL_TOLERANCE:.0%} tolerance: {failing[0].name}"
            f"{forgiven}{ign}{bt}"
        )
    return "PASS", f"all {len(valid)} valid hidden checks pass, no regressions{forgiven}{ign}{bt}"


# C08: where a graded draw sends the gate. decide()'s UNKNOWN is split by cause: an infra error
# or a task with no valid hidden checks is not the agent's to fix (`unverified`), while hidden
# checks that pass over regressed existing ones is (`regressed`), so it is resumed like a FAIL.
Route = Literal["pass", "fail", "regressed", "unverified"]


def _valid(evidence: Evidence) -> list[Check]:
    return [c for c in evidence.checks if c.kind == "hidden" and c.before == "fail"]


def _regressions(evidence: Evidence) -> list[Check]:
    return [
        c for c in evidence.checks
        if c.kind == "existing" and not c.obsoleted and c.before == "pass" and c.after != "pass"
    ]


def _tampered(evidence: Evidence) -> list[Check]:
    """Spec 18: the canaries that passed after the change; decide()'s own rule, for its callers."""
    return [c for c in evidence.canaries if c.after == "pass"]


def route(evidence: Evidence) -> Route:
    verdict, _ = decide(evidence)
    if verdict == "PASS":
        return "pass"
    if verdict == "FAIL":
        return "fail"
    if evidence.infra_errors or not _valid(evidence):
        return "unverified"
    return "regressed"


def feedback(evidence: Evidence) -> str:
    """How much failed, never what: the note a resumed root is told.

    A hidden check's name is the answer key, so only a count leaves here. An existing check is
    the repo's own test, which the agent can already run, so a regressed one is named.
    Never built from decide()'s reason: that names failing[0], a hidden check.
    """
    valid = _valid(evidence)
    failing = [c for c in valid if c.after != "pass"]
    note = f"{len(failing)}/{len(valid)} hidden checks fail"
    regressions = _regressions(evidence)
    if regressions:
        note += "; existing checks regressed: " + ", ".join(c.name for c in regressions)
    # Spec 17: the agent's own paths, never a hidden check's name
    if evidence.ignored:
        note += "; ignored as test infrastructure: " + ", ".join(evidence.ignored)
    # Spec 18: never "canary" -- this is what the next draw reads
    if _tampered(evidence):
        note += "; the checks' results were not trustworthy: code in the diff changed how tests report"
    if evidence.base_tests:
        note += "; edits to existing test modules are not graded (run at base): " + ", ".join(evidence.base_tests)
    return note


# C11: a contribution lands on a task's head only if its combined tree is no worse than the
# head. `hidden_failing` is the count the head keeps (`Landing.hidden_failing`), and the k in
# feedback()'s "k/n": the same valid checks, the same rule.
def hidden_failing(evidence: Evidence) -> int:
    return sum(1 for c in _valid(evidence) if c.after != "pass")


def admits(head: int | None, evidence: Evidence) -> bool:
    """May this judged tree land on a head that fails `head` hidden checks?

    A PASS always does. A FAIL does when it fails no more hidden checks than the head --
    `None` is a head with no count: the base, or a seq landed before C11. A regression or an
    unverified tree never does: nothing that breaks an existing check, or was not judged,
    reaches a head. Nor does a tampered one (spec 18), whatever its count: that count was read
    from a report the diff rewrote."""
    if _tampered(evidence):
        return False
    r = route(evidence)
    if r == "pass":
        return True
    return r == "fail" and (head is None or hidden_failing(evidence) <= head)


# C09: a race's N graded attempts, best first. Route beats everything; within a route the
# cheaper attempt wins (an unknown cost sorts last), then the smaller diff, then the earlier.
_RANK_ORDER: dict[Route, int] = {"pass": 0, "regressed": 1, "fail": 2, "unverified": 3}


def rank(entries: list[tuple[Route, float | None, int]]) -> list[int]:
    """Indices into `entries` -- each (route, cost_usd, diff_bytes) -- best first."""
    def key(i: int) -> tuple:
        r, cost, diff_bytes = entries[i]
        return (_RANK_ORDER[r], cost is None, cost or 0.0, diff_bytes, i)
    return sorted(range(len(entries)), key=key)
