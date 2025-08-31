from __future__ import annotations
import json
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_filters_and_dedupe(tmp_path, monkeypatch):
    # isolate workspace/.omega
    monkeypatch.chdir(tmp_path)
    # create two stubs
    r = client.post("/api/stubs", json={"name":"A","path":"/hello","env":"default","tags":["x"]})
    assert r.status_code == 201
    sid1 = r.json()["id"]

    r = client.post("/api/stubs", json={"name":"B","path":"/hello2","env":"staging","tags":["y"]})
    assert r.status_code == 201
    sid2 = r.json()["id"]
    assert sid1 != sid2

    # duplicate in same env should 409
    r = client.post("/api/stubs", json={"name":"C","path":"/hello","env":"default"})
    assert r.status_code == 409

    # filters
    assert client.get("/api/stubs?q=hello").status_code == 200
    data = client.get("/api/stubs?env=staging").json()
    assert len(data) == 1 and data[0]["env"] == "staging"

    data = client.get("/api/stubs?tag=x").json()
    assert len(data) == 1 and data[0]["tags"] == ["x"]

    # update: toggle and retag
    r = client.put(f"/api/stubs/{sid1}", json={"enabled": False, "tags": ["x","z","z"]})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False and body["tags"] == ["x","z"]

    # get missing -> 404
    assert client.get("/api/stubs/does-not-exist").status_code == 404