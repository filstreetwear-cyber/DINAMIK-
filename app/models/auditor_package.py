"""AuditorPackage — section 54/55: the handoff artifact for Agent 02, built now, consumed later."""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AuditorPackage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "auditor_packages"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"))
    # company/brand/amazon_data/products/competitors/keywords/website/
    # opportunities/scores/evidence/source_urls — assembled by
    # AuditorPackageService.create() (Milestone 16), stored as JSONB
    # rather than re-normalized into new tables since this is a point-in-
    # time export snapshot for Agent 02, not a queried entity itself.
    payload: Mapped[dict] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Numeric(5, 2))
