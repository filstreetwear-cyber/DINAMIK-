"""
BrowserSession / BrowserTask — new tables, added 2026-09-21 for the
"Helium 10 Optional + Browser Automation Layer" refactor.

These two tables did not exist before this refactor and their exact column
list was not itemized in the refactor spec excerpt available at the time
this file was written; the schema below is my own design against the
stated requirements (generic acquisition mechanism, not a data source;
API/BROWSER/MANUAL acquisition modes per provider; human-in-the-loop login
support; a first-class WAITING_FOR_HUMAN state that must never be silently
bypassed). Flag any mismatch with the original spec's intent and I will
adjust — this is additive, so changing it before anything depends on it
costs nothing.

* BrowserSession — one persistent browser profile/login state per
  (provider, account). The thing that survives across BrowserTasks, so a
  human only has to log in once, not once per task. Session state itself
  (cookies/storage) lives on disk/in the browser profile, not in this row —
  this row is metadata about that profile: whose it is, whether it is
  currently usable, and when it was last touched.
* BrowserTask — one row per browser automation execution. Independent of
  ToolCall (which is the generic "we called an external service" log) so a
  single ToolCall triggered via BROWSER acquisition can point at the
  BrowserTask that actually did the work, without forcing every ToolCall
  to carry browser-specific columns.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BrowserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "browser_sessions"

    provider: Mapped[str] = mapped_column(String(100), index=True)  # e.g. "helium10"
    account_label: Mapped[str] = mapped_column(String(255))  # human-readable, never the credential itself
    profile_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # persistent browser profile dir
    status: Mapped[str] = mapped_column(String(20))  # BrowserSessionStatus
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class BrowserTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "browser_tasks"

    browser_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(100), index=True)
    capability: Mapped[str | None] = mapped_column(String(50), nullable=True)  # Capability
    task_type: Mapped[str] = mapped_column(String(100))  # e.g. "helium10_xray_export", "login"
    status: Mapped[str] = mapped_column(String(20), index=True)  # BrowserTaskStatus
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    requires_human: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
