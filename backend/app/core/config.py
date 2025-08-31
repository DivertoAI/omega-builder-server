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
    """Global Omega Builder settings (loaded from env)."""

    # --- service ---
    service_name: str = Field(default="omega-builder", description="Service name")
    version: str = Field(default="0.1.0", description="Service version")
    environment: str = Field(default="dev", description="Environment name (dev/staging/prod)")

    # Logging
    log_level: str = Field(default="INFO", description="Root log level")
    log_format: str = Field(default="text", description="text|json")

    # --- OpenAI ---
    openai_api_key: str = Field(default="", description="OpenAI API key")
    # Optional: project/org pinning (useful in Teams/Enterprise)
    openai_project: str = Field(default="", description="OpenAI Project ID")
    openai_org_id: str = Field(default="", description="OpenAI Organization ID")

    omega_llm_model: str = Field(default="gpt-5", description="Default LLM model")
    omega_image_model: str = Field(default="gpt-image-1", description="Image model")
    omega_image_size: str = Field(default="1024x1024", description="Default image size")

    # --- feature flags ---
    omega_enable_web: bool = Field(default=True, description="Enable web search tool")
    omega_enable_file_search: bool = Field(default=True, description="Enable file search tool")
    omega_enable_mcp: bool = Field(default=False, description="Enable MCP connectors")

    # --- CORS (future REST UI/CLI) ---
    cors_allow_origins: List[str] = Field(default_factory=lambda: ["*"])
    cors_allow_methods: List[str] = Field(default_factory=lambda: ["*"])
    cors_allow_headers: List[str] = Field(default_factory=lambda: ["*"])

    # pydantic-settings v2 config
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()