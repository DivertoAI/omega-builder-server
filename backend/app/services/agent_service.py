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

# Where we persist the last run details for quick debugging/observability
OMEGA_DIR = Path("workspace/.omega")
LAST_RUN_PATH = OMEGA_DIR / "last_run.json"


SYSTEM = """You are Omega Builder Agent.

Goal:
- Produce a runnable, no-build web application (plain HTML/CSS/JS) that fulfills the provided OmegaSpec or brief.

Hard rules:
- Do NOT emit placeholder scaffolds. Deliver working pages, real UI, and real logic.
- Use only the filesystem tools: fs_map, fs_glob, fs_read, fs_write, fs_mkdir, fs_delete, fs_diff, fs_patch.
- Prefer minimal, incremental, idempotent edits: if re-run, patch/replace the same managed blocks without duplicating.
- Keep the app runnable with zero tooling: no bundlers, no frameworks. Only index.html, styles.css, main.js.
- Persist client-side state locally (e.g., localStorage) when the brief implies persistence.
- Before writing, fs_read existing files to avoid duplicates. Use fs_patch for surgical edits. After edits, run fs_diff.

Completion requirements (mandatory):
1) The app lives under workspace/apps/<slug>/ (slugify the spec/brief name).
2) At minimum, create:
   - index.html  (real UI that actually performs the requested tasks)
   - styles.css  (styling consistent with the brief, e.g., dark theme)
   - main.js     (actual behavior; no placeholders)
3) Verify with:
   - fs_glob on workspace/apps/<slug>/*
   - fs_read on index.html, styles.css, main.js to ensure they are non-empty and coherent
   - fs_diff to show the final changes
4) When you’re finished, reply with a short plain-text summary starting with: DONE:
"""

KICKOFF_INSTRUCTION = """Plan your edits first, then create a runnable app under workspace/apps/<slug>/.

Initial repo inspection:
- Call fs_glob on: "backend/**/*.py", "workspace/**/*", "assets/**/*", "pyproject.toml", "README.md".
- fs_read any file you intend to modify before writing.

Implementation order:
1) Determine <slug> from the spec/brief title (e.g., "todo" for a Todo app).
2) Ensure directory exists: workspace/apps/<slug> (fs_mkdir if missing).
3) Create real, working files:
   - index.html: Proper HTML structure, visible UI matching the brief, link styles.css and main.js.
   - styles.css: Real styles (e.g., dark theme when requested).
   - main.js: Actual logic (CRUD/state/events). Use localStorage if persistence is implied.
4) Idempotency: if files exist, use fs_read + fs_patch to update relevant sections without duplication.

Verification and finish:
- fs_glob workspace/apps/<slug>/*
- fs_read index.html, styles.css, main.js (ensure non-empty and coherent)
- fs_diff on workspace/apps/<slug>
- Then emit a DONE: summary.

Never output placeholder scaffolds; always produce a runnable app.
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

            # Build summary & persist
            unique_touched = sorted({p for p in touched_paths if isinstance(p, str) and p})
            summary = (
                "DONE: Updated files/folders -> "
                + ", ".join(unique_touched[:12])
                + ("..." if len(unique_touched) > 12 else "")
                if unique_touched
                else "DONE: Agent run completed."
            )
            diff_preview = _extract_diff_preview(tool_log)

            _persist_last_run(
                {
                    "job_id": job_id_seen,
                    "summary": summary,
                    "diff_preview": diff_preview,
                    "tool_log": tool_log,
                    "validate_only": validate_only,
                }
            )

            await pbar.set(0.97, "agent_summary", {"touched": unique_touched, "has_diff": bool(diff_preview)})
            await pbar.set(0.98, "agent_done")

    # --- Wrap-up (0.98 → 1.00 on client side)
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