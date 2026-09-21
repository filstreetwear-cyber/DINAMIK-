"""
Milestone 9: Scoring engine.

Real-Postgres-or-skip tests only (no pure unit tests, unlike
test_opportunity_engine.py -- this module's logic is entirely about
reading/writing DB rows: latest-Opportunity-per-type collapsing,
evidence_ids traced through ResearchResult, weight_used from config).
Same fixture/skip pattern as test_opportunity_engine.py.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import (
    AgentEvent,
    AgentRun,
    AmazonProduct,
    Brand,
    ErrorLog,
    Evidence,
    Opportunity,
    ResearchJob,
    ResearchResult,
    Score,
)
from app.models.enums import DataType, EvidenceSourceType, OpportunityType
from app.services.opportunity_engine import run_opportunity_engine
from app.services.scoring_engine import run_scoring_engine


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
        pytest.skip("PostgreSQL not reachable — scoring engine tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _brand_with_products(session: AsyncSession, name: str, n_products: int, **product_overrides: Any) -> Brand:
    brand = Brand(name=name, first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC))
    session.add(brand)
    await session.flush()
    defaults: dict[str, Any] = {"rating": 4.5, "review_count": 100}
    defaults.update(product_overrides)
    for i in range(n_products):
        session.add(
            AmazonProduct(
                asin=f"B0{name[:4].upper()}{i:04d}",
                marketplace="IT",
                brand_id=brand.id,
                title=f"{name} product {i}",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                **defaults,
            )
        )
    await session.flush()
    return brand


@pytest.mark.asyncio
async def test_run_creates_score_rows_from_opportunity_rows(session: AsyncSession):
    await _brand_with_products(session, "Alpha", n_products=1)
    await run_opportunity_engine(session)

    summary = await run_scoring_engine(session)

    assert summary.brands_considered == 1
    assert summary.scores_created == 2  # AMAZON_OPPORTUNITY + BUSINESS_VALUE

    scores = (await session.execute(select(Score))).scalars().all()
    assert len(scores) == 2
    types = {s.score_type for s in scores}
    assert types == {OpportunityType.AMAZON.value, OpportunityType.BUSINESS_VALUE.value}


@pytest.mark.asyncio
async def test_brand_with_no_opportunities_gets_no_score_rows(session: AsyncSession):
    """A brand with zero AmazonProduct rows gets zero Opportunity rows
    (Milestone 8's own rule) -- and therefore zero Score rows here, not a
    fabricated NONE score."""
    brand = Brand(name="Empty", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC))
    session.add(brand)
    await session.flush()
    await run_opportunity_engine(session)

    summary = await run_scoring_engine(session)

    assert summary.brands_considered == 1
    assert summary.scores_created == 0


@pytest.mark.asyncio
async def test_healthy_brand_scores_none_amazon_opportunity_and_high_business_value(session: AsyncSession):
    """Healthy rating/reviews (matches config/default.yaml's real
    thresholds) -> AMAZON_OPPORTUNITY level NONE -> score 0.0. High review
    volume -> BUSINESS_VALUE level HIGH -> score 90.0. Both computed from
    real rule_triggered names, not hardcoded to the level mapping in
    isolation from opportunity_engine.py's actual output."""
    await _brand_with_products(session, "Healthy", n_products=1, rating=4.8, review_count=600)
    await run_opportunity_engine(session)

    await run_scoring_engine(session)

    scores = (await session.execute(select(Score))).scalars().all()
    by_type = {s.score_type: s for s in scores}

    amazon_score = by_type[OpportunityType.AMAZON.value]
    assert float(amazon_score.score) == 0.0
    assert float(amazon_score.confidence) == 85.0  # real rating/review data was present

    business_score = by_type[OpportunityType.BUSINESS_VALUE.value]
    assert float(business_score.score) == 90.0


@pytest.mark.asyncio
async def test_low_confidence_when_opportunity_had_insufficient_data(session: AsyncSession):
    """No rating on any product -> evaluate_amazon_opportunity's
    "insufficient_product_data" rule -> this engine must report the lower
    confidence tier, not fabricate certainty about a NONE-level score that
    was really "we don't know", not "we checked and it's fine"."""
    await _brand_with_products(session, "NoRating", n_products=1, rating=None, review_count=None)
    await run_opportunity_engine(session)

    await run_scoring_engine(session)

    scores = (await session.execute(select(Score))).scalars().all()
    amazon_score = next(s for s in scores if s.score_type == OpportunityType.AMAZON.value)

    assert float(amazon_score.confidence) == 30.0
    assert any("insufficient_product_data" in r for r in amazon_score.reasons)


@pytest.mark.asyncio
async def test_weight_used_matches_config(session: AsyncSession):
    await _brand_with_products(session, "Weighted", n_products=1)
    await run_opportunity_engine(session)
    settings = get_settings()

    await run_scoring_engine(session, settings=settings)

    scores = (await session.execute(select(Score))).scalars().all()
    by_type = {s.score_type: s for s in scores}

    expected_weights = settings.score_weights()
    assert float(by_type[OpportunityType.AMAZON.value].weight_used) == pytest.approx(
        expected_weights["amazon_opportunity"]
    )
    assert float(by_type[OpportunityType.BUSINESS_VALUE.value].weight_used) == pytest.approx(
        expected_weights["business_value"]
    )


@pytest.mark.asyncio
async def test_evidence_ids_traced_through_research_result(session: AsyncSession):
    brand = await _brand_with_products(session, "Traced", n_products=1)

    job = ResearchJob(name="evidence trace job", marketplace="IT")
    session.add(job)
    await session.flush()
    result = ResearchResult(job_id=job.id, brand_id=brand.id, raw_payload={})
    session.add(result)
    await session.flush()

    evidence = Evidence(
        entity_type="research_result",
        entity_id=str(result.id),
        claim="asin=B0TRACED0000",
        data_type=DataType.FACT.value,
        source="amazon",
        source_type=EvidenceSourceType.AMAZON.value,
        confidence=100.0,
        observed_at=datetime.now(UTC),
    )
    session.add(evidence)
    await session.flush()

    await run_opportunity_engine(session)
    await run_scoring_engine(session)

    scores = (await session.execute(select(Score).where(Score.brand_id == brand.id))).scalars().all()
    assert all(str(evidence.id) in s.evidence_ids for s in scores)


@pytest.mark.asyncio
async def test_brand_with_no_research_results_gets_empty_evidence_ids(session: AsyncSession):
    await _brand_with_products(session, "NoEvidence", n_products=1)
    await run_opportunity_engine(session)

    await run_scoring_engine(session)

    scores = (await session.execute(select(Score))).scalars().all()
    assert all(s.evidence_ids == [] for s in scores)


@pytest.mark.asyncio
async def test_only_the_latest_opportunity_per_type_is_scored(session: AsyncSession):
    """Opportunity is append-only -- if run_opportunity_engine ran twice
    (or a future consumer wrote a corrective row), scoring must use the
    LATEST row per type, not double-score or use a stale one."""
    brand = await _brand_with_products(session, "Repeat", n_products=1, rating=4.8, review_count=600)
    await run_opportunity_engine(session)  # first pass: NONE / HIGH
    await run_opportunity_engine(session)  # second pass: identical inputs, new rows, same levels

    all_opportunities = (
        (await session.execute(select(Opportunity).where(Opportunity.brand_id == brand.id))).scalars().all()
    )
    assert len(all_opportunities) == 4  # 2 types x 2 runs

    summary = await run_scoring_engine(session)

    assert summary.scores_created == 2  # one Score per type, not per Opportunity row


@pytest.mark.asyncio
async def test_scores_are_append_only_across_repeated_runs(session: AsyncSession):
    await _brand_with_products(session, "AppendOnly", n_products=1)
    await run_opportunity_engine(session)

    first = await run_scoring_engine(session)
    second = await run_scoring_engine(session)

    assert first.scores_created == 2
    assert second.scores_created == 2  # not skipped, not updated

    all_scores = (await session.execute(select(Score))).scalars().all()
    assert len(all_scores) == 4


@pytest.mark.asyncio
async def test_max_brands_cap_is_respected(session: AsyncSession):
    for i in range(5):
        await _brand_with_products(session, f"Cap{i}", n_products=1)
    await run_opportunity_engine(session, max_brands=5)

    summary = await run_scoring_engine(session, max_brands=3)

    assert summary.brands_considered == 3
    assert summary.truncated is True


@pytest.mark.asyncio
async def test_merged_brand_is_excluded(session: AsyncSession):
    brand = await _brand_with_products(session, "Merged", n_products=1)
    await run_opportunity_engine(session)
    brand.status = "MERGED"
    await session.flush()

    summary = await run_scoring_engine(session)

    assert summary.brands_considered == 0
    assert summary.scores_created == 0


@pytest.mark.asyncio
async def test_agent_run_and_events_are_recorded(session: AsyncSession):
    await _brand_with_products(session, "Observed", n_products=1)
    await run_opportunity_engine(session)

    summary = await run_scoring_engine(session)

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.agent_name == "scoring_engine"
    assert agent_run.status == "SUCCESS"

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    assert len(events) == 2
    assert all(e.event_type == "score_created" for e in events)


@pytest.mark.asyncio
async def test_unhandled_exception_mid_run_is_caught_and_recorded(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    await _brand_with_products(session, "Boom", n_products=1)
    await run_opportunity_engine(session)

    async def _boom(*args: Any, **kwargs: Any):
        raise RuntimeError("simulated bug, not a domain error")

    monkeypatch.setattr("app.services.scoring_engine._score_brand", _boom)

    summary = await run_scoring_engine(session)  # must not raise

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.status == "FAILED"
    assert agent_run.error is not None
    assert "simulated bug" in agent_run.error

    error_logs = (
        (await session.execute(select(ErrorLog).where(ErrorLog.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    assert len(error_logs) == 1
    assert "simulated bug" in error_logs[0].message
