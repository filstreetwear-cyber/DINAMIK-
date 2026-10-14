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
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.bootstrap import build_default_ai_gateway
from app.core.config import get_settings
from app.database.session import get_session
from app.models import AIClaim, Brand, Opportunity, ResearchJob, Score, WeeklyScan
from app.schemas.api import (
    AIAnalysisStageResult,
    AIClaimListItem,
    BrandDeduplicationStageResult,
    BrandListItem,
    BrandResolutionStageResult,
    DiscoveryStageResult,
    HealthResponse,
    JobCreateRequest,
    JobListItem,
    JobRunResponse,
    OpportunityEngineStageResult,
    OpportunityListItemWithBrand,
    ProspectListItem,
    ScoringEngineStageResult,
    WeeklyScanItem,
)
from app.services.pipeline_orchestrator import run_research_pipeline
from app.services.prospect_service import list_prospects
from app.web import routes as ui_router_module
from app.web.routes import _LoginRequired as UILoginRequired
from app.web.routes import router as ui_router

settings = get_settings()

app = FastAPI(
    title="Dinamik Advisor — Amazon Intelligence Platform",
    version="0.1.0",
)

# 2026-09-22 (ops dashboard requirement): the dashboard is a separate
# static site, served from a different origin than this API (different
# port at minimum, possibly a different host once a real domain exists)
# -- the browser enforces CORS regardless of what's actually running on
# the same VPS. Wide open (`allow_origins=["*"]`) is deliberate for now:
# every response behind these routes is already gated by
# require_dashboard_key below once DASHBOARD_API_KEY is set, so the CORS
# policy's job here is just "let a browser call this API from anywhere",
# not a second layer of access control. Tightening this to a specific
# origin is a one-line change once the dashboard has a real deployed URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


async def require_dashboard_key(x_dashboard_key: str | None = Header(default=None)) -> None:
    """No-op when DASHBOARD_API_KEY is unset (default everywhere except a
    real deploy that configures it) -- same "open until explicitly
    configured" shape as every other optional Settings field in this
    project. When set, every dependent endpoint requires an exact match
    on the X-Dashboard-Key header. This is a single shared secret, not
    per-user auth -- see Settings.dashboard_api_key's own comment for
    what it is and isn't."""
    if not settings.dashboard_api_key:
        return
    if x_dashboard_key != settings.dashboard_api_key:
        raise HTTPException(status_code=401, detail="missing or invalid X-Dashboard-Key header")


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
    payload: JobCreateRequest,
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
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
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
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
    limit: int = 20,
    status: str = "ACTIVE",
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
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
async def get_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
) -> ResearchJob:
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


# --- 2026-09-22: ops dashboard requirement -------------------------------
# See app/schemas/api.py's own comment on this same date for why these
# four endpoints are new read surface rather than a CLI mirror.


@app.get("/opportunities", response_model=list[OpportunityListItemWithBrand])
async def list_opportunities(
    brand_id: uuid.UUID | None = None,
    opportunity_type: str | None = None,
    level: str | None = None,
    limit: int = Query(default=50, gt=0, le=500),
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
) -> list[OpportunityListItemWithBrand]:
    """No pagination cursor, no sort option beyond newest-first -- this is
    a browse view for a human, not a general-purpose list API; add those
    only if the dashboard actually needs them."""
    stmt = select(Opportunity, Brand.name).join(Brand, Opportunity.brand_id == Brand.id)
    if brand_id is not None:
        stmt = stmt.where(Opportunity.brand_id == brand_id)
    if opportunity_type is not None:
        stmt = stmt.where(Opportunity.opportunity_type == opportunity_type)
    if level is not None:
        stmt = stmt.where(Opportunity.level == level)
    stmt = stmt.order_by(Opportunity.created_at.desc()).limit(limit)

    rows = (await session.execute(stmt)).all()
    return [
        OpportunityListItemWithBrand(
            id=opp.id,
            created_at=opp.created_at,
            brand_id=opp.brand_id,
            brand_name=brand_name,
            opportunity_type=opp.opportunity_type,
            level=opp.level,
            rule_triggered=opp.rule_triggered,
            reasons=opp.reasons,
        )
        for opp, brand_name in rows
    ]


@app.get("/ai-claims", response_model=list[AIClaimListItem])
async def list_ai_claims(
    brand_id: uuid.UUID | None = None,
    limit: int = Query(default=50, gt=0, le=500),
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
) -> list[AIClaim]:
    """`app/services/ai_analysis_engine.py` only ever writes
    entity_type="brand" (see AIClaim's own docstring) -- entity_type isn't
    exposed as a filter here because there is nothing else to filter it
    to yet; add it if/when a second entity_type is ever written."""
    stmt = select(AIClaim).where(AIClaim.entity_type == "brand")
    if brand_id is not None:
        stmt = stmt.where(AIClaim.entity_id == str(brand_id))
    stmt = stmt.order_by(AIClaim.created_at.desc()).limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@app.get("/weekly-scans", response_model=list[WeeklyScanItem])
async def list_weekly_scans(
    limit: int = Query(default=10, gt=0, le=200),
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
) -> list[WeeklyScan]:
    """HTTP equivalent of `dinamik scan history`."""
    rows = (
        (await session.execute(select(WeeklyScan).order_by(WeeklyScan.period_end.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return list(rows)


@app.get("/weekly-scans/latest", response_model=WeeklyScanItem)
async def get_latest_weekly_scan(
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
) -> WeeklyScan:
    """HTTP equivalent of `dinamik scan status`. Declared BEFORE
    GET /weekly-scans/{scan_id} below on purpose -- FastAPI matches routes
    in declaration order, and "latest" would otherwise be swallowed by the
    {scan_id} path parameter and fail UUID parsing instead of resolving to
    this endpoint."""
    scan = (
        await session.execute(select(WeeklyScan).order_by(WeeklyScan.period_end.desc()).limit(1))
    ).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="no weekly scans have run yet")
    return scan


@app.get("/weekly-scans/{scan_id}", response_model=WeeklyScanItem)
async def get_weekly_scan(
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
) -> WeeklyScan:
    scan = await session.get(WeeklyScan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="weekly scan not found")
    return scan


# --- 2026-10-14 (Anello 1): prospect JSON + interfaccia web --------------


@app.get("/prospects", response_model=list[ProspectListItem])
async def list_prospects_api(
    status: str | None = None,
    q: str | None = None,
    include_non_target: bool = False,
    limit: int = Query(default=200, le=1000),
    session: AsyncSession = Depends(get_session),
    _key: None = Depends(require_dashboard_key),
) -> list[ProspectListItem]:
    rows = await list_prospects(
        session, status=status, search=q, target_only=not include_non_target, limit=limit
    )
    return [ProspectListItem.model_validate(r, from_attributes=True) for r in rows]


app.include_router(ui_router)
app.mount("/ui/static", StaticFiles(directory=str(Path(ui_router_module.__file__).parent / "static")), name="ui-static")


@app.exception_handler(UILoginRequired)
async def _ui_login_redirect(request: Request, exc: UILoginRequired) -> RedirectResponse:
    return RedirectResponse("/ui/login", status_code=303)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/ui", status_code=307)
