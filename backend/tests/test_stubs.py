from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_list_stubs_initially_empty():
    r = client.get("/api/stubs")
    assert r.status_code == 200
    assert r.json() == []


def test_create_get_update_delete_stub_happy_path():
    # create
    r = client.post("/api/stubs", json={"name": "ex", "path": "/hello", "env": "default"})
    assert r.status_code == 201
    created = r.json()
    sid = created["id"]
    assert created["name"] == "ex"
    assert created["path"] == "/hello"
    assert created["env"] == "default"
    assert created["enabled"] is True
    assert created["tags"] == []

    # get
    r = client.get(f"/api/stubs/{sid}")
    assert r.status_code == 200
    assert r.json()["id"] == sid

    # update (change path)
    r = client.put(f"/api/stubs/{sid}", json={"path": "/hello2"})
    assert r.status_code == 200
    assert r.json()["path"] == "/hello2"

    # delete
    r = client.delete(f"/api/stubs/{sid}")
    assert r.status_code == 204

    # get after delete -> 404
    r = client.get(f"/api/stubs/{sid}")
    assert r.status_code == 404


def test_prevent_duplicate_path_within_env():
    r1 = client.post("/api/stubs", json={"name": "a", "path": "/dup", "env": "prod"})
    assert r1.status_code == 201

    r2 = client.post("/api/stubs", json={"name": "b", "path": "/dup", "env": "prod"})
    assert r2.status_code == 409  # same env+path rejected

    # Same path in different env is OK
    r3 = client.post("/api/stubs", json={"name": "c", "path": "/dup", "env": "staging"})
    assert r3.status_code == 201