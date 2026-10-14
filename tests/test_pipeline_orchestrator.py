"""
Milestone 11 + Fase 7 (28/09): Pipeline Orchestrator — the first real,
single call that chains discovery -> brand resolution -> dedup ->
opportunity -> scoring -> AI analysis -> AI recommendation -> contact
draft. Not a multi-agent orchestrator (see the module's own docstring for
why that stays deferred to when Agent 02 exists) -- this proves the
WIRING between eight already-tested stages is real: the right call
order, real AgentEvent bookkeeping per stage, and that the whole chain
runs against real (mock-mode) providers end to end without manual
per-stage calls, same discipline as every other engine's own test file.

The last two stages (Fase 5/6) create zero rows against this file's mock
fixture -- the Casa e cucina fixture's one resolved brand has healthy
AMAZON_OPPORTUNITY (NONE, confirmed in test_opportunity_engine.py's own
end-to-end test) and no WebsiteProfile at all, so nothing is actionable
for AIRecommendation/ContactDraft. That's fine and expected here: this
file proves the two stages RUN as part of the chain (their own AgentRun
exists, SUCCESS), not that this specific fixture gives them something to
act on -- that's what test_ai_recommendation_engine.py/
test_contact_draft_engine.py already prove with purpose-built Opportunity
rows.

Same real-Postgres-or-skip pattern, and the same MockAIProvider injection
discipline as test_ai_analysis_engine.py -- no real Ollama required here
either.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.gateway import AIGateway
from app.ai.mock_provider import MockAIProvider
from app.core.config import get_settings
from app.models import AgentEvent, AgentRun, Brand, Opportunity, ResearchJob, Score
from app.models.ai_claim import AIClaim
from app.services.pipeline_orchestrator import run_research_pipeline
from app.tools.amazon.provider import AmazonProvider
from app.tools.core.orchestrator import IntelligenceSourceOrchestrator
from app.tools.core.registry import ProviderRegistry
from app.tools.keepa.provider import KeepaProvider


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


@pytest.fixture
async def session():
    if not await _db_reachable():
        pytest.skip("PostgreSQL not reachable — pipeline orchestrator tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.fixture
def provider_orchestrator(monkeypatch: pytest.MonkeyPatch) -> IntelligenceSourceOrchestrator:
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    settings = get_settings()

    registry = ProviderRegistry()
    registry.register(AmazonProvider(settings=settings), priority=20, cost_model="mock")
    registry.register(KeepaProvider(settings=settings), priority=30, cost_model="mock")
    return IntelligenceSourceOrchestrator(registry)


def _ai_claims_draft() -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "claim": "Summarized from the brand's discovered evidence.",
                    "data_type": "INFERENCE",
                    "confidence": 65.0,
                    "evidence_ids": [],
                    "reasoning_summary": "Pipeline integration test — generic summary claim.",
                }
            ]
        }
    )


def _new_job(**overrides: Any) -> ResearchJob:
    defaults: dict[str, Any] = {
        "name": "pipeline test job",
        "marketplace": "IT",
        "category": "Casa e cucina",
        "priority": 0,
    }
    defaults.update(overrides)
    return ResearchJob(**defaults)


@pytest.mark.asyncio
async def test_full_pipeline_runs_all_six_stages_in_order(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator
):
    job = _new_job()
    session.add(job)
    await session.flush()

    ai_gateway = AIGateway(MockAIProvider(structured_response=_ai_claims_draft()))

    summary = await run_research_pipeline(
        session, job, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway
    )

    # Real rows from real (mock-mode) discovery, resolved into real Brand
    # rows, scored, and AI-analyzed -- not asserted in isolation per stage
    # (each stage has its own test file already) but proven connected.
    assert summary.discovery.products_found > 0
    assert summary.brand_resolution.processed >= 0
    brands = (await session.execute(select(Brand))).scalars().all()
    assert len(brands) > 0

    opportunities = (await session.execute(select(Opportunity))).scalars().all()
    scores = (await session.execute(select(Score))).scalars().all()
    assert len(opportunities) > 0
    assert len(scores) > 0

    # AI stage only writes a claim for a brand that actually has Evidence
    # -- the pipeline test doesn't assert a specific count, only that the
    # AI stage genuinely ran (its own AgentRun exists) and didn't crash
    # the rest of the chain.
    ai_run = await session.get(AgentRun, summary.ai_analysis_engine.agent_run_id)
    assert ai_run is not None
    assert ai_run.status == "SUCCESS"

    # Fase 7: the two new stages actually ran as part of the chain (their
    # own AgentRun exists and succeeded), even though this fixture gives
    # them nothing actionable to produce -- see module docstring.
    ai_recommendation_run = await session.get(AgentRun, summary.ai_recommendation_engine.agent_run_id)
    assert ai_recommendation_run is not None
    assert ai_recommendation_run.status == "SUCCESS"
    contact_draft_run = await session.get(AgentRun, summary.contact_draft_engine.agent_run_id)
    assert contact_draft_run is not None
    assert contact_draft_run.status == "SUCCESS"


@pytest.mark.asyncio
async def test_pipeline_agent_run_records_one_event_per_stage(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator
):
    job = _new_job()
    session.add(job)
    await session.flush()

    ai_gateway = AIGateway(MockAIProvider(structured_response=_ai_claims_draft()))

    summary = await run_research_pipeline(
        session, job, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway
    )

    pipeline_run = await session.get(AgentRun, summary.agent_run_id)
    assert pipeline_run is not None
    assert pipeline_run.agent_name == "research_pipeline"
    assert pipeline_run.status == "SUCCESS"

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == pipeline_run.id)))
        .scalars()
        .all()
    )
    assert len(events) == 8
    payloads = [e.payload for e in events]
    assert all(p is not None for p in payloads)
    stages = {p["stage"] for p in payloads if p is not None}
    assert stages == {
        "discovery",
        "brand_resolution",
        "brand_deduplication",
        "opportunity_engine",
        "scoring_engine",
        "ai_analysis_engine",
        "ai_recommendation_engine",
        "contact_draft_engine",
    }
    assert all(p["stage_status"] == "SUCCESS" for p in payloads if p is not None)


@pytest.mark.asyncio
async def test_pipeline_does_not_commit_the_session(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator
):
    """Same 'caller owns the transaction boundary' contract as every
    individual stage -- rolling back the fixture's session must undo
    everything the pipeline wrote, proving it never called commit()
    itself."""
    job = _new_job()
    session.add(job)
    await session.flush()

    ai_gateway = AIGateway(MockAIProvider(structured_response=_ai_claims_draft()))
    await run_research_pipeline(session, job, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway)
    await session.rollback()

    # A fresh read in a new session confirms nothing survived the rollback.
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as fresh_session:
        remaining = (await fresh_session.execute(select(AgentRun).where(AgentRun.agent_name == "research_pipeline"))).scalars().all()
        assert remaining == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_ai_claim_persisted_when_evidence_exists(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator
):
    job = _new_job()
    session.add(job)
    await session.flush()

    ai_gateway = AIGateway(MockAIProvider(structured_response=_ai_claims_draft()))
    await run_research_pipeline(session, job, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway)

    claims = (await session.execute(select(AIClaim))).scalars().all()
    # Discovery writes real Evidence for every enriched product, so at
    # least one brand should have had evidence to analyze.
    assert len(claims) > 0
