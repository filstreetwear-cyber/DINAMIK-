"""
Amazon-side entities: seller, product, and time-series listing snapshots.

AmazonProduct is the canonical, deduplicated entity (unique per ASIN +
marketplace — section 10). AmazonListing is a point-in-time SNAPSHOT of
that product's listing content (title/bullets/images/A+ at the moment it
was observed) — this is what lets a future Listing Agent do BEFORE/AFTER
comparisons (section 6) and what data_freshness (section 65) actually
measures: a Product row doesn't go stale, a Listing observation does.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.company import Brand


class AmazonSeller(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "amazon_sellers"
    __table_args__ = (UniqueConstraint("seller_id", "marketplace", name="uq_seller_marketplace"),)

    seller_id: Mapped[str] = mapped_column(String(64), index=True)  # Amazon's own merchant id
    seller_name: Mapped[str] = mapped_column(String(255))
    marketplace: Mapped[str] = mapped_column(String(5), index=True)
    seller_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # section 28 dedup: a seller<->brand link starts unverified until the
    # BrandResolver (rules first, then AI) confirms it with enough confidence.
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    brand: Mapped[Brand | None] = relationship(back_populates="sellers")


class AmazonProduct(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "amazon_products"
    __table_args__ = (UniqueConstraint("asin", "marketplace", name="uq_asin_marketplace"),)

    asin: Mapped[str] = mapped_column(String(10), index=True)
    parent_asin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    marketplace: Mapped[str] = mapped_column(String(5), index=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    seller_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("amazon_sellers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(1000))
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    rating: Mapped[float | None] = mapped_column(Numeric(2, 1), nullable=True)
    review_count: Mapped[int | None] = mapped_column(nullable=True)
    bsr: Mapped[int | None] = mapped_column(nullable=True)  # Best Seller Rank
    product_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    listings: Mapped[list[AmazonListing]] = relationship(back_populates="product")


class AmazonListing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Point-in-time snapshot of a product's listing content quality."""

    __tablename__ = "amazon_listings"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("amazon_products.id", ondelete="CASCADE"), index=True
    )
    bullet_points: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    has_a_plus_content: Mapped[bool] = mapped_column(Boolean, default=False)
    image_count: Mapped[int | None] = mapped_column(nullable=True)
    video_count: Mapped[int | None] = mapped_column(nullable=True)
    sponsored_seen: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    product: Mapped[AmazonProduct] = relationship(back_populates="listings")
