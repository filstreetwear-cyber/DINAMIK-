"""
AgentRun / AgentEvent / ToolCall / ErrorLog — the observability spine
(sections 12, 13, 43, 44). Three distinct granularities, on purpose:

* AgentRun  — one row per top-level agent invocation (cost/model/duration).
* AgentEvent — fine-grained internal milestones within a run (e.g.
  "brand_resolved", "dedup_flagged_for_review") — cheap to query for
  debugging a single run without parsing AgentRun.output.
* ToolCall  — one row per external call (Keepa, Jungle Scout, AI provider),
  linked to the AgentRun that made it. Never stores secrets (section 13).
* ErrorLog ("errors" table) — every caught error, classified, optionally
  linked back to the job/run/tool_call it happened in.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AgentRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_runs"

    agent_name: Mapped[str] = mapped_column(String(100))
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    input: Mapped[dict] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)  # section 35
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)  # section 62
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # e.g. SUCCESS / FAILED
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_events"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ToolCall(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "tool_calls"

    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100))
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # never secrets — section 13
    status: Mapped[str] = mapped_column(String(20))  # ToolHealthStatus or SUCCESS/FAILED
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class ErrorLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "errors"

    category: Mapped[str] = mapped_column(String(50))  # ErrorCategory
    message: Mapped[str] = mapped_column(String(2000))
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="SET NULL"), nullable=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="SET NULL"), nullable=True
    )
