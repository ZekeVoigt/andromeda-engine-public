"""Domain-agnostic types. Nothing here knows about code, repos, or tests-as-files."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

Status = Literal["pass", "fail", "error"]
Verdict = Literal["PASS", "FAIL", "UNKNOWN"]
# "none" (spec 16): no independent grader exists for this acceptance kind -- a `checks`
# task has no SWE-bench gold to compare with. Not an error: `agrees` is None beside it.
GroundTruth = Literal["resolved", "unresolved", "error", "none"]


class Request(BaseModel):
    task_id: str
    text: str


class Check(BaseModel):
    name: str
    # "canary" (spec 18): a planted check that must fail; only ever in Evidence.canaries
    kind: Literal["hidden", "existing", "canary"]
    before: Status
    after: Status
    # "the diff deleted what this test covers": an existing test whose breakage is the change
    # working, not a regression. Defaulted, so every recorded checks.json still deserializes.
    obsoleted: bool = False
    # Spec 13: which rule obsoleted it -- the diff deleted a symbol the test's own code reaches,
    # or reverting one hunk the request needs restores it. None on every check spec 03 marked,
    # so its checks.json still loads; `obsoleted` stays the one flag every consumer reads.
    obsoleted_by: Literal["deleted-symbol", "required-hunk"] | None = None

    @model_validator(mode="after")
    def _a_reason_implies_obsoleted(self) -> "Check":
        if self.obsoleted_by is not None and not self.obsoleted:
            raise ValueError(f"obsoleted_by={self.obsoleted_by!r} on a check that is not obsoleted")
        return self


class Evidence(BaseModel):
    checks: list[Check] = Field(default_factory=list)
    infra_errors: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    # Spec 17: the test-infrastructure paths the graders ignored in the diff, sorted. Defaulted,
    # so every recorded checks.json still deserializes; [] leaves every reason byte-identical.
    ignored: list[str] = Field(default_factory=list)
    # Spec 18: the planted checks that must fail (every one kind "canary"; never in `checks`),
    # and the base test modules whose edits were set aside, sorted. Defaulted for `ignored`'s
    # reason; all-failing canaries and [] leave every reason byte-identical to spec 17's.
    canaries: list[Check] = Field(default_factory=list)
    base_tests: list[str] = Field(default_factory=list)
    # Spec 19: every hidden check was judged on the host against expectations that never
    # entered the box. Defaulted, so every recorded checks.json still deserializes.
    sealed: bool = False

    @model_validator(mode="after")
    def _canaries_apart(self) -> "Evidence":
        if any(c.kind == "canary" for c in self.checks) or any(c.kind != "canary" for c in self.canaries):
            raise ValueError("a canary is only ever in Evidence.canaries, and only a canary is there")
        return self


class OutcomeRecord(BaseModel):
    request: Request
    verdict: Verdict
    reason: str
    evidence: Evidence
    ground_truth: GroundTruth
    agrees: bool | None
    started_at: datetime
    finished_at: datetime
    patch_source: str = ""  # the --patch file path
    oracle_source: str = ""  # the oracle dir, or the sealed checks bundle


class Adapter(Protocol):
    async def gather_evidence(self, request: Request, artifact: str) -> Evidence: ...
