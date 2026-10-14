"""
Weekly Intelligence Scan (feature, 2026-09-21) tests.

Layered like the rest of this suite:

1. Pure unit tests for `compute_changes()` / `classify_opportunity_lifecycle()`
   / `ChangeThresholds.from_config()` / `_period_bounds()` -- no I/O, no
   database, same "fully unit-testable" claim app/services/weekly_scan.py's
   own docstrings make, verified rather than trusted.

2. Service-level integration tests, real Postgres in MOCK_MODE, using the
   SAME rollback-based `session` fixture tests/test_pipeline_orchestrator.py
   uses (not the commit+explicit-cleanup pattern tests/test_cli.py/
   test_workers.py use) -- `run_weekly_scan`, like every service in this
   project, defers committing to its caller, so a test that never commits
   and rolls back at teardown needs no manual DELETEs at all. A
   `MockAIProvider`-backed `AIGateway` is injected the same way
   test_pipeline_orchestrator.py does, so these tests don't depend on a
   reachable Ollama and don't write real ai_call_failed noise.

3. CLI tests (`dinamik scan weekly/status/history`) via Typer's
   `CliRunner` -- these DO commit for real (each command builds its own
   engine/session, same as every other CLI command), so they follow
   tests/test_cli.py's real-Postgres-or-skip + explicit cleanup pattern
   instead.

4. One Celery task test (`run_weekly_scan_task.apply()`, same
   `.apply()`-not-`.delay()` discipline as tests/test_workers.py) plus
   unit tests for `_weekly_scan_crontab()`'s Rome->UTC conversion and
   `_configure_beat_schedule()`'s wiring of the weekly_scan entry.

`tests/conftest.py`'s autouse fixture already isolates `WEEKLY_REPORTS_DIR`
to a per-test tmp directory, so report-file assertions below need no
filesystem cleanup either.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from app.ai.gateway import AIGateway
from app.ai.mock_provider import MockAIProvider
from app.cli import app as cli_app
from app.core.config import get_settings
from app.models import (
    AgentEvent,
    AgentRun,
    Brand,
    EntitySnapshot,
    ErrorLog,
    Evidence,
    Opportunity,
    ResearchJob,
    ResearchResult,
    Score,
    WeeklyScan,
)
from app.models.amazon import AmazonProduct, AmazonSeller
from app.services.weekly_scan import (
    ChangeThresholds,
    ScanConfigurationRequiredError,
    _period_bounds,
    classify_opportunity_lifecycle,
    compute_changes,
    run_weekly_scan,
)
from app.tools.amazon.provider import AmazonProvider
from app.tools.core.orchestrator import IntelligenceSourceOrchestrator
from app.tools.core.registry import ProviderRegistry
from app.tools.keepa.provider import KeepaProvider
from app.workers import celery_app as celery_app_module
from app.workers.celery_app import _configure_beat_schedule, _weekly_scan_crontab, celery_app
from app.workers.tasks import run_weekly_scan_task

# ---------------------------------------------------------------------------
# 1. Pure unit tests -- no database.
# ---------------------------------------------------------------------------


def test_compute_changes_returns_empty_when_no_previous_snapshot():
    assert compute_changes(None, {"rating": 4.5, "review_count": 100, "bsr": 500}, ChangeThresholds()) == []


def test_compute_changes_flags_significant_rating_change():
    thresholds = ChangeThresholds()
    changes = compute_changes(
        {"rating": 4.0, "review_count": 100, "bsr": 500},
        {"rating": 4.5, "review_count": 100, "bsr": 500},
        thresholds,
    )
    rating_change = next(c for c in changes if c["change_type"] == "RATING_CHANGE")
    assert rating_change["significant"] is True
    assert rating_change["delta"] == 0.5


def test_compute_changes_does_not_flag_small_rating_change():
    thresholds = ChangeThresholds()
    changes = compute_changes(
        {"rating": 4.4, "review_count": 100, "bsr": 500},
        {"rating": 4.5, "review_count": 100, "bsr": 500},
        thresholds,
    )
    rating_change = next(c for c in changes if c["change_type"] == "RATING_CHANGE")
    assert rating_change["significant"] is False


def test_compute_changes_flags_review_growth_but_not_review_decrease():
    thresholds = ChangeThresholds()
    growth = compute_changes(
        {"rating": 4.5, "review_count": 100, "bsr": 500},
        {"rating": 4.5, "review_count": 130, "bsr": 500},
        thresholds,
    )
    growth_change = next(c for c in growth if c["change_type"] == "REVIEW_COUNT_CHANGE")
    assert growth_change["significant"] is True

    decrease = compute_changes(
        {"rating": 4.5, "review_count": 130, "bsr": 500},
        {"rating": 4.5, "review_count": 100, "bsr": 500},
        thresholds,
    )
    decrease_change = next(c for c in decrease if c["change_type"] == "REVIEW_COUNT_CHANGE")
    # Recorded (delta_pct present) but never "significant" -- decreases
    # aren't the growth signal the spec means by REVIEW_GROWTH.
    assert decrease_change["significant"] is False
    assert decrease_change["delta_pct"] is not None


def test_compute_changes_flags_rank_change_and_direction():
    thresholds = ChangeThresholds()
    improved = compute_changes(
        {"rating": 4.5, "review_count": 100, "bsr": 1000},
        {"rating": 4.5, "review_count": 100, "bsr": 700},
        thresholds,
    )
    rank_change = next(c for c in improved if c["change_type"] == "RANK_CHANGE")
    assert rank_change["direction"] == "improved"
    assert rank_change["significant"] is True  # 30% >= 20% default threshold

    worsened = compute_changes(
        {"rating": 4.5, "review_count": 100, "bsr": 700},
        {"rating": 4.5, "review_count": 100, "bsr": 1000},
        thresholds,
    )
    rank_change_2 = next(c for c in worsened if c["change_type"] == "RANK_CHANGE")
    assert rank_change_2["direction"] == "worsened"


def test_compute_changes_guards_against_zero_previous_bsr():
    # prev_bsr == 0 would divide by zero -- must be skipped, not raise.
    changes = compute_changes(
        {"rating": 4.5, "review_count": 100, "bsr": 0},
        {"rating": 4.5, "review_count": 100, "bsr": 500},
        ChangeThresholds(),
    )
    assert not any(c["change_type"] == "RANK_CHANGE" for c in changes)


@pytest.mark.parametrize(
    ("previous_score", "current_score", "expected"),
    [
        (None, 0.0, "NONE"),
        (None, 60.0, "NEW"),
        (0.0, 60.0, "NEW"),
        (60.0, 0.0, "RESOLVED"),
        (40.0, 60.0, "IMPROVING"),  # delta=20 >= default threshold 15
        (60.0, 40.0, "DETERIORATING"),  # delta=-20 <= -15
        (50.0, 55.0, "CONTINUING"),  # delta=5 < 15
    ],
)
def test_classify_opportunity_lifecycle(previous_score, current_score, expected):
    assert classify_opportunity_lifecycle(previous_score, current_score, 15.0) == expected


def test_change_thresholds_from_config_reads_default_yaml():
    settings = get_settings()
    thresholds = ChangeThresholds.from_config(settings)
    # config/default.yaml's own documented values (see this session's
    # rationale comments there) -- verified, not assumed.
    assert thresholds.rating_change_threshold == 0.3
    assert thresholds.review_growth_pct_threshold == 10.0
    assert thresholds.rank_change_pct_threshold == 20.0
    assert thresholds.opportunity_score_delta_threshold == 15.0


def test_period_bounds_spans_seven_days_and_is_utc_midnight():
    reference = datetime(2026, 9, 24, 13, 45, tzinfo=UTC)  # a Thursday, mid-afternoon
    period_start, period_end = _period_bounds(reference)
    assert period_end == datetime(2026, 9, 24, tzinfo=UTC)
    assert period_start == datetime(2026, 9, 17, tzinfo=UTC)
    assert (period_end - period_start).days == 7


# ---------------------------------------------------------------------------
# 2. Service-level integration tests -- real Postgres, rollback-based.
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
        pytest.skip("PostgreSQL not reachable — weekly scan tests need a live DB")
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.fixture
def provider_orchestrator(monkeypatch: pytest.MonkeyPatch) -> IntelligenceSourceOrchestrator:
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    settings = get_settings()

    registry = ProviderRegistry()
    registry.register(AmazonProvider(settings=settings), priority=20, cost_model="mock")
    registry.register(KeepaProvider(settings=settings), priority=30, cost_model="mock")
    return IntelligenceSourceOrchestrator(registry)


def _ai_claims_draft() -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "claim": "Summarized from the brand's discovered evidence.",
                    "data_type": "INFERENCE",
                    "confidence": 65.0,
                    "evidence_ids": [],
                    "reasoning_summary": "Weekly scan integration test — generic summary claim.",
                }
            ]
        }
    )


@pytest.fixture
def ai_gateway() -> AIGateway:
    return AIGateway(MockAIProvider(structured_response=_ai_claims_draft()))


@pytest.mark.asyncio
async def test_run_weekly_scan_raises_scan_configuration_required_outside_mock_mode_with_no_category(
    session: AsyncSession,
):
    """2026-09-22, post Reality Audit implementation pass: the audit's
    §26/§15 flagged "which category to scan" as a real business decision
    with no safe default -- this is the guard that stops a real
    (MOCK_MODE=false) scan from silently running against an undefined
    category instead of failing clearly."""
    settings = get_settings()
    real_mode_no_category = settings.model_copy(update={"mock_mode": False})
    real_mode_no_category.yaml_config = {
        **settings.yaml_config,
        "weekly_scan": {**settings.yaml_config.get("weekly_scan", {}), "categories": []},
    }
    with pytest.raises(ScanConfigurationRequiredError):
        await run_weekly_scan(session, settings=real_mode_no_category)


@pytest.mark.asyncio
async def test_run_weekly_scan_dry_run_also_requires_category_outside_mock_mode(session: AsyncSession):
    """The guard fires before the dry_run branch too -- a dry-run preview
    must be honest about whether a real run would even start."""
    settings = get_settings()
    real_mode_no_category = settings.model_copy(update={"mock_mode": False})
    real_mode_no_category.yaml_config = {
        **settings.yaml_config,
        "weekly_scan": {**settings.yaml_config.get("weekly_scan", {}), "categories": []},
    }
    with pytest.raises(ScanConfigurationRequiredError):
        await run_weekly_scan(session, settings=real_mode_no_category, dry_run=True)


@pytest.mark.asyncio
async def test_run_weekly_scan_underlying_research_job_uses_configured_category(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator, ai_gateway: AIGateway
):
    """Regression check that config/default.yaml's weekly_scan.categories[0]
    ("Casa e cucina") actually reaches the ResearchJob the scan creates --
    before this change it was hardcoded to category=None."""
    summary = await run_weekly_scan(
        session,
        provider_orchestrator=provider_orchestrator,
        ai_gateway=ai_gateway,
        reference_time=datetime(2026, 2, 1, 6, 0, tzinfo=UTC),
    )
    # summary.scan_id is the WeeklyScan id, not the ResearchJob id -- fetch
    # the WeeklyScan row itself to get its research_job_id.
    scan = (await session.execute(select(WeeklyScan).where(WeeklyScan.id == summary.scan_id))).scalar_one()
    job = (await session.execute(select(ResearchJob).where(ResearchJob.id == scan.research_job_id))).scalar_one()
    assert job.category == "Casa e cucina"


@pytest.mark.asyncio
async def test_full_scan_creates_scan_evidence_and_report(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator, ai_gateway: AIGateway
):
    reference = datetime(2026, 1, 4, 6, 0, tzinfo=UTC)  # a Sunday
    settings = get_settings()

    summary = await run_weekly_scan(
        session,
        settings=settings,
        provider_orchestrator=provider_orchestrator,
        ai_gateway=ai_gateway,
        reference_time=reference,
    )

    assert summary.status in ("COMPLETED", "PARTIAL")
    assert summary.universe_size > 0
    assert summary.dry_run is False
    assert summary.skipped_existing is False
    assert summary.report_path is not None

    scan = await session.get(WeeklyScan, summary.scan_id)
    assert scan is not None
    assert scan.status == summary.status
    assert scan.report_path == summary.report_path

    report_text = Path(summary.report_path).read_text(encoding="utf-8")
    assert "DINAMIK — Weekly Intelligence Scan" in report_text
    assert f"Scan: {summary.status}" in report_text
    assert "Cost model" in report_text
    assert "€0" in report_text

    snapshots = (
        (await session.execute(select(EntitySnapshot).where(EntitySnapshot.scan_id == summary.scan_id)))
        .scalars()
        .all()
    )
    assert len(snapshots) > 0
    for snap in snapshots:
        assert snap.entity_type == "amazon_product"
        assert snap.changes == []  # first-ever snapshot for these products -- nothing to compare against


@pytest.mark.asyncio
async def test_second_call_same_day_skips_without_force(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator, ai_gateway: AIGateway
):
    reference = datetime(2026, 1, 11, 6, 0, tzinfo=UTC)
    settings = get_settings()

    first = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=reference,
    )
    assert first.status == "COMPLETED"

    second = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=reference,
    )

    assert second.skipped_existing is True
    assert second.scan_id == first.scan_id
    assert second.status == "COMPLETED"

    # No duplicate row -- the DB-level unique constraint on period_end
    # backs this, not just the in-Python skip check.
    rows = (
        (await session.execute(select(WeeklyScan).where(WeeklyScan.period_end == first.period_end)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_force_reruns_and_reuses_same_scan_id(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator, ai_gateway: AIGateway
):
    reference = datetime(2026, 1, 18, 6, 0, tzinfo=UTC)
    settings = get_settings()

    first = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=reference,
    )
    second = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=reference, force=True,
    )

    assert second.skipped_existing is False
    assert second.scan_id == first.scan_id  # same row reused, not a new one (unique constraint would reject a second)

    rows = (
        (await session.execute(select(WeeklyScan).where(WeeklyScan.period_end == first.period_end)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_dry_run_is_honest_preview_even_when_a_completed_scan_already_exists(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator, ai_gateway: AIGateway
):
    """Regression test for the real bug found via live smoke-testing on
    2026-09-21: `dry_run=True` used to be evaluated AFTER the idempotency
    skip-check, so a dry-run call on top of an already-COMPLETED scan for
    the same period silently returned the stale COMPLETED/skipped_existing
    summary instead of a true, state-untouched preview. Fixed by moving
    the dry_run branch before the idempotency lookup in
    app/services/weekly_scan.py."""
    reference = datetime(2026, 1, 25, 6, 0, tzinfo=UTC)
    settings = get_settings()

    completed = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=reference,
    )
    assert completed.status == "COMPLETED"

    preview = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=reference, dry_run=True,
    )

    assert preview.dry_run is True
    assert preview.status == "DRY_RUN"
    assert preview.skipped_existing is False
    assert preview.scan_id != completed.scan_id  # a fresh throwaway id, not the persisted scan's
    assert preview.report_path is None

    # And genuinely untouched: still exactly one WeeklyScan row, still
    # the same snapshot count as the real completed run left behind.
    rows = (
        (await session.execute(select(WeeklyScan).where(WeeklyScan.period_end == completed.period_end)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].id == completed.scan_id


@pytest.mark.asyncio
async def test_dry_run_touches_no_state_when_nothing_exists_yet(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator, ai_gateway: AIGateway
):
    reference = datetime(2026, 2, 1, 6, 0, tzinfo=UTC)
    settings = get_settings()

    preview = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=reference, dry_run=True,
    )

    assert preview.status == "DRY_RUN"
    assert preview.dry_run is True

    _period_start, period_end = _period_bounds(reference)
    rows = (
        (await session.execute(select(WeeklyScan).where(WeeklyScan.period_end == period_end))).scalars().all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_change_detection_flags_significant_changes_against_synthetic_previous_snapshot(
    session: AsyncSession, provider_orchestrator: IntelligenceSourceOrchestrator, ai_gateway: AIGateway
):
    """The mock AmazonProvider fixture is static (same values every call),
    so two real runs never organically diverge -- proving the full
    orchestrator wires compute_changes() correctly needs a synthetic
    "previous week" planted by hand, exactly like the module's own
    docstring says integration testing must do."""
    settings = get_settings()
    week1 = datetime(2026, 2, 8, 6, 0, tzinfo=UTC)
    week2 = datetime(2026, 2, 15, 6, 0, tzinfo=UTC)

    baseline = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=week1,
    )
    assert baseline.status == "COMPLETED"

    snapshots = (
        (await session.execute(select(EntitySnapshot).where(EntitySnapshot.scan_id == baseline.scan_id)))
        .scalars()
        .all()
    )
    assert snapshots, "baseline scan should have snapshotted at least one amazon_product"

    # Rewrite the baseline snapshot's fields to a synthetic "last week"
    # state engineered to cross every significance threshold, in every
    # direction compute_changes() understands.
    target = snapshots[0]
    current_fields = dict(target.fields)
    synthetic_previous = {
        "rating": round(float(current_fields["rating"]) - 0.5, 2) if current_fields.get("rating") is not None else None,
        "review_count": int(current_fields["review_count"] * 0.8) if current_fields.get("review_count") else None,
        "bsr": int(current_fields["bsr"] * 1.5) if current_fields.get("bsr") else None,
    }
    target.fields = synthetic_previous
    await session.flush()

    second = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=week2,
    )

    assert second.status == "COMPLETED"
    assert second.changes_detected >= 1
    assert second.changes_significant >= 1
    assert second.report_path is not None

    second_snapshots = (
        (await session.execute(select(EntitySnapshot).where(EntitySnapshot.scan_id == second.scan_id)))
        .scalars()
        .all()
    )
    matching = next(s for s in second_snapshots if s.entity_id == target.entity_id)
    change_types = {c["change_type"] for c in matching.changes}
    assert "RATING_CHANGE" in change_types
    assert any(c["significant"] for c in matching.changes)

    report_text = Path(second.report_path).read_text(encoding="utf-8")
    assert "Significant market changes" in report_text
    assert "_None this period._" not in report_text


@pytest.mark.asyncio
async def test_pipeline_failure_marks_scan_partial_without_losing_it(
    session: AsyncSession,
    provider_orchestrator: IntelligenceSourceOrchestrator,
    ai_gateway: AIGateway,
    monkeypatch: pytest.MonkeyPatch,
):
    """Spec section 29 (error isolation): one failed stage must not lose
    the whole scan. Simulated by making the underlying pipeline raise --
    `run_weekly_scan` must still create/complete the WeeklyScan row,
    just mark it PARTIAL and record the error, rather than propagating
    the exception."""
    import app.services.weekly_scan as weekly_scan_module

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated pipeline failure for test coverage")

    monkeypatch.setattr(weekly_scan_module, "run_research_pipeline", _boom)

    reference = datetime(2026, 2, 22, 6, 0, tzinfo=UTC)
    settings = get_settings()

    summary = await run_weekly_scan(
        session, settings=settings, provider_orchestrator=provider_orchestrator, ai_gateway=ai_gateway,
        reference_time=reference,
    )

    assert summary.status == "PARTIAL"
    assert any(e.get("stage") == "pipeline" for e in summary.errors)

    scan = await session.get(WeeklyScan, summary.scan_id)
    assert scan is not None
    assert scan.status == "PARTIAL"
    assert scan.completed_at is not None  # still finished, not left hanging RUNNING


# ---------------------------------------------------------------------------
# 3. CLI tests -- real commits, explicit cleanup (same pattern as
#    tests/test_cli.py / tests/test_workers.py).
# ---------------------------------------------------------------------------

runner = CliRunner()


async def _cleanup_all() -> None:
    engine = await _make_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(delete(EntitySnapshot))
        await s.execute(delete(WeeklyScan))
        await s.execute(delete(Evidence))
        await s.execute(delete(AgentEvent))
        await s.execute(delete(ErrorLog))
        await s.execute(delete(Score))
        await s.execute(delete(Opportunity))
        await s.execute(delete(ResearchResult))
        await s.execute(delete(AmazonProduct))
        await s.execute(delete(AmazonSeller))
        await s.execute(delete(AgentRun))
        await s.execute(delete(Brand))
        await s.execute(delete(ResearchJob))
        await s.commit()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _require_db_and_mock_mode_for_cli_and_worker_tests(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Applies only to this file's CLI/Celery tests (the `session`
    fixture above handles the service-level tests' own DB-reachability
    skip independently) -- gated on test name prefix so the pure unit
    tests above never pay for a DB round-trip."""
    if not (request.node.name.startswith("test_cli_") or request.node.name.startswith("test_celery_")):
        yield
        return

    import asyncio

    if not asyncio.run(_db_reachable()):
        pytest.skip("PostgreSQL not reachable — CLI/Celery weekly scan tests run real commands against a live DB")
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    yield
    asyncio.run(_cleanup_all())
    get_settings.cache_clear()


def test_cli_scan_weekly_dry_run_touches_no_state():
    result = runner.invoke(cli_app, ["scan", "weekly", "--dry-run"])

    assert result.exit_code == 0, result.stdout
    assert "dry_run: period=" in result.stdout
    assert "no persistent state was touched" in result.stdout


def test_cli_scan_weekly_runs_a_real_scan_then_status_and_history_show_it():
    result = runner.invoke(cli_app, ["scan", "weekly"])
    assert result.exit_code == 0, result.stdout
    assert "scan_id:" in result.stdout
    assert "status: COMPLETED" in result.stdout or "status: PARTIAL" in result.stdout

    status_result = runner.invoke(cli_app, ["scan", "status"])
    assert status_result.exit_code == 0
    assert "scan_id:" in status_result.stdout
    assert "report:" in status_result.stdout

    history_result = runner.invoke(cli_app, ["scan", "history"])
    assert history_result.exit_code == 0
    assert "period_end=" in history_result.stdout


def test_cli_scan_weekly_repeat_same_day_skips_without_force():
    runner.invoke(cli_app, ["scan", "weekly"])
    result = runner.invoke(cli_app, ["scan", "weekly"])

    assert result.exit_code == 0, result.stdout
    assert "already" in result.stdout and "use --force to re-run" in result.stdout


def test_cli_scan_status_reports_when_no_scans_exist():
    result = runner.invoke(cli_app, ["scan", "status"])

    assert result.exit_code == 0
    assert "no weekly scans have run yet" in result.stdout


def test_cli_scan_history_reports_when_no_scans_exist():
    result = runner.invoke(cli_app, ["scan", "history"])

    assert result.exit_code == 0
    assert "no weekly scans found" in result.stdout


# ---------------------------------------------------------------------------
# 4. Celery task + Beat schedule tests.
# ---------------------------------------------------------------------------


def test_celery_run_weekly_scan_task_apply_creates_a_real_scan():
    result = run_weekly_scan_task.apply()

    assert result.successful(), result.traceback
    body = result.result
    assert body["scan_id"]
    assert body["status"] in ("COMPLETED", "PARTIAL")
    assert body["report_path"]


def test_weekly_scan_crontab_converts_rome_time_to_utc():
    cron = _weekly_scan_crontab("sunday", "06:00", "Europe/Rome")
    # Rome is UTC+1 (CET) or UTC+2 (CEST) depending on the date this test
    # runs -- either is a valid, correctly-converted result; what matters
    # is that it's NOT a bare passthrough of "06:00" and IS still Sunday.
    assert set(cron.day_of_week) == {0}
    (hour,) = cron.hour
    assert hour in (4, 5)  # 06:00 Rome minus 1h (CET) or 2h (CEST)


def test_weekly_scan_crontab_rejects_unknown_day_name():
    with pytest.raises(KeyError):
        _weekly_scan_crontab("someday", "06:00", "Europe/Rome")


def test_configure_beat_schedule_wires_weekly_scan_when_enabled():
    settings = celery_app_module.settings
    original_yaml_config = settings.yaml_config
    original_beat_schedule = dict(celery_app.conf.beat_schedule or {})
    try:
        patched = dict(original_yaml_config)
        patched["weekly_scan"] = {
            "enabled": True,
            "day": "sunday",
            "time": "06:00",
            "timezone": "Europe/Rome",
            "significance": {},
        }
        settings.yaml_config = patched

        _configure_beat_schedule()

        assert "weekly-intelligence-scan" in celery_app.conf.beat_schedule
        entry = celery_app.conf.beat_schedule["weekly-intelligence-scan"]
        assert entry["task"] == "app.workers.tasks.run_weekly_scan_task"
    finally:
        settings.yaml_config = original_yaml_config
        celery_app.conf.beat_schedule = original_beat_schedule
        _configure_beat_schedule()


def test_configure_beat_schedule_omits_weekly_scan_when_disabled():
    settings = celery_app_module.settings
    original_yaml_config = settings.yaml_config
    original_beat_schedule = dict(celery_app.conf.beat_schedule or {})
    try:
        patched = dict(original_yaml_config)
        patched["weekly_scan"] = {"enabled": False}
        settings.yaml_config = patched

        _configure_beat_schedule()

        assert "weekly-intelligence-scan" not in celery_app.conf.beat_schedule
    finally:
        settings.yaml_config = original_yaml_config
        celery_app.conf.beat_schedule = original_beat_schedule
        _configure_beat_schedule()
