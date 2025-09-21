from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class ThemeRequest(BaseModel):
    palette: Optional[Dict[str, str]] = Field(default=None)
    fonts: Optional[Dict[str, str]] = Field(default=None)
    radius: Optional[Dict[str, int]] = Field(default=None)
    spacing: Optional[Dict[str, int]] = Field(default=None)
    motion: Optional[Dict[str, Any]] = Field(default=None)
    mode: Optional[str] = Field(default="system")  # "light" | "dark" | "system"

class PlanRequest(BaseModel):
    brief: Optional[str] = None
    spec: Optional[Dict[str, Any]] = None
    blueprint: Optional[str] = Field(default="omega_monorepo")
    adapters: Optional[List[str]] = Field(default=[])  # e.g. ["telemed_dummy","payments_stripe"]
    theme: Optional[ThemeRequest] = None
    project_name: Optional[str] = None

class GenerateRequest(PlanRequest):
    pass