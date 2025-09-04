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
    # Prefer a single entrypoint function if provided
    from backend.app.core.quality_gate import run_quality_gate as _run_quality_gate  # type: ignore
except Exception:  # pragma: no cover - best-effort optional import
    _run_quality_gate = None  # type: ignore

# Where we persist the last run details for quick debugging/observability
OMEGA_DIR = Path("workspace/.omega")
LAST_RUN_PATH = OMEGA_DIR / "last_run.json"


SYSTEM = """You are Omega Builder Agent.

Goal:
- Adapt this repository to implement/reflect the provided OmegaSpec or brief as a REAL, runnable application.
- If the caller hints at a platform/architecture (e.g., target=flutter, architecture=mvvm), that guidance is binding.

Hard rules:
- Do NOT create placeholder scaffolds or skeletal stubs.
- Author complete, runnable code and config for the chosen platform:
  * Flutter (MVVM): full project structure, pubspec, provider state mgmt, shared_preferences, tests.
  * Web (no build): index.html + styles.css + main.js, with real functionality and localStorage persistence.
- Use only the provided filesystem tools: fs_map, fs_glob, fs_read, fs_write, fs_mkdir, fs_delete, fs_diff, fs_patch.
- Prefer minimal, incremental edits that reflect the spec and brief.
- Maintain idempotency: reruns must not duplicate content; patch/replace managed regions.

Process (mandatory):
- Before writing to a file: fs_read it to see current content.
- Use fs_patch for surgical changes; fs_write for new or fully replaced files.
- After any write/patch sequence, run fs_diff to inspect changes you just made.
- ALWAYS FINISH by:
  1) Running fs_glob on expected output paths (project root / app dir).
  2) Running fs_diff to emit a final repo diff.
- Never duplicate content on reruns; replace/patch the same managed blocks instead.

Completion:
- When you believe you’re done, reply with a short plain-text summary starting with: DONE:
"""

USER_PREFIX = """Repository context:
- Python backend (FastAPI) already running.
- SSE progress bus available at /api/stream.
- Workspace root is this repo root.

Specification (OmegaSpec JSON or brief context follows):
"""

KICKOFF_INSTRUCTION = """Plan the app and THEN build it.

1) Inspect repo structure via fs_glob (backend/**/*.py, workspace/**/*, assets/**/*, pyproject.toml, README.md).
2) Determine the target platform and architecture from the latest user turn:
   - If dev instructions specify Flutter MVVM, create a full Flutter app (pubspec, lib/*, tests).
   - Else build a runnable web app with index.html/styles.css/main.js and actual feature logic.
3) Create necessary directories and files. Use fs_write for new files, fs_patch for edits.
4) After writing, run fs_diff to verify changes.
5) FINISH with fs_glob on the created app directory and a final fs_diff snapshot.
"""


# ---------------------------- helpers ----------------------------

def _assistant_message_to_dict(msg) -> Dict[str, Any]:
    """
    Normalize an SDK ChatCompletionMessage to a plain dict suitable for the next call.
    """
    tool_calls = []
    if getattr(msg, "tool_calls", None):
        for tc in msg.tool_calls:
            tool_calls.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        # Chat Completions returns arguments as a JSON string:
                        "arguments": tc.function.arguments,
                    },
                }
            )
    return {"role": msg.role, "content": msg.content, "tool_calls": tool_calls or None}


def _extract_diff_preview(tool_log: List[Dict[str, Any]], max_chars: int = 4000) -> Optional[str]:
    """
    Pull the latest fs_diff result and return a trimmed preview.
    """
    for entry in reversed(tool_log):
        if entry.get("name") == "fs_diff":
            res = entry.get("result") or {}
            candidate = (
                res.get("diff")
                or res.get("patch")
                or res.get("content")
                or res.get("output")
                or res.get("text")
                or res.get("unified")
                or res.get("summary")
                or ""
            )
            if isinstance(candidate, str) and candidate.strip():
                out = candidate
            else:
                try:
                    out = json.dumps(res, ensure_ascii=False, indent=2)
                except Exception:
                    out = str(res)
            if len(out) > max_chars:
                return out[-max_chars:]
            return out
    return None


def _tool_names(tools: Iterable[dict]) -> Set[str]:
    names: Set[str] = set()
    for t in tools or []:
        # Expect {"type":"function","function":{"name": "...", ...}}
        fn = t.get("function", {})
        name = fn.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


async def _warn_if_missing_tools(publish, tools: List[dict], required: Iterable[str]) -> None:
    have = _tool_names(tools)
    missing = [r for r in required if r not in have]
    if missing:
        try:
            await publish(
                "agent_tool_shape",
                data={"warning": f"Missing required tools: {', '.join(missing)}"},
                progress=None,
            )
        except Exception:
            pass


async def _chat_with_retries(client, kwargs: Dict[str, Any], max_attempts: int = 3, base_delay: float = 0.4):
    """
    Tiny retry helper for Chat Completions.
    Retries on 429 and 5xx; jitter added. Synchronous OpenAI call inside.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return client._client.chat.completions.create(**kwargs)
        except Exception as e:
            status = getattr(e, "status_code", None)
            retryable = (status == 429) or (isinstance(status, int) and 500 <= status < 600)
            if not retryable or attempt >= max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0.05, 0.25)
            await asyncio.sleep(delay)


def _sec_remaining(deadline_mono: float) -> float:
    return max(0.0, deadline_mono - time.monotonic())


def _persist_last_run(payload: Dict[str, Any]) -> None:
    """
    Best-effort persist of the last run. Trims very large tool logs.
    """
    try:
        OMEGA_DIR.mkdir(parents=True, exist_ok=True)
        trimmed = dict(payload)
        tl = trimmed.get("tool_log")
        if isinstance(tl, list) and len(tl) > 200:
            trimmed["tool_log"] = tl[-200:]  # keep the last 200 entries
        LAST_RUN_PATH.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # non-fatal
        pass


# ---------------------------- progress helpers ----------------------------

class _Pbar:
    """
    Simple progress allocator across phases:
      boot:         0.00 - 0.12
      agent loop:   0.12 - 0.92
      finalization: 0.92 - 0.98
      wrap-up:      0.98 - 1.00
    """

    def __init__(self, publish):
        self.publish = publish
        self.cur = 0.0

    async def set(self, value: float, event: str, data: Optional[dict] = None) -> None:
        value = max(0.0, min(1.0, value))
        if value > self.cur + 0.0001:  # avoid spam
            self.cur = value
        try:
            await self.publish(event, data=data or {}, progress=self.cur)
        except Exception:
            # never fail the run due to SSE hiccups
            pass

    async def bump(self, delta: float, event: str, data: Optional[dict] = None) -> None:
        await self.set(self.cur + delta, event, data)


def _loop_progress_fn(steps: int) -> Tuple[float, float]:
    """
    Return (base, per_step) allocation for the agent loop range 0.12..0.92 (~80%).
    """
    start, end = 0.12, 0.92
    total = end - start
    per_step = total / max(steps, 1)
    return start, per_step


# ---------------------------- quality gate integration ----------------------------

async def _maybe_run_quality_gate(
    spec: OmegaSpec,
    pbar: _Pbar,
    validate_only: bool,
) -> Optional[Dict[str, Any]]:
    """
    Attempts to run the optional quality gate and return a normalized dict result.
    Emits SSE progress events. Never raises.
    """
    try:
        await pbar.set(0.945, "quality_gate_start", {"validate_only": validate_only})
    except Exception:
        pass

    if _run_quality_gate is None:
        try:
            await pbar.bump(0.002, "quality_gate_missing", {"reason": "module_or_symbol_not_importable"})
        except Exception:
            pass
        return None

    # Try a few common signatures to be resilient against minor API drift.
    raw_result: Any = None
    try:
        try:
            raw_result = _run_quality_gate(spec=spec, validate_only=validate_only)  # type: ignore[arg-type]
        except TypeError:
            try:
                raw_result = _run_quality_gate(spec=spec)  # type: ignore[misc]
            except TypeError:
                raw_result = _run_quality_gate()  # type: ignore[misc]
    except Exception as e:
        try:
            await pbar.bump(0.003, "quality_gate_error", {"error": str(e)})
        except Exception:
            pass
        return {"ok": False, "error": str(e)}

    # Normalize to a dict
    if raw_result is None:
        result = {"ok": False, "error": "quality gate returned None"}
    elif isinstance(raw_result, dict):
        result = raw_result
    else:
        result = {
            "ok": bool(getattr(raw_result, "ok", False) or getattr(raw_result, "passed", False)),
            "summary": getattr(raw_result, "summary", None),
            "errors": getattr(raw_result, "errors", None),
            "checks": getattr(raw_result, "checks", None),
        }

    try:
        await pbar.bump(0.005, "quality_gate_done", {"ok": result.get("ok", None)})
    except Exception:
        pass
    return result


# ---------------------------- main loop ----------------------------

async def adapt_repository_with_agent(
    spec: OmegaSpec,
    dev_instructions: Optional[str] = None,
    *,
    validate_only: bool = False,
    wall_clock_budget_sec: Optional[float] = None,
    per_call_timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Orchestrates a Chat Completions function-calling loop to apply edits described by the spec.
    Returns a summary, a log of tool_call results, a diff preview, the job_id, and (if available) quality gate results.

    Behavior:
      - validate_only: run read/plan/diff but DO NOT mutate the filesystem (fs_write/fs_patch/fs_mkdir/fs_delete are skipped).
      - wall_clock_budget_sec: hard wall-clock budget for the entire run (default 60s).
      - per_call_timeout_sec: per ChatCompletion turn timeout (default 20s).
      - Forced finalization ensures fs_glob/fs_diff, quality gate attempt, and an agent_summary are emitted.
      - Persists last run to workspace/.omega/last_run.json for quick debugging.
    """
    # Defaults (overridable via settings)
    DEFAULT_BUDGET = 60.0
    DEFAULT_TURN_TIMEOUT = 20.0
    budget = float(wall_clock_budget_sec or getattr(settings, "omega_agent_budget_sec", DEFAULT_BUDGET))
    turn_timeout = float(per_call_timeout_sec or getattr(settings, "omega_agent_per_call_timeout_sec", DEFAULT_TURN_TIMEOUT))

    client = get_openai_client()
    tools = openai_tool_specs()  # list of {"type": "function", "function": {...}}
    tool_log: List[Dict[str, Any]] = []
    job_id_seen: Optional[str] = None

    # For summary after finalization
    touched_paths: List[str] = []
    WRITEY = {"fs_write", "fs_patch", "fs_mkdir", "fs_delete"}

    # Quality gate result we will surface
    quality_gate_result: Optional[Dict[str, Any]] = None

    async with start_job("generate", data={"mode": "agent"}) as (job_id, publish):
        job_id_seen = job_id
        pbar = _Pbar(publish)

        # --- Boot / setup (0.00 → 0.12)
        await pbar.set(0.02, "agent_job_started", {"job_id": job_id})
        await pbar.set(0.05, "agent_boot")
        try:
            first_tool = tools[0] if tools else {}
            await pbar.set(0.06, "agent_tool_shape", {"tool0": first_tool})
            await pbar.set(0.10, "agent_bootstrap", {"count": len(tools)})
            await _warn_if_missing_tools(publish, tools, required=("fs_diff", "fs_patch"))
        except Exception:
            pass
        await pbar.set(0.12, "agent_ready")

        # Seed conversation
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_PREFIX + spec.model_dump_json(indent=2)},
        ]
        if dev_instructions and isinstance(dev_instructions, str) and dev_instructions.strip():
            messages.append(
                {
                    "role": "user",
                    "content": f"DEV_INSTRUCTIONS (high priority for this run):\n{dev_instructions.strip()}",
                }
            )
        messages.append({"role": "user", "content": KICKOFF_INSTRUCTION})

        # Budget tracking
        deadline = time.monotonic() + budget

        # --- Agent loop (0.12 → 0.92)
        loop_steps = 13  # bounded for safety
        base, per_step = _loop_progress_fn(loop_steps)

        try:
            for step in range(1, loop_steps + 1):
                remaining = _sec_remaining(deadline)
                if remaining <= 0.0:
                    await pbar.set(0.93, "agent_budget_exhausted", {"at_step": step})
                    break

                # Small pre-step nudge so UI moves before the model call
                await pbar.set(min(base + (step - 1) * per_step + 0.01, 0.90), "agent_step_enter", {"step": step})

                create_kwargs: Dict[str, Any] = {
                    "model": settings.omega_llm_model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                }

                per_turn_budget = max(1.0, min(turn_timeout, max(1.0, remaining - 1.0)))

                try:
                    resp = await asyncio.wait_for(
                        _chat_with_retries(client, create_kwargs, max_attempts=3, base_delay=0.4),
                        timeout=per_turn_budget,
                    )
                except asyncio.TimeoutError:
                    tool_log.append(
                        {
                            "name": "model_call_timeout",
                            "args": {"per_call_timeout_sec": per_turn_budget, "step": step},
                            "result": {"ok": False, "error": "chat.completions timeout"},
                        }
                    )
                    await pbar.bump(0.005, "agent_turn_timeout", {"step": step, "timeout_sec": per_turn_budget})
                    break

                assistant_msg = resp.choices[0].message
                assistant_dict = _assistant_message_to_dict(assistant_msg)
                messages.append(assistant_dict)

                # Execute tool calls (if any)
                if assistant_dict.get("tool_calls"):
                    # Progress slice for this step
                    # We’ll distribute a few tiny bumps within the step while tools run
                    in_step_bump = min(per_step * 0.6, 0.03)  # cap per tool-burst bump
                    for tc in assistant_dict["tool_calls"]:
                        name = tc["function"]["name"]
                        raw_args = tc["function"].get("arguments") or "{}"
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        except Exception:
                            args = {}

                        if validate_only and name in WRITEY:
                            result = {
                                "ok": True,
                                "skipped": True,
                                "reason": "validate_only",
                                "tool": name,
                                "args": args,
                            }
                        else:
                            result = dispatch_tool_call(name, args)

                        tool_log.append({"name": name, "args": args, "result": result})

                        short_path = args.get("path") or args.get("root") or args.get("pattern") or ""
                        await pbar.bump(in_step_bump, "agent_tool_result", {"tool": name, "path": short_path})

                        if (not validate_only) and name in WRITEY:
                            p = args.get("path") or args.get("pattern") or args.get("root")
                            if isinstance(p, str) and p:
                                touched_paths.append(p)

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": json.dumps(result, ensure_ascii=False),
                            }
                        )

                    # Nudge at end of tools for this step
                    await pbar.set(min(base + step * per_step, 0.92), "agent_step_done", {"step": step})
                    # Let model react to tools next turn
                    continue

                # No tool calls; check for DONE sentinel
                text_out: str = assistant_dict.get("content") or ""
                if isinstance(text_out, str) and text_out.strip().upper().startswith("DONE:"):
                    await pbar.set(0.92, "agent_declared_done", {"step": step})
                    break

                # If model produced plain text but not DONE, advance a little
                await pbar.bump(min(per_step * 0.3, 0.02), "agent_text_only", {"step": step})

        finally:
            # --- Finalization (0.92 → 0.98)
            # Quick existence checks (non-fatal)
            for pat in ("workspace/**/*", "backend/**/*.py"):
                try:
                    res = dispatch_tool_call("fs_glob", {"pattern": pat, "max_matches": 2000})
                    tool_log.append({"name": "fs_glob", "args": {"pattern": pat, "max_matches": 2000}, "result": res})
                    await pbar.bump(0.01, "agent_tool_result", {"tool": "fs_glob", "path": pat})
                except Exception:
                    pass

            # Ensure we have a final diff snapshot even if the model didn't call fs_diff
            already_had_diff = any(e.get("name") == "fs_diff" for e in tool_log)
            if not already_had_diff:
                unique = sorted({p for p in touched_paths if isinstance(p, str) and p})
                candidate_paths: List[str] = []
                for p in unique:
                    head = p.split("/", 1)[0]
                    if head:
                        candidate_paths.append(head)
                if not candidate_paths:
                    candidate_paths = ["workspace", "backend"]
                candidate_paths = sorted({p for p in candidate_paths})

                try:
                    final_diff = dispatch_tool_call("fs_diff", {"paths": candidate_paths})
                    tool_log.append({"name": "fs_diff", "args": {"paths": candidate_paths}, "result": final_diff})
                    await pbar.bump(0.02, "agent_tool_result", {"tool": "fs_diff", "path": ", ".join(candidate_paths)})
                except Exception:
                    pass

            # Run the quality gate (best-effort; tolerant to absence or exceptions)
            try:
                quality_gate_result = await _maybe_run_quality_gate(spec, pbar, validate_only)
            except Exception:
                quality_gate_result = {"ok": False, "error": "quality gate wrapper failure"}

            # Build summary & persist
            unique_touched = sorted({p for p in touched_paths if isinstance(p, str) and p})
            summary = (
                "DONE: Updated files/folders -> "
                + ", ".join(unique_touched[:12])
                + ("..." if len(unique_touched) > 12 else "")
                if unique_touched
                else "DONE: Agent run completed."
            )

            # Annotate summary with quality signal if available
            if isinstance(quality_gate_result, dict) and ("ok" in quality_gate_result):
                q_ok = bool(quality_gate_result.get("ok"))
                q_errors = quality_gate_result.get("errors")
                n_err = (
                    len(q_errors)
                    if isinstance(q_errors, (list, tuple))
                    else int(quality_gate_result.get("fail_count") or 0)
                )
                qual_tag = f"QUALITY: {'PASS' if q_ok else 'FAIL'}"
                if not q_ok:
                    qual_tag += f" ({n_err} issue{'s' if n_err != 1 else ''})"
                summary = f"{summary} | {qual_tag}"

            diff_preview = _extract_diff_preview(tool_log)

            _persist_last_run(
                {
                    "job_id": job_id_seen,
                    "summary": summary,
                    "diff_preview": diff_preview,
                    "tool_log": tool_log,
                    "validate_only": validate_only,
                    "quality_gate": quality_gate_result,
                }
            )

            await pbar.set(0.97, "agent_summary", {
                "touched": unique_touched,
                "has_diff": bool(diff_preview),
                "quality": (quality_gate_result or {}).get("ok") if isinstance(quality_gate_result, dict) else None,
            })
            await pbar.set(0.98, "agent_done")

    # --- Wrap-up (0.98 → 1.00 on client side)
    base_summary = (
        "DONE: Updated files/folders -> "
        + ", ".join(sorted({p for p in touched_paths})[:12])
        + ("..." if len(set(touched_paths)) > 12 else "")
        if touched_paths
        else "DONE: Agent run completed."
    )
    if isinstance(quality_gate_result, dict) and ("ok" in quality_gate_result):
        q_ok = bool(quality_gate_result.get("ok"))
        q_errors = quality_gate_result.get("errors")
        n_err = (
            len(q_errors)
            if isinstance(q_errors, (list, tuple))
            else int(quality_gate_result.get("fail_count") or 0)
        )
        qual_tag = f"QUALITY: {'PASS' if q_ok else 'FAIL'}"
        if not q_ok:
            qual_tag += f" ({n_err} issue{'s' if n_err != 1 else ''})"
        base_summary = f"{base_summary} | {qual_tag}"

    return {
        "job_id": job_id_seen,
        "summary": base_summary,
        "tool_log": tool_log,
        "diff_preview": _extract_diff_preview(tool_log),
        "quality_gate": quality_gate_result,
    }