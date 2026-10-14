"""
Shared test fixtures.

The autouse fixture below keeps BROWSER_PROFILES_DIR, WEEKLY_REPORTS_DIR
and EXPORTS_DIR pointed at a per-test tmp directory for every test, not
only the ones that explicitly exercise browser session handling, the
weekly scan, or exports. Without it, any test that touches
WebsiteIntelligenceProvider's browser-fallback path — even with a mock
engine injected — still calls resolve_session_handle(), which creates a
real directory on disk at the configured default
(data/browser_profiles/...); same story for any test that runs the
weekly scan and writes a real markdown report to reports/weekly/, or
that runs an export and writes a real .xlsx to exports/. Left
unguarded, running the suite would leave those directories behind in
the working tree as a side effect of tests, not something any test is
actually asserting on. Caught while verifying Phase F for real: running
the full suite once was enough to reproduce it for BROWSER_PROFILES_DIR;
WEEKLY_REPORTS_DIR and EXPORTS_DIR are isolated the same way from the
start, not after hitting the same bug twice.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_generated_dirs(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BROWSER_PROFILES_DIR", str(tmp_path / "browser_profiles"))
    monkeypatch.setenv("WEEKLY_REPORTS_DIR", str(tmp_path / "weekly_reports"))
    monkeypatch.setenv("EXPORTS_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("SCOUT_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("SCOUT_REPORTS_DIR", str(tmp_path / "anello1_reports"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
