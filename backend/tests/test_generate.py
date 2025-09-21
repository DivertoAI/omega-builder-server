from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_generate_requires_spec_or_brief():
    # No 'spec' or 'brief' -> 400 with helpful message
    r = client.post("/api/generate", json={})
    assert r.status_code == 400
    body = r.json()
    assert "Provide either 'spec' (dict or JSON string) or 'brief'" in body.get("detail", "")


def test_generate_with_brief_smoke_without_openai_key(monkeypatch):
    """
    If OPENAI is configured, this will actually run the agent.
    If not, we just assert the endpoint exists and returns a reasonable error
    (plan_and_validate would normally require OpenAI).
    To keep this stable across environments, force a small stub by monkeypatching.
    """
    # If you want this to be a true integration, delete this monkeypatch
    # and ensure OPENAI_API_KEY is set.
    from backend.app.services import plan_service

    class DummySpec:
        def model_dump_json(self, indent=2):
            return '{"navigation":[],"endpoints":[]}'

    def fake_plan_and_validate(brief: str, max_repairs: int = 1):
        # Return a tiny spec-like object and raw
        return DummySpec(), {}

    monkeypatch.setattr(plan_service, "plan_and_validate", fake_plan_and_validate)

    r = client.post("/api/generate", json={"brief": "smoke"})
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"
    assert "result" in data
    assert "job_id" in data  # bubbled up