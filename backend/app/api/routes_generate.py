# backend/app/api/routes_generate.py
from __future__ import annotations

import json
import os
import re
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

# --- Spec validation / planning (agent path) ---
from backend.app.models.spec import validate_spec
import backend.app.services.plan_service as plan_service  # module import so tests can monkeypatch
from backend.app.services.agent_service import adapt_repository_with_agent

# --- Lightweight codegen (kept for completeness; default path is agent) ---
from backend.app.services.generate_service import plan_files, write_project

# --- Compile loop (optional) ---
from backend.app.services.compile_loop import run_compile_loop, serialize_report

# --- Last-run persistence & diff ---
from backend.app.services.meta_store import compute_workspace_diff_summary
from backend.app.services.job_store import save_last_run

router = APIRouter(prefix="/api", tags=["generate"])


def _workspace() -> Path:
    """
    Directory used by codegen/agent as the logical workspace root.
    NOTE: Use 'workspace' (NOT 'workspace/.omega'), since downstream
    generators place .omega state under this root.
    """
    return Path(os.getenv("OMEGA_WORKSPACE", "workspace"))


def _slugify(s: str, fallback: str = "app") -> str:
    s = (s or "").strip().lower()
    if not s:
        return fallback
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or fallback


def _infer_target_dir(brief: Optional[str], spec_obj: Optional[dict]) -> str:
    """
    Stable app directory under workspace/apps/<slug>.
    Uses spec.name if available; falls back to brief text.
    """
    name = None
    if isinstance(spec_obj, dict):
        name = (spec_obj.get("name") or spec_obj.get("description") or "").strip()
    if not name and brief:
        name = brief
    slug = _slugify(name or "omega-app")
    return f"workspace/apps/{slug}"


def _make_flutter_mvvm_instructions(app_dir: str) -> str:
    """
    High-signal guidance for the agent to produce a full Flutter MVVM app,
    not a stub. No external templates; everything must be authored by the agent.
    """
    return f"""
Create a complete Flutter application using the MVVM pattern at "{app_dir}".

Hard requirements:
- Do NOT produce a placeholder scaffold; author a runnable app end-to-end.
- Project layout (create directories/files as needed):
  {app_dir}/pubspec.yaml
  {app_dir}/README.md
  {app_dir}/analysis_options.yaml
  {app_dir}/lib/main.dart
  {app_dir}/lib/app.dart
  {app_dir}/lib/core/di/locator.dart
  {app_dir}/lib/core/routing/app_router.dart
  {app_dir}/lib/core/theme/app_theme.dart
  {app_dir}/lib/core/storage/local_store.dart
  {app_dir}/lib/core/net/api_client.dart
  {app_dir}/lib/features/common/widgets/
  {app_dir}/lib/features/<feature>/model/*.dart
  {app_dir}/lib/features/<feature>/repo/*.dart
  {app_dir}/lib/features/<feature>/service/*.dart
  {app_dir}/lib/features/<feature>/viewmodel/*.dart
  {app_dir}/lib/features/<feature>/view/*.dart
  {app_dir}/test/widget/smoke_test.dart
- State management: provider + ChangeNotifier (no bloc/cubit).
- Persistence: shared_preferences for small data (wrap in LocalStore).
- The app must compile with `flutter run` (no extra scripts).
- MVVM contracts:
  * ViewModel holds state + exposes actions; no platform/UI code.
  * View renders from ViewModel via Provider.
  * Model is pure data.
  * Core services (routing, storage, theme) live under lib/core.
- Add at least these screens as proof of completeness:
  * Home (list, filter, create items)
  * Details (edit/toggle one item)
  * Settings (switch dark/light; persists)
- Pubspec: include `provider` and `shared_preferences`. Lock SDK to a sane stable channel.
- README: explain how to run (flutter version, commands).

Process rules (very important):
- ALWAYS inspect with fs_glob/fs_read before writing.
- Use fs_write for new files; fs_patch for surgical edits.
- After writing a group of files, run fs_diff to verify.
- Finish by calling fs_glob("{app_dir}/**/*", max_matches=4000) and a final fs_diff.

Keep the run idempotent:
- If re-run, patch/replace content under managed regions; do not duplicate files/sections.
    """.strip()


def _maybe_infer_dev_instructions(
    provided: Optional[str],
    *,
    target: Optional[str],
    architecture: Optional[str],
    brief: Optional[str],
    spec_obj: Optional[dict],
) -> Optional[str]:
    """
    If the caller didn't supply dev_instructions, infer high-signal instructions
    that push the agent to produce a *real app* (no placeholder scaffold).
    """
    if provided and provided.strip():
        return provided

    app_dir = _infer_target_dir(brief, spec_obj)

    # Prefer Flutter MVVM if caller hints 'flutter' (or 'mobile') + 'mvvm'
    t = (target or "").lower().strip()
    arch = (architecture or "").lower().strip()

    if t in {"flutter", "mobile", "flutter_mvvm"} or arch in {"mvvm"}:
        return _make_flutter_mvvm_instructions(app_dir)

    # Otherwise, steer toward a runnable web app (vanilla or React) with no build step.
    return f"""
Create a real, runnable web app at "{app_dir}" (no placeholder scaffold).

Requirements:
- Files: index.html, styles.css, main.js placed under {app_dir}/
- Features described by the brief/spec must be implemented (add/toggle/delete items if it's a todo; adapt appropriately for other domains).
- No build step: pure HTML/CSS/JS; keep imports relative.
- Persist client state via localStorage where applicable.
- README with quickstart.
Process:
- Inspect existing files with fs_glob/fs_read.
- Write/patch files, then fs_diff.
- Finish with fs_glob("{app_dir}/*") and fs_diff.
Idempotent: patch content on reruns; do not duplicate sections.
    """.strip()


@router.post("/generate")
async def generate_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Two modes are supported:

    1) Agent mode (default): Adapt the repo to the given OmegaSpec using an OpenAI tool-calling agent.
       Provide either:
         - {"spec": <OmegaSpec JSON object or JSON string>, ...}
         - {"brief": "replan from scratch ...", ...}
       Optional (agent):
         - target: "flutter"|"react"|"mobile"|...
         - architecture: "mvvm"|...
         - dev_instructions: str
         - validate_only: bool
         - wall_clock_budget_sec: float
         - per_call_timeout_sec: float
         - compile_loop: bool (Flutter only; run analyze/test after agent writes)
         - run_tests: bool (default true when compile_loop=true)
         - max_rounds: int (safety-capped)
       Response (agent):
         {
           "status": "ok",
           "job_id": "<uuid>",
           "result": {
             "summary": "...",
             "tool_log": [...],
             "diff_preview": "<diff-like snapshot>",
             "job_id": "<uuid>",
             "compile_report": { ... }?,   # when compile_loop requested
             "ok": true|false               # mirrors compile_report.ok when present
           },
           "compile_report": { ... }?       # top-level mirror for CLI
         }

    2) Codegen mode (opt-in): Generate a small runnable scaffold to the workspace.
       Select with {"mode": "codegen"} and provide either:
         - {"mode":"codegen","brief":"...", "target":"react"?, "dry_run":true?}
         - {"mode":"codegen","spec":{OmegaSpec...}, "target":"react"?, "dry_run":true?}
    """
    try:
        mode = (payload.get("mode") or "agent").lower()
        compile_loop_flag: bool = bool(payload.get("compile_loop", False))
        run_tests_flag: bool = bool(payload.get("run_tests", True))
        max_rounds: int = int(payload.get("max_rounds", 3))
        if max_rounds < 1:
            max_rounds = 1
        if max_rounds > 25:
            max_rounds = 25

        # --- Optional timeout tuning for compile loop (passed through) ---
        pub_timeout_sec = int(payload.get("pub_timeout_sec", 180))
        analyze_timeout_sec = int(payload.get("analyze_timeout_sec", 300))
        test_timeout_sec = int(payload.get("test_timeout_sec", 600))
        test_timeout_first_sec = int(payload.get("test_timeout_first_sec", 900))

        # ---------------------------
        # MODE: CODEGEN
        # ---------------------------
        if mode == "codegen":
            target = (payload.get("target") or "react").lower()
            dry_run = bool(payload.get("dry_run", False))

            # Accept spec as dict OR as JSON string; otherwise try brief
            spec_obj = payload.get("spec")
            brief: Optional[str] = payload.get("brief")

            if isinstance(spec_obj, str) and spec_obj.strip():
                try:
                    spec_obj = json.loads(spec_obj)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"'spec' is a string but not valid JSON: {e}")

            if isinstance(spec_obj, dict):
                try:
                    spec = validate_spec(spec_obj)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"invalid spec: {e}")
            elif brief:
                spec, _raw = plan_service.plan_and_validate(brief, max_repairs=1)
            else:
                got_keys = list(payload.keys())
                raise HTTPException(
                    status_code=400,
                    detail=f"[codegen] Provide either 'spec' (dict or JSON string) or 'brief'. Got keys: {got_keys}",
                )

            if dry_run:
                files = [{"path": rel, "bytes": len(content.encode("utf-8"))} for rel, content in plan_files(spec, target)]
                return {"status": "ok", "target": target, "dry_run": True, "files": files}

            ws = _workspace()
            res = write_project(spec, ws, target)

            # Optional compile loop (Flutter only)
            if compile_loop_flag and target in {"flutter", "mobile", "flutter_mvvm"}:
                app_dir = Path(res["dir"]).resolve()
                try:
                    rep = run_compile_loop(
                        app_dir,
                        run_tests=run_tests_flag,
                        max_rounds=max_rounds,
                        pub_timeout_sec=pub_timeout_sec,
                        analyze_timeout_sec=analyze_timeout_sec,
                        test_timeout_sec=test_timeout_sec,
                        test_timeout_first_sec=test_timeout_first_sec,
                    )
                    comp_json = serialize_report(rep)
                except Exception as e:
                    comp_json = {"ok": False, "message": f"compile loop crashed: {e}", "rounds": []}
                res["compile_report"] = comp_json
                res["ok"] = bool(res.get("gate", {}).get("ok", True) and comp_json.get("ok", False))
            else:
                res["ok"] = res.get("gate", {}).get("ok", True)

            # Persist last run (diff summary from workspace root)
            try:
                diff_preview, summary = compute_workspace_diff_summary(Path("workspace"))
                save_last_run(
                    job_id=os.getenv("OMEGA_JOB_ID") or "manual",
                    summary=summary or "Codegen completed.",
                    diff_preview=diff_preview,
                    tool_log=res.get("gate", {}).get("checks", []),  # lightweight; agent logs not used in codegen path
                )
            except Exception:
                # best effort; do not fail the API on save issues
                pass

            # Mirror compile_report at top-level too (for CLI jq convenience)
            out: Dict[str, Any] = res.copy()
            if "compile_report" in res:
                out = res.copy()
                out["compile_report"] = res["compile_report"]
            return out

        # ---------------------------
        # MODE: AGENT (default)
        # ---------------------------
        spec_obj = payload.get("spec")
        brief: Optional[str] = payload.get("brief")
        target: Optional[str] = payload.get("target")
        architecture: Optional[str] = payload.get("architecture") or payload.get("arch")
        dev_instructions_in = payload.get("dev_instructions")

        validate_only: bool = bool(payload.get("validate_only", False))
        wall_clock_budget_sec: Optional[float] = payload.get("wall_clock_budget_sec")
        per_call_timeout_sec: Optional[float] = payload.get("per_call_timeout_sec")

        # Accept spec as dict OR as JSON string
        if isinstance(spec_obj, str) and spec_obj.strip():
            try:
                spec_obj = json.loads(spec_obj)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"'spec' is a string but not valid JSON: {e}")

        if isinstance(spec_obj, dict):
            try:
                spec = validate_spec(spec_obj)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid spec: {e}")
        elif brief:
            spec, _raw = plan_service.plan_and_validate(brief, max_repairs=1)
        else:
            got_keys = list(payload.keys())
            raise HTTPException(
                status_code=400,
                detail=f"Provide either 'spec' (dict or JSON string) or 'brief'. Got keys: {got_keys}",
            )

        # If the caller didn't provide dev_instructions, infer high-signal ones that
        # force a *real app* (Flutter MVVM for mobile; otherwise runnable web).
        spec_dict_for_dir = spec.model_dump() if hasattr(spec, "model_dump") else (spec_obj if isinstance(spec_obj, dict) else None)
        dev_instructions = _maybe_infer_dev_instructions(
            dev_instructions_in,
            target=target,
            architecture=architecture,
            brief=brief,
            spec_obj=spec_dict_for_dir,
        )

        # Run the agent
        result = await adapt_repository_with_agent(
            spec,
            dev_instructions=dev_instructions,
            validate_only=validate_only,
            wall_clock_budget_sec=wall_clock_budget_sec,
            per_call_timeout_sec=per_call_timeout_sec,
        )

        job_id = result.get("job_id") or os.getenv("OMEGA_JOB_ID") or "manual"

        # Optional compile loop after agent writes (Flutter only)
        compile_report_json: Optional[Dict[str, Any]] = None
        if compile_loop_flag and (target or "").lower().strip() in {"flutter", "mobile", "flutter_mvvm"}:
            app_dir_str = _infer_target_dir(brief, spec_dict_for_dir)
            app_dir = Path(app_dir_str).resolve()
            try:
                rep = run_compile_loop(
                    app_dir,
                    run_tests=run_tests_flag,
                    max_rounds=max_rounds,
                    pub_timeout_sec=pub_timeout_sec,
                    analyze_timeout_sec=analyze_timeout_sec,
                    test_timeout_sec=test_timeout_sec,
                    test_timeout_first_sec=test_timeout_first_sec,
                )
                compile_report_json = serialize_report(rep)
            except Exception as e:
                compile_report_json = {"ok": False, "message": f"compile loop crashed: {e}", "rounds": []}

            # Attach to result and set a boolean 'ok'
            result["compile_report"] = compile_report_json
            result["ok"] = bool(result.get("ok", True) and compile_report_json.get("ok", False))

        # Persist last run with fresh diff from workspace root; include agent tool_log if present
        try:
            diff_preview, summary = compute_workspace_diff_summary(Path("workspace"))
            save_last_run(
                job_id=job_id,
                summary=summary or result.get("summary") or "Agent run completed.",
                diff_preview=diff_preview,
                tool_log=result.get("tool_log", []),
            )
        except Exception:
            # best effort; do not fail the API on save issues
            pass

        # bubble job_id to top-level and mirror compile_report for CLI
        response: Dict[str, Any] = {"status": "ok", "job_id": job_id, "result": result}
        if "compile_report" in result:
            response["compile_report"] = result["compile_report"]
        return response

    except HTTPException:
        raise
    except Exception as e:
        # Always return JSON error instead of plain text 500 (with a small trace snippet)
        trace = "\n".join(traceback.format_exception_only(type(e), e)).strip()
        raise HTTPException(
            status_code=500,
            detail={"error": "generate_failed", "message": str(e), "trace": trace},
        )


# BEGIN OMEGA SECTION (managed)
# Notes:
# - Agent mode infers dev_instructions when not provided:
#   * target=flutter or architecture=mvvm => generate a full Flutter MVVM app (runnable).
#   * otherwise => generate a runnable web app (no build step).
# - Codegen mode remains available for tests and quick scaffolds.
# - Optional Flutter compile loop can be enabled with {"compile_loop": true}.
# - Each run saves a last_run snapshot (summary + diff + logs).
# - Errors are returned as structured JSON (HTTP 500 with {"error","message","trace"}).
# - Keep this block idempotent; Omega Builder may update it on future runs.
# END OMEGA SECTION