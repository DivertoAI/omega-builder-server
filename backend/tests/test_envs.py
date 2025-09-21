from __future__ import annotations

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_envs_list_has_default():
    r = client.get("/api/environments")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # ensure default env exists and has expected shape
    default = next((e for e in data if e.get("id") == "default"), None)
    assert default is not None
    assert set(default.keys()) == {"id", "description", "enabled"}
    assert default["enabled"] is True


def test_envs_create_and_update_roundtrip():
    # create/overwrite staging
    payload = {"description": "Staging env", "enabled": False}
    r = client.put("/api/environments/staging", json=payload)
    assert r.status_code == 200
    env = r.json()
    assert env["id"] == "staging"
    assert env["description"] == "Staging env"
    assert env["enabled"] is False

    # list should contain our env
    r = client.get("/api/environments")
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()]
    assert "staging" in ids

    # update enabled -> True
    r = client.put("/api/environments/staging", json={"enabled": True})
    assert r.status_code == 200
    env = r.json()
    assert env["enabled"] is True


def test_envs_rejects_bad_types():
    # enabled must be boolean
    r = client.put("/api/environments/dev", json={"enabled": "yes"})
    assert r.status_code == 400
    assert "enabled" in r.json()["detail"]

    # description must be str or null
    r = client.put("/api/environments/dev", json={"description": {"oops": 1}})
    assert r.status_code == 400
    assert "description" in r.json()["detail"]