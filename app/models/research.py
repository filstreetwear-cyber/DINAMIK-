"""ResearchJob (section 11) and ResearchResult — raw discovery output before brand resolution."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ResearchJobStatus


class ResearchJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_jobs"

    name: Mapped[str] = mapped_column(String(255))
    marketplace: Mapped[str] = mapped_column(String(5))
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(255), nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ResearchJobStatus.PENDING.value)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    results: Mapped[list[ResearchResult]] = relationship(back_populates="job")


class ResearchResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One raw item returned by the Discovery Service, before brand resolution/dedup."""

    __tablename__ = "research_results"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("amazon_products.id", ondelete="SET NULL"), nullable=True
    )
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    raw_payload: Mapped[dict] = mapped_column(JSONB)

    job: Mapped[ResearchJob] = relationship(back_populates="results")
