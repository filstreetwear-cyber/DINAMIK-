"""
Shared test fixtures.

The autouse fixture below keeps BROWSER_PROFILES_DIR pointed at a
per-test tmp directory for every test, not only the ones that explicitly
exercise browser session handling. Without it, any test that touches
WebsiteIntelligenceProvider's browser-fallback path — even with a mock
engine injected — still calls resolve_session_handle(), which creates a
real directory on disk at the configured default
(data/browser_profiles/...). Left unguarded, running the suite would
leave that directory behind in the working tree as a side effect of
tests, not something any test is actually asserting on. Caught while
verifying Phase F for real: running the full suite once was enough to
reproduce it.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_browser_profiles_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BROWSER_PROFILES_DIR", str(tmp_path / "browser_profiles"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
