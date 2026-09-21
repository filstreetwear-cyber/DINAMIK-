"""
Pipeline Orchestrator — Milestone 11 ("Agent orchestration" per README's
roadmap), scoped honestly rather than to the milestone's name.

**What "orchestration" cannot mean yet**: the README roadmap names Agent
02 (Amazon Auditor) and Agent 03 (Sales Copilot) as future modules, and
`docs/architecture/development-agents.md` (Milestone 8.5's own audit)
explicitly deferred evaluating Deep Agents/Agno — the two real multi-agent
frameworks found on PyPI — to "when Milestone 11 is addressed... together,
not one at a time." Today there is exactly ONE real agent
(`amazon_scout`, `app/agents/registry.py`). Building a multi-agent
router/coordinator, or committing to a framework dependency, for a
registry with one entry would be the same "don't build for an imagined
future" mistake `AgentRegistry`'s own docstring already warns against for
a base `Agent` class. That decision stays deferred, unchanged, until
Agent 02 exists — this module does not revisit it.

**What "orchestration" concretely, honestly means today**: five real,
already-working stages exist — `run_amazon_scout_discovery` (Milestone 5),
`resolve_research_job` (6), `run_brand_deduplication` (7),
`run_opportunity_engine` (8), `run_scoring_engine` (9), `run_ai_analysis_
engine` (10) — and nothing in this codebase runs them together as one
job. Every test that exercises more than one stage (e.g.
`test_scoring_engine.py` calling `run_opportunity_engine` then
`run_scoring_engine`) does so by hand, one call at a time, inside the
test itself. This module is that missing wiring: a single callable that
runs the full chain in the right order, reusable by a future CLI/API
(Milestone 12/13) or scheduler (Milestone 14) instead of each of those
reimplementing the same six-call sequence.

**Why a straight sequential chain is safe, not naive**: every stage
already fails safe internally — each wraps its own body in a try/except
that marks ITS OWN `AgentRun` `FAILED`, writes an `ErrorLog`, and returns
its summary object normally rather than raising (confirmed by reading
`resolve_research_job`/`run_brand_deduplication`/`run_opportunity_engine`/
`run_scoring_engine`/`run_ai_analysis_engine` — the pattern is identical
in all five). So this orchestrator does not need its own per-stage
try/except to prevent one stage's failure from blocking the next --
that guarantee already exists one layer down. What this module adds is
purely sequencing plus a top-level `AgentRun` (`agent_name=
"research_pipeline"`) whose `AgentEvent`s record which stage ran, its own
`agent_run_id`, and whether that stage's own `AgentRun.status` was
SUCCESS or FAILED -- so a caller can see the whole pipeline's health in
one place instead of six.

**Scope mismatch, documented, not hidden**: `run_amazon_scout_discovery`
is scoped to ONE `ResearchJob`; every stage after it (`resolve_research_
job` aside, which also takes a `job`) operates on ALL pending work across
the whole table (`resolve_research_job` is job-scoped; `run_brand_
deduplication`/`run_opportunity_engine`/`run_scoring_engine`/`run_ai_
analysis_engine` are not job-scoped at all -- they sweep every ACTIVE
brand regardless of which job produced it). This module does not paper
over that by inventing a job-scoping filter for the downstream stages --
that would be new logic those stages were never designed for. It runs
each stage exactly as it already exists, in the established order, and
documents the asymmetry here instead of hiding it.

Does not commit the session -- same "caller owns the transaction
boundary" contract every stage already states in its own docstring.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.amazon_scout.discovery import DiscoveryOutcome, run_amazon_scout_discovery
from app.ai.bootstrap import build_default_ai_gateway
from app.ai.gateway import AIGateway
from app.core.config import Settings, get_settings
from app.models import AgentEvent, AgentRun, ResearchJob
from app.services.ai_analysis_engine import AIAnalysisEngineSummary, run_ai_analysis_engine
from app.services.brand_deduplication import DeduplicationSummary, run_brand_deduplication
from app.services.brand_resolution import BrandResolutionJobSummary, resolve_research_job
from app.services.opportunity_engine import OpportunityEngineSummary, run_opportunity_engine
from app.services.scoring_engine import ScoringEngineSummary, run_scoring_engine
from app.tools.core.bootstrap import build_default_registry
from app.tools.core.orchestrator import IntelligenceSourceOrchestrator

_STAGE_NAMES = (
    "discovery",
    "brand_resolution",
    "brand_deduplication",
    "opportunity_engine",
    "scoring_engine",
    "ai_analysis_engine",
)


@dataclass
class PipelineSummary:
    agent_run_id: uuid.UUID
    discovery: DiscoveryOutcome
    brand_resolution: BrandResolutionJobSummary
    brand_deduplication: DeduplicationSummary
    opportunity_engine: OpportunityEngineSummary
    scoring_engine: ScoringEngineSummary
    ai_analysis_engine: AIAnalysisEngineSummary


async def _stage_status(session: AsyncSession, agent_run_id: uuid.UUID) -> str:
    run = await session.get(AgentRun, agent_run_id)
    return run.status if run is not None else "UNKNOWN"


async def run_research_pipeline(
    session: AsyncSession,
    job: ResearchJob,
    *,
    settings: Settings | None = None,
    provider_orchestrator: IntelligenceSourceOrchestrator | None = None,
    ai_gateway: AIGateway | None = None,
    max_products: int = 20,
    max_batch: int = 200,
) -> PipelineSummary:
    """Runs the full discovery -> resolution -> dedup -> opportunity ->
    scoring -> AI analysis chain for one call, in that order. `job` is a
    not-yet-run `ResearchJob` the caller has already added to `session`
    (mirrors `run_amazon_scout_discovery`'s own contract -- this function
    does not create the job). `provider_orchestrator`/`ai_gateway` are
    injectable (tests pass mock-backed ones); left `None` in production
    they resolve via `build_default_registry()`/`build_default_ai_gateway()`,
    the same defaults every other real caller uses.
    """
    settings = settings or get_settings()
    resolved_provider_orchestrator = provider_orchestrator or IntelligenceSourceOrchestrator(
        build_default_registry(settings)
    )
    resolved_ai_gateway = ai_gateway if ai_gateway is not None else build_default_ai_gateway(settings)

    agent_run = AgentRun(
        agent_name="research_pipeline",
        job_id=job.id,
        input={"job_id": str(job.id), "max_products": max_products, "max_batch": max_batch},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    discovery = await run_amazon_scout_discovery(
        session, job, resolved_provider_orchestrator, max_products=max_products
    )
    brand_resolution = await resolve_research_job(session, job, max_results=max_batch)
    brand_deduplication = await run_brand_deduplication(session, max_brands=max_batch)
    opportunity = await run_opportunity_engine(session, max_brands=max_batch, settings=settings)
    scoring = await run_scoring_engine(session, max_brands=max_batch, settings=settings)
    ai_analysis = await run_ai_analysis_engine(
        session, max_brands=max_batch, settings=settings, gateway=resolved_ai_gateway
    )

    stage_run_ids = (
        discovery.agent_run_id,
        brand_resolution.agent_run_id,
        brand_deduplication.agent_run_id,
        opportunity.agent_run_id,
        scoring.agent_run_id,
        ai_analysis.agent_run_id,
    )
    for stage_name, stage_run_id in zip(_STAGE_NAMES, stage_run_ids, strict=True):
        session.add(
            AgentEvent(
                agent_run_id=agent_run.id,
                event_type="pipeline_stage_completed",
                payload={
                    "stage": stage_name,
                    "stage_agent_run_id": str(stage_run_id),
                    "stage_status": await _stage_status(session, stage_run_id),
                },
            )
        )
    await session.flush()

    agent_run.status = "SUCCESS"  # the pipeline itself ran to completion; per-stage health is in the events above
    agent_run.output = {"job_id": str(job.id)}
    await session.flush()

    return PipelineSummary(
        agent_run_id=agent_run.id,
        discovery=discovery,
        brand_resolution=brand_resolution,
        brand_deduplication=brand_deduplication,
        opportunity_engine=opportunity,
        scoring_engine=scoring,
        ai_analysis_engine=ai_analysis,
    )
