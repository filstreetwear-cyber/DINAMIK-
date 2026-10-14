"""
Contact — the output of the "Contactability" scoring discussed before
building started: legal seller info -> company registry -> website ->
LinkedIn, in that priority order (see docs/architecture.md section 5).

ProspectStatus is the human-in-the-loop gate from section 37: nothing
reaches Outreach without an explicit status transition, and the history
of transitions (not just the current state) is kept — it is an append-only
log, not a single mutable field on Brand, so "who approved this and when"
is always answerable.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.company import Company


class Contact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "contacts"

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=True, index=True
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(50))  # e.g. "amazon_seller_legal_info", "registry", "linkedin"
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped[Company | None] = relationship(back_populates="contacts")


class ProspectStatus(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only status transition log — current status = latest row for a brand_id."""

    __tablename__ = "prospect_status"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20))  # ProspectQualification
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    changed_by: Mapped[str] = mapped_column(String(255))  # "system" or a human identifier
