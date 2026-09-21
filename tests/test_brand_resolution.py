"""
Milestone 6: Brand resolution.

Same real-Postgres-or-skip pattern as tests/test_amazon_scout_discovery.py
and tests/test_database_integration.py — unique constraints
(uq_seller_marketplace, uq_asin_marketplace), JSONB raw_payload and
FK SET NULL behavior aren't faithfully testable against anything else.

Covers: the four BrandResolutionRule outcomes individually
(EXISTING_SELLER_LINK, EXACT_BRAND_NAME_MATCH, NEW_BRAND_CREATED,
UNRESOLVED_MISSING_DATA), that AmazonSeller.verified stays False under
purely deterministic resolution, seller/brand conflict handling (existing
link kept, conflict flagged and recorded as an AgentEvent),
resolve_research_job's skip of already-resolved results, a real
Milestone-5-into-Milestone-6 end-to-end composition against the mock
fixture (two ASINs collapsing into one Brand/one AmazonSeller), and the
same unhandled-exception-is-caught-at-the-agent-run-boundary discipline as
discovery.py's own test.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.amazon_scout.discovery import run_amazon_scout_discovery
from app.core.config import get_settings
from app.models import (
    AgentEvent,
    AgentRun,
    AmazonProduct,
    AmazonSeller,
    Brand,
    ErrorLog,
    ResearchJob,
    ResearchResult,
)
from app.services.brand_resolution import (
    BrandResolutionRule,
    resolve_research_job,
    resolve_research_result,
)
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
        pytest.skip("PostgreSQL not reachable — brand resolution tests need a live DB")
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


def _new_job(**overrides: Any) -> ResearchJob:
    defaults: dict[str, Any] = {
        "name": "test brand resolution job",
        "marketplace": "IT",
        "category": "Casa e cucina",
        "priority": 0,
    }
    defaults.update(overrides)
    return ResearchJob(**defaults)


def _new_result(job: ResearchJob, **payload_overrides: Any) -> ResearchResult:
    payload: dict[str, Any] = {
        "asin": "B0MOCK0001",
        "marketplace": "IT",
        "title": "Mock Product — Ceramic Coffee Grinder",
        "category": "Casa e cucina",
        "rating": 4.3,
        "review_count": 812,
        "seller_id": "MOCKSELLER01",
        "seller_name": "MockBrand Kitchenware Srl",
        "brand": "MockBrand Kitchenware",
        "bsr": 1250,
    }
    payload.update(payload_overrides)
    return ResearchResult(job_id=job.id, raw_payload=payload)


@pytest.mark.asyncio
async def test_new_brand_created_on_first_observation(session: AsyncSession):
    job = _new_job()
    session.add(job)
    await session.flush()
    result = _new_result(job)
    session.add(result)
    await session.flush()

    outcome = await resolve_research_result(session, result)

    assert outcome.rule_applied == BrandResolutionRule.NEW_BRAND_CREATED
    assert outcome.product_id is not None
    assert outcome.brand_id is not None
    assert outcome.seller_row_id is not None
    assert outcome.verified is False
    assert outcome.conflict is False

    assert result.product_id == outcome.product_id
    assert result.brand_id == outcome.brand_id

    brand = await session.get(Brand, outcome.brand_id)
    assert brand is not None
    assert brand.name == "MockBrand Kitchenware"
    assert brand.company_id is None  # explicitly out of scope for this milestone

    seller = await session.get(AmazonSeller, outcome.seller_row_id)
    assert seller is not None
    assert seller.seller_id == "MOCKSELLER01"
    assert seller.brand_id == brand.id
    assert seller.verified is False  # never set True by deterministic resolution

    product = await session.get(AmazonProduct, outcome.product_id)
    assert product is not None
    assert product.asin == "B0MOCK0001"
    assert product.brand_id == brand.id
    assert product.seller_id == seller.id
    assert product.title == "Mock Product — Ceramic Coffee Grinder"


@pytest.mark.asyncio
async def test_exact_brand_name_match_reuses_existing_brand_case_insensitively(
    session: AsyncSession,
):
    job = _new_job()
    session.add(job)
    await session.flush()

    # Pre-existing brand from an unrelated seller, different casing.
    existing_brand = Brand(name="mockbrand kitchenware")
    session.add(existing_brand)
    await session.flush()

    result = _new_result(job, seller_id="MOCKSELLER02", seller_name="Another Reseller Srl")
    session.add(result)
    await session.flush()

    outcome = await resolve_research_result(session, result)

    assert outcome.rule_applied == BrandResolutionRule.EXACT_BRAND_NAME_MATCH
    assert outcome.brand_id == existing_brand.id

    seller = await session.get(AmazonSeller, outcome.seller_row_id)
    assert seller is not None
    assert seller.brand_id == existing_brand.id


@pytest.mark.asyncio
async def test_existing_seller_link_is_reused_not_relinked(session: AsyncSession):
    job = _new_job()
    session.add(job)
    await session.flush()

    result_1 = _new_result(job)
    session.add(result_1)
    await session.flush()
    first_outcome = await resolve_research_result(session, result_1)

    # Second observation of the same seller_id/marketplace, second ASIN.
    result_2 = _new_result(
        job,
        asin="B0MOCK0002",
        title="Mock Product — Insulated Steel Water Bottle 1L",
    )
    session.add(result_2)
    await session.flush()
    second_outcome = await resolve_research_result(session, result_2)

    assert second_outcome.rule_applied == BrandResolutionRule.EXISTING_SELLER_LINK
    assert second_outcome.brand_id == first_outcome.brand_id
    assert second_outcome.seller_row_id == first_outcome.seller_row_id
    assert second_outcome.conflict is False

    sellers = (
        (
            await session.execute(
                select(AmazonSeller).where(AmazonSeller.seller_id == "MOCKSELLER01")
            )
        )
        .scalars()
        .all()
    )
    assert len(sellers) == 1  # not duplicated by the second observation

    brands = (await session.execute(select(Brand))).scalars().all()
    assert len(brands) == 1  # not duplicated either


@pytest.mark.asyncio
async def test_seller_brand_conflict_keeps_existing_link_and_flags_conflict(
    session: AsyncSession,
):
    job = _new_job()
    session.add(job)
    await session.flush()

    result_1 = _new_result(job)
    session.add(result_1)
    await session.flush()
    first_outcome = await resolve_research_result(session, result_1)

    # Same seller_id, but this time the listing claims a different brand.
    result_2 = _new_result(
        job,
        asin="B0MOCK0002",
        brand="Some Other Brand",
    )
    session.add(result_2)
    await session.flush()
    second_outcome = await resolve_research_result(session, result_2)

    assert second_outcome.rule_applied == BrandResolutionRule.EXISTING_SELLER_LINK
    assert second_outcome.conflict is True
    # The EXISTING brand link is kept, never silently overwritten.
    assert second_outcome.brand_id == first_outcome.brand_id

    brands = (
        (await session.execute(select(Brand).where(Brand.name == "Some Other Brand")))
        .scalars()
        .all()
    )
    assert brands == []  # the conflicting brand name was never created


@pytest.mark.asyncio
async def test_missing_seller_or_brand_data_resolves_product_only(session: AsyncSession):
    job = _new_job()
    session.add(job)
    await session.flush()
    result = _new_result(job, seller_id=None, seller_name=None, brand=None)
    session.add(result)
    await session.flush()

    outcome = await resolve_research_result(session, result)

    assert outcome.rule_applied == BrandResolutionRule.UNRESOLVED_MISSING_DATA
    assert outcome.product_id is not None  # the product itself still resolves
    assert outcome.brand_id is None
    assert outcome.seller_row_id is None

    assert result.product_id == outcome.product_id
    assert result.brand_id is None

    product = await session.get(AmazonProduct, outcome.product_id)
    assert product is not None
    assert product.brand_id is None
    assert product.seller_id is None


@pytest.mark.asyncio
async def test_missing_asin_or_marketplace_resolves_nothing(session: AsyncSession):
    job = _new_job()
    session.add(job)
    await session.flush()
    result = _new_result(job, asin=None)
    session.add(result)
    await session.flush()

    outcome = await resolve_research_result(session, result)

    assert outcome.rule_applied == BrandResolutionRule.UNRESOLVED_MISSING_DATA
    assert outcome.product_id is None
    assert result.product_id is None

    products = (await session.execute(select(AmazonProduct))).scalars().all()
    assert products == []  # nothing was created at all


@pytest.mark.asyncio
async def test_job_driver_skips_already_resolved_results(session: AsyncSession):
    job = _new_job()
    session.add(job)
    await session.flush()

    result_1 = _new_result(job)
    session.add(result_1)
    await session.flush()
    await resolve_research_result(session, result_1)  # resolved ahead of the job driver

    result_2 = _new_result(job, asin="B0MOCK0002")
    session.add(result_2)
    await session.flush()

    summary = await resolve_research_job(session, job)

    assert summary.processed == 1  # only result_2, result_1 already had product_id set
    assert summary.resolved == 1
    assert summary.unresolved == 0
    assert summary.conflicts == 0

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.agent_name == "brand_resolver"
    assert agent_run.status == "SUCCESS"
    assert agent_run.job_id == job.id
    assert agent_run.output == {
        "processed": 1,
        "resolved": 1,
        "unresolved": 0,
        "conflicts": 0,
    }

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].event_type == "result_resolved"

    # job.status untouched — that belongs to the discovery step, not this one.
    assert job.status is None or job.status != "COMPLETED"


@pytest.mark.asyncio
async def test_job_driver_records_conflicts_as_agent_events(session: AsyncSession):
    job = _new_job()
    session.add(job)
    await session.flush()

    result_1 = _new_result(job)
    session.add(result_1)
    await session.flush()
    await resolve_research_result(session, result_1)

    result_2 = _new_result(job, asin="B0MOCK0002", brand="Some Other Brand")
    session.add(result_2)
    await session.flush()

    summary = await resolve_research_job(session, job)

    assert summary.processed == 1
    assert summary.conflicts == 1

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    event_types = [e.event_type for e in events]
    assert event_types.count("seller_brand_conflict") == 1
    assert event_types.count("result_resolved") == 1


@pytest.mark.asyncio
async def test_end_to_end_discovery_into_brand_resolution_collapses_mock_fixture(
    session: AsyncSession, orchestrator: IntelligenceSourceOrchestrator
):
    """Real Milestone 5 discovery run against the mock fixture, fed straight
    into Milestone 6 resolution: both mock ASINs share seller_id
    MOCKSELLER01 / brand "MockBrand Kitchenware" (fixtures/amazon_mock_products.json)
    and must collapse into exactly one Brand row and one AmazonSeller row,
    with both products linked to both."""
    job = _new_job()
    session.add(job)
    await session.flush()

    discovery_outcome = await run_amazon_scout_discovery(session, job, orchestrator)
    assert discovery_outcome.products_found == 2

    summary = await resolve_research_job(session, job)

    assert summary.processed == 2
    assert summary.resolved == 2
    assert summary.unresolved == 0
    assert summary.conflicts == 0

    brands = (await session.execute(select(Brand))).scalars().all()
    assert len(brands) == 1
    assert brands[0].name == "MockBrand Kitchenware"

    sellers = (await session.execute(select(AmazonSeller))).scalars().all()
    assert len(sellers) == 1
    assert sellers[0].seller_id == "MOCKSELLER01"
    assert sellers[0].brand_id == brands[0].id
    assert sellers[0].verified is False

    products = (await session.execute(select(AmazonProduct))).scalars().all()
    assert len(products) == 2
    assert {p.asin for p in products} == {"B0MOCK0001", "B0MOCK0002"}
    assert all(p.brand_id == brands[0].id for p in products)
    assert all(p.seller_id == sellers[0].id for p in products)

    results = (
        (await session.execute(select(ResearchResult).where(ResearchResult.job_id == job.id)))
        .scalars()
        .all()
    )
    assert len(results) == 2
    assert all(r.product_id is not None for r in results)
    assert all(r.brand_id == brands[0].id for r in results)


@pytest.mark.asyncio
async def test_unhandled_exception_mid_job_is_caught_and_recorded(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Same agent-run boundary discipline as discovery.py's own test: a real
    bug during resolution must not propagate out of resolve_research_job —
    caught, recorded as AgentRun.status=FAILED + ErrorLog, function returns
    instead of raising."""
    job = _new_job()
    session.add(job)
    await session.flush()

    result = _new_result(job)
    session.add(result)
    await session.flush()

    async def _boom(*args: Any, **kwargs: Any):
        raise RuntimeError("simulated bug, not a domain error")

    monkeypatch.setattr(
        "app.services.brand_resolution.resolve_research_result", _boom
    )

    summary = await resolve_research_job(session, job)  # must not raise

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
