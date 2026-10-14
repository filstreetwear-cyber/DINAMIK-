"""
Milestone 14 (Workers + scheduler) tests.

Celery tasks are invoked via `.apply(...)` -- Celery's own documented way
to execute a task's real body synchronously, in-process, without a
broker. This is not a mock of the task: `run_research_pipeline_task`'s
actual code runs, against real Postgres, in MOCK_MODE (same as every
other stage's tests) -- `.apply()` only skips the Redis round-trip, the
same distinction `tests/test_cli.py` draws between "the real object" and
"a mock of the layer". A genuine end-to-end run through a real Redis
broker and a real `celery -A app.workers.celery_app worker` subprocess
was done by hand while building this (see docs/architecture.md addendum
(19)) rather than as an automated test here -- a subprocess worker
byte-for-byte reproducing `make run-worker` is valuable to prove once,
live, but orchestrating it inside pytest would trade a small amount of
extra realism for real flakiness (process startup timing, orphaned
processes on a failed teardown) that the rest of this suite has
deliberately avoided.

Real-Postgres-or-skip and explicit per-test cleanup, same discipline (and
same table list) as tests/test_cli.py and tests/test_main.py --
`run_research_pipeline_task` commits through the identical
`run_research_pipeline` call every other real-pipeline-run test does.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
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
from app.workers import celery_app as celery_app_module
from app.workers.celery_app import _configure_beat_schedule, _crontab_from_string, celery_app
from app.workers.tasks import run_research_pipeline_task, run_scheduled_research_job


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
        pytest.skip("PostgreSQL not reachable — worker tests run real tasks against a live DB")
    yield
    asyncio.run(_cleanup_all())


def test_run_research_pipeline_task_creates_job_and_commits():
    result = run_research_pipeline_task.apply(
        kwargs={"name": "worker-test-job", "marketplace": "IT", "category": "Casa e cucina"}
    )

    assert result.successful(), result.traceback
    body = result.result
    assert body["job_id"]
    assert "products_found" in body
    assert "scores_created" in body
    assert body["deduplicated"] is False


def test_run_research_pipeline_task_retry_policy_is_configured():
    """2026-09-22 (post Reality Audit §25 "Celery reliability"): the audit
    found zero retry/backoff on any task (grep of app/workers/ returned
    nothing). This asserts the real Celery task object's registered
    options, not a re-implementation of Celery's own retry mechanics."""
    task = run_research_pipeline_task
    assert task.max_retries == 3
    assert task.retry_backoff is True
    assert task.retry_backoff_max == 600
    assert task.retry_jitter is True
    # ProviderNotConfiguredError/ProviderAuthError (permanent config/
    # credential errors) must NOT be in this set -- retrying them would
    # just waste attempts before failing the same way every time.
    from app.tools.core.provider import (
        ProviderNotConfiguredError,
        ProviderRateLimitedError,
        ProviderUnavailableError,
    )

    assert ProviderUnavailableError in task.autoretry_for
    assert ProviderRateLimitedError in task.autoretry_for
    assert ProviderNotConfiguredError not in task.autoretry_for


def test_run_research_pipeline_task_deduplicates_identical_call_within_window():
    """Best-effort idempotency guard (§25/§38) -- calling the task body
    twice with identical parameters (simulating a Celery autoretry
    re-invocation after a transient failure) must not create a second
    ResearchJob row."""
    first = run_research_pipeline_task.apply(
        kwargs={"name": "worker-dedup-test-job", "marketplace": "IT", "category": "Casa e cucina"}
    )
    assert first.successful(), first.traceback
    assert first.result["deduplicated"] is False

    second = run_research_pipeline_task.apply(
        kwargs={"name": "worker-dedup-test-job", "marketplace": "IT", "category": "Casa e cucina"}
    )
    assert second.successful(), second.traceback
    assert second.result["deduplicated"] is True
    assert second.result["job_id"] == first.result["job_id"]


async def _fetch_job_by_id(job_id: str) -> ResearchJob | None:
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        row = await session.get(ResearchJob, uuid.UUID(job_id))
    await engine.dispose()
    return row


def test_scheduled_task_uses_default_marketplace_and_no_category():
    import asyncio

    result = run_scheduled_research_job.apply()
    assert result.successful(), result.traceback
    body = result.result
    assert body["job_id"]

    job = asyncio.run(_fetch_job_by_id(body["job_id"]))

    assert job is not None
    assert job.marketplace == "IT"  # config/default.yaml's default_marketplace
    assert job.category is None
    assert job.name.startswith("Scheduled research")


def test_scheduler_config_reflects_default_yaml():
    settings = get_settings()
    cfg = settings.scheduler_config()
    assert cfg["enabled"] is False
    assert cfg["research_job_cron"] == "0 8 * * 1"


def test_crontab_from_string_parses_configs_own_cron_expression():
    cron = _crontab_from_string("0 8 * * 1")
    assert set(cron.hour) == {8}
    assert set(cron.minute) == {0}
    assert set(cron.day_of_week) == {1}


def test_configure_beat_schedule_wires_the_periodic_task_when_enabled():
    """`_configure_beat_schedule()` reads the module-level `settings`
    object `app/workers/celery_app.py` captured once at import time (same
    "one process, one Settings instance" pattern app/main.py uses) -- NOT
    a fresh `get_settings()` call. `tests/conftest.py`'s autouse fixture
    calls `get_settings.cache_clear()` around every test in this suite,
    so a fresh `get_settings()` here would return a different object than
    the one this function actually reads and silently prove nothing (a
    real trap, hit once while writing this test). Patch the module's own
    `settings` object instead, matching what the code under test
    genuinely depends on."""
    settings = celery_app_module.settings
    original_yaml_config = settings.yaml_config
    original_beat_schedule = dict(celery_app.conf.beat_schedule or {})
    try:
        patched = dict(original_yaml_config)
        patched["scheduler"] = {"enabled": True, "research_job_cron": "0 8 * * 1"}
        settings.yaml_config = patched

        _configure_beat_schedule()

        assert "weekly-research-job" in celery_app.conf.beat_schedule
        entry = celery_app.conf.beat_schedule["weekly-research-job"]
        assert entry["task"] == "app.workers.tasks.run_scheduled_research_job"
    finally:
        settings.yaml_config = original_yaml_config
        celery_app.conf.beat_schedule = original_beat_schedule
