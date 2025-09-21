# backend/app/models/spec.py
from __future__ import annotations

from typing import Any, List, Optional, Union

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ConfigDict,
    field_validator,
    model_validator,
)


# ----------------------------
# Atomic submodels
# ----------------------------

class Theme(BaseModel):
    """Visual defaults for generated clients."""
    model_config = ConfigDict(extra="ignore")

    colors: List[str] = Field(default_factory=list, description="Optional brand color tokens.")
    typography: List[str] = Field(default_factory=list, description="Optional font families or tokens.")
    radius: List[int] = Field(default_factory=lambda: [8], description="Corner radii (list of ints).")

    @field_validator("colors")
    @classmethod
    def _colors_are_strings(cls, v: Any) -> List[str]:
        if not isinstance(v, list):
            raise ValueError("theme.colors must be a list of strings")
        if any(not isinstance(x, str) or not x.strip() for x in v):
            raise ValueError("theme.colors entries must be non-empty strings")
        return [x.strip() for x in v]

    @field_validator("typography")
    @classmethod
    def _typography_are_strings(cls, v: Any) -> List[str]:
        if not isinstance(v, list):
            raise ValueError("theme.typography must be a list of strings")
        if any(not isinstance(x, str) or not x.strip() for x in v):
            raise ValueError("theme.typography entries must be non-empty strings")
        return [x.strip() for x in v]

    @field_validator("radius", mode="before")
    @classmethod
    def _coerce_radius_before(cls, v: Any) -> List[int]:
        """
        Accepts:
          - int/float -> [int]
          - list of numbers -> list[int]
        Rejects anything else with a short error. We clamp to [0, 64].
        """
        if v is None:
            return [8]
        if isinstance(v, (int, float)):
            iv = int(v)
            return [max(0, min(64, iv))]
        if isinstance(v, list):
            out: List[int] = []
            for i, item in enumerate(v):
                if not isinstance(item, (int, float)):
                    raise ValueError(f"theme.radius[{i}] must be an integer")
                iv = int(item)
                out.append(max(0, min(64, iv)))
            return out
        raise ValueError("theme.radius must be a list of integers")

    @field_validator("radius")
    @classmethod
    def _radius_nonempty(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("theme.radius must have at least one integer")
        return v


class NavLink(BaseModel):
    """Navigation entry object form. Strings are also allowed at Navigation.items."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Route id, e.g., 'home' or 'products'.")
    title: Optional[str] = Field(default=None, description="Display label. Defaults to title-cased id.")

    @field_validator("id")
    @classmethod
    def _id_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("navigation.items[].id is required")
        return v

    @model_validator(mode="after")
    def _default_title(self) -> "NavLink":
        if not self.title:
            self.title = self.id.replace("-", " ").replace("_", " ").title()
        return self


class Navigation(BaseModel):
    """Top-level navigation config."""
    model_config = ConfigDict(extra="ignore")

    home: str = Field(default="home", description="Default route id to launch on app start.")
    items: List[Union[str, NavLink]] = Field(default_factory=list, description="Nav items: strings or objects.")

    @field_validator("home")
    @classmethod
    def _home_non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("navigation.home must be a non-empty string")
        return v

    @model_validator(mode="after")
    def _validate_items(self) -> "Navigation":
        # Ensure each item is either a non-empty string or a NavLink with id
        for idx, it in enumerate(self.items):
            if isinstance(it, str):
                if not it.strip():
                    raise ValueError(f"navigation.items[{idx}] must be a non-empty string")
            elif isinstance(it, NavLink):
                # NavLink already validated
                pass
            else:
                raise ValueError(
                    f"navigation.items[{idx}] must be a string route id or an object with 'id'"
                )
        return self


class Entity(BaseModel):
    """Domain entity (kept permissive; only id required)."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Entity identifier, e.g., 'product'.")

    @field_validator("id")
    @classmethod
    def _entity_id_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("entities[].id is required")
        return v


class API(BaseModel):
    """External or internal API definition (permissive)."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="API identifier, e.g., 'catalog'.")

    @field_validator("id")
    @classmethod
    def _api_id_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("apis[].id is required")
        return v


class AcceptanceItem(BaseModel):
    """Human-readable acceptance checks, e.g., health checks."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Stable id, kebab-case recommended.")
    description: str = Field(..., description="Short description of the check.")

    @field_validator("id")
    @classmethod
    def _acc_id_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("acceptance[].id is required")
        return v

    @field_validator("description")
    @classmethod
    def _acc_desc_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("acceptance[].description is required")
        return v


# ----------------------------
# Root model
# ----------------------------

class OmegaSpec(BaseModel):
    """
    Minimal, adaptive spec for Omega Builder.
    Keep it lean (not template-oriented) but structurally sound.
    """
    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="Omega App", description="Project name.")
    description: str = Field(default="OmegaSpec derived from brief.", description="Short description.")
    theme: Theme = Field(default_factory=Theme)
    navigation: Navigation = Field(default_factory=Navigation)
    entities: List[Entity] = Field(default_factory=list)
    apis: List[API] = Field(default_factory=list)
    acceptance: List[AcceptanceItem] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name must be a non-empty string")
        return v

    @field_validator("description")
    @classmethod
    def _desc_non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("description must be a non-empty string")
        return v

    @model_validator(mode="after")
    def _acceptance_non_empty(self) -> "OmegaSpec":
        if not self.acceptance:
            # Keep error short and actionable to help O3 repair loops
            raise ValueError("acceptance must contain at least one check item")
        return self


# ----------------------------
# Public API
# ----------------------------

def _short_pydantic_error(err: ValidationError) -> str:
    """
    Render Pydantic errors as short, actionable lines:
      - path: message
    This keeps messages compact for O3 self-repair loops.
    """
    lines: List[str] = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e.get("loc", ())) or "<root>"
        msg = e.get("msg", "invalid value")
        lines.append(f"{loc}: {msg}")
    return "Spec validation failed:\n" + "\n".join(f"- {ln}" for ln in lines)


def validate_spec(data: Any) -> OmegaSpec:
    """
    Validate/normalize a raw spec dict into OmegaSpec.
    Raises ValueError with short, actionable messages on failure.
    """
    try:
        return OmegaSpec.model_validate(data)
    except ValidationError as e:
        raise ValueError(_short_pydantic_error(e)) from e