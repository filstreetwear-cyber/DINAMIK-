"""
BrowserAutomationEngine — the acquisition mechanism, not a data source
(refactor doc: this distinction is the whole point of Phase F). Nothing in
this file knows about Helium10, Amazon, or any specific target — it
implements a small, generic set of task_types; a provider that needs
browser automation (WebsiteIntelligenceProvider today; a future
Helium10BrowserProvider once Phase G is unblocked) supplies its own
url/selectors as BrowserTaskSpec.params.

Two task_types implemented today, chosen because they are exactly what
Phase E's "JS fallback / Browser fallback" tiers and Phase G's
human-in-the-loop requirement need, without inventing anything
site-specific:

* "fetch_rendered_html" — navigate, wait for network-idle, return the
  fully rendered HTML (post-JS-execution) DOM. This is the Website
  Intelligence provider's browser-tier fallback.
* "check_requires_login" — navigate, look for a password input field.
  Generic proxy for "this page needs a human to authenticate" — it does
  not attempt to log in, fill any field, or solve anything. A provider
  that needs a REAL login flow (Helium10BrowserProvider, later) is
  expected to drive that itself using the session this engine opens; this
  engine's job stops at "yes, a human needs to act here."

WAITING_FOR_HUMAN is never converted into a retry-until-success loop by
this engine — one call, one outcome. Retrying is an orchestration decision
that belongs above this layer.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.models.enums import BrowserTaskStatus
from app.tools.browser.session import BrowserSessionHandle
from app.tools.browser.task import BrowserTaskOutcome, BrowserTaskSpec

# The pre-installed Chromium in this project's environments is reached via
# a stable symlink (see the environment's own guidance: launch with this
# executable_path instead of letting Playwright try to download a browser,
# which fails in a network-restricted sandbox and is unnecessary when a
# compatible browser is already provisioned).
_DEFAULT_EXECUTABLE_PATH = "/opt/pw-browsers/chromium"

_SUPPORTED_TASK_TYPES = frozenset({"fetch_rendered_html", "check_requires_login"})


class BrowserAutomationEngine(ABC):
    @abstractmethod
    async def run(self, spec: BrowserTaskSpec, session: BrowserSessionHandle) -> BrowserTaskOutcome: ...


class UnsupportedBrowserTaskError(Exception):
    def __init__(self, task_type: str) -> None:
        self.task_type = task_type
        super().__init__(f"no generic browser task_type '{task_type}' — supported: {sorted(_SUPPORTED_TASK_TYPES)}")


class PlaywrightBrowserAutomationEngine(BrowserAutomationEngine):
    def __init__(self, executable_path: str | None = None, headless: bool = True) -> None:
        self._executable_path = executable_path or _DEFAULT_EXECUTABLE_PATH
        self._headless = headless

    async def run(self, spec: BrowserTaskSpec, session: BrowserSessionHandle) -> BrowserTaskOutcome:
        if spec.task_type not in _SUPPORTED_TASK_TYPES:
            raise UnsupportedBrowserTaskError(spec.task_type)

        # Imported lazily so importing this module (e.g. for type-checking,
        # or when MockBrowserAutomationEngine is all a test needs) never
        # requires the playwright package or a browser binary to be present.
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright

        started = time.monotonic()
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(session.profile_path),
                    headless=self._headless,
                    executable_path=self._executable_path,
                )
                try:
                    output = await self._dispatch(context, spec)
                finally:
                    await context.close()
        except PlaywrightError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return BrowserTaskOutcome(
                status=BrowserTaskStatus.FAILED,
                output=None,
                error=str(exc),
                requires_human=False,
                duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        requires_human = bool(output.get("requires_human", False))
        return BrowserTaskOutcome(
            status=BrowserTaskStatus.WAITING_FOR_HUMAN if requires_human else BrowserTaskStatus.COMPLETED,
            output=output,
            error=None,
            requires_human=requires_human,
            duration_ms=duration_ms,
        )

    async def _dispatch(self, context: Any, spec: BrowserTaskSpec) -> dict[str, Any]:
        page = await context.new_page()
        try:
            if spec.task_type == "fetch_rendered_html":
                await page.goto(spec.params["url"], wait_until="networkidle")
                html = await page.content()
                return {"html": html, "final_url": page.url}

            if spec.task_type == "check_requires_login":
                await page.goto(spec.params["url"], wait_until="networkidle")
                password_field = await page.query_selector("input[type='password']")
                return {"login_form_detected": password_field is not None, "requires_human": password_field is not None}

            raise UnsupportedBrowserTaskError(spec.task_type)
        finally:
            await page.close()


def default_executable_path_exists() -> bool:
    """Cheap check used at startup/health time to decide whether the real
    engine can even be constructed, before attempting a launch."""
    return Path(_DEFAULT_EXECUTABLE_PATH).exists()


def build_browser_engine(settings: Settings | None = None) -> BrowserAutomationEngine:
    """The one place that decides real vs mock, so callers (providers,
    tests) never have to duplicate this logic. MOCK_MODE always wins; a
    missing Chromium binary outside MOCK_MODE also falls back to mock
    rather than crashing a whole research job over one missing browser."""
    from app.tools.browser.mock_engine import MockBrowserAutomationEngine

    settings = settings or get_settings()
    if settings.mock_mode or not default_executable_path_exists():
        return MockBrowserAutomationEngine()
    return PlaywrightBrowserAutomationEngine(headless=settings.browser_headless)
