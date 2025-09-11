# backend/app/workers/worker.py
from __future__ import annotations

import asyncio
import os
import traceback
from typing import Any, Dict, Optional

from backend.app.core.progress import start_job
from backend.app.services.job_store import put_job
from backend.app.services.agent_service import adapt_repository_with_agent
from backend.app.models.spec import OmegaSpec
from backend.app.core.config import settings


def _compose_dev_instructions(
    user_dev_instructions: Optional[str],
    *,
    allow_images: bool,
    allow_codegen: bool,
) -> str:
    """
    Prefix guardrails so the agent won't spam-costly tools (image/gen) unless explicitly enabled.
    """
    rails: list[str] = [
        "You are running inside Omega Builder's cost-guarded worker.",
        "Follow these hard rules:",
        # Codegen
        ("- Code generation is ALLOWED." if allow_codegen else "- Code generation is DISABLED. Only analyze/validate."),
        # Images
        ("- DO NOT call any image generation or editing endpoints." if not allow_images
         else "- Image generation is allowed but must respect the per-run budget and step/time limits."),
        "- Never loop. Never self-retry. If a tool fails once, surface the error and stop.",
        "- Prefer dry-run reasoning and diff planning before writing.",
        "- Keep outputs small; avoid giant assets and binaries.",
        "- Write deterministic files; avoid timestamps or nonces in code.",
    ]
    user = (user_dev_instructions or "").strip()
    if user:
        rails += ["", "User developer instructions (verbatim):", user]
    return "\n".join(rails)


def _effective(value: Optional[float], fallback: float) -> float:
    try:
        if value is None:
            return float(fallback)
        return float(value)
    except Exception:
        return float(fallback)


def run_generate_task(
    spec_dict: Dict[str, Any],
    dev_instructions: Optional[str] = None,
    validate_only: bool = False,
    wall_clock_budget_sec: Optional[float] = None,
    per_call_timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Entrypoint called by the job runner (RQ-like). Synchronous wrapper that internally
    runs an async flow. Adds strict guardrails to prevent runaway spend.

    Guardrails in place:
      - Global kill-switch via env/Settings (omega_allow_code_generation / omega_allow_images).
      - Per-run wall clock budget + per-call timeout (defaults from Settings).
      - No image generation when disallowed; injected into dev_instructions.
      - Always records job status transitions in Redis via job_store.
    """
    # Initial queue marker (legacy convention)
    put_job("pending", "queued", {"note": "worker enqueued"})

    # Settings / kill-switches
    allow_codegen = bool(settings.omega_allow_code_generation)
    allow_images = bool(settings.omega_allow_images)
    if os.getenv("OMEGA_KILLSWITCH", "").lower() in {"1", "true", "on"}:
        allow_codegen = False
        allow_images = False

    # If validate_only is True, force-disable codegen/images regardless of config.
    if validate_only:
        allow_codegen = False
        allow_images = False

    # Effective budgets/timeouts
    wall_budget = _effective(
        wall_clock_budget_sec,
        settings.omega_wall_clock_budget_sec if hasattr(settings, "omega_wall_clock_budget_sec") else 300.0,
    )
    call_timeout = _effective(
        per_call_timeout_sec,
        settings.omega_per_call_timeout_sec if hasattr(settings, "omega_per_call_timeout_sec") else 60.0,
    )

    # Compose guarded dev instructions passed to the agent
    dev_note = _compose_dev_instructions(
        dev_instructions,
        allow_images=allow_images,
        allow_codegen=allow_codegen,
    )

    result: Dict[str, Any] = {}

    async def _run() -> None:
        nonlocal result
        # Progress context: publishes start/updates/completion to Redis pub/sub
        async with start_job("generate", data={
            "mode": "agent",
            "validate_only": validate_only,
            "allow_codegen": allow_codegen,
            "allow_images": allow_images,
            "wall_budget_sec": wall_budget,
            "per_call_timeout_sec": call_timeout,
        }) as (job_id, publish):
            # Transition to running once we have a concrete job_id
            put_job(job_id, "running", {"note": "agent started"})
            await publish({"stage": "init", "message": "Worker started", "job_id": job_id})

            # Hard-stop if codegen fully disabled and not a validate request
            if not allow_codegen and not validate_only:
                msg = "Global code generation is disabled by configuration."
                put_job(job_id, "blocked", {"reason": msg})
                result.update({"status": "blocked", "reason": msg, "job_id": job_id})
                await publish({"stage": "blocked", "message": msg})
                return

            # Build the runtime spec
            spec = OmegaSpec(**spec_dict)

            # Invoke the agent with strict budgets/timeouts
            try:
                res = await adapt_repository_with_agent(
                    spec,
                    dev_instructions=dev_note,
                    validate_only=validate_only or (not allow_codegen),
                    wall_clock_budget_sec=wall_budget,
                    per_call_timeout_sec=call_timeout,
                    # Some agent impls respect these kwargs; ignore if unknown
                    allow_images=allow_images,
                    allow_codegen=allow_codegen,
                )
            except Exception as e:
                tb = traceback.format_exc()
                await publish({"stage": "error", "message": str(e)})
                put_job(job_id, "fail", {"error": str(e), "traceback": tb})
                # Propagate after recording (RQ will log too)
                raise

            # Success bookkeeping
            result = res or {}
            result.setdefault("status", "ok")
            result.setdefault("job_id", job_id)

            # Persist a concise summary for dashboards
            put_job(job_id, "ok", {
                "summary": result.get("summary"),
                "diff_preview": result.get("diff_preview"),
                "dir": result.get("dir"),
                "files_written": result.get("files_written"),
            })
            await publish({"stage": "done", "message": "Generation complete", "status": "ok"})

    try:
        asyncio.run(_run())
    except Exception:
        # We already stored failure above whenever possible; add a last-resort record.
        put_job("unknown", "fail", {"traceback": traceback.format_exc()})
        # Re-raise to allow the queue system to mark the job as failed
        raise

    return result