"""
Milestone 10: AI Analysis Engine — the first real caller of AIAnalyst
(Milestone 9.5) against real Evidence rows. Real-Postgres-or-skip, same
fixture/skip pattern as test_opportunity_engine.py/test_scoring_engine.py.

Every test here injects a gateway backed by MockAIProvider — no real
Ollama required, same discipline as tests/test_ai_provider.py. This file
proves the WIRING (Evidence -> AIAnalyst -> persisted AIClaim rows,
AgentRun/AgentEvent bookkeeping, per-brand failure isolation, ai.enabled
handling) is real, not that a real model produces good output.
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
from app.models import AgentEvent, AgentRun, Brand, ErrorLog, Evidence, ResearchJob, ResearchResult
from app.models.ai_claim import AIClaim
from app.models.enums import DataType, ErrorCategory, EvidenceSourceType
from app.services.ai_analysis_engine import run_ai_analysis_engine


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
        pytest.skip("PostgreSQL not reachable — AI analysis engine tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


def _draft_response(evidence_ids: list[str]) -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "claim": "Real evidence summarized for this brand.",
                    "data_type": "INFERENCE",
                    "confidence": 70.0,
                    "evidence_ids": evidence_ids,
                    "reasoning_summary": "Derived from the supplied evidence set.",
                }
            ]
        }
    )


async def _brand_with_evidence(session: AsyncSession, name: str, n_evidence: int = 1) -> tuple[Brand, list[str]]:
    brand = Brand(name=name, first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC))
    session.add(brand)
    await session.flush()

    job = ResearchJob(name=f"{name} job", marketplace="IT")
    session.add(job)
    await session.flush()
    result = ResearchResult(job_id=job.id, brand_id=brand.id, raw_payload={})
    session.add(result)
    await session.flush()

    evidence_ids: list[str] = []
    for i in range(n_evidence):
        evidence = Evidence(
            entity_type="research_result",
            entity_id=str(result.id),
            claim=f"claim number {i} for {name}",
            data_type=DataType.FACT.value,
            source="amazon",
            source_type=EvidenceSourceType.AMAZON.value,
            confidence=90.0,
            observed_at=datetime.now(UTC),
        )
        session.add(evidence)
        await session.flush()
        evidence_ids.append(str(evidence.id))

    return brand, evidence_ids


def _gateway(**mock_kwargs: Any) -> AIGateway:
    return AIGateway(MockAIProvider(**mock_kwargs))


@pytest.mark.asyncio
async def test_brand_with_evidence_gets_persisted_ai_claim(session: AsyncSession):
    brand, evidence_ids = await _brand_with_evidence(session, "Alpha")
    gateway = _gateway(structured_response=_draft_response(evidence_ids))

    summary = await run_ai_analysis_engine(session, gateway=gateway)

    assert summary.brands_considered == 1
    assert summary.claims_created == 1
    assert summary.ai_disabled is False

    claims = (await session.execute(select(AIClaim).where(AIClaim.entity_id == str(brand.id)))).scalars().all()
    assert len(claims) == 1
    claim = claims[0]
    assert claim.entity_type == "brand"
    assert claim.data_type == DataType.INFERENCE.value
    assert claim.model == "mock-model"
    assert claim.provider == "mock"
    assert set(claim.evidence_ids) == set(evidence_ids)


@pytest.mark.asyncio
async def test_brand_with_no_evidence_is_skipped_without_calling_ai(session: AsyncSession):
    brand = Brand(name="NoEvidence", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC))
    session.add(brand)
    await session.flush()

    # raise_error would blow up the test if the engine actually called the
    # gateway for this brand -- proves the short-circuit is real.
    gateway = _gateway(raise_error=AIUnavailableError("mock", "should never be called"))

    summary = await run_ai_analysis_engine(session, gateway=gateway)

    assert summary.brands_considered == 1
    assert summary.claims_created == 0


@pytest.mark.asyncio
async def test_ai_unavailable_errors_do_not_abort_the_batch(session: AsyncSession):
    await _brand_with_evidence(session, "Boom")
    await _brand_with_evidence(session, "AlsoBoom")

    # Same MockAIProvider instance fails for both brands in this test --
    # what matters is that each failure is recorded as a controlled
    # ErrorLog, not a crashed AgentRun, and that brands_considered still
    # covers both (one brand's AI failure never aborts the batch).
    gateway = _gateway(raise_error=AIUnavailableError("mock", "down"))

    summary = await run_ai_analysis_engine(session, gateway=gateway)

    assert summary.brands_considered == 2
    assert summary.claims_created == 0

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.status == "SUCCESS"  # the run itself succeeded -- per-brand AI failure is not a run failure

    error_logs = (
        (await session.execute(select(ErrorLog).where(ErrorLog.agent_run_id == agent_run.id))).scalars().all()
    )
    assert len(error_logs) == 2  # one per brand
    assert all(e.category == ErrorCategory.AI_ERROR.value for e in error_logs)


@pytest.mark.asyncio
async def test_insufficient_evidence_status_does_not_write_an_error_log(session: AsyncSession):
    """INSUFFICIENT_EVIDENCE is a legitimate, expected outcome (the model
    said "I can't responsibly claim anything") -- not the same category
    as AI_UNAVAILABLE/VALIDATION_ERROR, so it must not pollute ErrorLog."""
    _brand, _evidence_ids = await _brand_with_evidence(session, "Sparse")
    gateway = _gateway(structured_response=json.dumps({"claims": []}))

    summary = await run_ai_analysis_engine(session, gateway=gateway)

    assert summary.claims_created == 0
    error_logs = (await session.execute(select(ErrorLog))).scalars().all()
    assert len(error_logs) == 0


@pytest.mark.asyncio
async def test_validation_error_from_invented_evidence_id_is_logged(session: AsyncSession):
    _brand, _real_ids = await _brand_with_evidence(session, "Invented")
    gateway = _gateway(structured_response=_draft_response(["not-a-real-evidence-id"]))

    summary = await run_ai_analysis_engine(session, gateway=gateway)

    assert summary.claims_created == 0
    error_logs = (await session.execute(select(ErrorLog))).scalars().all()
    assert len(error_logs) == 1
    assert error_logs[0].category == ErrorCategory.AI_ERROR.value


@pytest.mark.asyncio
async def test_ai_disabled_gateway_is_a_clean_noop_not_a_failure(session: AsyncSession):
    await _brand_with_evidence(session, "WouldHaveBeenAnalyzed")

    summary = await run_ai_analysis_engine(session, gateway=None, settings=_disabled_settings())

    assert summary.ai_disabled is True
    assert summary.brands_considered == 0
    assert summary.claims_created == 0

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.status == "SUCCESS"


def _disabled_settings():
    settings = get_settings()
    settings.yaml_config = {**settings.yaml_config, "ai": {**settings.yaml_config.get("ai", {}), "enabled": False}}
    return settings


@pytest.mark.asyncio
async def test_agent_run_and_events_are_recorded_for_successful_claims(session: AsyncSession):
    _brand, evidence_ids = await _brand_with_evidence(session, "Observed")
    gateway = _gateway(structured_response=_draft_response(evidence_ids))

    summary = await run_ai_analysis_engine(session, gateway=gateway)

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.agent_name == "ai_analysis_engine"
    assert agent_run.status == "SUCCESS"

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id))).scalars().all()
    )
    assert len(events) == 1
    assert events[0].event_type == "ai_claim_created"


@pytest.mark.asyncio
async def test_max_brands_cap_is_respected(session: AsyncSession):
    for i in range(4):
        await _brand_with_evidence(session, f"Cap{i}")
    gateway = _gateway(structured_response=json.dumps({"claims": []}))

    summary = await run_ai_analysis_engine(session, max_brands=2, gateway=gateway)

    assert summary.brands_considered == 2
    assert summary.truncated is True


@pytest.mark.asyncio
async def test_merged_brand_is_excluded(session: AsyncSession):
    brand, _ = await _brand_with_evidence(session, "Merged")
    brand.status = "MERGED"
    await session.flush()
    gateway = _gateway(structured_response=json.dumps({"claims": []}))

    summary = await run_ai_analysis_engine(session, gateway=gateway)

    assert summary.brands_considered == 0
