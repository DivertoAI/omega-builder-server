# backend/app/services/agent_service.py
from __future__ import annotations

import json
import asyncio
import random
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Iterable, Set, Tuple

from backend.app.core.config import settings
from backend.app.core.progress import start_job
from backend.app.integrations.openai.client import get_openai_client
from backend.app.integrations.agent.tools import openai_tool_specs, dispatch_tool_call
from backend.app.models.spec import OmegaSpec

# Optional import: quality gate (tolerant to absence / signature drift)
try:
    from backend.app.core.quality_gate import run_quality_gate as _run_quality_gate  # type: ignore
except Exception:
    _run_quality_gate = None  # type: ignore

OMEGA_DIR = Path("workspace/.omega")
LAST_RUN_PATH = OMEGA_DIR / "last_run.json"

# ----------------------------
# SYSTEM PROMPTS
# ----------------------------

SYSTEM = """You are Omega Builder Agent.

North Star Goal:
- Adapt this repository to implement/reflect the OmegaSpec or brief as a REAL, runnable, **industry-level app**.
- Final target: apps with 1000+ features, 1000+ assets, fonts, and infra.

Contracts:
- /design/fonts → Google Fonts or custom font files.
- /design/tokens → JSON/YAML for colors, spacing, radius, elevation.
- /design/theme → ThemeData (Flutter) or CSS theme (Web).
- /assets/ → image assets, consistent style, 1000+ scalable.
- /infra/ → docker-compose, env files, CI stubs.
- /adapters/ → env-gated integrations (auth, payments, OCR, telemed, logistics, Firebase).

Rules:
- No scaffolds or placeholders. Write complete, runnable code.
- Use only fs_* tools for file ops.
- Maintain idempotency — reruns must not duplicate content.
- Always finish with fs_glob + fs_diff.

Process:
1. Inspect repo (fs_glob).
2. Apply edits incrementally (fs_patch/fs_write).
3. Validate design/tokens/fonts/theme, assets, adapters, infra.
4. Run fs_diff.
5. FINISH with fs_glob + fs_diff and DONE: summary.
"""

USER_PREFIX = """Repository context:
- Python backend (FastAPI) is running.
- SSE progress bus at /api/stream.
- Workspace root is repo root.
- Spec (OmegaSpec) follows:
"""

KICKOFF_INSTRUCTION = """Plan the app and THEN build it.

1) Inspect structure via fs_glob (backend/**/*.py, workspace/**/*, design/**/*, infra/**/*).
2) Ensure design system, assets, adapters, infra are present.
3) Build app code (Flutter MVVM or runnable web).
4) Run fs_diff and fs_glob.
5) FINISH with DONE: summary.
"""

# ----------------------------
# Helpers
# ----------------------------

def _extract_diff_preview(tool_log: List[Dict[str, Any]], max_chars: int = 4000) -> Optional[str]:
    for entry in reversed(tool_log):
        if entry.get("name") == "fs_diff":
            res = entry.get("result") or {}
            candidate = (
                res.get("diff")
                or res.get("unified")
                or res.get("summary")
                or ""
            )
            out = candidate if isinstance(candidate, str) else json.dumps(res, indent=2)
            if len(out) > max_chars:
                return out[-max_chars:]
            return out
    return None

async def _responses_call_with_retries(client, *, model: str, messages: List[dict],
                                       max_output_tokens: Optional[int], max_attempts: int = 3,
                                       base_delay: float = 0.4) -> str:
    attempt = 0
    while True:
        attempt += 1
        try:
            return client.respond(
                model=model,
                messages=messages,
                max_output_tokens=max_output_tokens,
            ) or ""
        except Exception as e:
            status = getattr(e, "status_code", None)
            retryable = (status == 429) or (isinstance(status, int) and 500 <= status < 600) or (status is None)
            if not retryable or attempt >= max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0.05, 0.25)
            await asyncio.sleep(delay)

def _persist_last_run(payload: Dict[str, Any]) -> None:
    try:
        OMEGA_DIR.mkdir(parents=True, exist_ok=True)
        if isinstance(payload.get("tool_log"), list) and len(payload["tool_log"]) > 200:
            payload["tool_log"] = payload["tool_log"][-200:]
        LAST_RUN_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass

# ----------------------------
# Progress
# ----------------------------

class _Pbar:
    def __init__(self, publish): self.publish, self.cur = publish, 0.0
    async def set(self, value: float, event: str, data: Optional[dict] = None):
        value = max(0.0, min(1.0, value))
        if value > self.cur + 0.0001: self.cur = value
        try: await self.publish(event, data=data or {}, progress=self.cur)
        except Exception: pass
    async def bump(self, delta: float, event: str, data: Optional[dict] = None):
        await self.set(self.cur + delta, event, data)

# ----------------------------
# Main
# ----------------------------

async def adapt_repository_with_agent(
    spec: OmegaSpec,
    dev_instructions: Optional[str] = None,
    *,
    validate_only: bool = False,
    wall_clock_budget_sec: Optional[float] = None,
    per_call_timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    DEFAULT_BUDGET = 60.0
    DEFAULT_TURN_TIMEOUT = 20.0
    budget = float(wall_clock_budget_sec or getattr(settings, "omega_agent_budget_sec", DEFAULT_BUDGET))
    turn_timeout = float(per_call_timeout_sec or getattr(settings, "omega_agent_per_call_timeout_sec", DEFAULT_TURN_TIMEOUT))

    client = get_openai_client()
    tool_log: List[Dict[str, Any]] = []
    touched_paths: List[str] = []
    quality_gate_result: Optional[Dict[str, Any]] = None

    async with start_job("generate", data={"mode": "agent"}) as (job_id, publish):
        pbar = _Pbar(publish)
        await pbar.set(0.05, "agent_boot")
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_PREFIX + spec.model_dump_json(indent=2)},
        ]
        if dev_instructions:
            messages.append({"role": "user", "content": f"DEV_INSTRUCTIONS:\n{dev_instructions.strip()}"})
        messages.append({"role": "user", "content": KICKOFF_INSTRUCTION})

        deadline = time.monotonic() + budget
        loop_steps = 15
        start, per_step = 0.12, (0.92 - 0.12) / loop_steps

        for step in range(1, loop_steps + 1):
            if time.monotonic() > deadline: break
            await pbar.set(start + (step - 1) * per_step, "agent_step", {"step": step})
            try:
                text = await asyncio.wait_for(
                    _responses_call_with_retries(client, model=settings.omega_llm_model,
                                                 messages=messages, max_output_tokens=2048),
                    timeout=turn_timeout,
                )
            except asyncio.TimeoutError:
                tool_log.append({"name": "timeout", "step": step})
                break

            assistant_dict = {"role": "assistant", "content": text}
            messages.append(assistant_dict)
            if isinstance(text, str) and text.strip().upper().startswith("DONE:"):
                await pbar.set(0.92, "agent_declared_done", {"step": step})
                break

        # Finalization
        for pat in ("workspace/**/*", "design/**/*", "infra/**/*", "backend/**/*.py"):
            try:
                res = dispatch_tool_call("fs_glob", {"pattern": pat, "max_matches": 2000})
                tool_log.append({"name": "fs_glob", "args": {"pattern": pat}, "result": res})
            except Exception: pass

        try:
            final_diff = dispatch_tool_call("fs_diff", {"paths": ["workspace", "design", "infra", "backend"]})
            tool_log.append({"name": "fs_diff", "args": {"paths": ["workspace","design","infra","backend"]}, "result": final_diff})
        except Exception: pass

        if _run_quality_gate:
            try: quality_gate_result = _run_quality_gate(spec=spec)
            except Exception as e: quality_gate_result = {"ok": False, "error": str(e)}

        summary = "DONE: Agent run completed."
        if quality_gate_result and isinstance(quality_gate_result, dict) and "ok" in quality_gate_result:
            summary += f" | QUALITY: {'PASS' if quality_gate_result.get('ok') else 'FAIL'}"

        diff_preview = _extract_diff_preview(tool_log)
        _persist_last_run({
            "job_id": job_id,
            "summary": summary,
            "diff_preview": diff_preview,
            "tool_log": tool_log,
            "validate_only": validate_only,
            "quality_gate": quality_gate_result,
        })
        await pbar.set(0.98, "agent_done")

    return {
        "job_id": job_id,
        "summary": summary,
        "tool_log": tool_log,
        "diff_preview": diff_preview,
        "quality_gate": quality_gate_result,
    }