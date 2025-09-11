from __future__ import annotations
import os, json
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

AI_VM_URL = os.getenv("AI_VM_URL", "http://ai-vm:8080")

async def compile_in_vm(
    app_dir: Path,
    *,
    run_tests: bool = True,
    pub_timeout_sec: int = 180,
    analyze_timeout_sec: int = 300,
    test_timeout_first_sec: int = 900,
    test_timeout_sec: int = 360,
    max_rounds: int = 2,
) -> Dict[str, Any]:
    url = f"{AI_VM_URL}/api/compile"
    payload = {
        "app_dir": str(app_dir),
        "run_tests": run_tests,
        "pub_timeout_sec": pub_timeout_sec,
        "analyze_timeout_sec": analyze_timeout_sec,
        "test_timeout_first_sec": test_timeout_first_sec,
        "test_timeout_sec": test_timeout_sec,
        "max_rounds": max_rounds,
    }
    async with httpx.AsyncClient(timeout=1200.0) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()