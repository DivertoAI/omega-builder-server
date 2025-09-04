from __future__ import annotations
import traceback
from typing import Any, Dict, Optional

from backend.app.core.progress import start_job
from backend.app.services.job_store import put_job
from backend.app.services.agent_service import adapt_repository_with_agent
from backend.app.models.spec import OmegaSpec

def run_generate_task(spec_dict: Dict[str, Any],
                      dev_instructions: Optional[str] = None,
                      validate_only: bool = False,
                      wall_clock_budget_sec: Optional[float] = None,
                      per_call_timeout_sec: Optional[float] = None) -> Dict[str, Any]:
    """
    RQ calls this in a background worker process.
    """
    # Mark queued->running (we don't know job_id yet; start_job will create one)
    put_job("pending", "queued", {"note": "rq enqueued"})

    # Run the agent inside start_job so progress flows to Redis pub/sub
    result: Dict[str, Any] = {}
    async def _run():
        nonlocal result
        async with start_job("generate", data={"mode": "agent"}) as (job_id, publish):
            # job became known
            put_job(job_id, "running", {"note": "agent started"})
            spec = OmegaSpec(**spec_dict)
            res = await adapt_repository_with_agent(
                spec,
                dev_instructions=dev_instructions,
                validate_only=validate_only,
                wall_clock_budget_sec=wall_clock_budget_sec,
                per_call_timeout_sec=per_call_timeout_sec,
            )
            result = res
            # store summary
            put_job(job_id, "ok", {
                "summary": res.get("summary"),
                "diff_preview": res.get("diff_preview"),
            })

    # Run the async coroutine in a fresh loop (RQ is sync)
    import asyncio
    try:
        asyncio.run(_run())
    except Exception:
        # We cannot publish here (loop ended), but we can store failure
        put_job("unknown", "fail", {"traceback": traceback.format_exc()})
        raise
    return result