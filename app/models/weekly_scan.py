"""
WeeklyScan / EntitySnapshot — the Weekly Intelligence Scan feature (built
2026-09-21, on top of Milestones 1-14, not itself numbered in the
original milestone roadmap).

Two tables, not the larger set a literal reading of the feature spec
might suggest (no separate `Change`/`ScanUniverse` table): `WeeklyScan`
is the scan-level record (id/period/status/counters/errors -- exactly
the shape "the existing architecture already represents this kind of
thing" via `ResearchJob`/`AgentRun`, mirrored here rather than
reinvented). `EntitySnapshot` is BOTH the historical snapshot ("what did
DINAMIK know about this entity last week") AND the change log for that
entity in that scan (a `changes` JSONB list on the same row) -- one row
per (scan, entity) captures everything section 14/15 of the spec asks
for (entity/field/value/timestamp + the detected deltas) without a third
table whose only content would be "the same thing EntitySnapshot already
computed, denormalized". `ScanUniverse` is not a table at all: the
universe is the existing ACTIVE `Brand` set every other sweeping service
(`opportunity_engine`/`scoring_engine`/`ai_analysis_engine`) already
uses -- reusing it here, not inventing a second universe mechanism, per
the feature spec's own instruction (section 7: "Do not invent a new
universe mechanism if one already exists").

`WeeklyScan.status` reuses `ResearchJobStatus`
(PENDING/RUNNING/COMPLETED/PARTIAL/FAILED) instead of a new enum --
it already has every state the spec asks for, including PARTIAL for
"some entities failed, the scan still produced a usable result".

Idempotency (spec section 4): `period_end` carries a unique constraint.
`app/services/weekly_scan.py`'s orchestrator computes `period_end` as the
start of the UTC day the scan is triggered on, so at most one COMPLETED
scan can exist per calendar day regardless of how many times it's
invoked that day -- a second same-day run finds the existing row and
returns it (or, with `force=True`, is a deliberate re-run, not an
accident this constraint needs to catch).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class WeeklyScan(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "weekly_scans"
    __table_args__ = (UniqueConstraint("period_end", name="uq_weekly_scan_period_end"),)

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20))  # ResearchJobStatus
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # research_jobs.id of the run_research_pipeline job this scan created
    # for its discovery/resolution/dedup/opportunity/scoring/AI leg --
    # nullable because a dry run never creates one.
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="SET NULL"), nullable=True
    )

    universe_size: Mapped[int] = mapped_column(Integer, default=0)
    evidence_created: Mapped[int] = mapped_column(Integer, default=0)
    changes_detected: Mapped[int] = mapped_column(Integer, default=0)
    changes_significant: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_new: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_improving: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_deteriorating: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_resolved: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_continuing: Mapped[int] = mapped_column(Integer, default=0)

    # [{"entity_type": ..., "entity_id": ..., "error": ...}, ...] -- one
    # failed entity must not lose the others (spec section 29); this is
    # where its failure is recorded instead of swallowed.
    errors: Mapped[list] = mapped_column(JSONB, default=list)
    # duration_ms, ai_calls_attempted, ai_calls_unavailable, and any other
    # scan-level metric that doesn't need its own column -- see
    # app/services/weekly_scan.py for exactly what's populated today.
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)

    report_path: Mapped[str | None] = mapped_column(String(500), nullable=True)


class EntitySnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "entity_snapshots"

    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("weekly_scans.id", ondelete="CASCADE"), index=True
    )
    # Same deliberately loose (entity_type, entity_id-as-string) shape as
    # Evidence (app/models/evidence.py) and for the same reason: a new
    # entity type this feature snapshots later (a Brand, a WebsiteProfile)
    # should not need a migration to add another nullable FK column here.
    entity_type: Mapped[str] = mapped_column(String(50), index=True)  # "amazon_product" today
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # The fields this snapshot actually observed -- only ones with real
    # data (see app/services/weekly_scan.py's module docstring for which
    # AmazonProduct fields are genuinely populated by this pipeline today
    # and which are not). e.g. {"rating": 4.3, "review_count": 812, "bsr": 1250}
    fields: Mapped[dict] = mapped_column(JSONB)

    # Changes detected against the most recent PRIOR snapshot for this
    # same entity (any scan, not just the immediately preceding one --
    # see compute_changes()'s caller). Empty list for an entity's first
    # snapshot ever (nothing to compare against) or when nothing changed.
    # [{"field": "rating", "change_type": "RATING_CHANGE", "previous": 4.1,
    #   "current": 4.3, "delta_pct": 4.88, "significant": true}, ...]
    changes: Mapped[list] = mapped_column(JSONB, default=list)

    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
