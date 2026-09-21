"""
Evidence — section 14/15, the mechanism that stops a conclusion from
floating free of its source.

entity_type/entity_id are a deliberately loose (non-FK) polymorphic
reference: Evidence attaches to companies, brands, products, opportunities
or scores alike, and the spec (section 14) describes it generically rather
than one-evidence-table-per-entity. A hard FK per entity_type would mean a
new nullable column (and a migration) every time evidence needs to attach
to a new entity — exactly the kind of change section 67/68 wants to avoid.
The tradeoff is explicit: the database cannot enforce that entity_id points
to a real row of entity_type. That check belongs in the service layer
(EvidenceService), covered by Milestone 3 tests, not in a constraint here.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class Evidence(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evidence"

    entity_type: Mapped[str] = mapped_column(String(50), index=True)  # "brand", "product", ...
    entity_id: Mapped[str] = mapped_column(String(36), index=True)  # stored as str, see note above
    claim: Mapped[str] = mapped_column(String(1000))
    data_type: Mapped[str] = mapped_column(String(20))  # FACT / ESTIMATE / INFERENCE / HYPOTHESIS
    source: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_type: Mapped[str] = mapped_column(String(20))  # EvidenceSourceType
    raw_value: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(5, 2))  # 0-100
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
