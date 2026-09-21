"""
MockBrowserAutomationEngine — no real browser process, no network. Used
whenever MOCK_MODE=true or a real Chromium binary isn't available (this
project's own dev/CI environment may not have one — see
engine.default_executable_path_exists()).

Default behavior is a plausible COMPLETED outcome with empty output,
deliberately not test data pretending to be a real page — a caller that
needs specific content back for a test passes it via `scripted_outcomes`.
"""
from __future__ import annotations

from app.models.enums import BrowserTaskStatus
from app.tools.browser.engine import BrowserAutomationEngine
from app.tools.browser.session import BrowserSessionHandle
from app.tools.browser.task import BrowserTaskOutcome, BrowserTaskSpec


class MockBrowserAutomationEngine(BrowserAutomationEngine):
    def __init__(self, scripted_outcomes: dict[str, BrowserTaskOutcome] | None = None) -> None:
        self._scripted_outcomes = scripted_outcomes or {}

    async def run(self, spec: BrowserTaskSpec, session: BrowserSessionHandle) -> BrowserTaskOutcome:
        if spec.task_type in self._scripted_outcomes:
            return self._scripted_outcomes[spec.task_type]
        return BrowserTaskOutcome(
            status=BrowserTaskStatus.COMPLETED,
            output={},
            error=None,
            requires_human=False,
            duration_ms=0,
        )
