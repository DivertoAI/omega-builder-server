from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    # Basic contract checks
    assert data.get("status") == "ok"
    assert isinstance(data.get("env"), dict)
    assert isinstance(data.get("features"), dict)
    assert isinstance(data.get("probes"), dict)