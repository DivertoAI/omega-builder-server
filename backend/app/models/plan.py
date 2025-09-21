from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class ThemeTokens(BaseModel):
    palette: Dict[str, str] = Field(default_factory=dict)
    typography: Dict[str, Any] = Field(default_factory=dict)
    radius: List[int] = Field(default_factory=lambda: [4, 8, 12])

class PlanRequest(BaseModel):
    brief: str
    blueprint: Optional[str] = Field(
        default=None, description="Name of blueprint pack: blank|diary|pharmacy"
    )
    adapters: List[str] = Field(
        default_factory=list,
        description="Adapters to request: ocr|telemed|payments|logistics|firebase|bluetooth",
    )
    theme: Optional[ThemeTokens] = None
    max_repairs: int = 1

class AppSpec(BaseModel):
    name: str
    kind: str  # flutter_app | flutter_dashboard | fastapi_service | design_system | infra
    path: str
    options: Dict[str, Any] = Field(default_factory=dict)

class PlanResponse(BaseModel):
    project: str = "omega_project"
    apps: List[AppSpec] = Field(default_factory=list)
    design: Dict[str, Any] = Field(default_factory=dict)
    adapters: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)