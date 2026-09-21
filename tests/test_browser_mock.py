"""
Browser Automation Engine — Phase F. MockBrowserAutomationEngine tests run
everywhere (no browser needed). The Playwright-backed engine tests are
skipped automatically if the pre-installed Chromium binary isn't present
in this environment (rather than failing the whole suite over
infrastructure the test runner may not have) — same pattern as the
Postgres-dependent tests in test_database_integration.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import BrowserTaskStatus
from app.tools.browser.engine import (
    PlaywrightBrowserAutomationEngine,
    UnsupportedBrowserTaskError,
    default_executable_path_exists,
)
from app.tools.browser.mock_engine import MockBrowserAutomationEngine
from app.tools.browser.session import BrowserSessionHandle, resolve_session_handle
from app.tools.browser.task import BrowserTaskOutcome, BrowserTaskSpec

_requires_chromium = pytest.mark.skipif(
    not default_executable_path_exists(), reason="pre-installed Chromium binary not present in this environment"
)


@pytest.mark.asyncio
async def test_mock_engine_default_outcome_is_completed_and_empty():
    engine = MockBrowserAutomationEngine()
    session = BrowserSessionHandle(provider="test", profile_path=Path("/tmp/unused"))

    outcome = await engine.run(BrowserTaskSpec(provider="test", task_type="anything", params={}), session)

    assert outcome.status == BrowserTaskStatus.COMPLETED
    assert outcome.requires_human is False
    assert outcome.output == {}


@pytest.mark.asyncio
async def test_mock_engine_returns_scripted_outcome_for_matching_task_type():
    scripted = BrowserTaskOutcome(
        status=BrowserTaskStatus.WAITING_FOR_HUMAN,
        output={"login_form_detected": True},
        error=None,
        requires_human=True,
        duration_ms=0,
    )
    engine = MockBrowserAutomationEngine(scripted_outcomes={"check_requires_login": scripted})
    session = BrowserSessionHandle(provider="test", profile_path=Path("/tmp/unused"))

    outcome = await engine.run(
        BrowserTaskSpec(provider="test", task_type="check_requires_login", params={}), session
    )

    assert outcome is scripted


def test_resolve_session_handle_creates_namespaced_profile_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_PROFILES_DIR", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()
    handle = resolve_session_handle(provider="website_intelligence", account_label="acct-1")

    assert handle.profile_path == tmp_path / "website_intelligence" / "acct-1"
    assert handle.profile_path.is_dir()


@_requires_chromium
@pytest.mark.asyncio
async def test_real_engine_fetch_rendered_html_against_local_page(tmp_path):
    engine = PlaywrightBrowserAutomationEngine()
    session = BrowserSessionHandle(provider="test", profile_path=tmp_path / "profile")

    outcome = await engine.run(
        BrowserTaskSpec(provider="test", task_type="fetch_rendered_html", params={"url": "about:blank"}), session
    )

    assert outcome.status == BrowserTaskStatus.COMPLETED
    assert outcome.output is not None
    assert "<html>" in outcome.output["html"]


@_requires_chromium
@pytest.mark.asyncio
async def test_real_engine_detects_login_form_as_waiting_for_human(tmp_path):
    engine = PlaywrightBrowserAutomationEngine()
    session = BrowserSessionHandle(provider="test", profile_path=tmp_path / "profile")
    login_page = 'data:text/html,<html><body><form><input type="password"></form></body></html>'

    outcome = await engine.run(
        BrowserTaskSpec(provider="test", task_type="check_requires_login", params={"url": login_page}), session
    )

    assert outcome.status == BrowserTaskStatus.WAITING_FOR_HUMAN
    assert outcome.requires_human is True


@_requires_chromium
@pytest.mark.asyncio
async def test_real_engine_page_without_login_form_completes_normally(tmp_path):
    engine = PlaywrightBrowserAutomationEngine()
    session = BrowserSessionHandle(provider="test", profile_path=tmp_path / "profile")
    plain_page = "data:text/html,<html><body><p>no login here</p></body></html>"

    outcome = await engine.run(
        BrowserTaskSpec(provider="test", task_type="check_requires_login", params={"url": plain_page}), session
    )

    assert outcome.status == BrowserTaskStatus.COMPLETED
    assert outcome.requires_human is False


@pytest.mark.asyncio
async def test_unsupported_task_type_raises_before_any_engine_work():
    engine = PlaywrightBrowserAutomationEngine()
    session = BrowserSessionHandle(provider="test", profile_path=Path("/tmp/unused"))

    with pytest.raises(UnsupportedBrowserTaskError):
        await engine.run(BrowserTaskSpec(provider="test", task_type="do_something_helium10_specific", params={}), session)
