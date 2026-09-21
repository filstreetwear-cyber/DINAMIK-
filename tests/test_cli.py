"""
Milestone 12: CLI. Real end-to-end invocation via Typer's own
`CliRunner` — not a mock of the CLI layer, the actual `app.cli.app`
Typer application, the same object `pyproject.toml`'s `dinamik =
"app.cli:app"` entry point points at.

Plain sync test functions on purpose (not `async def`): each CLI command
internally does its own `asyncio.run(...)` (see app/cli.py's own
docstring on why), so invoking it from a test that already has an event
loop running (pytest-asyncio's `asyncio_mode = "auto"` wraps every
`async def` test) would raise "asyncio.run() cannot be called from a
running event loop". `CliRunner.invoke()` is itself synchronous, so a
plain `def test_...()` is the correct shape here, not an oversight.

Same real-Postgres-or-skip discipline as every other stage's tests. Runs
`pipeline run` against real mock-mode providers (Amazon/Keepa), same as
test_amazon_scout_discovery.py/test_pipeline_orchestrator.py, and cleans
up every row it writes so it never pollutes another test's unfiltered
queries in the same database — a real, easy-to-make mistake with a CLI
command that (correctly) commits.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from app.cli import app
from app.core.config import get_settings
from app.models import (
    AgentEvent,
    AgentRun,
    Brand,
    ErrorLog,
    Evidence,
    Opportunity,
    ResearchJob,
    ResearchResult,
    Score,
)
from app.models.amazon import AmazonProduct, AmazonSeller

runner = CliRunner()


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


async def _cleanup_all() -> None:
    """Deletes every row this test file's CLI invocations may have
    committed -- run_research_pipeline (via `pipeline run`) genuinely
    commits, unlike every other test in this suite which relies on a
    rolled-back fixture session. Order respects FK/no-FK dependencies.
    ErrorLog ("errors") is included deliberately -- this sandbox has no
    reachable Ollama, so every `pipeline run` invocation here writes real
    AI_ERROR rows via the AI analysis stage; missing this cleanup once
    already broke test_ai_analysis_engine.py's unfiltered ErrorLog
    assertions in a later, unrelated test file in the same run."""
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(Evidence))
        await session.execute(delete(AgentEvent))
        await session.execute(delete(ErrorLog))
        await session.execute(delete(Score))
        await session.execute(delete(Opportunity))
        await session.execute(delete(ResearchResult))
        await session.execute(delete(AmazonProduct))
        await session.execute(delete(AmazonSeller))
        await session.execute(delete(AgentRun))
        await session.execute(delete(Brand))
        await session.execute(delete(ResearchJob))
        await session.commit()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _require_db_and_mock_mode(monkeypatch: pytest.MonkeyPatch):
    import asyncio

    if not asyncio.run(_db_reachable()):
        pytest.skip("PostgreSQL not reachable — CLI tests run real commands against a live DB")
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    yield
    asyncio.run(_cleanup_all())
    get_settings.cache_clear()


def test_health_command_reports_config_db_and_ai_status():
    result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    assert "environment:" in result.stdout
    assert "mock_mode: True" in result.stdout
    assert "database: reachable" in result.stdout
    assert "ai:" in result.stdout  # UNAVAILABLE in this sandbox (no Ollama) or AVAILABLE elsewhere -- not asserted


def test_pipeline_run_creates_and_commits_real_rows():
    result = runner.invoke(
        app,
        ["pipeline", "run", "--name", "cli-test-job", "--marketplace", "IT", "--category", "Casa e cucina"],
    )

    assert result.exit_code == 0, result.stdout
    assert "job_id:" in result.stdout
    assert "discovery: products_found=" in result.stdout
    assert "scoring_engine: scores_created=" in result.stdout


def test_jobs_list_shows_a_job_created_by_pipeline_run():
    runner.invoke(app, ["pipeline", "run", "--name", "cli-test-job-for-listing", "--marketplace", "IT"])

    result = runner.invoke(app, ["jobs", "list"])

    assert result.exit_code == 0
    assert "cli-test-job-for-listing" in result.stdout


def test_brands_list_shows_a_brand_created_by_pipeline_run():
    runner.invoke(app, ["pipeline", "run", "--name", "cli-test-job-for-brands", "--marketplace", "IT"])

    result = runner.invoke(app, ["brands", "list"])

    assert result.exit_code == 0
    assert "no brands with status=ACTIVE found" not in result.stdout


def test_brands_list_reports_no_brands_when_table_is_empty():
    result = runner.invoke(app, ["brands", "list"])

    assert result.exit_code == 0
    assert "no brands with status=ACTIVE found" in result.stdout


def test_jobs_list_reports_no_jobs_when_table_is_empty():
    result = runner.invoke(app, ["jobs", "list"])

    assert result.exit_code == 0
    assert "no research jobs found" in result.stdout
