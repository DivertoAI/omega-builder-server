from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_export_import_roundtrip(tmp_path, monkeypatch):
    # Start clean
    r = client.get("/api/stubs")
    assert r.status_code == 200

    # Create one
    r = client.post("/api/stubs", json={"name":"ex","path":"/hello","env":"default"})
    assert r.status_code == 201

    # Export
    r = client.get("/api/stubs/export")
    assert r.status_code == 200
    data = r.json()
    assert "stubs" in data and isinstance(data["stubs"], list)
    n_before = len(data["stubs"])
    assert n_before >= 1

    # Import a new one (merge)
    r = client.post("/api/stubs/import", json={
        "stubs":[{"name":"bulk","path":"/bulk","env":"default"}],
        "mode":"merge"
    })
    assert r.status_code == 200
    assert r.json()["imported"] == 1

    # Verify count grew
    r = client.get("/api/stubs")
    assert r.status_code == 200
    assert len(r.json()) >= n_before + 1