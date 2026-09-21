"""
Milestone 1 smoke test for the FastAPI app itself.

Previously verified only manually (uvicorn + curl). This makes it a
repeatable, automated check so a regression here fails CI, not just a
demo.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_reports_status_and_runtime_config():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert "environment" in body
    assert "mock_mode" in body
