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

`provider` / `acquisition_method` added 2026-09-21 (refactor: Helium10
Optional + Provider/Capability architecture) — additive, both nullable.
`source_type` already says WHERE a claim came from at the category level
(AMAZON/KEEPA/HELIUM10/...); `provider` records the specific
IntelligenceProvider instance that produced it (redundant with
source_type today, but source_type is a closed vocabulary while a given
category may eventually have more than one concrete provider), and
`acquisition_method` records HOW it was fetched (API/BROWSER/MANUAL/...)
per row — the orchestrator's choice at that specific moment, which can
change call to call even for the same provider.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, Numeric, String
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
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    acquisition_method: Mapped[str | None] = mapped_column(String(20), nullable=True)  # AcquisitionMethod


class EvidenceFile(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Anello 1 (2026-10-14): copia binaria di un'evidenza visiva (oggi
    solo gli screenshot delle schede dei venditori target). Lo scraper gira
    sul PC di casa ma l'interfaccia gira sul server OVH: senza questa
    tabella lo screenshot esisterebbe solo sul disco del PC. Solo target,
    JPEG ~100-200 KB: il volume resta piccolo."""

    __tablename__ = "evidence_files"

    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("amazon_listings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(30))  # "screenshot"
    content_type: Mapped[str] = mapped_column(String(50))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer)
