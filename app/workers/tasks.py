"""
Celery tasks — Milestone 14 ("Workers + scheduler").

Two real tasks, both grounded in what already exists rather than invented:

`run_research_pipeline_task` is the async-dispatchable equivalent of
`dinamik pipeline run` (Milestone 12) and `POST /jobs` (Milestone 13) —
same parameters, same job-creation + `run_research_pipeline` + commit
contract, just runnable through Celery instead of a terminal or an HTTP
request. It exists so `run_scheduled_research_job` below has real work to
dispatch, and so a future caller (the API, if it ever needs `POST /jobs`
to stop blocking on a slow pipeline run — deliberately NOT changed in
this milestone, see app/main.py's own docstring) has something to queue
instead of reinventing the job-creation dance a third time.

`run_scheduled_research_job` is a Celery Beat periodic task wired to
`config/default.yaml`'s own `scheduler.research_job_cron` (declared,
unread, since the original refactor pass — see Settings.scheduler_config's
docstring). It has one honest gap: nothing in this codebase specifies
WHAT a periodic research job should search for. `default_marketplace: IT`
already exists in config/default.yaml and is reused here (not invented);
no category is specified anywhere, so this task passes `category=None` —
already a real, tested code path (every CLI/API test that omits
`--category`/`category` exercises exactly this), not a shortcut. A human
deciding what categories a recurring scan should cover is a business
decision this project has consistently refused to invent (see e.g.
SellerFastFitContract's docstring) — flagged here, not papered over.

Same "own engine per invocation" pattern as `app/cli.py`, for the same
reason: a Celery worker process may run many task invocations across its
lifetime (by default one worker process reuses one OS process across
tasks, but each task body here does its own `asyncio.run(...)`), so a
shared, import-time engine would hit the exact "Event loop is closed"
bug Milestone 12 already found and fixed for the CLI. Building this
module without re-deriving that the hard way would be assuming safety
Milestone 12 never established for this call shape — confirmed instead
by reusing the same per-invocation `create_async_engine(...)` +
`engine.dispose()` pattern verified there and in Milestone 13's tests.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.models import ResearchJob
from app.services.pipeline_orchestrator import run_research_pipeline
from app.services.weekly_scan import run_weekly_scan
from app.workers.celery_app import celery_app


def _session_factory(settings: Settings):
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)


@celery_app.task(name="app.workers.tasks.run_research_pipeline_task")
def run_research_pipeline_task(
    *,
    name: str,
    marketplace: str = "IT",
    category: str | None = None,
    subcategory: str | None = None,
    max_products: int = 20,
    max_batch: int = 200,
) -> dict[str, Any]:
    """Creates a ResearchJob and runs the full Milestone 11 pipeline
    against it, then commits -- the queued equivalent of `dinamik pipeline
    run` / `POST /jobs`. Returns a plain dict (Celery's JSON result
    serializer can't carry the dataclass PipelineSummary directly) with
    the same scalar fields app/schemas/api.py's JobRunResponse exposes."""
    return asyncio.run(
        _run_research_pipeline_task_async(
            name=name,
            marketplace=marketplace,
            category=category,
            subcategory=subcategory,
            max_products=max_products,
            max_batch=max_batch,
        )
    )


async def _run_research_pipeline_task_async(
    *, name: str, marketplace: str, category: str | None, subcategory: str | None, max_products: int, max_batch: int
) -> dict[str, Any]:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            job = ResearchJob(name=name, marketplace=marketplace, category=category, subcategory=subcategory)
            session.add(job)
            await session.flush()

            summary = await run_research_pipeline(
                session, job, settings=settings, max_products=max_products, max_batch=max_batch
            )
            await session.commit()

            return {
                "job_id": str(job.id),
                "agent_run_id": str(summary.agent_run_id),
                "products_found": summary.discovery.products_found,
                "brands_resolved": summary.brand_resolution.resolved,
                "opportunities_created": summary.opportunity_engine.opportunities_created,
                "scores_created": summary.scoring_engine.scores_created,
                "ai_claims_created": summary.ai_analysis_engine.claims_created,
            }
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.tasks.run_scheduled_research_job")
def run_scheduled_research_job() -> dict[str, Any]:
    """Celery Beat's periodic entry point, scheduled from
    `config/default.yaml`'s `scheduler.research_job_cron` when
    `scheduler.enabled: true` (see app/workers/celery_app.py). Runs the
    real pipeline directly (a plain function call, not another
    `.delay()`) -- this task IS already executing inside a worker process
    when Beat fires it, so a second broker round-trip would add latency
    for no reason. `default_marketplace` comes from config/default.yaml
    (already declared, reused here, not invented); no category is passed
    -- see this module's own docstring for why that's an honest gap, not
    an oversight."""
    settings = get_settings()
    marketplace = settings.yaml_config.get("default_marketplace", "IT")
    name = f"Scheduled research — {datetime.now(UTC):%Y-%m-%d %H:%M UTC}"
    return run_research_pipeline_task(name=name, marketplace=marketplace, category=None, subcategory=None)


@celery_app.task(name="app.workers.tasks.run_weekly_scan_task")
def run_weekly_scan_task(*, force: bool = False) -> dict[str, Any]:
    """Celery Beat's weekly-intelligence-scan entry point, scheduled from
    `config/default.yaml`'s `weekly_scan.day`/`time`/`timezone` (converted
    to a UTC crontab -- see app/workers/celery_app.py's docstring) when
    `weekly_scan.enabled: true`. Runs `run_weekly_scan` (feature,
    2026-09-21) directly against its own short-lived engine -- same
    per-invocation pattern as every other task in this module, for the
    same "Event loop is closed" reason documented above -- and commits,
    since Beat firing this is the one caller responsible for persisting
    the result (`run_weekly_scan` itself, like every service in this
    project, defers committing to its caller).

    `force` exists for manual re-dispatch
    (`run_weekly_scan_task.delay(force=True)` from a shell, or a future
    admin action) -- Beat's own periodic fire never passes it, since a
    normal weekly tick should skip an already-COMPLETED scan for the
    same period (idempotency, spec section 4) rather than redo it.
    Returns a plain dict (Celery's JSON result serializer can't carry
    the dataclass WeeklyScanSummary directly)."""
    return asyncio.run(_run_weekly_scan_task_async(force=force))


async def _run_weekly_scan_task_async(*, force: bool) -> dict[str, Any]:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            summary = await run_weekly_scan(session, settings=settings, force=force)
            await session.commit()

            return {
                "scan_id": str(summary.scan_id),
                "status": summary.status,
                "period_start": summary.period_start.isoformat(),
                "period_end": summary.period_end.isoformat(),
                "universe_size": summary.universe_size,
                "changes_detected": summary.changes_detected,
                "changes_significant": summary.changes_significant,
                "opportunities_new": summary.opportunities_new,
                "opportunities_improving": summary.opportunities_improving,
                "opportunities_deteriorating": summary.opportunities_deteriorating,
                "opportunities_resolved": summary.opportunities_resolved,
                "opportunities_continuing": summary.opportunities_continuing,
                "skipped_existing": summary.skipped_existing,
                "report_path": summary.report_path,
            }
    finally:
        await engine.dispose()
