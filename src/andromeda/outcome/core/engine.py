from andromeda.outcome.core.types import Adapter, Evidence, Request, Verdict
from andromeda.outcome.core.verdict import decide


async def verify(request: Request, artifact: str, adapter: Adapter) -> tuple[Verdict, str, Evidence]:
    try:
        evidence = await adapter.gather_evidence(request, artifact)
    except Exception as e:  # any adapter failure is an UNKNOWN, never a crash
        evidence = Evidence(infra_errors=[f"{type(e).__name__}: {e}"])
        return "UNKNOWN", f"evidence gathering failed: {type(e).__name__}: {e}", evidence
    verdict, reason = decide(evidence)  # sync and pure; the async boundary stops here
    return verdict, reason, evidence
