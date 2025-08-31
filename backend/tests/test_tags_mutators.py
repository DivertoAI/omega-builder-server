from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_tags_add_and_delete():
    # list
    r = client.get("/api/tags")
    assert r.status_code == 200
    tags0 = set(r.json())

    # add
    r = client.post("/api/tags", json={"tag":"alpha"})
    assert r.status_code == 200

    r = client.get("/api/tags")
    assert r.status_code == 200
    assert "alpha" in set(r.json())

    # delete
    r = client.delete("/api/tags/alpha")
    assert r.status_code == 200
    r = client.get("/api/tags")
    assert r.status_code == 200
    assert "alpha" not in set(r.json())