from __future__ import annotations

from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Response, Query
from backend.app.core.metrics import REGISTRY
from backend.app.core.config import settings

router = APIRouter(prefix="/api", tags=["metrics"])


def _exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False


def _count_bytes(root: Path, exts: set[str] | None = None) -> tuple[int, int]:
    ct = 0
    total = 0
    if not root.exists():
        return 0, 0
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if exts and f.suffix.lower() not in exts:
            continue
        try:
            total += f.stat().st_size
            ct += 1
        except Exception:
            pass
    return ct, total


def _pick_project_root(project_arg: Optional[str]) -> Path:
    """
    Priority:
      1) If ?project=name is given, prefer /workspace/<name> if present.
      2) If env-configured staging_root exists, use it.
      3) If exactly one subdir exists under /workspace, use it.
      4) Fallback to /staging.
    """
    # 1) explicit ?project
    if project_arg:
        p = Path("/workspace") / project_arg
        if p.exists():
            return p.resolve()

    # 2) configured staging root
    try:
        cfg = Path(settings.staging_root).resolve()
        if cfg.exists():
            return cfg
    except Exception:
        pass

    # 3) single project auto-detect
    ws = Path("/workspace")
    if ws.exists():
        try:
            subs = [d for d in ws.iterdir() if d.is_dir() and not d.name.startswith(".")]
            if len(subs) == 1:
                return subs[0].resolve()
        except Exception:
            pass

    # 4) fallback
    return Path("/staging").resolve()


@router.get("/metrics")
def metrics(project: Optional[str] = Query(default=None)) -> Response:
    # Base prometheus payload from registry
    payload = REGISTRY.render_prometheus()

    # Determine project root for readiness gauges
    project_root = _pick_project_root(project)

    design_dir = project_root / "design"
    fonts_dir = design_dir / "fonts"
    tokens_dir = design_dir / "tokens"
    theme_dir = design_dir / "theme"
    assets_dir = project_root / "assets"
    infra_dir = project_root / "infra"
    adapters_dir = project_root / "adapters"

    design_ready = int(_exists(fonts_dir) and _exists(tokens_dir) and _exists(theme_dir))
    assets_ct, assets_bytes = _count_bytes(assets_dir, {".png", ".jpg", ".jpeg", ".webp", ".svg"})
    infra_ready = int(_exists(infra_dir / "docker-compose.yml") and _exists(infra_dir / ".env.example"))
    adapters_ready = int(
        _exists(adapters_dir / "payments_adapter.dart")
        and _exists(adapters_dir / "ocr_adapter.dart")
        and _exists(adapters_dir / "telemed_adapter.dart")
        and _exists(adapters_dir / "logistics_adapter.dart")
    )

    extra = []
    extra.append("# HELP omega_design_ready Design system readiness (1 ready, 0 not)\n# TYPE omega_design_ready gauge\n")
    extra.append(f"omega_design_ready {design_ready}\n")
    extra.append("# HELP omega_assets_images_total Number of image assets discovered\n# TYPE omega_assets_images_total gauge\n")
    extra.append(f"omega_assets_images_total {assets_ct}\n")
    extra.append("# HELP omega_assets_images_bytes Total bytes of image assets\n# TYPE omega_assets_images_bytes gauge\n")
    extra.append(f"omega_assets_images_bytes {assets_bytes}\n")
    extra.append("# HELP omega_infra_ready Infra readiness (docker/env/ci present)\n# TYPE omega_infra_ready gauge\n")
    extra.append(f"omega_infra_ready {infra_ready}\n")
    extra.append("# HELP omega_adapters_ready Adapter stubs readiness\n# TYPE omega_adapters_ready gauge\n")
    extra.append(f"omega_adapters_ready {adapters_ready}\n")
    # also expose which root we used
    extra.append("# HELP omega_metrics_project_root_info Selected project root (as label)\n# TYPE omega_metrics_project_root_info gauge\n")
    root_label = str(project_root).replace("\\", "\\\\").replace('"', '\\"')
    extra.append(f'omega_metrics_project_root_info{{root="{root_label}"}} 1\n')

    return Response(content=payload + "".join(extra), media_type="text/plain; version=0.0.4")