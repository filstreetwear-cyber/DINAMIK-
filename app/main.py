"""
FastAPI application entrypoint — Milestone 13 ("API").

**Scope, grounded exactly like every prior milestone**: this file's own
docstring has said since Milestone 1 that the full API surface ("research
jobs, prospects, brands, agent runs") lands here, and Milestone 12 already
built the real precedent for what that surface concretely means — four
commands (`health`, `pipeline run`, `jobs list`, `brands list`) that are
the ONLY way today to actually run or inspect anything in this system from
outside a test. This milestone exposes those same four capabilities over
HTTP — `GET /health`, `POST /jobs`, `GET /jobs`, `GET /brands` — plus one
genuinely new endpoint, `GET /jobs/{job_id}`, added because an HTTP caller
(unlike a terminal user) needs a way to fetch the single job `POST /jobs`
just created by the id it returned, without listing and filtering
client-side; it reuses the same `ResearchJob` row and `JobListItem` shape
as `GET /jobs`, no new business logic. No "prospects" or "agent runs"
endpoints exist yet — nothing in this codebase writes a `ProspectStatus`
row anywhere (confirmed by grep — the model exists, Milestone 6's own
scope note, but no service populates it), and "agent runs" beyond what a
job's pipeline summary already reports would be new read surface invented
for this milestone, not something an existing CLI command already proves
is needed. Building either now would be exactly the kind of
ungrounded scope-guessing this session has avoided at every prior
milestone.

**Reuses `app/database/session.py`'s `get_session()` dependency, unlike
the CLI.** The CLI (Milestone 12) deliberately built its OWN per-invocation
engine because each Typer command's `asyncio.run(...)` call opens and
closes its own event loop — a shared, import-time-created engine's pooled
asyncpg connections broke across that boundary. FastAPI under uvicorn has
exactly the opposite lifecycle: one process, one long-lived event loop for
the app's whole life. `get_session()` was built for precisely that case
(its own docstring says so) and `tests/test_main.py`'s existing
`TestClient`-based test already exercises it correctly for `/health` in
the pre-Milestone-13 code — the new endpoints below reuse it as-is rather
than reintroducing the CLI's workaround for a problem this app doesn't
have.

**POST /jobs is synchronous, not a background job.** It creates a
`ResearchJob` and runs Milestone 11's full pipeline
(`run_research_pipeline`) to completion before returning — the exact same
contract as `dinamik pipeline run`. There is no task queue or worker yet
(Milestone 14, `make run-worker` doesn't work — see README); building an
async/background version of this endpoint now would be scope from a
milestone that hasn't happened. A slow pipeline run means a slow HTTP
response today, honestly, not a fire-and-forget 202 this codebase can't
yet back up with a way to check completion.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.bootstrap import build_default_ai_gateway
from app.core.config import get_settings
from app.database.session import get_session
from app.models import Brand, ResearchJob, Score
from app.schemas.api import (
    AIAnalysisStageResult,
    BrandDeduplicationStageResult,
    BrandListItem,
    BrandResolutionStageResult,
    DiscoveryStageResult,
    HealthResponse,
    JobCreateRequest,
    JobListItem,
    JobRunResponse,
    OpportunityEngineStageResult,
    ScoringEngineStageResult,
)
from app.services.pipeline_orchestrator import run_research_pipeline

settings = get_settings()

app = FastAPI(
    title="Dinamik Advisor — Amazon Intelligence Platform",
    version="0.1.0",
)


@app.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    """Config + real DB connectivity (via the same session every other
    endpoint uses, not a separate probe) + real AI gateway status — the
    HTTP equivalent of `dinamik health`, superseding the old config-only
    version."""
    try:
        await session.execute(text("SELECT 1"))
        database_status = "reachable"
    except Exception as exc:  # noqa: BLE001 -- a health check reports, it never 500s the caller
        database_status = f"UNREACHABLE ({exc})"

    ai_gateway = build_default_ai_gateway(settings)
    if ai_gateway is None:
        ai_status = "disabled (config/default.yaml ai.enabled=false)"
    else:
        ai_status = (await ai_gateway.health_check()).value

    return HealthResponse(
        status="healthy",
        environment=settings.environment,
        mock_mode=settings.mock_mode,
        database=database_status,
        ai=ai_status,
    )


@app.post("/jobs", response_model=JobRunResponse, status_code=201)
async def create_job(
    payload: JobCreateRequest, session: AsyncSession = Depends(get_session)
) -> JobRunResponse:
    """Creates a ResearchJob and runs the full Milestone 11 pipeline
    against it synchronously, then commits — same contract, same
    transaction-boundary ownership, as `dinamik pipeline run`
    (app/cli.py's own `_pipeline_run_async`)."""
    job = ResearchJob(
        name=payload.name,
        marketplace=payload.marketplace,
        category=payload.category,
        subcategory=payload.subcategory,
    )
    session.add(job)
    await session.flush()

    summary = await run_research_pipeline(
        session,
        job,
        settings=settings,
        max_products=payload.max_products,
        max_batch=payload.max_batch,
    )
    await session.commit()

    return JobRunResponse(
        job_id=job.id,
        agent_run_id=summary.agent_run_id,
        discovery=DiscoveryStageResult(
            products_found=summary.discovery.products_found,
            products_enriched=summary.discovery.products_enriched,
        ),
        brand_resolution=BrandResolutionStageResult(
            processed=summary.brand_resolution.processed,
            resolved=summary.brand_resolution.resolved,
            unresolved=summary.brand_resolution.unresolved,
            conflicts=summary.brand_resolution.conflicts,
        ),
        brand_deduplication=BrandDeduplicationStageResult(
            merged_pairs=len(summary.brand_deduplication.merged_pairs),
            candidates_flagged=summary.brand_deduplication.candidates_flagged,
        ),
        opportunity_engine=OpportunityEngineStageResult(
            opportunities_created=summary.opportunity_engine.opportunities_created,
        ),
        scoring_engine=ScoringEngineStageResult(
            scores_created=summary.scoring_engine.scores_created,
        ),
        ai_analysis_engine=AIAnalysisStageResult(
            ai_disabled=summary.ai_analysis_engine.ai_disabled,
            claims_created=summary.ai_analysis_engine.claims_created,
        ),
    )


@app.get("/jobs", response_model=list[JobListItem])
async def list_jobs(
    limit: int = 20, session: AsyncSession = Depends(get_session)
) -> list[ResearchJob]:
    """HTTP equivalent of `dinamik jobs list`."""
    rows = (
        (await session.execute(select(ResearchJob).order_by(ResearchJob.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return list(rows)


@app.get("/brands", response_model=list[BrandListItem])
async def list_brands(
    limit: int = 20, status: str = "ACTIVE", session: AsyncSession = Depends(get_session)
) -> list[BrandListItem]:
    """HTTP equivalent of `dinamik brands list` — each brand's most recent
    Score per score_type, read-only, purely for display (same caveat as
    the CLI: NOT a reimplementation of scoring_engine.py's latest-per-type
    logic for any decision-making purpose)."""
    brands = (
        (
            await session.execute(
                select(Brand).where(Brand.status == status).order_by(Brand.first_seen_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )

    result: list[BrandListItem] = []
    for brand in brands:
        scores = (
            (await session.execute(select(Score).where(Score.brand_id == brand.id).order_by(Score.created_at.desc())))
            .scalars()
            .all()
        )
        latest_by_type: dict[str, Score] = {}
        for score in scores:
            latest_by_type.setdefault(score.score_type, score)
        result.append(
            BrandListItem(
                id=brand.id,
                name=brand.name,
                scores={score_type: float(score.score) for score_type, score in latest_by_type.items()},
            )
        )
    return result


@app.get("/jobs/{job_id}", response_model=JobListItem)
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ResearchJob:
    """Not mirrored from any CLI command -- the one genuinely new read
    endpoint in this milestone, added because REST callers (unlike a
    terminal user re-running `jobs list`) need a way to fetch a single job
    by the id POST /jobs just handed them, without listing and filtering
    client-side. Still no new business logic: same ResearchJob row, same
    JobListItem shape as GET /jobs."""
    job = await session.get(ResearchJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="research job not found")
    return job
