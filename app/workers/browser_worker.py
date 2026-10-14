"""
Browser worker — Phase J (Docker/VPS).

This is the process that runs inside the dedicated `browser-worker`
container in docker-compose.yml. Its job is narrower than the name might
suggest, on purpose: there is no queue of `BrowserTask` rows to pull from
yet — that needs a real Celery worker wired to Redis (Milestone 14:
Workers + scheduler, still unbuilt; `app/workers/celery_app.py` does not
exist). Building a fake task consumer for a queue nothing populates would
be exactly the kind of step this project has committed to not skipping
just to make Phase J look more finished than it is.

What Phase J actually needs from this container, and what this module
does: prove the browser-worker image builds with a working Chromium (not
just that `pip install playwright` succeeded), prove the persistent
BROWSER_PROFILES_DIR volume mounts and is writable, and give Docker a
healthcheck endpoint that reflects a genuine browser launch rather than
"the process is running" alone. It runs one real (or honestly-labeled
mock, in MOCK_MODE) browser smoke test at startup via the exact same
`build_browser_engine()` / `PlaywrightBrowserAutomationEngine` path every
other provider uses — not a separate, unverified code path — caches the
result, and serves it at GET /health. Milestone 14 is expected to grow
this file into a real task consumer; until then, this is what "the
browser-worker container is healthy" honestly means.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI

from app.core.config import get_settings
from app.models.enums import BrowserTaskStatus
from app.tools.browser.engine import build_browser_engine
from app.tools.browser.mock_engine import MockBrowserAutomationEngine
from app.tools.browser.session import resolve_session_handle
from app.tools.browser.task import BrowserTaskSpec

_SMOKE_TEST_PROVIDER = "browser_worker_smoke_test"

_smoke_test_result: dict[str, Any] | None = None


async def run_smoke_test() -> dict[str, Any]:
    """Navigate to about:blank and confirm the engine returns rendered
    HTML. about:blank needs no network access, so this is a genuine
    "does Chromium launch and drive a page inside this container" check
    that works the same in a network-restricted environment as on the
    real VPS."""
    settings = get_settings()
    engine = build_browser_engine(settings)
    engine_mode = "mock" if isinstance(engine, MockBrowserAutomationEngine) else "real"
    checked_at = datetime.now(UTC).isoformat()

    session = resolve_session_handle(
        provider=_SMOKE_TEST_PROVIDER, account_label="startup", settings=settings
    )
    spec = BrowserTaskSpec(
        provider=_SMOKE_TEST_PROVIDER,
        task_type="fetch_rendered_html",
        params={"url": "about:blank"},
    )

    try:
        outcome = await engine.run(spec, session)
    except Exception as exc:  # noqa: BLE001 — a bug here must not take the health server down with it
        return {
            "engine_mode": engine_mode,
            "ok": False,
            "error": str(exc),
            "checked_at": checked_at,
        }

    ok = outcome.status in (BrowserTaskStatus.COMPLETED, BrowserTaskStatus.WAITING_FOR_HUMAN)
    return {
        "engine_mode": engine_mode,
        "ok": ok,
        "status": outcome.status.value,
        "duration_ms": outcome.duration_ms,
        "checked_at": checked_at,
    }


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _smoke_test_result
    _smoke_test_result = await run_smoke_test()
    yield


app = FastAPI(title="Dinamik Advisor — Browser Worker", version="0.1.0", lifespan=_lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    result = _smoke_test_result or await run_smoke_test()
    return {
        "status": "healthy" if result.get("ok") else "unhealthy",
        "browser_profiles_dir": settings.browser_profiles_dir,
        "smoke_test": result,
        "note": (
            "Proves the container's browser toolchain and BROWSER_PROFILES_DIR "
            "volume work end-to-end. Does not yet consume real BrowserTask rows "
            "from a queue -- that is Milestone 14 (Workers + scheduler), not "
            "part of Phase J."
        ),
    }
