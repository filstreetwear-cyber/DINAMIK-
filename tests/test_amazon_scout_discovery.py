"""
Milestone 5 / Phase I: Amazon Scout discovery agent.

Same real-Postgres-or-skip pattern as tests/test_database_integration.py
(section 6 of the spec is explicit about PostgreSQL; JSONB/FK-cascade
behavior isn't faithfully testable against anything else).

Covers, per the standing "verify at every step" discipline:
* a full run produces the expected ResearchResult/Evidence/AgentRun/
  AgentEvent/ToolCall rows, from real (mock-mode) providers, not fakes;
* the Amazon -> Keepa fallback for SALES_RANK actually happens when Amazon
  is forced to fail it — proving the agent goes through the orchestrator's
  real fallback path, not just a happy path;
* a failed search doesn't crash the job — it's recorded via job.status/
  job.error/ErrorLog, and the job function returns instead of raising;
* the agent module imports no concrete provider class.
"""
from __future__ import annotations

import ast
from pathlib import Path
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
    ErrorLog,
    Evidence,
    ResearchJob,
    ResearchResult,
    ToolCall,
)
from app.models.enums import ResearchJobStatus
from app.tools.amazon.provider import AmazonProvider
from app.tools.core.orchestrator import IntelligenceSourceOrchestrator
from app.tools.core.provider import ProviderUnavailableError
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
        pytest.skip("PostgreSQL not reachable — discovery agent tests need a live DB")
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
        "name": "test discovery job",
        "marketplace": "IT",
        "category": "Casa e cucina",
        "priority": 0,
    }
    defaults.update(overrides)
    return ResearchJob(**defaults)


@pytest.mark.asyncio
async def test_full_discovery_run_creates_expected_rows(
    session: AsyncSession, orchestrator: IntelligenceSourceOrchestrator
):
    job = _new_job()
    session.add(job)
    await session.flush()

    outcome = await run_amazon_scout_discovery(session, job, orchestrator)

    assert job.status == ResearchJobStatus.COMPLETED.value
    assert job.started_at is not None
    assert job.completed_at is not None
    # Both fixture ASINs are category="Casa e cucina", marketplace="IT".
    assert outcome.products_found == 2
    assert outcome.products_enriched == 2
    assert len(outcome.research_result_ids) == 2

    results = (
        (await session.execute(select(ResearchResult).where(ResearchResult.job_id == job.id)))
        .scalars()
        .all()
    )
    assert len(results) == 2
    for result in results:
        assert result.product_id is None  # Milestone 6 territory, not this one
        assert result.brand_id is None
        assert result.raw_payload["asin"] in {"B0MOCK0001", "B0MOCK0002"}
        assert "title" in result.raw_payload  # PRODUCT_DATA merged in
        assert "bsr" in result.raw_payload  # SALES_RANK merged in

    agent_run = (
        await session.execute(select(AgentRun).where(AgentRun.id == outcome.agent_run_id))
    ).scalar_one()
    assert agent_run.agent_name == "amazon_scout"
    assert agent_run.status == "SUCCESS"
    assert agent_run.job_id == job.id

    events = (
        (await session.execute(select(AgentEvent).where(AgentEvent.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    event_types = [e.event_type for e in events]
    assert event_types.count("search_completed") == 1
    assert event_types.count("product_enriched") == 2

    tool_calls = (
        (await session.execute(select(ToolCall).where(ToolCall.agent_run_id == agent_run.id)))
        .scalars()
        .all()
    )
    # 1 PRODUCT_SEARCH + 2 products * 4 enrichment capabilities = 9
    assert len(tool_calls) == 9
    assert all(tc.status == "SUCCESS" for tc in tool_calls)
    assert {tc.provider for tc in tool_calls} == {"amazon"}  # nothing forced Keepa in this run

    evidence_rows = (
        (
            await session.execute(
                select(Evidence).where(Evidence.entity_type == "research_result")
            )
        )
        .scalars()
        .all()
    )
    # 2 products * 4 enrichment capabilities, one ProviderResult each = 8
    assert len(evidence_rows) == 8
    assert all(e.provider == "amazon" for e in evidence_rows)
    assert all(e.acquisition_method == "IMPORT" for e in evidence_rows)

    search_evidence = (
        (
            await session.execute(
                select(Evidence).where(Evidence.entity_type == "research_job")
            )
        )
        .scalars()
        .all()
    )
    assert len(search_evidence) == 1
    assert search_evidence[0].entity_id == str(job.id)


@pytest.mark.asyncio
async def test_empty_search_result_completes_the_job_without_error(
    session: AsyncSession, orchestrator: IntelligenceSourceOrchestrator
):
    job = _new_job(category="Nonexistent Category")
    session.add(job)
    await session.flush()

    outcome = await run_amazon_scout_discovery(session, job, orchestrator)

    assert job.status == ResearchJobStatus.COMPLETED.value  # vacuous 0==0, not a failure
    assert job.error is None
    assert outcome.products_found == 0
    assert outcome.products_enriched == 0
    assert outcome.research_result_ids == []


@pytest.mark.asyncio
async def test_sales_rank_falls_back_from_amazon_to_keepa(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Forces AmazonProvider to fail SALES_RANK specifically, proving the
    orchestrator's real fallback path is exercised end-to-end by the agent,
    not just the happy "first provider always answers" path."""
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    settings = get_settings()

    real_amazon = AmazonProvider(settings=settings)

    class _AmazonWithoutSalesRank(AmazonProvider):
        async def fetch(self, capability, **params):  # type: ignore[override]
            from app.tools.core.capability import Capability as _Capability

            if capability is _Capability.SALES_RANK:
                raise ProviderUnavailableError(self.name, "forced failure for fallback test")
            return await real_amazon.fetch(capability, **params)

    registry = ProviderRegistry()
    registry.register(_AmazonWithoutSalesRank(settings=settings), priority=20, cost_model="mock")
    registry.register(KeepaProvider(settings=settings), priority=30, cost_model="mock")
    orchestrator = IntelligenceSourceOrchestrator(registry)

    job = _new_job()
    session.add(job)
    await session.flush()

    outcome = await run_amazon_scout_discovery(session, job, orchestrator)

    assert job.status == ResearchJobStatus.COMPLETED.value
    assert outcome.products_enriched == 2

    sales_rank_calls = (
        (
            await session.execute(
                select(ToolCall).where(
                    ToolCall.agent_run_id == outcome.agent_run_id,
                    ToolCall.capability == "SALES_RANK",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(sales_rank_calls) == 2  # one per product
    assert all(tc.provider == "keepa" for tc in sales_rank_calls)
    assert all(tc.status == "SUCCESS" for tc in sales_rank_calls)

    keepa_evidence = (
        (await session.execute(select(Evidence).where(Evidence.provider == "keepa")))
        .scalars()
        .all()
    )
    assert len(keepa_evidence) == 2
    assert all(e.source_type == "KEEPA" for e in keepa_evidence)


@pytest.mark.asyncio
async def test_search_failure_is_recorded_not_raised(session: AsyncSession):
    """No providers registered at all -> PRODUCT_SEARCH has nothing to try.
    The job must finish (COMPLETED, 0 found), not raise and not crash."""
    empty_orchestrator = IntelligenceSourceOrchestrator(ProviderRegistry())

    job = _new_job()
    session.add(job)
    await session.flush()

    outcome = await run_amazon_scout_discovery(session, job, empty_orchestrator)

    assert job.status == ResearchJobStatus.COMPLETED.value
    assert outcome.products_found == 0

    search_tool_call = (
        (
            await session.execute(
                select(ToolCall).where(
                    ToolCall.agent_run_id == outcome.agent_run_id,
                    ToolCall.capability == "PRODUCT_SEARCH",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(search_tool_call) == 1
    assert search_tool_call[0].status == "FAILED"
    assert search_tool_call[0].provider is None


@pytest.mark.asyncio
async def test_unhandled_exception_mid_job_is_caught_and_recorded(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """A real bug (not a ProviderError) during enrichment must not propagate
    out of run_amazon_scout_discovery — it's caught at the agent-run
    boundary and surfaced via job.status/job.error/ErrorLog, per the module
    docstring's documented exception to "let bugs propagate"."""
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    settings = get_settings()

    real_amazon = AmazonProvider(settings=settings)

    class _AmazonThatBugsOut(AmazonProvider):
        async def fetch(self, capability, **params):  # type: ignore[override]
            from app.tools.core.capability import Capability as _Capability

            if capability is _Capability.PRODUCT_DATA:
                raise RuntimeError("simulated bug, not a ProviderError")
            return await real_amazon.fetch(capability, **params)

    registry = ProviderRegistry()
    registry.register(_AmazonThatBugsOut(settings=settings), priority=20, cost_model="mock")
    orchestrator = IntelligenceSourceOrchestrator(registry)

    job = _new_job()
    session.add(job)
    await session.flush()

    outcome = await run_amazon_scout_discovery(session, job, orchestrator)  # must not raise

    assert job.status == ResearchJobStatus.FAILED.value
    assert job.error is not None
    assert "simulated bug" in job.error

    error_logs = (
        (await session.execute(select(ErrorLog).where(ErrorLog.agent_run_id == outcome.agent_run_id)))
        .scalars()
        .all()
    )
    assert len(error_logs) == 1
    assert "simulated bug" in error_logs[0].message


def test_discovery_agent_imports_no_concrete_provider_class():
    """Static check of the literal Phase I requirement: the agent talks
    only to IntelligenceSourceOrchestrator/Capability, never to a concrete
    provider — enforced here so a future edit can't quietly reintroduce a
    `from app.tools.amazon.provider import AmazonProvider` and have nothing
    catch it."""
    source_path = Path(__file__).resolve().parents[1] / "app" / "agents" / "amazon_scout" / "discovery.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    banned_modules = {
        "app.tools.amazon.provider",
        "app.tools.keepa.provider",
        "app.tools.website.provider",
    }
    banned_names = {"AmazonProvider", "KeepaProvider", "WebsiteIntelligenceProvider"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in banned_modules:
            pytest.fail(f"discovery.py imports directly from {node.module}")
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in banned_names
