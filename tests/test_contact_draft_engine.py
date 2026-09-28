"""
Fase 6 (28/09): Contact Draft Engine — primo chiamante reale di
AIOutreachWriter contro AIRecommendation reali. Stesso fixture/skip
pattern (Postgres reale o skip) di test_ai_recommendation_engine.py.
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
from app.models import AgentEvent, AgentRun, Brand, ErrorLog, Opportunity, ProspectStatus
from app.models.ai_recommendation import AIRecommendation
from app.models.contact_draft import ContactDraft
from app.models.enums import ErrorCategory, ProspectQualification
from app.services.contact_draft_engine import run_contact_draft_engine


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
        pytest.skip("PostgreSQL not reachable — contact draft engine tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


def _draft_response(subject: str = "Oggetto", body: str = "Corpo bozza.") -> str:
    return json.dumps({"subject": subject, "body": body})


async def _brand(session: AsyncSession, name: str) -> Brand:
    brand = Brand(name=name, first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC))
    session.add(brand)
    await session.flush()
    return brand


async def _brand_with_recommendation(session: AsyncSession, name: str) -> tuple[Brand, AIRecommendation]:
    brand = await _brand(session, name)
    opp = Opportunity(
        brand_id=brand.id,
        opportunity_type="WEBSITE_OPPORTUNITY",
        level="MEDIUM",
        rule_triggered="mobile_and_performance_signals_v1",
        reasons=["mobile_friendly=False -- no responsive viewport detected"],
    )
    session.add(opp)
    await session.flush()

    rec = AIRecommendation(
        entity_type="brand",
        entity_id=str(brand.id),
        opportunity_id=opp.id,
        opportunity_type=opp.opportunity_type,
        priority=opp.level,
        recommendation="Add a responsive viewport meta tag.",
        reasoning_summary="Directly addresses the finding.",
        evidence_ids=[],
        model="mock-model",
        provider="mock",
        generated_at=datetime.now(UTC),
    )
    session.add(rec)
    await session.flush()
    return brand, rec


def _gateway(**mock_kwargs: Any) -> AIGateway:
    return AIGateway(MockAIProvider(**mock_kwargs))


@pytest.mark.asyncio
async def test_brand_with_recommendation_gets_a_persisted_draft_and_prospect_status(session: AsyncSession):
    brand, rec = await _brand_with_recommendation(session, "Alpha")
    gateway = _gateway(structured_response=_draft_response())

    summary = await run_contact_draft_engine(session, gateway=gateway)

    assert summary.brands_considered == 1
    assert summary.drafts_created == 1

    drafts = (await session.execute(select(ContactDraft).where(ContactDraft.brand_id == brand.id))).scalars().all()
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.subject == "Oggetto"
    assert draft.recommendation_ids == [str(rec.id)]
    assert draft.model == "mock-model"

    statuses = (
        (await session.execute(select(ProspectStatus).where(ProspectStatus.brand_id == brand.id))).scalars().all()
    )
    assert len(statuses) == 1
    assert statuses[0].status == ProspectQualification.NEEDS_REVIEW.value
    assert statuses[0].changed_by == "system"


@pytest.mark.asyncio
async def test_brand_with_no_recommendations_is_skipped_without_calling_ai(session: AsyncSession):
    await _brand(session, "NoRecommendations")
    gateway = _gateway(raise_error=AIUnavailableError("mock", "should never be called"))

    summary = await run_contact_draft_engine(session, gateway=gateway)

    assert summary.brands_considered == 1
    assert summary.drafts_created == 0


@pytest.mark.asyncio
async def test_a_second_run_does_not_generate_a_second_draft(session: AsyncSession):
    await _brand_with_recommendation(session, "Repeat")
    gateway = _gateway(structured_response=_draft_response())

    first = await run_contact_draft_engine(session, gateway=gateway)
    second = await run_contact_draft_engine(session, gateway=gateway)

    assert first.drafts_created == 1
    assert second.drafts_created == 0

    all_drafts = (await session.execute(select(ContactDraft))).scalars().all()
    assert len(all_drafts) == 1


@pytest.mark.asyncio
async def test_ai_unavailable_errors_do_not_abort_the_batch(session: AsyncSession):
    await _brand_with_recommendation(session, "Boom")
    await _brand_with_recommendation(session, "AlsoBoom")
    gateway = _gateway(raise_error=AIUnavailableError("mock", "down"))

    summary = await run_contact_draft_engine(session, gateway=gateway)

    assert summary.brands_considered == 2
    assert summary.drafts_created == 0

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
    await _brand_with_recommendation(session, "WouldHaveBeenDrafted")

    summary = await run_contact_draft_engine(session, gateway=None, settings=_disabled_settings())

    assert summary.ai_disabled is True
    assert summary.brands_considered == 0
    assert summary.drafts_created == 0

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.status == "SUCCESS"


def _disabled_settings():
    settings = get_settings()
    settings.yaml_config = {**settings.yaml_config, "ai": {**settings.yaml_config.get("ai", {}), "enabled": False}}
    return settings


@pytest.mark.asyncio
async def test_agent_run_and_events_are_recorded_for_successful_drafts(session: AsyncSession):
    _brand, rec = await _brand_with_recommendation(session, "Observed")
    gateway = _gateway(structured_response=_draft_response())

    summary = await run_contact_draft_engine(session, gateway=gateway)

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.agent_name == "contact_draft_engine"
    assert agent_run.status == "SUCCESS"

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id))).scalars().all()
    )
    assert len(events) == 1
    assert events[0].event_type == "contact_draft_created"
    payload = events[0].payload
    assert payload is not None
    assert payload["recommendation_ids"] == [str(rec.id)]


@pytest.mark.asyncio
async def test_max_brands_cap_is_respected(session: AsyncSession):
    for i in range(4):
        await _brand_with_recommendation(session, f"Cap{i}")
    gateway = _gateway(structured_response=_draft_response())

    summary = await run_contact_draft_engine(session, max_brands=2, gateway=gateway)

    assert summary.brands_considered == 2
    assert summary.truncated is True


@pytest.mark.asyncio
async def test_merged_brand_is_excluded(session: AsyncSession):
    brand, _rec = await _brand_with_recommendation(session, "Merged")
    brand.status = "MERGED"
    await session.flush()
    gateway = _gateway(structured_response=_draft_response())

    summary = await run_contact_draft_engine(session, gateway=gateway)

    assert summary.brands_considered == 0
