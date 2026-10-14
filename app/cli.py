"""
CLI — Milestone 12. `pyproject.toml`'s `[project.scripts]` has declared
`dinamik = "app.cli:app"` since Milestone 1 — this is the first milestone
that actually gives that entry point something to import. `typer>=0.12`
has likewise been a dependency since Milestone 1, unused until now.

**Why this milestone matters more than its name suggests**: `make
run-worker` doesn't work (Milestone 14, celery app not built — see
README) and there is no API yet (Milestone 13, `app/main.py` only has
`/health`). Before this file, the ONLY way to actually run anything in
this system — discovery, resolution, dedup, opportunity, scoring, AI
analysis, or the Milestone 11 pipeline that chains all of them — was to
write a throwaway script or a test. This CLI is genuinely the first real
entrypoint a human (Fil, or Frank's team later) can run from a terminal.

**Scope, deliberately bounded**: four commands, not an admin console.
`health` (config/DB/AI status — the terminal equivalent of `/health`,
plus the two checks that endpoint doesn't do), `pipeline run` (creates a
`ResearchJob` and runs Milestone 11's `run_research_pipeline` end to end
— the CLI owns the transaction boundary and commits, since every stage
underneath explicitly defers that to "the caller"), `jobs list` and
`brands list` (read-only inspection — otherwise running `pipeline run`
leaves no way to see what happened without querying Postgres directly).
Nothing here mutates state beyond what `pipeline run` already does by
calling existing, tested services; the CLI adds no new business logic.

Async services, sync Typer commands: every command wraps its real
`async def _*_async(...)` body in `asyncio.run(...)`, the standard pattern
for a Typer CLI over an asyncio codebase (Typer does not await a command
function's return value itself).

**Deliberately does NOT reuse `app/database/session.py`'s module-level
`engine`/`SessionLocal`.** That module builds its engine once at import
time for FastAPI's single long-lived event loop (one process, one loop,
for the app's whole lifetime) — a different usage pattern from this CLI,
where each command's `asyncio.run(...)` call creates and later closes its
OWN event loop. A shared asyncpg-based engine whose pooled connections
were opened under one `asyncio.run()` call breaks on the next one with
"RuntimeError: Event loop is closed" — confirmed the hard way while
writing `tests/test_cli.py`, which invokes several commands in the same
process via Typer's `CliRunner` (exactly the scenario that exposes this;
a real user's separate shell invocations wouldn't hit it, but a script
importing `app.cli` and calling multiple commands would). Every
`_*_async` function here builds and disposes its own short-lived engine
instead — the same `create_async_engine(...)`-per-call pattern every test
file in this project already uses for the same reason.
"""
from __future__ import annotations

import asyncio

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.models import Brand, ResearchJob, Score, WeeklyScan
from app.services.export_service import ExportDisabledError, run_brand_export
from app.services.pipeline_orchestrator import run_research_pipeline
from app.services.weekly_scan import run_weekly_scan


def _session_factory(settings: Settings):
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)

app = typer.Typer(name="dinamik", help="Dinamik Advisor — Amazon Intelligence Platform CLI")
jobs_app = typer.Typer(help="Inspect research jobs.")
brands_app = typer.Typer(help="Inspect resolved brands.")
pipeline_app = typer.Typer(help="Run the Milestone 11 research pipeline.")
scan_app = typer.Typer(help="Run and inspect the weekly intelligence scan.")
export_app = typer.Typer(help="Export data to spreadsheets for the internal team.")

app.add_typer(jobs_app, name="jobs")
app.add_typer(brands_app, name="brands")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(scan_app, name="scan")
app.add_typer(export_app, name="export")


@app.command()
def health() -> None:
    """Config + real DB connectivity + real AI gateway status — three
    genuine checks, not three assumptions. `/health` in app/main.py only
    does the first; this does what a human actually needs before running
    `pipeline run` for real."""
    asyncio.run(_health_async())


async def _health_async() -> None:
    from app.ai.bootstrap import build_default_ai_gateway

    settings = get_settings()
    typer.echo(f"environment: {settings.environment}")
    typer.echo(f"mock_mode: {settings.mock_mode}")

    engine, _factory = _session_factory(settings)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(lambda _: None)
        typer.echo("database: reachable")
    except Exception as exc:  # noqa: BLE001 -- a health check reports, it never crashes the CLI
        typer.echo(f"database: UNREACHABLE ({exc})")
    finally:
        await engine.dispose()

    ai_gateway = build_default_ai_gateway(settings)
    if ai_gateway is None:
        typer.echo("ai: disabled (config/default.yaml ai.enabled=false)")
    else:
        status = await ai_gateway.health_check()
        typer.echo(f"ai: {status.value}")


@pipeline_app.command("run")
def pipeline_run(
    name: str = typer.Option(..., help="A human-readable label for this research job."),
    marketplace: str = typer.Option("IT", help="Amazon marketplace code, e.g. IT/DE/FR/ES/UK/US."),
    category: str | None = typer.Option(None, help="Category to search (Amazon Scout discovery)."),
    subcategory: str | None = typer.Option(None, help="Optional subcategory."),
    max_products: int = typer.Option(20, help="Cap on ASINs discovered for this job."),
    max_batch: int = typer.Option(200, help="Cap on brands processed per downstream stage."),
) -> None:
    """Creates a new ResearchJob and runs the full Milestone 11 pipeline
    (discovery -> resolution -> dedup -> opportunity -> scoring -> AI
    analysis -> AI recommendation -> contact draft, Fase 7 addendum 28/09)
    against it, then commits. This is the first command in this
    codebase that actually commits real data — every stage underneath
    explicitly leaves that to the caller (see pipeline_orchestrator.py's
    own docstring), and this command is that caller."""
    asyncio.run(
        _pipeline_run_async(
            name=name,
            marketplace=marketplace,
            category=category,
            subcategory=subcategory,
            max_products=max_products,
            max_batch=max_batch,
        )
    )


async def _pipeline_run_async(
    *, name: str, marketplace: str, category: str | None, subcategory: str | None, max_products: int, max_batch: int
) -> None:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            job = ResearchJob(name=name, marketplace=marketplace, category=category, subcategory=subcategory)
            session.add(job)
            await session.flush()

            summary = await run_research_pipeline(
                session, job, settings=settings, max_products=max_products, max_batch=max_batch
            )
            await session.commit()
    finally:
        await engine.dispose()

    typer.echo(f"job_id: {job.id}")
    typer.echo(f"pipeline agent_run_id: {summary.agent_run_id}")
    typer.echo(f"discovery: products_found={summary.discovery.products_found} "
               f"products_enriched={summary.discovery.products_enriched}")
    typer.echo(f"brand_resolution: processed={summary.brand_resolution.processed} "
               f"resolved={summary.brand_resolution.resolved}")
    typer.echo(f"brand_deduplication: merged_pairs={len(summary.brand_deduplication.merged_pairs)} "
               f"candidates_flagged={summary.brand_deduplication.candidates_flagged}")
    typer.echo(f"opportunity_engine: opportunities_created={summary.opportunity_engine.opportunities_created}")
    typer.echo(f"scoring_engine: scores_created={summary.scoring_engine.scores_created}")
    if summary.ai_analysis_engine.ai_disabled:
        typer.echo("ai_analysis_engine: disabled (ai.enabled=false)")
    else:
        typer.echo(f"ai_analysis_engine: claims_created={summary.ai_analysis_engine.claims_created}")


@jobs_app.command("list")
def jobs_list(limit: int = typer.Option(20, help="Max jobs to show, most recent first.")) -> None:
    asyncio.run(_jobs_list_async(limit))


async def _jobs_list_async(limit: int) -> None:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            rows = (
                (await session.execute(select(ResearchJob).order_by(ResearchJob.created_at.desc()).limit(limit)))
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()
    if not rows:
        typer.echo("no research jobs found")
        return
    for row in rows:
        typer.echo(
            f"{row.id}  {row.created_at:%Y-%m-%d %H:%M}  status={row.status:<10}  "
            f"marketplace={row.marketplace}  name={row.name}"
        )


@brands_app.command("list")
def brands_list(
    limit: int = typer.Option(20, help="Max brands to show, most recently first-seen first."),
    status: str = typer.Option("ACTIVE", help="Brand.status filter — ACTIVE or MERGED."),
) -> None:
    """Read-only: each brand's most recent Score per score_type, purely
    for display — NOT a reimplementation of scoring_engine.py's
    latest-per-type logic for any decision-making purpose, just a way to
    see what a `pipeline run` actually produced without a raw SQL query."""
    asyncio.run(_brands_list_async(limit=limit, status=status))


async def _brands_list_async(*, limit: int, status: str) -> None:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            brands = (
                (
                    await session.execute(
                        select(Brand)
                        .where(Brand.status == status)
                        .order_by(Brand.first_seen_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not brands:
                typer.echo(f"no brands with status={status} found")
                return

            for brand in brands:
                scores = (
                    (
                        await session.execute(
                            select(Score).where(Score.brand_id == brand.id).order_by(Score.created_at.desc())
                        )
                    )
                    .scalars()
                    .all()
                )
                latest_by_type: dict[str, Score] = {}
                for score in scores:
                    latest_by_type.setdefault(score.score_type, score)

                score_summary = ", ".join(
                    f"{score_type}={float(score.score):.0f}" for score_type, score in latest_by_type.items()
                ) or "no scores yet"
                typer.echo(f"{brand.id}  {brand.name:<40}  {score_summary}")
    finally:
        await engine.dispose()


@scan_app.command("weekly")
def scan_weekly(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the scan (universe size only) without touching any persistent state."),
    force: bool = typer.Option(False, "--force", help="Re-run even if a COMPLETED scan already exists for this period, reusing its scan_id."),
    max_products: int = typer.Option(20, help="Cap on ASINs discovered per brand during the underlying pipeline run."),
    max_batch: int = typer.Option(200, help="Cap on brands processed per downstream pipeline stage."),
) -> None:
    """Runs (or previews) the weekly intelligence scan for the current
    period. Idempotent by default: re-running the same day without
    --force returns the already-COMPLETED scan untouched. --dry-run never
    creates a WeeklyScan/EntitySnapshot row or writes a report, regardless
    of whether one already exists for today (see app/services/weekly_scan.py
    for why that ordering matters -- a real bug, found and fixed 2026-09-21,
    used to let an existing COMPLETED scan silently swallow --dry-run)."""
    asyncio.run(
        _scan_weekly_async(dry_run=dry_run, force=force, max_products=max_products, max_batch=max_batch)
    )


async def _scan_weekly_async(*, dry_run: bool, force: bool, max_products: int, max_batch: int) -> None:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            summary = await run_weekly_scan(
                session,
                settings=settings,
                force=force,
                dry_run=dry_run,
                max_products=max_products,
                max_batch=max_batch,
            )
            if not dry_run:
                await session.commit()
    finally:
        await engine.dispose()

    if summary.dry_run:
        typer.echo(f"dry_run: period={summary.period_start:%Y-%m-%d}..{summary.period_end:%Y-%m-%d}")
        typer.echo(f"universe_size (active brands): {summary.universe_size}")
        typer.echo("no persistent state was touched -- no WeeklyScan/EntitySnapshot row, no report file")
        return

    if summary.skipped_existing:
        typer.echo(f"scan_id: {summary.scan_id}  (already {summary.status} for this period -- use --force to re-run)")
    else:
        typer.echo(f"scan_id: {summary.scan_id}")
    typer.echo(f"status: {summary.status}")
    typer.echo(f"period: {summary.period_start:%Y-%m-%d} .. {summary.period_end:%Y-%m-%d}")
    typer.echo(f"universe_size: {summary.universe_size}")
    typer.echo(f"changes_detected: {summary.changes_detected}  (significant: {summary.changes_significant})")
    typer.echo(
        f"opportunities: new={summary.opportunities_new} improving={summary.opportunities_improving} "
        f"deteriorating={summary.opportunities_deteriorating} resolved={summary.opportunities_resolved} "
        f"continuing={summary.opportunities_continuing}"
    )
    if summary.errors:
        typer.echo(f"errors: {len(summary.errors)} (first: {summary.errors[0]})")
    if summary.report_path:
        typer.echo(f"report: {summary.report_path}")


@scan_app.command("status")
def scan_status() -> None:
    """Shows the most recent WeeklyScan row -- what happened during the
    last scan, whether it ran today or weeks ago."""
    asyncio.run(_scan_status_async())


async def _scan_status_async() -> None:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            scan = (
                await session.execute(select(WeeklyScan).order_by(WeeklyScan.period_end.desc()).limit(1))
            ).scalar_one_or_none()
    finally:
        await engine.dispose()

    if scan is None:
        typer.echo("no weekly scans have run yet")
        return

    typer.echo(f"scan_id: {scan.id}")
    typer.echo(f"period: {scan.period_start:%Y-%m-%d} .. {scan.period_end:%Y-%m-%d}")
    typer.echo(f"status: {scan.status}")
    typer.echo(f"started_at: {scan.started_at}")
    typer.echo(f"completed_at: {scan.completed_at}")
    typer.echo(f"universe_size: {scan.universe_size}")
    typer.echo(f"evidence_created: {scan.evidence_created}")
    typer.echo(f"changes_detected: {scan.changes_detected}  (significant: {scan.changes_significant})")
    typer.echo(
        f"opportunities: new={scan.opportunities_new} improving={scan.opportunities_improving} "
        f"deteriorating={scan.opportunities_deteriorating} resolved={scan.opportunities_resolved} "
        f"continuing={scan.opportunities_continuing}"
    )
    typer.echo(f"errors: {len(scan.errors or [])}")
    typer.echo(f"report: {scan.report_path}")


@scan_app.command("history")
def scan_history(limit: int = typer.Option(10, help="Max scans to show, most recent period first.")) -> None:
    """Lists past weekly scans -- the audit trail spec section 41 asks
    for ('what happened during last Sunday's scan'), without a raw SQL
    query against weekly_scans."""
    asyncio.run(_scan_history_async(limit))


async def _scan_history_async(limit: int) -> None:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            rows = (
                (await session.execute(select(WeeklyScan).order_by(WeeklyScan.period_end.desc()).limit(limit)))
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()

    if not rows:
        typer.echo("no weekly scans found")
        return
    for row in rows:
        typer.echo(
            f"{row.id}  period_end={row.period_end:%Y-%m-%d}  status={row.status:<10}  "
            f"changes={row.changes_detected} (sig={row.changes_significant})  "
            f"new_opps={row.opportunities_new}  errors={len(row.errors or [])}"
        )


@export_app.command("brands")
def export_brands(
    status: str = typer.Option("ACTIVE", help="Brand.status filter — ACTIVE or MERGED."),
    limit: int = typer.Option(2000, help="Max brands to include, most recently seen first."),
) -> None:
    """Writes a .xlsx workbook (one row per brand, aggregated product
    stats + latest Amazon/Business Value opportunity + evidence count)
    to EXPORTS_DIR -- the internal-team spreadsheet Frank's team uses to
    prioritize outreach across scanned brands (Milestone 15, scope
    clarified directly with Fil 2026-09-21 -- not a prospect-facing
    package, see app/services/export_service.py's own docstring for why
    that's Milestone 16's job instead). Read-only: never writes to the
    database."""
    asyncio.run(_export_brands_async(status=status, limit=limit))


async def _export_brands_async(*, status: str, limit: int) -> None:
    settings = get_settings()
    engine, factory = _session_factory(settings)
    try:
        async with factory() as session:
            try:
                summary = await run_brand_export(session, settings=settings, status=status, limit=limit)
            except ExportDisabledError as exc:
                typer.echo(f"export disabled: {exc}")
                raise typer.Exit(code=1) from exc
    finally:
        await engine.dispose()

    typer.echo(f"rows_exported: {summary.rows_exported}")
    typer.echo(f"status_filter: {summary.status_filter}")
    typer.echo(f"path: {summary.path}")


if __name__ == "__main__":
    app()
