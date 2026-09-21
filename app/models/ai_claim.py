"""
AIClaim (persisted) — Milestone 10. The persisted counterpart of
`app/ai/contracts.py`'s pydantic `AIClaim`: same fields, this is the ORM
row `app/services/ai_analysis_engine.py` writes once `AIAnalyst`
(Milestone 9.5, `app/ai/analyst.py`) has produced a real, validated claim
from real `Evidence`.

Polymorphic `entity_type`/`entity_id`, same non-FK pattern as `Evidence`
(`app/models/evidence.py`) and for the same reason — a hard FK per entity
type would mean a new nullable column/migration every time an AI claim
needs to attach to a new kind of entity. This milestone only ever writes
`entity_type="brand"` — `app/services/ai_analysis_engine.py`'s own
docstring explains why (evidence is queryable per-brand today via
`ResearchResult`, not yet in isolation per-seller/per-product).

`CreatedAtMixin`, not `TimestampMixin`: append-only, same as `Evidence` —
an `AIClaim` is never edited after the fact, only ever superseded by a
newer claim from a later run (same "append-only, no unique constraint"
pattern `Opportunity`/`Score` already use). `created_at` (from the mixin)
is the row's insert time; `generated_at` (own column, below) is the real
`AIGenerationResult`-derived timestamp from `app.ai.contracts.AIClaim` —
the two will differ only by milliseconds in practice, but they are
different facts ("when did the model produce this" vs. "when did Postgres
receive the INSERT"), same distinction `Evidence.created_at` vs.
`Evidence.observed_at` already makes for a data claim.

`evidence_ids` stored as JSONB `list[str]`, same representation
`Score.evidence_ids` already uses — the real `Evidence.id` values
`AIAnalyst` validated as a subset of what was actually supplied, never
invented by the model (`app/ai/analyst.py`'s own guarantee).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AIClaim(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ai_claims"

    entity_type: Mapped[str] = mapped_column(String(50), index=True)  # "brand" only, this milestone
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    claim: Mapped[str] = mapped_column(String(1000))
    data_type: Mapped[str] = mapped_column(String(20))  # DataType — FACT/ESTIMATE/INFERENCE/HYPOTHESIS
    confidence: Mapped[float] = mapped_column(Numeric(5, 2))  # 0-100, the MODEL's own self-reported confidence
    evidence_ids: Mapped[list] = mapped_column(JSONB)  # list[str(uuid)] — validated subset of real Evidence ids
    reasoning_summary: Mapped[str] = mapped_column(String(2000))
    model: Mapped[str] = mapped_column(String(100))  # e.g. "qwen3:1.7b" — never self-reported by the model itself
    provider: Mapped[str] = mapped_column(String(50))  # e.g. "ollama"
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # from AIGenerationResult, not insert time
