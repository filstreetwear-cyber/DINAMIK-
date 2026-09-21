"""
Declarative base and shared mixins for all models.

Design decisions (relevant for the Milestone 2 review):

* Primary keys are UUIDs, generated application-side (`uuid.uuid4`), not
  Postgres `SERIAL`. This system writes from multiple async workers and
  will eventually need idempotent, mergeable inserts across jobs — UUIDs
  avoid the coordination problem integer sequences create in that setup.
* Status/category vocabularies (job status, tool source type, evidence
  data_type, etc.) are plain `String` columns backed by Python `enum.Enum`
  classes for validation at the application layer, NOT native Postgres
  ENUM types. Native enums make adding a value ("just add PERPETUA" as
  happened during architecture review) an ALTER TYPE migration; a
  CHECK-free string column with app-level validation does not. This is a
  deliberate tradeoff: slightly weaker DB-level guarantees in exchange for
  matching section 67/68 of the spec ("configuration and feature flags
  changeable without code changes").
* Every table gets `created_at`; tables that are mutated after creation
  also get `updated_at`. Append-only log tables (AgentEvent, ToolCall,
  Error) get only `created_at` — there is nothing to update.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CreatedAtMixin:
    """For append-only log tables: no updated_at, nothing is ever mutated."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
