"""
Milestone 7: Brand deduplication.

Same real-Postgres-or-skip pattern as tests/test_brand_resolution.py — the
DB-backed tests need real FK behavior (merged_into_id self-reference,
brand_id repointing across AmazonSeller/AmazonProduct/ResearchResult).

Covers: normalize_brand_name's own edge cases (legal-suffix stripping,
dotted-acronym collapsing, diacritics, the never-return-empty guard) as
pure unit tests; the two-tier design (exact-after-normalization auto-merge
vs near-duplicate flag-only) with real calibrated similarity scores;
canonical selection (earliest first_seen_at, deterministic id tie-break);
company_id conflict handling on merge; a 3-way duplicate group collapsing
into one canonical; max_brands/truncated capping and its idempotent
repeated-call behavior; and the unhandled-exception-is-caught discipline
shared with brand_resolution.py and discovery.py.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    Company,
    ErrorLog,
    ResearchJob,
    ResearchResult,
)
from app.services.brand_deduplication import (
    DEFAULT_SIMILARITY_THRESHOLD,
    normalize_brand_name,
    run_brand_deduplication,
)

# ---------------------------------------------------------------------------
# normalize_brand_name — pure function, no DB needed
# ---------------------------------------------------------------------------


def test_normalize_strips_case_and_collapses_whitespace():
    assert normalize_brand_name("  Acme   Kitchenware  ") == "acme kitchenware"


def test_normalize_strips_known_legal_suffix_token():
    assert normalize_brand_name("Acme Kitchenware Srl") == "acme kitchenware"
    assert normalize_brand_name("Acme Kitchenware LLC") == "acme kitchenware"


def test_normalize_collapses_dotted_acronym_suffix():
    """The bug this module's own docstring example depends on: a
    dot-punctuated Italian legal form must normalize identically to its
    unpunctuated spelling, not fragment into single-letter tokens."""
    assert normalize_brand_name("ACME KITCHENWARE, S.R.L.") == "acme kitchenware"
    assert normalize_brand_name("Mock Brand S.p.A.") == "mock brand"


def test_normalize_strips_diacritics():
    assert normalize_brand_name("Café Élégance") == "cafe elegance"


def test_normalize_never_returns_empty_for_suffix_only_name():
    """A brand literally named "LLC" must not normalize to "" — the guard
    falls back to the pre-suffix-strip token list."""
    assert normalize_brand_name("LLC") == "llc"


def test_normalize_distinguishes_genuinely_different_brands():
    assert normalize_brand_name("Acme Kitchenware") != normalize_brand_name("Zenith Kitchenware")


def test_similarity_threshold_separates_spelling_variants_from_unrelated_names():
    """Calibration check for DEFAULT_SIMILARITY_THRESHOLD itself, so a
    future edit that silently changes the threshold's practical behavior
    gets caught here, not discovered by an actual bad merge."""
    import difflib

    def ratio(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, normalize_brand_name(a), normalize_brand_name(b)).ratio()

    assert ratio("Acme Kitchenware", "Acme Kitchen Ware") >= DEFAULT_SIMILARITY_THRESHOLD
    assert ratio("Acme Kitchenware", "Zenith Kitchenware") < DEFAULT_SIMILARITY_THRESHOLD
    assert ratio("Acme Kitchenware", "Acme") < DEFAULT_SIMILARITY_THRESHOLD


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
        pytest.skip("PostgreSQL not reachable — brand deduplication tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


def _dt(days_ago: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_ago)


def _new_brand(name: str, first_seen_days_ago: int = 0, **overrides: Any) -> Brand:
    defaults: dict[str, Any] = {
        "name": name,
        "first_seen_at": _dt(first_seen_days_ago),
        "last_seen_at": _dt(0),
    }
    defaults.update(overrides)
    return Brand(**defaults)


@pytest.mark.asyncio
async def test_exact_after_normalization_duplicates_are_auto_merged(session: AsyncSession):
    canonical = _new_brand("Acme Kitchenware", first_seen_days_ago=10)
    duplicate = _new_brand("ACME KITCHENWARE, S.R.L.", first_seen_days_ago=5)
    session.add_all([canonical, duplicate])
    await session.flush()

    job = ResearchJob(name="dedup test job", marketplace="IT", category="Casa e cucina")
    session.add(job)
    await session.flush()

    seller = AmazonSeller(
        seller_id="DUPSELLER01",
        seller_name="Acme Reseller Srl",
        marketplace="IT",
        brand_id=duplicate.id,
        first_seen_at=_dt(5),
        last_seen_at=_dt(0),
    )
    product = AmazonProduct(
        asin="B0DUPTEST1",
        marketplace="IT",
        brand_id=duplicate.id,
        title="Dup Product",
        first_seen_at=_dt(5),
        last_seen_at=_dt(0),
    )
    session.add_all([seller, product])
    await session.flush()

    result = ResearchResult(job_id=job.id, brand_id=duplicate.id, raw_payload={"asin": "B0DUPTEST1"})
    session.add(result)
    await session.flush()

    summary = await run_brand_deduplication(session)

    assert summary.brands_considered == 2
    assert len(summary.merged_pairs) == 1
    merge = summary.merged_pairs[0]
    assert merge.canonical_id == canonical.id
    assert merge.duplicate_id == duplicate.id
    assert merge.sellers_repointed == 1
    assert merge.products_repointed == 1
    assert merge.research_results_repointed == 1
    assert merge.company_conflict is False
    assert summary.candidates_flagged == 0

    await session.refresh(canonical)
    await session.refresh(duplicate)
    assert canonical.status == "ACTIVE"
    assert canonical.merged_into_id is None
    assert duplicate.status == "MERGED"
    assert duplicate.merged_into_id == canonical.id

    await session.refresh(seller)
    await session.refresh(product)
    await session.refresh(result)
    assert seller.brand_id == canonical.id
    assert product.brand_id == canonical.id
    assert result.brand_id == canonical.id

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    assert agent_run.agent_name == "brand_deduplicator"
    assert agent_run.status == "SUCCESS"

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    assert [e.event_type for e in events] == ["brand_merged"]


@pytest.mark.asyncio
async def test_canonical_is_the_earliest_first_seen_brand(session: AsyncSession):
    older = _new_brand("Acme Kitchenware Srl", first_seen_days_ago=30)
    newer = _new_brand("Acme Kitchenware", first_seen_days_ago=1)
    session.add_all([older, newer])
    await session.flush()

    summary = await run_brand_deduplication(session)

    assert len(summary.merged_pairs) == 1
    assert summary.merged_pairs[0].canonical_id == older.id
    assert summary.merged_pairs[0].duplicate_id == newer.id


@pytest.mark.asyncio
async def test_three_way_duplicate_group_collapses_into_one_canonical(session: AsyncSession):
    b1 = _new_brand("Acme Kitchenware", first_seen_days_ago=20)
    b2 = _new_brand("Acme Kitchenware Srl", first_seen_days_ago=10)
    b3 = _new_brand("ACME KITCHENWARE, S.R.L.", first_seen_days_ago=5)
    session.add_all([b1, b2, b3])
    await session.flush()

    summary = await run_brand_deduplication(session)

    assert len(summary.merged_pairs) == 2
    assert {m.canonical_id for m in summary.merged_pairs} == {b1.id}
    assert {m.duplicate_id for m in summary.merged_pairs} == {b2.id, b3.id}

    active = (await session.execute(select(Brand).where(Brand.status == "ACTIVE"))).scalars().all()
    assert [b.id for b in active] == [b1.id]


@pytest.mark.asyncio
async def test_company_conflict_keeps_canonical_link_and_flags_it(session: AsyncSession):
    company_a = Company(legal_name="Acme Holdings Srl")
    company_b = Company(legal_name="Some Other Legal Entity Srl")
    session.add_all([company_a, company_b])
    await session.flush()

    canonical = _new_brand("Acme Kitchenware", first_seen_days_ago=10, company_id=company_a.id)
    duplicate = _new_brand("Acme Kitchenware Srl", first_seen_days_ago=5, company_id=company_b.id)
    session.add_all([canonical, duplicate])
    await session.flush()

    summary = await run_brand_deduplication(session)

    assert len(summary.merged_pairs) == 1
    assert summary.merged_pairs[0].company_conflict is True

    await session.refresh(canonical)
    assert canonical.company_id == company_a.id  # never silently overwritten

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    event_types = [e.event_type for e in events]
    assert "brand_merge_company_conflict" in event_types


@pytest.mark.asyncio
async def test_company_id_gap_is_filled_from_duplicate_without_conflict(session: AsyncSession):
    company = Company(legal_name="Acme Holdings Srl")
    session.add(company)
    await session.flush()

    canonical = _new_brand("Acme Kitchenware", first_seen_days_ago=10)  # no company_id
    duplicate = _new_brand("Acme Kitchenware Srl", first_seen_days_ago=5, company_id=company.id)
    session.add_all([canonical, duplicate])
    await session.flush()

    summary = await run_brand_deduplication(session)

    assert summary.merged_pairs[0].company_conflict is False
    await session.refresh(canonical)
    assert canonical.company_id == company.id


@pytest.mark.asyncio
async def test_near_duplicate_is_flagged_not_merged(session: AsyncSession):
    brand_a = _new_brand("Acme Kitchenware", first_seen_days_ago=10)
    brand_b = _new_brand("Acme Kitchen Ware", first_seen_days_ago=5)
    session.add_all([brand_a, brand_b])
    await session.flush()

    summary = await run_brand_deduplication(session)

    assert summary.merged_pairs == []
    assert summary.candidates_flagged == 1

    await session.refresh(brand_a)
    await session.refresh(brand_b)
    assert brand_a.status == "ACTIVE"
    assert brand_b.status == "ACTIVE"

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].event_type == "duplicate_candidate"
    payload = events[0].payload
    assert payload is not None
    assert payload["similarity"] >= DEFAULT_SIMILARITY_THRESHOLD


@pytest.mark.asyncio
async def test_unrelated_brands_are_not_flagged_at_all(session: AsyncSession):
    brand_a = _new_brand("Acme Kitchenware")
    brand_b = _new_brand("Zenith Outdoors")
    session.add_all([brand_a, brand_b])
    await session.flush()

    summary = await run_brand_deduplication(session)

    assert summary.merged_pairs == []
    assert summary.candidates_flagged == 0

    agent_run = await session.get(AgentRun, summary.agent_run_id)
    assert agent_run is not None
    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    assert events == []


@pytest.mark.asyncio
async def test_already_merged_brand_is_excluded_from_a_later_run(session: AsyncSession):
    canonical = _new_brand("Acme Kitchenware", first_seen_days_ago=10)
    duplicate = _new_brand("Acme Kitchenware Srl", first_seen_days_ago=5)
    session.add_all([canonical, duplicate])
    await session.flush()

    first_summary = await run_brand_deduplication(session)
    assert len(first_summary.merged_pairs) == 1

    second_summary = await run_brand_deduplication(session)
    assert second_summary.brands_considered == 1  # only the surviving canonical is ACTIVE
    assert second_summary.merged_pairs == []
    assert second_summary.candidates_flagged == 0


@pytest.mark.asyncio
async def test_max_brands_cap_is_respected_and_drains_over_repeated_calls(session: AsyncSession):
    # 5 distinct, unrelated brands -- nothing here should ever merge or be
    # flagged; this test is purely about how many rows one call inspects.
    names = ["Alpha Brand", "Beta Brand", "Gamma Brand", "Delta Brand", "Epsilon Brand"]
    brands = [_new_brand(n, first_seen_days_ago=len(names) - i) for i, n in enumerate(names)]
    session.add_all(brands)
    await session.flush()

    first_summary = await run_brand_deduplication(session, max_brands=3)
    assert first_summary.brands_considered == 3
    assert first_summary.truncated is True

    second_summary = await run_brand_deduplication(session, max_brands=3)
    assert second_summary.brands_considered == 3  # all 5 still ACTIVE, cap still applies
    assert second_summary.truncated is True

    full_summary = await run_brand_deduplication(session, max_brands=10)
    assert full_summary.brands_considered == 5
    assert full_summary.truncated is False


@pytest.mark.asyncio
async def test_unhandled_exception_mid_run_is_caught_and_recorded(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    canonical = _new_brand("Acme Kitchenware", first_seen_days_ago=10)
    duplicate = _new_brand("Acme Kitchenware Srl", first_seen_days_ago=5)
    session.add_all([canonical, duplicate])
    await session.flush()

    async def _boom(*args: Any, **kwargs: Any):
        raise RuntimeError("simulated bug, not a domain error")

    monkeypatch.setattr("app.services.brand_deduplication._merge_brand", _boom)

    summary = await run_brand_deduplication(session)  # must not raise

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
