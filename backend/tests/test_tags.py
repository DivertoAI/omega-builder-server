from __future__ import annotations
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_tags_create_and_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = client.get("/api/tags")
    assert r.status_code == 200 and r.json() == []

    r = client.post("/api/tags", json={"name":"backend"})
    assert r.status_code == 200
    assert r.json()["created"] is True

    r = client.post("/api/tags", json={"name":"backend"})
    assert r.status_code == 200
    assert r.json()["created"] is False

    r = client.get("/api/tags")
    assert r.status_code == 200
    assert r.json() == ["backend"]