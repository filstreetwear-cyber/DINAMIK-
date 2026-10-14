"""WebsiteProfile — section 29/9 (WEBSITE_AUDITOR groundwork): does the brand have its own store,
and on what technology. This is the field that makes seller_fast_fit computable at all (Seller Fast
installs on WordPress/Shopify, not on Amazon — see docs/architecture.md)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WebsiteProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "website_profiles"

    brand_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), unique=True, index=True
    )
    domain: Mapped[str] = mapped_column(String(255))
    technology: Mapped[str] = mapped_column(String(50), default="UNKNOWN")  # WebsiteTechnology
    has_chatbot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_product_recommendation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mobile_friendly: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    page_speed_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seo_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
