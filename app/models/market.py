"""Competitor and Keyword — market intelligence gathered during enrichment (section 29)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class Competitor(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "competitors"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    competitor_brand_name: Mapped[str] = mapped_column(String(255))
    competitor_asin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    marketplace: Mapped[str] = mapped_column(String(5))
    relationship_type: Mapped[str] = mapped_column(String(50), default="DIRECT")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Keyword(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "keywords"

    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("amazon_products.id", ondelete="CASCADE"), nullable=True, index=True
    )
    keyword: Mapped[str] = mapped_column(String(255), index=True)
    search_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(50))  # EvidenceSourceType
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
