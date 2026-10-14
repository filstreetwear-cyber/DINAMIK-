"""
Milestone 2 integration tests: run against a real PostgreSQL instance
(not sqlite, not a mock) — the spec (section 6) is explicit about
PostgreSQL, and unique constraints / JSONB / server-side defaults behave
differently enough across engines that testing against anything else
would be testing the wrong thing.

Requires DATABASE_URL to point at a reachable Postgres. Skipped
automatically if the database is unreachable (e.g. in an environment
that hasn't provisioned one yet) rather than failing the whole suite.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import (
    AmazonProduct,
    Brand,
    BrowserSession,
    BrowserTask,
    Company,
    Evidence,
    ResearchJob,
    ToolCall,
)


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
        pytest.skip("PostgreSQL not reachable — Milestone 2 integration tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()  # never leave fixture rows behind for other tests
    await engine.dispose()


@pytest.mark.asyncio
async def test_company_brand_relationship_round_trip(session: AsyncSession):
    company = Company(legal_name="Test Co Srl", country="IT")
    brand = Brand(name="TestBrand", company=company)
    session.add_all([company, brand])
    await session.flush()

    fetched = (await session.execute(select(Brand).where(Brand.id == brand.id))).scalar_one()
    assert fetched.company_id == company.id
    assert fetched.company is not None
    assert fetched.company.legal_name == "Test Co Srl"


@pytest.mark.asyncio
async def test_asin_marketplace_unique_constraint_is_enforced(session: AsyncSession):
    now = datetime.now(UTC)
    product_1 = AmazonProduct(
        asin="B000TEST01", marketplace="IT", title="Test product", first_seen_at=now, last_seen_at=now
    )
    session.add(product_1)
    await session.flush()

    duplicate = AmazonProduct(
        asin="B000TEST01", marketplace="IT", title="Duplicate ASIN", first_seen_at=now, last_seen_at=now
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.asyncio
async def test_same_asin_different_marketplace_is_allowed(session: AsyncSession):
    """The unique constraint is (asin, marketplace) — same ASIN across IT/DE is a normal case."""
    now = datetime.now(UTC)
    session.add(
        AmazonProduct(
            asin="B000TEST02", marketplace="IT", title="Italian listing", first_seen_at=now, last_seen_at=now
        )
    )
    session.add(
        AmazonProduct(
            asin="B000TEST02", marketplace="DE", title="German listing", first_seen_at=now, last_seen_at=now
        )
    )
    await session.flush()  # must NOT raise


@pytest.mark.asyncio
async def test_evidence_and_tool_call_accept_provider_and_acquisition_method(session: AsyncSession):
    """Refactor 2026-09-21: the new nullable columns round-trip, and rows written
    before this migration (provider/acquisition_method = NULL) remain valid —
    the columns are additive, not backfilled."""
    now = datetime.now(UTC)
    evidence_with_provenance = Evidence(
        entity_type="brand",
        entity_id="00000000-0000-0000-0000-000000000001",
        claim="Website runs on Shopify",
        data_type="FACT",
        source="website_intelligence",
        source_type="WEBSITE",
        confidence=95.0,
        observed_at=now,
        provider="website_intelligence",
        acquisition_method="HTTP",
    )
    evidence_legacy_shape = Evidence(
        entity_type="brand",
        entity_id="00000000-0000-0000-0000-000000000002",
        claim="Pre-refactor row, no provenance columns set",
        data_type="FACT",
        source="amazon",
        source_type="AMAZON",
        confidence=90.0,
        observed_at=now,
    )
    session.add_all([evidence_with_provenance, evidence_legacy_shape])
    await session.flush()

    assert evidence_with_provenance.provider == "website_intelligence"
    assert evidence_legacy_shape.provider is None
    assert evidence_legacy_shape.acquisition_method is None


@pytest.mark.asyncio
async def test_browser_session_and_task_round_trip(session: AsyncSession):
    """New tables from the refactor: a session (persistent login state) and a
    task that references it, plus the WAITING_FOR_HUMAN status the spec
    requires as a first-class state, not a bypassed edge case."""
    tool_call = ToolCall(tool_name="helium10_dashboard", status="PENDING", provider="helium10")
    session.add(tool_call)
    await session.flush()

    browser_session = BrowserSession(
        provider="helium10",
        account_label="dinamik-advisor-basic",
        status="REQUIRES_LOGIN",
    )
    session.add(browser_session)
    await session.flush()

    task = BrowserTask(
        browser_session_id=browser_session.id,
        tool_call_id=tool_call.id,
        provider="helium10",
        task_type="login",
        status="WAITING_FOR_HUMAN",
        requires_human=True,
    )
    session.add(task)
    await session.flush()

    fetched = (await session.execute(select(BrowserTask).where(BrowserTask.id == task.id))).scalar_one()
    assert fetched.status == "WAITING_FOR_HUMAN"
    assert fetched.requires_human is True
    assert fetched.browser_session_id == browser_session.id
    assert fetched.tool_call_id == tool_call.id


@pytest.mark.asyncio
async def test_research_job_created_at_gets_a_default(session: AsyncSession):
    """2026-09-21 fix (Milestone 5): unlike every other model, ResearchJob's
    created_at was hand-rolled without default=utcnow — it worked only as
    long as every caller remembered to set it manually. Constructing one
    without passing created_at must not raise NOT NULL and must produce a
    real, recent timestamp."""
    job = ResearchJob(name="created_at default check", marketplace="IT")
    session.add(job)
    await session.flush()

    assert job.created_at is not None
    assert (datetime.now(UTC) - job.created_at).total_seconds() < 30
