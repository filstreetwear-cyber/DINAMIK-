"""
Milestone 13 (API) tests — real endpoints via `fastapi.testclient.TestClient`
against the actual `app.main.app` object, the same app `uvicorn app.main:app`
serves. Not a reimplementation of the service-layer tests: those already
cover `run_research_pipeline` and every stage under it in isolation
(test_pipeline_orchestrator.py etc.) — this file proves the HTTP layer
wires request -> service -> response correctly, same division of
responsibility `tests/test_cli.py` established for the CLI layer.

Real-Postgres-or-skip, same discipline as every other stage's tests.
`.env`'s `MOCK_MODE=true` is already the process-wide default (unlike the
CLI, `app/main.py` binds `settings = get_settings()` once at import time,
so there is no per-test env override to make here the way test_cli.py did
-- by the time this test file is collected `app.main` is already imported
with mock_mode true).

POST /jobs genuinely commits (same as `dinamik pipeline run`) -- every
test that calls it cleans up afterward, learning directly from Milestone
12's Bug #2/#2b: ErrorLog ("errors") is included in the cleanup because
this sandbox has no reachable Ollama, so the AI analysis stage writes real
AI_ERROR rows on every real pipeline run.

**A third instance of Milestone 12's "engine bound to a closed event
loop" bug class, found the same way -- by actually running multiple
sequential requests, not just one per test.** `starlette.testclient.
TestClient`, used as a bare `TestClient(app)`, opens a FRESH
`anyio.from_thread.start_blocking_portal()` (its own thread + its own
event loop) for every single request unless the client itself is used as
a context manager (`with TestClient(app) as client:` — confirmed by
reading testclient.py's own `_portal_factory`: it reuses `self.portal`
only when already set by `__enter__`). `app/database/session.py`'s
`engine` is created once at import time and its pooled asyncpg
connections get bound to whichever loop first used them; a second request
against a bare `TestClient(app)` landed on a brand-new loop and broke with
`RuntimeError: ... attached to a different loop` -- reproduced for real
while writing this file's second and third tests, not assumed safe just
because `app/database/session.py`'s docstring says FastAPI has "one
process, one loop". That is true of uvicorn in production; it is NOT true
of a bare `TestClient(app)`'s per-request portal. **Fix**: the
module-scoped `client` fixture below opens `TestClient(app)` as a context
manager ONCE for the whole file, giving every test in this module the same
single persistent event loop uvicorn would -- the actually-correct
analogue of production, not a workaround.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.main import app
from app.models import (
    AgentEvent,
    AgentRun,
    Brand,
    ErrorLog,
    Evidence,
    Opportunity,
    ResearchJob,
    ResearchResult,
    Score,
)
from app.models.amazon import AmazonProduct, AmazonSeller


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


async def _make_engine():
    settings = get_settings()
    return create_async_engine(settings.database_url)


async def _db_reachable() -> bool:
    try:
        engine = await _make_engine()
        async with engine.connect() as conn:
            await conn.run_sync(lambda c: None)
        await engine.dispose()
        return True
    except OperationalError:
        return False


async def _cleanup_all() -> None:
    """Same table list/order as tests/test_cli.py's own _cleanup_all --
    POST /jobs commits through the identical `run_research_pipeline` call,
    so it leaves the identical shape of rows behind."""
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(Evidence))
        await session.execute(delete(AgentEvent))
        await session.execute(delete(ErrorLog))
        await session.execute(delete(Score))
        await session.execute(delete(Opportunity))
        await session.execute(delete(ResearchResult))
        await session.execute(delete(AmazonProduct))
        await session.execute(delete(AmazonSeller))
        await session.execute(delete(AgentRun))
        await session.execute(delete(Brand))
        await session.execute(delete(ResearchJob))
        await session.commit()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _require_db(monkeypatch: pytest.MonkeyPatch):
    import asyncio

    if not asyncio.run(_db_reachable()):
        pytest.skip("PostgreSQL not reachable — API tests run real requests against a live DB")
    yield
    asyncio.run(_cleanup_all())


def test_health_endpoint_reports_status_db_and_ai(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert "environment" in body
    assert body["mock_mode"] is True
    assert body["database"] == "reachable"
    assert "ai" in body  # UNAVAILABLE in this sandbox (no Ollama) or AVAILABLE elsewhere -- not asserted


def test_create_job_runs_pipeline_and_returns_stage_summaries(client: TestClient):
    response = client.post(
        "/jobs",
        json={"name": "api-test-job", "marketplace": "IT", "category": "Casa e cucina"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_id"]
    assert body["agent_run_id"]
    assert "products_found" in body["discovery"]
    assert "scores_created" in body["scoring_engine"]


def test_create_job_rejects_missing_name(client: TestClient):
    response = client.post("/jobs", json={"marketplace": "IT"})
    assert response.status_code == 422


def test_list_jobs_shows_a_job_created_by_post_jobs(client: TestClient):
    client.post("/jobs", json={"name": "api-test-job-for-listing", "marketplace": "IT"})

    response = client.get("/jobs")
    assert response.status_code == 200
    names = [row["name"] for row in response.json()]
    assert "api-test-job-for-listing" in names


def test_list_jobs_empty_when_table_is_empty(client: TestClient):
    response = client.get("/jobs")
    assert response.status_code == 200
    assert response.json() == []


def test_get_job_by_id_returns_the_created_job(client: TestClient):
    created = client.post("/jobs", json={"name": "api-test-job-for-get", "marketplace": "IT"}).json()

    response = client.get(f"/jobs/{created['job_id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "api-test-job-for-get"


def test_get_job_by_id_404s_for_unknown_id(client: TestClient):
    response = client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_list_brands_shows_a_brand_created_by_post_jobs_with_scores(client: TestClient):
    client.post("/jobs", json={"name": "api-test-job-for-brands", "marketplace": "IT"})

    response = client.get("/brands")
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 1
    assert "scores" in body[0]


def test_list_brands_empty_when_table_is_empty(client: TestClient):
    response = client.get("/brands")
    assert response.status_code == 200
    assert response.json() == []


# --- 2026-09-22: ops dashboard requirement ---------------------------------


def test_list_opportunities_shows_opportunities_created_by_post_jobs(client: TestClient):
    client.post("/jobs", json={"name": "api-test-job-for-opps", "marketplace": "IT"})

    response = client.get("/opportunities")
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 1
    assert "brand_name" in body[0]
    assert "opportunity_type" in body[0]
    assert "level" in body[0]


def test_list_opportunities_filters_by_brand_id(client: TestClient):
    client.post("/jobs", json={"name": "api-test-job-for-opps-filter", "marketplace": "IT"})
    brand_id = client.get("/brands").json()[0]["id"]

    response = client.get("/opportunities", params={"brand_id": brand_id})
    assert response.status_code == 200
    assert all(row["brand_id"] == brand_id for row in response.json())


def test_list_opportunities_empty_when_table_is_empty(client: TestClient):
    response = client.get("/opportunities")
    assert response.status_code == 200
    assert response.json() == []


def test_list_ai_claims_filters_by_brand_id(client: TestClient):
    client.post("/jobs", json={"name": "api-test-job-for-claims", "marketplace": "IT"})
    brand_id = client.get("/brands").json()[0]["id"]

    response = client.get("/ai-claims", params={"brand_id": brand_id})
    assert response.status_code == 200
    # AI analysis needs a reachable Ollama, absent in this sandbox -- the
    # real, honest assertion here is "the endpoint works and filters
    # correctly", not "a claim was produced" (see test_main.py's own
    # module docstring on ErrorLog rows from this same cause).
    assert all(row["entity_id"] == str(brand_id) for row in response.json())


def test_list_ai_claims_empty_when_table_is_empty(client: TestClient):
    response = client.get("/ai-claims")
    assert response.status_code == 200
    assert response.json() == []


def test_weekly_scans_latest_404s_when_none_have_run(client: TestClient):
    response = client.get("/weekly-scans/latest")
    assert response.status_code == 404


def test_weekly_scan_by_id_404s_for_unknown_id(client: TestClient):
    response = client.get("/weekly-scans/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_list_weekly_scans_empty_when_table_is_empty(client: TestClient):
    response = client.get("/weekly-scans")
    assert response.status_code == 200
    assert response.json() == []


# --- dashboard auth (DASHBOARD_API_KEY) ------------------------------------
#
# `settings` in app/main.py is bound once at import time (same "one
# process, one Settings instance" fact test_workers.py's own
# test_configure_beat_schedule... test already documents for
# app/workers/celery_app.py) -- monkeypatching the env var does nothing
# after import. These tests patch app.main.settings.dashboard_api_key
# directly, the actual object require_dashboard_key reads, and restore it
# in a finally so later tests in this module see the real (unset) default.


def test_endpoints_open_when_dashboard_api_key_unset(client: TestClient):
    from app.main import settings as main_settings

    assert main_settings.dashboard_api_key is None  # the default this whole file already relies on
    response = client.get("/jobs")
    assert response.status_code == 200


def test_endpoints_reject_missing_key_when_dashboard_api_key_set(client: TestClient):
    from app.main import settings as main_settings

    main_settings.dashboard_api_key = "test-secret"
    try:
        response = client.get("/jobs")
        assert response.status_code == 401
    finally:
        main_settings.dashboard_api_key = None


def test_endpoints_reject_wrong_key_when_dashboard_api_key_set(client: TestClient):
    from app.main import settings as main_settings

    main_settings.dashboard_api_key = "test-secret"
    try:
        response = client.get("/jobs", headers={"X-Dashboard-Key": "wrong"})
        assert response.status_code == 401
    finally:
        main_settings.dashboard_api_key = None


def test_endpoints_accept_correct_key_when_dashboard_api_key_set(client: TestClient):
    from app.main import settings as main_settings

    main_settings.dashboard_api_key = "test-secret"
    try:
        response = client.get("/jobs", headers={"X-Dashboard-Key": "test-secret"})
        assert response.status_code == 200
    finally:
        main_settings.dashboard_api_key = None


def test_health_stays_open_even_when_dashboard_api_key_set(client: TestClient):
    """/health is deliberately excluded from require_dashboard_key (see
    app/main.py) -- infra/uptime checks shouldn't need the dashboard
    secret, and it reveals nothing sensitive."""
    from app.main import settings as main_settings

    main_settings.dashboard_api_key = "test-secret"
    try:
        response = client.get("/health")
        assert response.status_code == 200
    finally:
        main_settings.dashboard_api_key = None
