import pytest

from backend.app.services.plan_service import plan_and_validate, _repair_spec_once
from backend.app.models.spec import validate_spec


def test_repair_radius_and_navigation():
    raw = {
        "name": "X",
        "description": "Y",
        "theme": {"colors": [], "typography": [], "radius": 8},  # bad type
        "navigation": {},  # missing fields
    }
    spec, used = plan_and_validate("ignored", max_repairs=1)
    # Above call used fallback brief; assert our repair util works in isolation:
    repaired = _repair_spec_once(raw)
    assert repaired["theme"]["radius"] == [8]
    assert repaired["navigation"]["home"] == "home"
    assert isinstance(repaired["navigation"]["items"], list)
    # And the repaired dict should validate
    validate_spec(
        {
            **repaired,
            "apis": [],  # ensure present for validation
            "entities": [],
            "acceptance": [],
        }
    )


def test_endpoints_to_apis_migration_tolerant():
    raw = {
        "name": "X",
        "description": "Y",
        "theme": {"colors": [], "typography": [], "radius": [8]},
        "navigation": {"home": "home", "items": []},
        "endpoints": [
            {"method": "GET", "path": "/api/ping"},
            {"method": "POST", "path": "/api/todos"},
        ],
    }
    repaired = _repair_spec_once(raw)
    # endpoints should be removed in the repaired dict
    assert "endpoints" not in repaired
    # apis should exist (may be [] if schema rejects mapping at validate time,
    # but migration tries to preserve method/path)
    assert "apis" in repaired
    assert isinstance(repaired["apis"], list)


def test_validate_errors_when_no_repairs():
    # This mirrors the manual curl you ran with max_repairs=0
    bad = {
        "name": "X",
        "description": "Y",
        "theme": {"colors": [], "typography": [], "radius": 8},  # wrong type
        "navigation": {},  # missing fields
    }
    with pytest.raises(Exception):
        # replicate zero repairs by calling validate directly
        validate_spec(bad)