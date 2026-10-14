"""
Browser worker (Phase J) — the health-check process for the dedicated
browser-worker container. It is not a task consumer (see the module's own
docstring); these tests verify what it actually claims: mock/real engine
selection is honest and reflects the same MOCK_MODE/availability logic as
every provider, the smoke test genuinely drives the engine rather than
returning a canned response, and a real Chromium launch through this exact
code path succeeds in this sandbox (not just through
PlaywrightBrowserAutomationEngine tested directly, as test_browser_mock.py
already does).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.tools.browser.engine import default_executable_path_exists
from app.workers.browser_worker import app as browser_worker_app

_requires_chromium = pytest.mark.skipif(
    not default_executable_path_exists(), reason="pre-installed Chromium binary not present in this environment"
)


def test_health_reports_mock_engine_in_mock_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()

    with TestClient(browser_worker_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["smoke_test"]["engine_mode"] == "mock"
    assert body["smoke_test"]["ok"] is True


@_requires_chromium
def test_health_reports_real_engine_and_a_genuine_chromium_launch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MOCK_MODE", "false")
    get_settings.cache_clear()

    with TestClient(browser_worker_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["smoke_test"]["engine_mode"] == "real"
    assert body["smoke_test"]["status"] == "COMPLETED"
    assert body["smoke_test"]["ok"] is True
    assert body["status"] == "healthy"


def test_health_response_names_the_missing_task_consumer_honestly(monkeypatch: pytest.MonkeyPatch):
    """Not a throwaway assertion: this is the guardrail against silently
    turning the health note into a claim this container doesn't back up
    (e.g. dropping it during a later edit and letting /health imply a
    working task consumer that still doesn't exist)."""
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()

    with TestClient(browser_worker_app) as client:
        response = client.get("/health")

    assert "Milestone 14" in response.json()["note"]


def test_browser_executable_path_override_is_honored(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A BROWSER_EXECUTABLE_PATH pointing at a real file is treated as
    available even though it isn't this sandbox's hardcoded default path —
    proving build_browser_engine() actually reads the setting (Phase J)
    rather than only ever falling back to the hardcoded constant."""
    fake_binary = tmp_path / "chromium"
    fake_binary.write_text("not a real binary, just needs to exist")

    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("BROWSER_EXECUTABLE_PATH", str(fake_binary))
    get_settings.cache_clear()

    from app.tools.browser.engine import (
        PlaywrightBrowserAutomationEngine,
        browser_engine_available,
        build_browser_engine,
    )

    settings = get_settings()
    assert browser_engine_available(settings) is True
    engine = build_browser_engine(settings)
    assert isinstance(engine, PlaywrightBrowserAutomationEngine)
