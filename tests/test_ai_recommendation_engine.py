"""
Fase 5 (28/09): AI Recommendation Engine — primo chiamante reale di
AIAdvisor (app/ai/advisor.py) contro Opportunity/Evidence reali. Stesso
fixture/skip pattern (Postgres reale o skip) di test_ai_analysis_engine.py/
test_opportunity_engine.py. Ogni test inietta un gateway con
MockAIProvider — nessun Ollama reale richiesto.

Le Opportunity qui sono inserite direttamente (non tramite
opportunity_engine.run_opportunity_engine) -- questo file testa il
WIRING di ai_recommendation_engine.py (selezione dell'Opportunity più
recente per tipo, filtro tipo/livello, deduplica per opportunity_id,
bookkeeping), non opportunity_engine.py stesso (già coperto da
test_opportunity_engine.py).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.gateway import AIGateway
from app.ai.mock_provider import MockAIProvider
from app.ai.provider import AIUnavailableError
from app.core.config import get_settings
from app.models import AgentEvent, AgentRun, Brand, ErrorLog, Opportunity
from app.models.ai_recommendation import AIRecommendation
from app.models.enums import ErrorCategory
from app.services.ai_recommendation_engine import run_ai_recommendation_engine


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
        pytest.skip("PostgreSQL not reachable — AI recommendation engine tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


def _draft_response(evidence_ids: list[str] | None = None) -> str:
    return json.dumps(
        {
            "recommendations": [
                {
                    "recommendation": "Add a responsive viewport meta tag.",
                    "reasoning_summary": "Directly addresses the reason given.",
                    "evidence_ids": evidence_ids or [],
                }
            ]
        }
    )


async def _brand(session: AsyncSession, name: str) -> Brand:
    brand = Brand(name=name, first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC))
    session.add(brand)
    await session.flush()
    return brand


async def _opportunity(
    session: AsyncSession,
    brand: Brand,
    *,
    opportunity_type: str = "WEBSITE_OPPORTUNITY",
    level: str = "MEDIUM",
    rule_triggered: str = "mobile_and_performance_signals_v1",
    reasons: list[str] | None = None,
) -> Opportunity:
    opp = Opportunity(
        brand_id=brand.id,
        opportunity_type=opportunity_type,
        level=level,
        rule_triggered=rule_triggered,
        reasons=reasons if reasons is not None else ["mobile_friendly=False -- no responsive viewport detected"],
    )
    session.add(opp)
    await session.flush()
    return opp


def _gateway(**mock_kwargs: Any) -> AIGateway:
    return AIGateway(MockAIProvider(**mock_kwargs))


@pytest.mark.asyncio
async def test_actionable_opportunity_gets_a_persisted_recommendation(session: AsyncSession):
    brand = await _brand(session, "Alpha")
    opp = await _opportunity(session, brand)
    gateway = _gateway(structured_response=_draft_response())

    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    assert summary.brands_considered == 1
    assert summary.recommendations_created == 1

    recs = (
        (await session.execute(select(AIRecommendation).where(AIRecommendation.entity_id == str(brand.id))))
        .scalars()
        .all()
    )
    assert len(recs) == 1
    rec = recs[0]
    assert rec.opportunity_id == opp.id
    assert rec.opportunity_type == "WEBSITE_OPPORTUNITY"
    assert rec.priority == "MEDIUM"
    assert rec.model == "mock-model"


@pytest.mark.asyncio
async def test_brand_with_no_opportunities_is_skipped_without_calling_ai(session: AsyncSession):
    await _brand(session, "NoOpportunities")
    gateway = _gateway(raise_error=AIUnavailableError("mock", "should never be called"))

    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    assert summary.brands_considered == 1
    assert summary.recommendations_created == 0


@pytest.mark.asyncio
async def test_none_level_opportunity_is_not_recommended(session: AsyncSession):
    brand = await _brand(session, "Healthy")
    await _opportunity(session, brand, level="NONE", reasons=[])
    gateway = _gateway(raise_error=AIUnavailableError("mock", "should never be called"))

    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    assert summary.recommendations_created == 0


@pytest.mark.asyncio
async def test_business_value_opportunity_is_never_recommended_even_at_high(session: AsyncSession):
    """BUSINESS_VALUE e' un segnale di targeting, non un difetto da
    correggere -- vedi module docstring di ai_recommendation_engine.py."""
    brand = await _brand(session, "BigAccount")
    await _opportunity(
        session,
        brand,
        opportunity_type="BUSINESS_VALUE",
        level="HIGH",
        rule_triggered="product_and_review_footprint_v1",
        reasons=["product_count=10 meets high threshold 8"],
    )
    gateway = _gateway(raise_error=AIUnavailableError("mock", "should never be called"))

    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    assert summary.recommendations_created == 0


@pytest.mark.asyncio
async def test_only_the_latest_opportunity_per_type_is_considered(session: AsyncSession):
    """Opportunity e' append-only -- una riga HIGH vecchia superata da una
    riga NONE piu' recente per lo stesso (brand, tipo) non deve generare
    un consiglio basato sulla valutazione superata."""
    brand = await _brand(session, "Improved")
    await _opportunity(session, brand, level="HIGH", reasons=["mobile_friendly=False -- old finding"])
    await _opportunity(session, brand, level="NONE", reasons=[])  # rivalutato dopo, ora sano
    gateway = _gateway(raise_error=AIUnavailableError("mock", "should never be called"))

    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    assert summary.recommendations_created == 0


@pytest.mark.asyncio
async def test_same_opportunity_is_not_recommended_twice_across_runs(session: AsyncSession):
    brand = await _brand(session, "Repeat")
    await _opportunity(session, brand)
    gateway = _gateway(structured_response=_draft_response())

    first = await run_ai_recommendation_engine(session, gateway=gateway)
    second = await run_ai_recommendation_engine(session, gateway=gateway)

    assert first.recommendations_created == 1
    assert second.recommendations_created == 0  # stessa Opportunity, gia' consigliata

    all_recs = (await session.execute(select(AIRecommendation))).scalars().all()
    assert len(all_recs) == 1


@pytest.mark.asyncio
async def test_a_new_opportunity_row_for_the_same_brand_gets_recommended_again(session: AsyncSession):
    """Una NUOVA riga Opportunity (nuova valutazione) e' un accertamento
    diverso -- non la stessa riga gia' consigliata -- e deve ricevere un
    nuovo tentativo."""
    brand = await _brand(session, "Reassessed")
    await _opportunity(session, brand)
    gateway = _gateway(structured_response=_draft_response())
    await run_ai_recommendation_engine(session, gateway=gateway)

    await _opportunity(session, brand)  # nuova valutazione, stesso tipo, nuova riga
    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    assert summary.recommendations_created == 1


@pytest.mark.asyncio
async def test_ai_unavailable_errors_do_not_abort_the_batch(session: AsyncSession):
    brand_a = await _brand(session, "Boom")
    await _opportunity(session, brand_a)
    brand_b = await _brand(session, "AlsoBoom")
    await _opportunity(session, brand_b)
    gateway = _gateway(raise_error=AIUnavailableError("mock", "down"))

    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    assert summary.brands_considered == 2
    assert summary.recommendations_created == 0

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.status == "SUCCESS"

    error_logs = (
        (await session.execute(select(ErrorLog).where(ErrorLog.agent_run_id == agent_run.id))).scalars().all()
    )
    assert len(error_logs) == 2
    assert all(e.category == ErrorCategory.AI_ERROR.value for e in error_logs)


@pytest.mark.asyncio
async def test_ai_disabled_gateway_is_a_clean_noop_not_a_failure(session: AsyncSession):
    brand = await _brand(session, "WouldHaveBeenRecommended")
    await _opportunity(session, brand)

    summary = await run_ai_recommendation_engine(session, gateway=None, settings=_disabled_settings())

    assert summary.ai_disabled is True
    assert summary.brands_considered == 0
    assert summary.recommendations_created == 0

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.status == "SUCCESS"


def _disabled_settings():
    settings = get_settings()
    settings.yaml_config = {**settings.yaml_config, "ai": {**settings.yaml_config.get("ai", {}), "enabled": False}}
    return settings


@pytest.mark.asyncio
async def test_agent_run_and_events_are_recorded_for_successful_recommendations(session: AsyncSession):
    brand = await _brand(session, "Observed")
    opp = await _opportunity(session, brand)
    gateway = _gateway(structured_response=_draft_response())

    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.agent_name == "ai_recommendation_engine"
    assert agent_run.status == "SUCCESS"

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id))).scalars().all()
    )
    assert len(events) == 1
    assert events[0].event_type == "ai_recommendation_created"
    payload = events[0].payload
    assert payload is not None
    assert payload["opportunity_id"] == str(opp.id)


@pytest.mark.asyncio
async def test_max_brands_cap_is_respected(session: AsyncSession):
    for i in range(4):
        brand = await _brand(session, f"Cap{i}")
        await _opportunity(session, brand)
    gateway = _gateway(structured_response=_draft_response())

    summary = await run_ai_recommendation_engine(session, max_brands=2, gateway=gateway)

    assert summary.brands_considered == 2
    assert summary.truncated is True


@pytest.mark.asyncio
async def test_merged_brand_is_excluded(session: AsyncSession):
    brand = await _brand(session, "Merged")
    await _opportunity(session, brand)
    brand.status = "MERGED"
    await session.flush()
    gateway = _gateway(structured_response=_draft_response())

    summary = await run_ai_recommendation_engine(session, gateway=gateway)

    assert summary.brands_considered == 0
