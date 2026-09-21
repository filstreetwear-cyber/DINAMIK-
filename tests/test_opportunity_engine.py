"""
Milestone 8: Opportunity engine.

Pure unit tests for the two implemented rule functions (no DB needed --
AmazonProduct/AmazonSeller instances here are never persisted, just used
as plain attribute holders), plus real-Postgres-or-skip tests for
run_opportunity_engine's driver: brand selection (ACTIVE only, MERGED
excluded per Milestone 7), the append-only write pattern, max_brands
capping, and a real Milestone 5 -> 6 -> 8 composition against the mock
fixture (Milestone 7 deliberately skipped in that chain -- a single brand
has nothing to deduplicate against).
"""
from __future__ import annotations

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
    AmazonSeller,
    Brand,
    ErrorLog,
    Opportunity,
    ResearchJob,
)
from app.models.enums import OpportunityLevel, OpportunityType
from app.services.brand_resolution import resolve_research_job
from app.services.opportunity_engine import (
    evaluate_amazon_opportunity,
    evaluate_business_value,
    run_opportunity_engine,
)
from app.tools.amazon.provider import AmazonProvider
from app.tools.core.orchestrator import IntelligenceSourceOrchestrator
from app.tools.core.registry import ProviderRegistry
from app.tools.keepa.provider import KeepaProvider

# ---------------------------------------------------------------------------
# evaluate_amazon_opportunity / evaluate_business_value -- pure unit tests
# ---------------------------------------------------------------------------


def _product(**overrides: Any) -> AmazonProduct:
    defaults: dict[str, Any] = {"rating": None, "review_count": None}
    defaults.update(overrides)
    return AmazonProduct(**defaults)


def test_amazon_opportunity_returns_none_for_no_products():
    settings = get_settings()
    assert evaluate_amazon_opportunity([], settings) is None


def test_amazon_opportunity_insufficient_data_when_no_ratings():
    settings = get_settings()
    products = [_product(review_count=100), _product(review_count=50)]
    result = evaluate_amazon_opportunity(products, settings)
    assert result is not None
    assert result.level == OpportunityLevel.NONE
    assert result.rule_triggered == "insufficient_product_data"
    assert result.reasons == []


def test_amazon_opportunity_high_when_both_reasons_trigger():
    settings = get_settings()
    products = [_product(rating=3.5, review_count=5), _product(rating=3.8, review_count=3)]
    result = evaluate_amazon_opportunity(products, settings)
    assert result is not None
    assert result.level == OpportunityLevel.HIGH
    assert len(result.reasons) == 2


def test_amazon_opportunity_medium_when_one_reason_triggers():
    settings = get_settings()
    # Good rating, but thin review base.
    products = [_product(rating=4.8, review_count=5)]
    result = evaluate_amazon_opportunity(products, settings)
    assert result is not None
    assert result.level == OpportunityLevel.MEDIUM
    assert len(result.reasons) == 1


def test_amazon_opportunity_none_level_when_both_are_healthy():
    settings = get_settings()
    products = [_product(rating=4.5, review_count=200), _product(rating=4.6, review_count=300)]
    result = evaluate_amazon_opportunity(products, settings)
    assert result is not None
    assert result.level == OpportunityLevel.NONE
    assert result.rule_triggered == "rating_and_review_volume_v1"
    assert result.reasons == []


def test_business_value_returns_none_for_no_products():
    settings = get_settings()
    assert evaluate_business_value([], [], settings) is None


def test_business_value_low_for_a_single_product():
    settings = get_settings()
    result = evaluate_business_value([_product()], [], settings)
    assert result is not None
    assert result.level == OpportunityLevel.LOW


def test_business_value_medium_at_the_product_count_threshold():
    settings = get_settings()
    products = [_product() for _ in range(3)]
    result = evaluate_business_value(products, [], settings)
    assert result is not None
    assert result.level == OpportunityLevel.MEDIUM


def test_business_value_high_at_the_product_count_threshold():
    settings = get_settings()
    products = [_product() for _ in range(8)]
    result = evaluate_business_value(products, [], settings)
    assert result is not None
    assert result.level == OpportunityLevel.HIGH


def test_business_value_high_from_review_volume_alone():
    settings = get_settings()
    products = [_product(review_count=600)]
    result = evaluate_business_value(products, [], settings)
    assert result is not None
    assert result.level == OpportunityLevel.HIGH


def test_business_value_notes_multi_seller_presence():
    settings = get_settings()
    sellers = [AmazonSeller(seller_id="S1", marketplace="IT"), AmazonSeller(seller_id="S2", marketplace="IT")]
    result = evaluate_business_value([_product()], sellers, settings)
    assert result is not None
    assert any("multi-seller" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


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
        pytest.skip("PostgreSQL not reachable — opportunity engine tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.fixture
def orchestrator(monkeypatch: pytest.MonkeyPatch) -> IntelligenceSourceOrchestrator:
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    settings = get_settings()

    registry = ProviderRegistry()
    registry.register(AmazonProvider(settings=settings), priority=20, cost_model="mock")
    registry.register(KeepaProvider(settings=settings), priority=30, cost_model="mock")
    return IntelligenceSourceOrchestrator(registry)


async def _brand_with_products(session: AsyncSession, name: str, n_products: int) -> Brand:
    from datetime import UTC, datetime

    brand = Brand(name=name, first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC))
    session.add(brand)
    await session.flush()
    for i in range(n_products):
        session.add(
            AmazonProduct(
                asin=f"B0{name[:4].upper()}{i:04d}",
                marketplace="IT",
                brand_id=brand.id,
                title=f"{name} product {i}",
                rating=4.5,
                review_count=100,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
        )
    await session.flush()
    return brand


@pytest.mark.asyncio
async def test_run_creates_opportunity_rows_for_a_brand_with_products(session: AsyncSession):
    brand = await _brand_with_products(session, "Alpha", n_products=1)

    summary = await run_opportunity_engine(session)

    assert summary.brands_considered == 1
    assert summary.opportunities_created == 2  # AMAZON_OPPORTUNITY + BUSINESS_VALUE

    opportunities = (
        (await session.execute(select(Opportunity).where(Opportunity.brand_id == brand.id)))
        .scalars()
        .all()
    )
    assert len(opportunities) == 2
    types = {o.opportunity_type for o in opportunities}
    assert types == {OpportunityType.AMAZON.value, OpportunityType.BUSINESS_VALUE.value}

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.agent_name == "opportunity_engine"
    assert agent_run.status == "SUCCESS"

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    assert len(events) == 2
    assert all(e.event_type == "opportunity_created" for e in events)


@pytest.mark.asyncio
async def test_brand_with_no_products_gets_no_opportunity_rows(session: AsyncSession):
    from datetime import UTC, datetime

    brand = Brand(name="Empty Brand", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC))
    session.add(brand)
    await session.flush()

    summary = await run_opportunity_engine(session)

    assert summary.brands_considered == 1
    assert summary.opportunities_created == 0

    opportunities = (
        (await session.execute(select(Opportunity).where(Opportunity.brand_id == brand.id)))
        .scalars()
        .all()
    )
    assert opportunities == []


@pytest.mark.asyncio
async def test_merged_brand_is_excluded_from_evaluation(session: AsyncSession):
    brand = await _brand_with_products(session, "Merged", n_products=1)
    brand.status = "MERGED"
    await session.flush()

    summary = await run_opportunity_engine(session)

    assert summary.brands_considered == 0
    assert summary.opportunities_created == 0


@pytest.mark.asyncio
async def test_opportunities_are_append_only_across_repeated_runs(session: AsyncSession):
    await _brand_with_products(session, "Repeat", n_products=1)

    first_summary = await run_opportunity_engine(session)
    second_summary = await run_opportunity_engine(session)

    assert first_summary.opportunities_created == 2
    assert second_summary.opportunities_created == 2  # not skipped, not updated -- a fresh assessment

    all_opportunities = (await session.execute(select(Opportunity))).scalars().all()
    assert len(all_opportunities) == 4  # 2 + 2, both runs' rows kept


@pytest.mark.asyncio
async def test_max_brands_cap_is_respected(session: AsyncSession):
    for i in range(5):
        await _brand_with_products(session, f"Cap{i}", n_products=1)

    summary = await run_opportunity_engine(session, max_brands=3)

    assert summary.brands_considered == 3
    assert summary.truncated is True


@pytest.mark.asyncio
async def test_unhandled_exception_mid_run_is_caught_and_recorded(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    await _brand_with_products(session, "Boom", n_products=1)

    async def _boom(*args: Any, **kwargs: Any):
        raise RuntimeError("simulated bug, not a domain error")

    monkeypatch.setattr("app.services.opportunity_engine._evaluate_brand", _boom)

    summary = await run_opportunity_engine(session)  # must not raise

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


@pytest.mark.asyncio
async def test_end_to_end_discovery_into_resolution_into_opportunity_engine(
    session: AsyncSession, orchestrator: IntelligenceSourceOrchestrator
):
    """Real Milestone 5 -> 6 -> 8 chain against the mock fixture. Both mock
    ASINs (rating 4.3/4.6, review_count 812/2301, one shared seller/brand)
    collapse into one Brand with 2 products -- known values, asserted
    exactly against config/default.yaml's real thresholds (min_rating=4.2,
    min_review_count=50, review_count_high=500): average rating and review
    volume are both healthy for AMAZON_OPPORTUNITY (-> NONE); for
    BUSINESS_VALUE, only 2 products is below the medium threshold of 3, but
    total_reviews=3113 clears the review_count_high threshold of 500 on its
    own, so the level is HIGH via the review-volume reason alone."""
    from app.agents.amazon_scout.discovery import run_amazon_scout_discovery

    job = ResearchJob(name="e2e opportunity test", marketplace="IT", category="Casa e cucina")
    session.add(job)
    await session.flush()

    discovery_outcome = await run_amazon_scout_discovery(session, job, orchestrator)
    assert discovery_outcome.products_found == 2

    await resolve_research_job(session, job)

    summary = await run_opportunity_engine(session)
    assert summary.brands_considered == 1
    assert summary.opportunities_created == 2

    brands = (await session.execute(select(Brand))).scalars().all()
    assert len(brands) == 1
    brand = brands[0]

    opportunities = (
        (await session.execute(select(Opportunity).where(Opportunity.brand_id == brand.id)))
        .scalars()
        .all()
    )
    by_type = {o.opportunity_type: o for o in opportunities}

    amazon_opp = by_type[OpportunityType.AMAZON.value]
    assert amazon_opp.level == OpportunityLevel.NONE.value
    assert amazon_opp.reasons == []

    business_value_opp = by_type[OpportunityType.BUSINESS_VALUE.value]
    assert business_value_opp.level == OpportunityLevel.HIGH.value
    assert any("total_reviews" in r for r in business_value_opp.reasons)
