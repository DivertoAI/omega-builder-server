from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from repo root if present
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)


class Settings(BaseSettings):
    """Global Omega Builder settings (loaded from env).

    Key model roles:
      - Planner (o3*): creates multi-step plans/specs.
      - Coder (gpt-5*): turns spec+context into concrete code.
      - Image (gpt-image-1*): generates/edits assets.

    Execution:
      - "code_interpreter_*" toggles a local sandbox (AI-VM) for the compile/run loop.
      - "omega_enable_*" toggles tool availability to the agent.
    """

    # --- service ---
    service_name: str = Field(default="omega-builder", description="Service name")
    version: str = Field(default="0.1.0", description="Service version")
    environment: str = Field(default="dev", description="Environment name (dev/staging/prod)")
    host: str = Field(default="0.0.0.0", description="API bind host")
    port: int = Field(default=8000, description="API bind port")

    # Logging
    log_level: str = Field(default="INFO", description="Root log level")
    log_format: str = Field(default="text", description="text|json")

    # --- OpenAI (billing controlled via openai_enabled) ---
    openai_api_key: str = Field(default="", description="OpenAI API key")
    openai_project: str = Field(default="", description="OpenAI Project ID (optional)")
    openai_org_id: str = Field(default="", description="OpenAI Organization ID (optional)")

    # Hard kill switch: when False, the generator returns stubs and never calls OpenAI.
    openai_enabled: bool = Field(default=False, description="Enable real OpenAI calls (billing)")

    # Health probe toggles (to avoid accidental spend from /api/health)
    health_probe_text: bool = Field(
        default=False, description="Ping OpenAI text in /api/health"
    )
    health_probe_image: bool = Field(
        default=False, description="Ping OpenAI images in /api/health"
    )

    # --- Models & roles ---
    # Primary coder model (used for code/spec generation where planning isn't required)
    omega_llm_model: str = Field(
        default="gpt-5",
        description="Default LLM model for code/spec (coder role)"
    )
    # Dedicated planner model (o3 family)
    omega_planner_model: str = Field(
        default="o3",
        description="Planner model (o3 family) used for deep planning/spec"
    )
    # Optional: explicit codegen model override if you want a different one than omega_llm_model
    omega_codegen_model: str = Field(
        default="",
        description="Explicit codegen model; if empty, falls back to omega_llm_model"
    )
    # Reasoning / temperature style knobs
    planner_temperature: float = Field(
        default=0.2, ge=0.0, le=2.0,
        description="Planner creativity/variance (o3 prompt wrappers may ignore)"
    )
    coder_temperature: float = Field(
        default=0.1, ge=0.0, le=2.0,
        description="Coder creativity/variance"
    )

    # Images
    omega_image_model: str = Field(default="gpt-image-1", description="Image model")
    omega_image_size: str = Field(default="1024x1024", description="Default image size")

    # --- Feature flags (internal tool toggles) ---
    omega_enable_web: bool = Field(default=True, description="Enable web search tool")
    omega_enable_file_search: bool = Field(default=True, description="Enable file search tool")
    omega_enable_mcp: bool = Field(default=False, description="Enable MCP connectors")

    # --- Code Interpreter / AI-VM (local sandbox for compile/run) ---
    code_interpreter_enabled: bool = Field(
        default=True,
        description="Enable the local code execution loop (AI-VM/sandbox)"
    )
    code_interpreter_image: str = Field(
        default="omega/ai-vm:latest",
        description="Container image/tag for AI-VM jobs"
    )
    code_interpreter_entrypoint: Path = Field(
        default=Path("ai-vm/scripts/job_runner.sh"),
        description="Script executed inside the AI-VM to run jobs"
    )
    code_interpreter_workdir: Path = Field(
        default=Path("/workspace"),
        description="Default working directory inside the sandbox"
    )
    code_interpreter_timeout_seconds: int = Field(
        default=300,
        description="Max execution time for a single sandboxed step"
    )

    # --- CORS (future REST UI/CLI) ---
    cors_allow_origins: List[str] = Field(default_factory=lambda: ["*"])
    cors_allow_methods: List[str] = Field(default_factory=lambda: ["*"])
    cors_allow_headers: List[str] = Field(default_factory=lambda: ["*"])

    # --- Storage / workspace paths ---
    workspace_root: Path = Field(default=Path("/app/workspace"), description="Workspace root")
    staging_root: Path = Field(default=Path("/app/staging"), description="Temporary staging root")
    artifacts_root: Path = Field(default=Path("/app/artifacts"), description="Final artifacts root")

    # --- Redis / job queue ---
    redis_url: str = Field(default="redis://omega-redis:6379/0", description="Redis connection URL")
    job_queue_name: str = Field(default="jobs:generate", description="Redis list name for jobs")
    job_ttl_seconds: int = Field(default=86400, description="TTL for job keys (seconds)")
    worker_poll_seconds: int = Field(default=2, description="Worker idle sleep between polls (s)")

    # --- Rate limiting (simple token bucket per IP) ---
    rate_limit_rps: float = Field(default=0.2, description="Refill rate tokens/sec (0.2=1 req/5s)")
    rate_limit_burst: int = Field(default=2, description="Max burst tokens per IP")

    # --- Quality gates (toggles) ---
    gate_enable_compile_guard: bool = Field(default=True, description="Ensure compile-safe baseline")
    gate_enable_mvvm_checks: bool = Field(default=True, description="Advisory MVVM checks for Flutter")
    gate_enable_web_checks: bool = Field(default=True, description="Basic web artifact checks")

    # --- Misc / operational toggles surfaced from your .env ---
    omega_reset_on_start: bool = Field(
        default=False,
        description="If true, reset queues/caches on service start"
    )
    omega_progress_backend: str = Field(
        default="redis",
        description="Progress backend (e.g., redis)"
    )

    # pydantic-settings v2 config
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Convenience helpers ----
    @property
    def effective_codegen_model(self) -> str:
        """Return the effective coder model (explicit override or default LLM)."""
        return self.omega_codegen_model or self.omega_llm_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()