from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator, AliasChoices


# --- small helpers ---
def _slugify(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"['\"`]+", "", t)
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t or "item"


def _title_from_id_or_path(id_val: Optional[str], path: Optional[str]) -> str:
    if id_val:
        return id_val.replace("_", " ").replace("-", " ").title()
    if path:
        seg = path.strip("/").split("/")[-1]
        seg = re.sub(r"[:{}]", "", seg)
        return seg.replace("_", " ").replace("-", " ").title() or "Untitled"
    return "Untitled"


def _action_dict_to_string(d: Dict[str, Any]) -> str:
    if not isinstance(d, dict):
        return str(d)

    t = (d.get("type") or "").strip().lower()
    if t == "navigate":
        to = d.get("to") or d.get("path") or "/"
        return f"nav:{to}"
    if t == "api":
        name = d.get("name") or d.get("endpoint") or d.get("id") or "api"
        return f"api:{name}"
    if t == "toast":
        msg = d.get("message") or d.get("text") or ""
        return f"toast:{msg}"

    try:
        return "json:" + json.dumps(d, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return str(d)


# --- Tokens / Theme ---
class ColorToken(BaseModel):
    name: str
    value: str
    role: Optional[str] = None
    model_config = {"extra": "ignore"}


class TypographyToken(BaseModel):
    name: str
    font_family: str = Field(default="Inter")
    size: int = Field(default=14)
    weight: int = Field(default=400)
    line_height: Optional[int] = None
    model_config = {"extra": "ignore"}


# --- Entities & APIs ---
class FieldDef(BaseModel):
    name: str
    type: Literal["string", "int", "double", "bool", "image", "date", "list", "map"] = "string"
    required: bool = False
    model_config = {"extra": "ignore"}


class Entity(BaseModel):
    name: str
    fields: List[FieldDef]
    model_config = {"extra": "ignore"}


class Endpoint(BaseModel):
    method: Optional[Literal["GET", "POST", "PUT", "PATCH", "DELETE"]] = None
    path: str
    mock_file: Optional[str] = None
    model_config = {"extra": "ignore"}


class ApiDef(BaseModel):
    """
    Flexible API shape:
    - Accepts `id` OR `name` (stored in `name`)
    - `endpoints` may be:
        * dict[str, Endpoint]
        * list[str]                            -> paths
        * list[dict{ name/id/operationId, ...}] -> normalized to Endpoint
    - Single-endpoint shortcut via `method` + `path`
    """
    name: str = Field(default="", validation_alias=AliasChoices("name", "id"), description="API name")
    base_url: Optional[str] = None
    mock_file: Optional[str] = None
    endpoints: Dict[str, Endpoint] = Field(default_factory=dict)
    method: Optional[str] = None
    path: Optional[str] = None

    @field_validator("endpoints", mode="before")
    @classmethod
    def _normalize_endpoints_before(cls, v):
        if v is None:
            return {}
        # dict -> keep but coerce inner values
        if isinstance(v, dict):
            out = {}
            for key, val in v.items():
                if isinstance(val, str):
                    out[key] = {"path": val}
                elif isinstance(val, dict):
                    out[key] = {
                        "method": val.get("method") or val.get("http_method") or val.get("verb"),
                        "path": val.get("path") or val.get("url") or val.get("route") or val.get("endpoint") or "/",
                        "mock_file": val.get("mock_file") or val.get("mock"),
                    }
                else:
                    out[key] = {"path": str(val)}
            return out
        # list -> convert to dict with stable keys
        if isinstance(v, list):
            out: Dict[str, Dict[str, Any]] = {}
            for i, item in enumerate(v, start=1):
                if isinstance(item, str):
                    key = f"ep{i}"
                    out[key] = {"path": item}
                    continue
                if isinstance(item, dict):
                    key = (
                        item.get("key")
                        or item.get("name")
                        or item.get("id")
                        or item.get("operationId")
                        or f"ep{i}"
                    )
                    out[key] = {
                        "method": item.get("method") or item.get("http_method") or item.get("verb"),
                        "path": item.get("path") or item.get("url") or item.get("route") or item.get("endpoint") or "/",
                        "mock_file": item.get("mock_file") or item.get("mock"),
                    }
                    continue
                # fallback
                out[f"ep{i}"] = {"path": str(item)}
            return out
        return v

    def model_post_init(self, __ctx) -> None:  # type: ignore[override]
        if not self.endpoints and self.path:
            key = (self.method or "get").lower()
            key = f"{key}_{(self.name or '').strip()}" or key
            self.endpoints = {
                key: Endpoint(method=self.method, path=self.path, mock_file=self.mock_file)
            }

    model_config = {"extra": "ignore"}


# --- Navigation ---
_ALLOWED_TEMPLATES = {"list", "detail", "cart", "orders", "profile", "seller_form", "search"}


class NavItem(BaseModel):
    # accept either "id" or "name"
    id: str = Field(default="", validation_alias=AliasChoices("id", "name"))
    title: Optional[str] = None
    template: str = "list"
    data_source: Optional[str] = None
    item_fields: Optional[List[str]] = None
    actions: Optional[List[str]] = None
    path: Optional[str] = None
    endpoint: Optional[str] = None

    @field_validator("template", mode="before")
    @classmethod
    def _coerce_template(cls, v: str) -> str:
        if not isinstance(v, str):
            return "list"
        vv = v.lower().strip()
        if vv == "grid":
            return "list"
        return vv if vv in _ALLOWED_TEMPLATES else "list"

    @field_validator("actions", mode="before")
    @classmethod
    def _normalize_actions(cls, v):
        if v is None:
            return None
        out: List[str] = []
        if isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    out.append(it)
                elif isinstance(it, dict):
                    out.append(_action_dict_to_string(it))
                else:
                    out.append(str(it))
            return out or None
        if isinstance(v, dict):
            return [_action_dict_to_string(v)]
        return [str(v)]

    @model_validator(mode="before")
    @classmethod
    def _fill_id_from_other_fields(cls, data):
        if isinstance(data, dict):
            cur = data.get("id")
            if not cur or not str(cur).strip():
                candidate = data.get("name") or data.get("path") or data.get("title")
                data["id"] = _slugify(str(candidate) if candidate else "item")
        return data

    @model_validator(mode="after")
    def _ensure_title(self):
        if not self.title or not self.title.strip():
            self.title = _title_from_id_or_path(self.id, self.path)
        return self

    model_config = {"extra": "ignore"}


class Navigation(BaseModel):
    home: str
    items: List[NavItem]

    @field_validator("home", mode="before")
    @classmethod
    def _coerce_home(cls, v):
        # treat blank as missing
        if isinstance(v, str):
            return v if v.strip() else "home"
        if isinstance(v, dict):
            cand = v.get("id") or v.get("name")
            if not cand:
                cand = _slugify(v.get("path") or v.get("title") or "home")
            return str(cand)
        if isinstance(v, list) and v:
            it = v[0]
            if isinstance(it, str):
                return it
            if isinstance(it, dict):
                return cls._coerce_home(it)
        return "home"

    @field_validator("items", mode="before")
    @classmethod
    def _normalize_items_list(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            out = []
            for k, val in v.items():
                if isinstance(val, dict):
                    val = {"id": val.get("id") or k, **val}
                else:
                    val = {"id": k, "title": str(val)}
                out.append(val)
            return out
        if isinstance(v, list):
            out = []
            for it in v:
                if isinstance(it, str):
                    out.append({"id": _slugify(it), "title": it})
                elif isinstance(it, dict):
                    if not it.get("id") or not str(it.get("id")).strip():
                        it["id"] = _slugify(it.get("name") or it.get("path") or it.get("title") or "item")
                    out.append(it)
                else:
                    out.append({"id": "item", "title": str(it)})
            return out
        return v

    @model_validator(mode="after")
    def _ensure_home_in_items(self):
        # ensure all item ids are non-empty
        for it in self.items:
            if not it.id or not it.id.strip():
                it.id = _slugify(it.title or it.path or "item")

        if self.items:
            ids = [it.id for it in self.items if it.id]
            if not self.home or not str(self.home).strip():
                self.home = ids[0]
            elif self.home not in ids:
                slug_map = {_slugify(x): x for x in ids}
                self.home = slug_map.get(_slugify(self.home), ids[0])
        else:
            self.home = "home"
        return self

    model_config = {"extra": "ignore"}


# --- Acceptance tests ---
class AcceptanceCase(BaseModel):
    id: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _accept_desc_aliases(cls, data):
        if isinstance(data, dict):
            if "description" not in data or not data.get("description"):
                desc = data.get("title") or data.get("desc") or data.get("details")
                if desc:
                    data["description"] = desc
        return data

    @model_validator(mode="after")
    def _ensure_id_and_desc(self):
        if not self.description:
            self.description = "Acceptance criterion"
        if not self.id:
            self.id = _slugify(self.description)[:64]
        return self

    model_config = {"extra": "ignore"}


# --- Root Spec ---
class OmegaSpec(BaseModel):
    name: str
    description: Optional[str] = None
    theme: Dict[str, List] = Field(default_factory=lambda: {"colors": [], "typography": []})
    entities: List[Entity] = Field(default_factory=list)
    apis: List[ApiDef] = Field(default_factory=list)
    navigation: Navigation
    acceptance: List[AcceptanceCase] = Field(default_factory=list)
    model_config = {"extra": "ignore"}


def validate_spec(obj: dict) -> OmegaSpec:
    """
    Validate raw dict into OmegaSpec.
    """
    theme = obj.get("theme", {})
    colors = theme.get("colors", [])
    typography = theme.get("typography", [])

    def _coerce_list(items, model):
        out: List = []
        for it in items or []:
            try:
                out.append(model.model_validate(it))
            except Exception:
                out.append(it)
        return out

    if isinstance(theme, dict):
        theme["colors"] = _coerce_list(colors, ColorToken)
        theme["typography"] = _coerce_list(typography, TypographyToken)
        obj["theme"] = theme

    return OmegaSpec.model_validate(obj)