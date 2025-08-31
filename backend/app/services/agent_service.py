from __future__ import annotations

import json
import asyncio
import random
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Iterable, Set

from backend.app.core.config import settings
from backend.app.core.progress import start_job
from backend.app.integrations.openai.client import get_openai_client
from backend.app.integrations.agent.tools import openai_tool_specs, dispatch_tool_call
from backend.app.models.spec import OmegaSpec

# Where we persist the last run details for quick debugging/observability
OMEGA_DIR = Path("workspace/.omega")
LAST_RUN_PATH = OMEGA_DIR / "last_run.json"


SYSTEM = """You are Omega Builder Agent.

Goal:
- Adapt this repository to implement/reflect the provided OmegaSpec.

Hard rules:
- DO NOT create placeholder mocks or fake assets.
- Use only the provided filesystem tools: fs_map, fs_glob, fs_read, fs_write, fs_mkdir, fs_delete, fs_diff, fs_patch.
- Prefer minimal, incremental edits that make the app skeleton reflect the spec (routes, stubs, TODOs).
- Keep changes self-contained and compile-friendly; add TODO comments instead of stubbing external deps.
- Maintain idempotency: reruns must not duplicate content.
- When you need repo context, CALL TOOLS. Do not guess file contents.

Mini playbook (very important):
- Before writing to a file: fs_read it to see current content.
- Prefer fs_patch for small or surgical changes; use fs_write for whole-file writes only when needed.
- After any write/patch sequence, run fs_diff to inspect the changes you just made.
- Always FINISH by:
  1) Running fs_glob on a few expected paths to verify outputs exist (e.g., main routes, workspace docs).
  2) Running fs_diff to emit a final repo diff.
- Never duplicate content on reruns; replace/patch the same managed blocks instead.

Completion:
- When you believe you’re done, reply with a short plain-text summary starting with: DONE:
"""

USER_PREFIX = """Repository context:
- Python backend (FastAPI) already running.
- SSE progress bus available at /api/stream.
- Workspace root is this repo root.

Specification (OmegaSpec JSON):
"""

KICKOFF_INSTRUCTION = """First, inspect the repo structure. Call fs_glob with each of these patterns (one call per pattern), then selectively fs_read interesting files:

- "backend/**/*.py"
- "workspace/**/*"
- "assets/**/*"
- "pyproject.toml"
- "README.md"

After inspection, plan minimal edits to reflect navigation and endpoints from the spec. Then perform edits using fs_write/fs_mkdir/fs_patch as needed.

Edits priority (do in order, skipping any that already exist):
1) Ensure backend/main.py includes plan & generate routes and SSE.
2) Create/patch a minimal frontend/workspace README with routes reflecting spec.navigation.
3) Add TODO stubs in appropriate places (no mock data).
4) Keep changes idempotent. If a file already matches, do nothing.

Remember to fs_diff after modifying files, and finish with a final fs_diff + fs_glob checks.
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
    Returns a summary, a log of tool_call results, a diff preview, and the job_id.

    Behavior:
      - validate_only: run read/plan/diff but DO NOT mutate the filesystem (fs_write/fs_patch/fs_mkdir/fs_delete are skipped).
      - wall_clock_budget_sec: hard wall-clock budget for the entire run (default 60s).
      - per_call_timeout_sec: per ChatCompletion turn timeout (default 20s).
      - Forced finalization ensures fs_glob/fs_diff and an agent_summary are emitted.
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

    async with start_job("generate", data={"mode": "agent"}) as (job_id, publish):
        job_id_seen = job_id
        await publish("agent_boot", progress=0.05)

        # Helpful telemetry
        try:
            first_tool = tools[0] if tools else {}
            await publish("agent_tool_shape", data={"tool0": first_tool}, progress=0.06)
            await publish("agent_bootstrap", data={"count": len(tools)}, progress=0.12)
            await _warn_if_missing_tools(publish, tools, required=("fs_diff", "fs_patch"))
        except Exception:
            pass

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

        # Always-finalize block
        try:
            # bounded loop for safety
            for step in range(1, 14):
                # Budget check before making another turn
                remaining = _sec_remaining(deadline)
                if remaining <= 0.0:
                    try:
                        await publish("agent_budget_exhausted", data={"at_step": step}, progress=0.93)
                    except Exception:
                        pass
                    break

                await publish(f"agent_step_{step}", progress=min(0.14 + step * 0.06, 0.92))

                create_kwargs: Dict[str, Any] = {
                    "model": settings.omega_llm_model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                }

                # Per-call timeout can't exceed remaining wall-clock by too much
                per_turn_budget = max(1.0, min(turn_timeout, max(1.0, remaining - 1.0)))

                try:
                    resp = await asyncio.wait_for(
                        _chat_with_retries(client, create_kwargs, max_attempts=3, base_delay=0.4),
                        timeout=per_turn_budget,
                    )
                except asyncio.TimeoutError:
                    # Record timeout and stop the loop; finalization will still run.
                    tool_log.append(
                        {
                            "name": "model_call_timeout",
                            "args": {"per_call_timeout_sec": per_turn_budget, "step": step},
                            "result": {"ok": False, "error": "chat.completions timeout"},
                        }
                    )
                    try:
                        await publish("agent_turn_timeout", data={"step": step, "timeout_sec": per_turn_budget})
                    except Exception:
                        pass
                    break

                assistant_msg = resp.choices[0].message

                # Record assistant message
                assistant_dict = _assistant_message_to_dict(assistant_msg)
                messages.append(assistant_dict)

                # Execute tool calls (if any) — reply with a single tool result message per tool_call_id
                if assistant_dict.get("tool_calls"):
                    for tc in assistant_dict["tool_calls"]:
                        name = tc["function"]["name"]
                        raw_args = tc["function"].get("arguments") or "{}"
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        except Exception:
                            args = {}

                        # Run (or skip) tool
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
                        try:
                            await publish("agent_tool_result", data={"tool": name, "path": short_path})
                        except Exception:
                            pass

                        # Track touched paths for summary
                        if (not validate_only) and name in WRITEY:
                            p = args.get("path") or args.get("pattern") or args.get("root")
                            if isinstance(p, str) and p:
                                touched_paths.append(p)

                        # Link result back with tool_call_id
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": json.dumps(result, ensure_ascii=False),
                            }
                        )
                    # Let the model react to tool outputs
                    continue

                # No tool calls; check for DONE sentinel
                text_out: str = assistant_dict.get("content") or ""
                if isinstance(text_out, str) and text_out.strip().upper().startswith("DONE:"):
                    break

        finally:
            # ---------------- Forced Finalization ----------------
            # Quick existence checks (non-fatal)
            for pat in ("workspace/**/*", "backend/**/*.py"):
                try:
                    res = dispatch_tool_call("fs_glob", {"pattern": pat, "max_matches": 2000})
                    tool_log.append({"name": "fs_glob", "args": {"pattern": pat, "max_matches": 2000}, "result": res})
                    try:
                        await publish("agent_tool_result", data={"tool": "fs_glob", "path": pat})
                    except Exception:
                        pass
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
                    try:
                        await publish("agent_tool_result", data={"tool": "fs_diff", "path": ", ".join(candidate_paths)})
                    except Exception:
                        pass
                except Exception:
                    pass

            # Build and publish summary (always)
            unique_touched = sorted({p for p in touched_paths if isinstance(p, str) and p})
            summary = (
                "DONE: Updated files/folders -> "
                + ", ".join(unique_touched[:12])
                + ("..." if len(unique_touched) > 12 else "")
                if unique_touched
                else "DONE: Agent run completed."
            )
            diff_preview = _extract_diff_preview(tool_log)

            # Persist last run (trimmed)
            _persist_last_run(
                {
                    "job_id": job_id_seen,
                    "summary": summary,
                    "diff_preview": diff_preview,
                    "tool_log": tool_log,
                    "validate_only": validate_only,
                }
            )

            try:
                await publish("agent_summary", data={"touched": unique_touched, "has_diff": bool(diff_preview)}, progress=0.97)
                await publish("agent_done", progress=0.98)
            except Exception:
                pass

    # Final payload
    return {
        "job_id": job_id_seen,
        "summary": (
            "DONE: Updated files/folders -> "
            + ", ".join(sorted({p for p in touched_paths})[:12])
            + ("..." if len(set(touched_paths)) > 12 else "")
            if touched_paths
            else "DONE: Agent run completed."
        ),
        "tool_log": tool_log,
        "diff_preview": _extract_diff_preview(tool_log),
    }