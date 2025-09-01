# backend/app/services/stubs_store.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class StubStoreProto(Protocol):
    """Minimal interface used by the routes and tests."""
    def list(self) -> list: ...
    def exists(self, *, env: str, path: str) -> bool: ...
    def create(self, *, name: str, path: str, env: str) -> None: ...


_STUBS: Optional[StubStoreProto] = None


def _workspace_from_env() -> Path:
    return Path(os.getenv("OMEGA_WORKSPACE", "workspace/.omega"))


def _new_store(workspace: Path) -> StubStoreProto:
    """
    Import the concrete implementation lazily so env is set first.
    >>> IMPORTANT: update the import path below if your StubStore lives elsewhere.
    """
    # Adjust this line to your actual implementation module:
    from backend.app.services.stubs import StubStore  # <- change path if needed
    return StubStore(workspace)  # type: ignore[return-value]


def get_store() -> StubStoreProto:
    global _STUBS
    if _STUBS is None:
        _STUBS = _new_store(_workspace_from_env())
    return _STUBS


def reset_store(workspace: Optional[Path] = None) -> None:
    """
    Re-create the store pointing at the given workspace; wipes in-memory index.
    Useful for tests.
    """
    global _STUBS
    ws = workspace or _workspace_from_env()
    _STUBS = _new_store(ws)