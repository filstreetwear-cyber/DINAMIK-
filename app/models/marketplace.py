"""Marketplace and Category — section 66: never hardcode Amazon.it."""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Marketplace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketplaces"

    code: Mapped[str] = mapped_column(String(5), unique=True, index=True)  # IT, DE, FR, ES, UK, US
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3))
    amazon_domain: Mapped[str] = mapped_column(String(100))  # amazon.it, amazon.de, ...
    active: Mapped[bool] = mapped_column(default=True)


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("marketplace_id", "amazon_category_id", name="uq_category_marketplace_amazon_id"),)

    marketplace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("marketplaces.id"), index=True)
    parent_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255))
    amazon_category_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    marketplace: Mapped[Marketplace] = relationship()
    parent: Mapped[Category | None] = relationship(remote_side="Category.id")
