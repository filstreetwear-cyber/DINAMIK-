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
from app.models import AmazonProduct, Brand, Company


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
