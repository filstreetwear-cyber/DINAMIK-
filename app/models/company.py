"""Company and Brand — kept as separate entities (spec section 7-8: company != brand != seller)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

if TYPE_CHECKING:
    from app.models.amazon import AmazonSeller
    from app.models.contact import Contact


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("vat_country", "vat_number", name="uq_company_vat"),)

    legal_name: Mapped[str] = mapped_column(String(255))
    brand_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Anello 1 (2026-10-14): identità legale verificata via VIES. La P.IVA
    # è la chiave di deduplica forte delle aziende: stessa P.IVA = stessa
    # azienda, qualunque nome mostri il seller su Amazon.
    vat_number: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    vat_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    vies_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vies_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    brands: Mapped[list[Brand]] = relationship(back_populates="company")
    contacts: Mapped[list[Contact]] = relationship(back_populates="company")


class Brand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brands"

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), index=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amazon_storefront: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # first/last_seen_at track the discovery window independently of
    # created_at/updated_at (row bookkeeping) — a brand can be RE-observed
    # in a later research job without its row being freshly "created".
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE")
    # Milestone 7 (deduplication): set only when status == "MERGED" — points
    # at the canonical Brand this row was folded into. Without this pointer
    # "MERGED" would be a dead-end status with no way to find the surviving
    # row; SET NULL (not CASCADE) because losing the canonical brand later
    # should not delete the history that it once absorbed a duplicate.
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )

    company: Mapped[Company | None] = relationship(back_populates="brands")
    sellers: Mapped[list[AmazonSeller]] = relationship(back_populates="brand")
