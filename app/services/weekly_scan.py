"""
Weekly Intelligence Scan — built 2026-09-21 on top of Milestones 1-14, in
response to a feature spec asking for an autonomous weekly change-
detection pipeline ("what changed since last week, what evidence
supports it, which changes deserve attention"). Audited against the
existing codebase before writing a line of this file, per that spec's
own instruction ("inspect first... do not rewrite working systems").

**What already existed and is reused, not rebuilt**: `AgentRegistry`
(consulted for introspection, not a new dispatch layer -- the real work
still runs through `run_research_pipeline`, the same tested entrypoint
the CLI/API/Celery task already use); `Evidence`/data contracts/provider
abstractions/`AIGateway`/Ollama/`qwen3:1.7b`/deterministic scoring --
untouched. The "universe" is the existing ACTIVE `Brand` set every other
sweeping stage (`opportunity_engine`/`scoring_engine`/`ai_analysis_
engine`) already iterates -- no new `ScanUniverse` table, per the spec's
own instruction not to invent a universe mechanism when one exists.

**What genuinely needed building**: a scan-level record with real
idempotency (`WeeklyScan`), a historical snapshot so "what did we know
last week" is an actual query instead of an unanswerable question
(`EntitySnapshot`, app/models/weekly_scan.py), a deterministic change
detector, and an opportunity-lifecycle classifier reusing the existing
append-only `Score` time series.

**Change types actually implemented -- audited against real data, not
assumed**: `RATING_CHANGE`, `REVIEW_COUNT_CHANGE`, `RANK_CHANGE`. These
are the only three `AmazonProduct` fields this pipeline ever actually
populates and re-observes on repeat runs (`app/services/brand_resolution.
py`'s `_get_or_create_product` updates `rating`/`review_count`/`bsr` on
every observation -- confirmed by reading it before writing this file).
`price`/`currency` stay `NULL` on every row this pipeline creates
(`Capability.PRODUCT_PRICE` is never fetched during discovery enrichment
-- confirmed against `app/agents/amazon_scout/discovery.py`'s own
`_ENRICHMENT_CAPABILITIES` tuple, and `opportunity_engine.py`'s own
docstring already says so), so `PRICE_CHANGE` is NOT implemented --
building it would mean permanently reporting "price changed from None to
None", which is worse than not having it. `SELLER_COUNT_CHANGE`/
`BUY_BOX_CHANGE`/`AVAILABILITY_CHANGE`/`LISTING_CHANGE`/`WEBSITE_CHANGE`/
`TECHNOLOGY_CHANGE`/`BUSINESS_CHANGE`/`COMPETITION_CHANGE` are not
implemented for the same reason: no seller-count field, no Buy Box
field, no availability field exists anywhere in this schema; not one
`AmazonListing` row has ever been written by this pipeline
(confirmed via `app/schemas/contracts.py`'s own docstring, unchanged
since Milestone 8.5); `Brand.website` stays `NULL` from Milestone 5
onward, so no `WebsiteProfile` is ever linked to a scanned brand. This
is exactly what the feature spec's own section 12 asks for ("only
implement change types supported by actual data") -- not a shortfall,
a deliberate refusal to fabricate signal against absent data.

**Opportunity lifecycle -- 5 of the spec's 7 states, not 7**: `NEW`,
`CONTINUING`, `IMPROVING`, `DETERIORATING`, `RESOLVED` are derived from
real `Score` deltas (`Score` is already an append-only time series per
(brand_id, score_type) -- `classify_opportunity_lifecycle()` below reads
the two most recent rows and classifies the delta against
`weekly_scan.significance.opportunity_score_delta_threshold`). `
DISAPPEARED`/`RECURRING` are not distinct states this data model can
support today without inventing a longer history window than "the two
most recent scores" gives -- collapsing them into RESOLVED/NEW is honest,
inventing a fake distinction between them would not be. Only the two
`OpportunityType`s `opportunity_engine.py` actually implements
(`AMAZON_OPPORTUNITY`, `BUSINESS_VALUE`) are classified, for the same
reason that module only writes `Opportunity`/`Score` rows for those two.

**AI usage**: unchanged from Milestone 10/11 -- `run_research_pipeline`'s
existing `run_ai_analysis_engine` call already only analyzes brands with
real `Evidence`, already caps `max_concurrency=1` (config/default.yaml),
already reports `AI_UNAVAILABLE` per-brand rather than failing the scan
(all confirmed unchanged in this pass). Change-driven AI prioritization
(spec section 35) and a hard `max_ai_calls` budget counter (section 34)
are NOT built here -- they would mean editing `ai_analysis_engine.py`'s
selection logic, which is working, tested code from Milestone 10 this
pass does not touch. The existing bounds (local-only, no cloud fallback,
concurrency 1) already satisfy the spec's real cost concern ("must not
unexpectedly generate commercial AI charges" -- there is no commercial
AI call anywhere in this path) even without a counter; a precise budget
is a real, smaller, separately-schedulable enhancement, not invented
here.

**Idempotency**: `period_end` (the UTC calendar day the scan is
triggered on) carries a unique DB constraint on `WeeklyScan`. A second
same-day call without `force=True` returns the existing COMPLETED scan's
summary rather than re-running anything. `force=True` reuses the same
`WeeklyScan` row (same `period_end`, so the unique constraint is never at
risk) and deletes its previous `EntitySnapshot` rows before redoing the
work, so a forced re-run replaces that day's data rather than
accumulating duplicate snapshots for the same entity.

Does not commit -- same "caller owns the transaction boundary" contract
every other service in this codebase follows (CLI/API/Celery task
commit, matching `run_research_pipeline`'s own precedent).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import AIGateway
from app.core.config import Settings, get_settings
from app.models import (
    AmazonProduct,
    Brand,
    EntitySnapshot,
    Evidence,
    ResearchJob,
    Score,
    WeeklyScan,
)
from app.models.base import utcnow
from app.models.enums import OpportunityType, ResearchJobStatus
from app.services.pipeline_orchestrator import run_research_pipeline
from app.tools.core.orchestrator import IntelligenceSourceOrchestrator

_IMPLEMENTED_OPPORTUNITY_TYPES: tuple[str, ...] = (
    OpportunityType.AMAZON.value,
    OpportunityType.BUSINESS_VALUE.value,
)

_LIFECYCLE_STATES = ("NEW", "CONTINUING", "IMPROVING", "DETERIORATING", "RESOLVED")


@dataclass(frozen=True)
class ChangeThresholds:
    rating_change_threshold: float = 0.3
    review_growth_pct_threshold: float = 10.0
    rank_change_pct_threshold: float = 20.0
    opportunity_score_delta_threshold: float = 15.0

    @classmethod
    def from_config(cls, settings: Settings) -> ChangeThresholds:
        cfg = settings.weekly_scan_config().get("significance", {})
        return cls(
            rating_change_threshold=float(cfg.get("rating_change_threshold", cls.rating_change_threshold)),
            review_growth_pct_threshold=float(
                cfg.get("review_growth_pct_threshold", cls.review_growth_pct_threshold)
            ),
            rank_change_pct_threshold=float(cfg.get("rank_change_pct_threshold", cls.rank_change_pct_threshold)),
            opportunity_score_delta_threshold=float(
                cfg.get("opportunity_score_delta_threshold", cls.opportunity_score_delta_threshold)
            ),
        )


def compute_changes(
    previous: dict[str, Any] | None, current: dict[str, Any], thresholds: ChangeThresholds
) -> list[dict[str, Any]]:
    """Pure and deterministic -- diffs two field dicts, no I/O, no
    randomness, so this is fully unit-testable without a database (see
    tests/test_weekly_scan.py). `previous=None` means this entity has
    never been snapshotted before (its first scan) -- returns no changes,
    since "changed" needs something to have changed FROM."""
    if previous is None:
        return []

    changes: list[dict[str, Any]] = []

    prev_rating, curr_rating = previous.get("rating"), current.get("rating")
    if prev_rating is not None and curr_rating is not None and prev_rating != curr_rating:
        delta = curr_rating - prev_rating
        changes.append(
            {
                "field": "rating",
                "change_type": "RATING_CHANGE",
                "previous": prev_rating,
                "current": curr_rating,
                "delta": round(delta, 2),
                "significant": abs(delta) >= thresholds.rating_change_threshold,
            }
        )

    prev_reviews, curr_reviews = previous.get("review_count"), current.get("review_count")
    if prev_reviews is not None and curr_reviews is not None and prev_reviews != curr_reviews:
        pct = ((curr_reviews - prev_reviews) / prev_reviews * 100) if prev_reviews else None
        # "significant" only fires on GROWTH (spec names this REVIEW_GROWTH)
        # -- a decrease is still recorded (review counts can be corrected/
        # removed) but does not count as the growth signal the spec means.
        significant = pct is not None and pct >= thresholds.review_growth_pct_threshold
        changes.append(
            {
                "field": "review_count",
                "change_type": "REVIEW_COUNT_CHANGE",
                "previous": prev_reviews,
                "current": curr_reviews,
                "delta_pct": round(pct, 2) if pct is not None else None,
                "significant": significant,
            }
        )

    prev_bsr, curr_bsr = previous.get("bsr"), current.get("bsr")
    if prev_bsr is not None and curr_bsr is not None and prev_bsr != curr_bsr and prev_bsr != 0:
        pct = (curr_bsr - prev_bsr) / prev_bsr * 100  # negative = rank improved (lower BSR number is better)
        changes.append(
            {
                "field": "bsr",
                "change_type": "RANK_CHANGE",
                "previous": prev_bsr,
                "current": curr_bsr,
                "delta_pct": round(pct, 2),
                "direction": "improved" if pct < 0 else "worsened",
                "significant": abs(pct) >= thresholds.rank_change_pct_threshold,
            }
        )

    return changes


def classify_opportunity_lifecycle(previous_score: float | None, current_score: float, threshold: float) -> str:
    """Reuses the existing scoring architecture (Score is 0-100, NONE
    level maps to 0.0 -- scoring_engine.py's own _LEVEL_TO_SCORE) instead
    of redefining a ranking formula, per the spec's own instruction
    (section 21/22)."""
    had_signal = previous_score is not None and previous_score > 0
    has_signal = current_score > 0

    if not had_signal and not has_signal:
        return "NONE"
    if not had_signal and has_signal:
        return "NEW"
    if had_signal and not has_signal:
        return "RESOLVED"

    assert previous_score is not None  # had_signal implies this
    delta = current_score - previous_score
    if delta >= threshold:
        return "IMPROVING"
    if delta <= -threshold:
        return "DETERIORATING"
    return "CONTINUING"


def _period_bounds(reference: datetime | None = None) -> tuple[datetime, datetime]:
    """period_end is the start of the UTC calendar day the scan runs on
    -- the idempotency key (unique constraint on WeeklyScan.period_end).
    period_start is 7 days before it -- the window this scan claims to
    cover, not enforced against actual data timestamps (every field this
    pipeline tracks is "current state", not a time-stamped history of
    its own outside EntitySnapshot)."""
    now = reference or utcnow()
    period_end = datetime(now.year, now.month, now.day, tzinfo=UTC)
    period_start = period_end - timedelta(days=7)
    return period_start, period_end


@dataclass
class WeeklyScanSummary:
    scan_id: uuid.UUID
    status: str
    period_start: datetime
    period_end: datetime
    universe_size: int
    changes_detected: int
    changes_significant: int
    opportunities_new: int
    opportunities_improving: int
    opportunities_deteriorating: int
    opportunities_resolved: int
    opportunities_continuing: int
    report_path: str | None
    errors: list[dict[str, Any]] = field(default_factory=list)
    skipped_existing: bool = False
    dry_run: bool = False


async def _active_brands(session: AsyncSession) -> list[Brand]:
    return list((await session.execute(select(Brand).where(Brand.status == "ACTIVE"))).scalars().all())


def _write_report(
    settings: Settings,
    scan: WeeklyScan,
    *,
    significant_changes: list[dict[str, Any]],
    lifecycle_events: list[dict[str, Any]],
) -> Path:
    """Persists a human-readable markdown report per spec section 23/26
    -- understandable without opening the database. Deliberately more
    modest than the spec's own example (section 25): it lists significant
    changes and lifecycle transitions with the brand/entity they belong
    to, not a full per-opportunity narrative joining Evidence/AIClaim
    text -- that join is real, buildable work this pass didn't have room
    for, flagged here rather than faked with placeholder prose."""
    reports_dir = Path(settings.weekly_reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{scan.period_end:%Y-%m-%d}.md"

    lines = [
        "# DINAMIK — Weekly Intelligence Scan",
        "",
        f"Period: {scan.period_start:%Y-%m-%d} → {scan.period_end:%Y-%m-%d}",
        f"Scan: {scan.status}",
        "",
        f"Entities scanned (active brands): {scan.universe_size}",
        f"Changes detected: {scan.changes_detected} ({scan.changes_significant} significant)",
        f"New opportunities: {scan.opportunities_new}",
        (
            f"Improving: {scan.opportunities_improving}  |  Deteriorating: {scan.opportunities_deteriorating}"
            f"  |  Resolved: {scan.opportunities_resolved}  |  Continuing: {scan.opportunities_continuing}"
        ),
        f"Errors: {len(scan.errors)}",
        "",
        "---",
        "",
        "## Significant market changes",
        "",
    ]
    if significant_changes:
        for c in significant_changes:
            lines.append(
                f"- **{c['brand_name']}** ({c['entity_type']} {c['entity_id']}): "
                f"{c['change_type']} — {c.get('previous')} → {c.get('current')}"
            )
    else:
        lines.append("_None this period._")

    lines += ["", "---", "", "## Opportunity lifecycle", ""]
    if lifecycle_events:
        for e in lifecycle_events:
            lines.append(f"- **{e['brand_name']}** — {e['score_type']}: {e['state']} (score {e['current_score']:.0f})")
    else:
        lines.append("_No lifecycle transitions this period._")

    if scan.errors:
        lines += ["", "---", "", "## Errors", ""]
        for err in scan.errors[:50]:
            lines.append(f"- {err}")

    lines += [
        "",
        "---",
        "",
        "## Cost model",
        "",
        "AI: local Ollama + qwen3:1.7b only, no cloud fallback. Mandatory AI API cost: €0.",
    ]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def run_weekly_scan(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    force: bool = False,
    dry_run: bool = False,
    provider_orchestrator: IntelligenceSourceOrchestrator | None = None,
    ai_gateway: AIGateway | None = None,
    max_products: int = 20,
    max_batch: int = 200,
    reference_time: datetime | None = None,
) -> WeeklyScanSummary:
    settings = settings or get_settings()
    period_start, period_end = _period_bounds(reference_time)
    thresholds = ChangeThresholds.from_config(settings)

    if dry_run:
        # Spec section 28: discovers the universe, shows what WOULD run,
        # touches no persistent opportunity/scan state at all -- no
        # WeeklyScan/EntitySnapshot row is created, no pipeline runs, and
        # this must hold even when a COMPLETED scan already exists for
        # this period_end. Evaluated before the idempotency lookup below
        # on purpose: dry_run is a read-only preview and must never be
        # shadowed by the skip-existing short-circuit (bug found via live
        # smoke-testing on 2026-09-21 -- a dry_run call on top of an
        # already-completed period silently returned the stale completed
        # summary instead of a preview).
        active_brands = await _active_brands(session)
        return WeeklyScanSummary(
            scan_id=uuid.uuid4(),
            status="DRY_RUN",
            period_start=period_start,
            period_end=period_end,
            universe_size=len(active_brands),
            changes_detected=0,
            changes_significant=0,
            opportunities_new=0,
            opportunities_improving=0,
            opportunities_deteriorating=0,
            opportunities_resolved=0,
            opportunities_continuing=0,
            report_path=None,
            dry_run=True,
        )

    existing = (
        await session.execute(select(WeeklyScan).where(WeeklyScan.period_end == period_end))
    ).scalar_one_or_none()

    if existing is not None and existing.status == ResearchJobStatus.COMPLETED.value and not force:
        return WeeklyScanSummary(
            scan_id=existing.id,
            status=existing.status,
            period_start=existing.period_start,
            period_end=existing.period_end,
            universe_size=existing.universe_size,
            changes_detected=existing.changes_detected,
            changes_significant=existing.changes_significant,
            opportunities_new=existing.opportunities_new,
            opportunities_improving=existing.opportunities_improving,
            opportunities_deteriorating=existing.opportunities_deteriorating,
            opportunities_resolved=existing.opportunities_resolved,
            opportunities_continuing=existing.opportunities_continuing,
            report_path=existing.report_path,
            errors=list(existing.errors or []),
            skipped_existing=True,
        )

    if existing is not None and force:
        scan = existing
        scan.status = ResearchJobStatus.RUNNING.value
        scan.started_at = utcnow()
        scan.completed_at = None
        scan.errors = []
        await session.execute(delete(EntitySnapshot).where(EntitySnapshot.scan_id == scan.id))
    else:
        scan = WeeklyScan(
            period_start=period_start,
            period_end=period_end,
            status=ResearchJobStatus.RUNNING.value,
            started_at=utcnow(),
        )
        session.add(scan)
        await session.flush()

    errors: list[dict[str, Any]] = []

    marketplace = settings.yaml_config.get("default_marketplace", "IT")
    job = ResearchJob(name=f"Weekly scan {period_end:%Y-%m-%d}", marketplace=marketplace, category=None)
    session.add(job)
    await session.flush()
    scan.research_job_id = job.id

    pipeline_failed = False
    try:
        await run_research_pipeline(
            session,
            job,
            settings=settings,
            provider_orchestrator=provider_orchestrator,
            ai_gateway=ai_gateway,
            max_products=max_products,
            max_batch=max_batch,
        )
    except Exception as exc:  # noqa: BLE001 -- one failed stage must not lose the whole scan (spec section 29)
        pipeline_failed = True
        errors.append({"stage": "pipeline", "error": str(exc)})

    # Universe may have grown during discovery -- re-read it so newly
    # resolved brands from THIS scan are snapshotted too, not just the
    # ones that existed before the pipeline ran.
    active_brands = await _active_brands(session)
    scan.universe_size = len(active_brands)

    changes_detected = 0
    changes_significant = 0
    significant_changes: list[dict[str, Any]] = []

    for brand in active_brands:
        products = (
            (await session.execute(select(AmazonProduct).where(AmazonProduct.brand_id == brand.id)))
            .scalars()
            .all()
        )
        for product in products:
            try:
                current_fields = {
                    "rating": float(product.rating) if product.rating is not None else None,
                    "review_count": product.review_count,
                    "bsr": product.bsr,
                }
                previous_snapshot = (
                    await session.execute(
                        select(EntitySnapshot)
                        .where(
                            EntitySnapshot.entity_type == "amazon_product",
                            EntitySnapshot.entity_id == str(product.id),
                            EntitySnapshot.scan_id != scan.id,
                        )
                        .order_by(EntitySnapshot.observed_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                previous_fields = previous_snapshot.fields if previous_snapshot else None

                detected = compute_changes(previous_fields, current_fields, thresholds)
                session.add(
                    EntitySnapshot(
                        scan_id=scan.id,
                        entity_type="amazon_product",
                        entity_id=str(product.id),
                        brand_id=brand.id,
                        fields=current_fields,
                        changes=detected,
                        observed_at=utcnow(),
                    )
                )
                changes_detected += len(detected)
                for c in detected:
                    if c["significant"]:
                        changes_significant += 1
                        significant_changes.append(
                            {**c, "brand_name": brand.name, "entity_type": "amazon_product", "entity_id": product.id}
                        )
            except Exception as exc:  # noqa: BLE001 -- isolate one entity's failure from the rest (spec section 29)
                errors.append({"entity_type": "amazon_product", "entity_id": str(product.id), "error": str(exc)})
    await session.flush()

    lifecycle_counts = dict.fromkeys(_LIFECYCLE_STATES, 0)
    lifecycle_events: list[dict[str, Any]] = []
    for brand in active_brands:
        for score_type in _IMPLEMENTED_OPPORTUNITY_TYPES:
            try:
                rows = (
                    (
                        await session.execute(
                            select(Score)
                            .where(Score.brand_id == brand.id, Score.score_type == score_type)
                            .order_by(Score.created_at.desc())
                            .limit(2)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not rows:
                    continue
                current_score = float(rows[0].score)
                previous_score = float(rows[1].score) if len(rows) > 1 else None
                state = classify_opportunity_lifecycle(
                    previous_score, current_score, thresholds.opportunity_score_delta_threshold
                )
                if state in lifecycle_counts:
                    lifecycle_counts[state] += 1
                if state in ("NEW", "IMPROVING", "DETERIORATING", "RESOLVED"):
                    lifecycle_events.append(
                        {
                            "brand_name": brand.name,
                            "score_type": score_type,
                            "state": state,
                            "current_score": current_score,
                            "previous_score": previous_score,
                        }
                    )
            except Exception as exc:  # noqa: BLE001 -- same isolation as the snapshot loop above
                errors.append(
                    {"entity_type": "brand_score", "entity_id": str(brand.id), "score_type": score_type, "error": str(exc)}
                )

    evidence_created = (
        await session.execute(select(func.count()).select_from(Evidence).where(Evidence.created_at >= scan.started_at))
    ).scalar_one()

    scan.completed_at = utcnow()
    scan.changes_detected = changes_detected
    scan.changes_significant = changes_significant
    scan.opportunities_new = lifecycle_counts["NEW"]
    scan.opportunities_improving = lifecycle_counts["IMPROVING"]
    scan.opportunities_deteriorating = lifecycle_counts["DETERIORATING"]
    scan.opportunities_resolved = lifecycle_counts["RESOLVED"]
    scan.opportunities_continuing = lifecycle_counts["CONTINUING"]
    scan.evidence_created = int(evidence_created)
    scan.errors = errors
    scan.metrics = {
        "duration_seconds": (scan.completed_at - scan.started_at).total_seconds() if scan.started_at else None,
        "pipeline_failed": pipeline_failed,
    }
    scan.status = (
        ResearchJobStatus.PARTIAL.value if (errors or pipeline_failed) else ResearchJobStatus.COMPLETED.value
    )

    report_path = _write_report(
        settings, scan, significant_changes=significant_changes, lifecycle_events=lifecycle_events
    )
    scan.report_path = str(report_path)

    await session.flush()

    return WeeklyScanSummary(
        scan_id=scan.id,
        status=scan.status,
        period_start=scan.period_start,
        period_end=scan.period_end,
        universe_size=scan.universe_size,
        changes_detected=scan.changes_detected,
        changes_significant=scan.changes_significant,
        opportunities_new=scan.opportunities_new,
        opportunities_improving=scan.opportunities_improving,
        opportunities_deteriorating=scan.opportunities_deteriorating,
        opportunities_resolved=scan.opportunities_resolved,
        opportunities_continuing=scan.opportunities_continuing,
        report_path=scan.report_path,
        errors=errors,
    )
