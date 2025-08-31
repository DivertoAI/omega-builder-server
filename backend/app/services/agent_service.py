from __future__ import annotations

import json
from typing import Dict, Any, List

from backend.app.core.config import settings
from backend.app.core.progress import start_job
from backend.app.integrations.openai.client import get_openai_client
from backend.app.integrations.agent.tools import openai_tool_specs, dispatch_tool_call
from backend.app.models.spec import OmegaSpec


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
- "app/**/*.py"
- "main.py"
- "workspace/**/*"
- "assets/**/*"
- "pyproject.toml"
- "README.md"

After inspection, plan minimal edits to reflect navigation and endpoints from the spec. Then perform edits using fs_write/fs_mkdir as needed.

Edits priority (do in order, skipping any that already exist):
1) Ensure backend/main.py includes plan & generate routes and SSE.
2) Create/patch a minimal frontend/workspace README with routes reflecting spec.navigation.
3) Add TODO stubs in appropriate places (no mock data).
4) Keep changes idempotent. If a file already matches, do nothing.
"""


def _chat_tools_spec() -> List[dict]:
    """
    Chat Completions expects the nested `function` schema:
    [{ "type": "function", "function": { "name": ..., "description": ..., "parameters": {...} } }]
    """
    tools = openai_tool_specs()
    out: List[dict] = []
    for t in tools:
        if "function" in t:
            out.append(t)
        else:
            out.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                }
            })
    return out


def _assistant_msg_dict(msg_obj) -> dict:
    """
    Convert SDK assistant message object into a raw dict the API accepts,
    preserving `tool_calls` so that subsequent tool messages can legally reference them.
    """
    content = getattr(msg_obj, "content", None)
    tool_calls = getattr(msg_obj, "tool_calls", None) or []

    # Convert tool calls into the wire shape the API expects
    tc_list = []
    for tc in tool_calls:
        fn = tc.function
        tc_list.append({
            "id": tc.id,
            "type": "function",
            "function": {
                "name": fn.name,
                # Chat Completions returns arguments as a JSON string
                "arguments": fn.arguments if isinstance(fn.arguments, str) else json.dumps(fn.arguments or {}),
            },
        })

    out = {"role": "assistant"}
    if content is not None:
        out["content"] = content
    if tc_list:
        out["tool_calls"] = tc_list
    return out


def _msg_text_from_assistant(text: str | None) -> str:
    return (text or "").strip()


async def adapt_repository_with_agent(spec: OmegaSpec) -> Dict[str, Any]:
    """
    Orchestrates a Chat Completions + function-calling loop to apply edits.
    Returns a summary and a log of tool_call results.
    """
    client = get_openai_client()
    tools = _chat_tools_spec()
    tool_log: List[Dict[str, Any]] = []

    async with start_job("generate", data={"mode": "agent"}) as (job_id, publish):
        await publish("agent_boot", progress=0.05)
        if tools:
            await publish("agent_tool_shape", progress=0.06, data={"tool0": tools[0]})
        await publish("agent_bootstrap", progress=0.12, data={"count": len(tools)})

        # Classic chat-style messages
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_PREFIX + spec.model_dump_json(indent=2)},
            {"role": "user", "content": KICKOFF_INSTRUCTION},
        ]

        consecutive_no_tool = 0

        for step in range(1, 14):  # bounded loop for safety
            await publish(f"agent_step_{step}", progress=min(0.08 + step * 0.07, 0.92))

            # Avoid temperature for broad model compatibility
            resp = client._client.chat.completions.create(
                model=settings.omega_llm_model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                parallel_tool_calls=True,
            )

            choice = resp.choices[0]
            msg = choice.message

            # IMPORTANT: append assistant message INCLUDING tool_calls when present
            assistant_msg = _assistant_msg_dict(msg)
            messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls", [])  # already normalized
            if tool_calls:
                # Execute each tool call and append corresponding tool result
                for tc in tool_calls:
                    fn = tc["function"]
                    name = fn["name"]
                    try:
                        args = json.loads(fn["arguments"]) if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
                    except Exception:
                        args = {}

                    result = dispatch_tool_call(name, args)
                    tool_log.append({"name": name, "args": args, "result": result})

                    # MUST pair the tool result with the same tool_call_id
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )

                # Loop back so the model can react to tool outputs
                consecutive_no_tool = 0
                continue

            # No tool calls this round — check for DONE sentinel
            text = _msg_text_from_assistant(assistant_msg.get("content"))
            if text.upper().startswith("DONE:"):
                break

            consecutive_no_tool += 1
            if consecutive_no_tool >= 2:
                break

        await publish("agent_done", progress=0.97)

    # Build a compact summary if the model didn’t emit DONE:
    summary: str = "Edits applied via agent (see tool_log)."
    if tool_log:
        touched: List[str] = []
        for entry in tool_log:
            if entry["name"] in {"fs_write", "fs_mkdir", "fs_delete", "fs_patch"}:
                args = entry.get("args") or {}
                p = args.get("path") or args.get("pattern")
                if isinstance(p, str):
                    touched.append(p)
        if touched:
            unique = sorted({p for p in touched})
            summary = "DONE: Updated files/folders -> " + ", ".join(unique[:12]) + ("..." if len(unique) > 12 else "")

    return {"summary": summary, "tool_log": tool_log}