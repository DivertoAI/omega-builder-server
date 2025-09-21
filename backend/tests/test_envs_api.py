from __future__ import annotations
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_envs_update_and_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # default exists
    r = client.get("/api/environments")
    assert r.status_code == 200
    assert any(e["id"] == "default" for e in r.json())

    # create/update a good id
    r = client.put("/api/environments/staging-1", json={"description": "staging", "enabled": True})
    assert r.status_code == 200
    r = client.get("/api/environments")
    ids = [e["id"] for e in r.json()]
    assert "staging-1" in ids

    # bad ids
    assert client.put("/api/environments/bad space", json={}).status_code == 400