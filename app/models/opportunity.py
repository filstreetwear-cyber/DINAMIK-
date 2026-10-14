"""
Opportunity (section 30, deterministic rule output) and Score (section
31-33: deterministic scoring engine + validated AI signals, never a bare
AI-generated number).
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Opportunity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "opportunities"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    opportunity_type: Mapped[str] = mapped_column(String(50))  # OpportunityType
    level: Mapped[str] = mapped_column(String(20))  # OpportunityLevel
    rule_triggered: Mapped[str] = mapped_column(String(255))  # which deterministic rule fired
    reasons: Mapped[list] = mapped_column(JSONB)


class Score(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scores"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    score_type: Mapped[str] = mapped_column(String(50))  # OpportunityType — reused as score dimension
    score: Mapped[float] = mapped_column(Numeric(5, 2))  # 0-100
    confidence: Mapped[float] = mapped_column(Numeric(5, 2))  # 0-100
    reasons: Mapped[list] = mapped_column(JSONB)
    evidence_ids: Mapped[list] = mapped_column(JSONB)  # list[str(uuid)] — see evidence.py note
    weight_used: Mapped[float] = mapped_column(Numeric(4, 3))  # from config/default.yaml at calc time
