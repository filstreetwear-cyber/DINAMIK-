"""
BrowserTaskSpec / BrowserTaskOutcome — the in-memory, execution-time shape
of a browser automation call. Deliberately NOT the same classes as
app.models.browser.BrowserTask (the persisted DB row from Phase B): a spec
is what you ask the engine to do, an outcome is what happened, and the DB
row is the durable record of both — writing that row is service-layer work
(Milestone 3+ EvidenceService-equivalent for browser tasks), not the
engine's job, same boundary the providers already keep for Evidence.

task_type is a free-string contract between caller and engine, not an enum
— app/tools/browser/engine.py documents which task_types the generic
engine actually implements. This file stays free of any provider-specific
concept (no Helium10, no "login to X"); a future Helium10BrowserProvider
supplies its own URL/selectors as `params`, it does not get its own
task_type baked in here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.enums import BrowserTaskStatus


@dataclass(frozen=True)
class BrowserTaskSpec:
    provider: str  # who is asking, e.g. "website_intelligence" — for logging/attribution only
    task_type: str
    params: dict[str, Any]
    capability: str | None = None  # Capability.value, if this task is serving one — optional, for traceability


@dataclass(frozen=True)
class BrowserTaskOutcome:
    status: BrowserTaskStatus
    output: dict[str, Any] | None
    error: str | None
    requires_human: bool
    duration_ms: int
