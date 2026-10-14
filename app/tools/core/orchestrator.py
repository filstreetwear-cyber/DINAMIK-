"""
IntelligenceSourceOrchestrator — the thing that turns "I need
Capability.SALES_RANK" into "call the right provider, and if it fails, try
the next one" without the caller ever naming a provider (refactor doc,
sections 2-13). This is the piece section 106 of the audit called out as
the actual point of the whole refactor.

Phase H closes out the five things that section explicitly lists:

* capability routing / fallback — for_capability() ordering (registry.py)
  plus the try/except loop below. Unchanged since Phase C.
* availability — NEW here: before calling fetch(), a provider's health()
  is checked. Only AVAILABLE/DEGRADED providers are actually called; a
  provider that already knows it's unusable (AUTH_REQUIRED,
  CONFIGURATION_ERROR, RATE_LIMITED, ...) is skipped without a wasted
  call, and the skip is recorded in `attempts` exactly like a fetch()
  failure would be.
* cost awareness — NEW in registry.py: providers are registered with a
  cost_model, and for_capability() sorts by (priority, cost rank) instead
  of priority alone. A full per-job spend budget is NOT implemented here —
  it needs a place to track spend across a whole research job, which does
  not exist until Milestone 5+ wires ResearchJob into this. What IS real:
  cheaper providers are preferred as a tiebreaker today, not just in
  theory.
* confidence — NEW here: an optional `min_confidence` lets a caller say
  "a result below this confidence isn't good enough, try the next
  provider". Deliberately does NOT aggregate/merge results across
  providers or re-score anything — that belongs to whatever consumes
  OrchestratorResult later (a future EvidenceService/scoring engine,
  Milestone 8/9), not to routing.

Fallback rule unchanged from Phase C: only ProviderError subclasses
(provider.py) or an explicit health/confidence skip move to the next
provider — a bug inside a provider's fetch() is not caught here and
propagates normally, because silently swallowing a real exception and
calling it "provider unavailable" would hide the bug.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.enums import ToolHealthStatus
from app.tools.core.capability import Capability
from app.tools.core.provider import ProviderError, ProviderResult
from app.tools.core.registry import ProviderRegistry

_SERVABLE_HEALTH_STATES = frozenset({ToolHealthStatus.AVAILABLE, ToolHealthStatus.DEGRADED})


@dataclass
class ProviderAttempt:
    provider: str
    succeeded: bool
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class OrchestratorResult:
    capability: Capability
    provider: str  # the provider that ultimately succeeded
    results: list[ProviderResult]
    attempts: list[ProviderAttempt]


class NoProviderAvailableError(Exception):
    """Every registered provider for this capability failed (or was
    skipped for health/confidence reasons). `attempts` carries the full
    trail so the caller can log/report exactly why, rather than a bare
    'no data'."""

    def __init__(self, capability: Capability, attempts: list[ProviderAttempt]) -> None:
        self.capability = capability
        self.attempts = attempts
        tried = ", ".join(a.provider for a in attempts) or "none registered"
        super().__init__(f"No provider could serve {capability.value} (tried: {tried})")


class IntelligenceSourceOrchestrator:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def fetch(
        self, capability: Capability, min_confidence: float | None = None, **params: Any
    ) -> OrchestratorResult:
        attempts: list[ProviderAttempt] = []
        providers = self._registry.for_capability(capability)
        for provider in providers:
            health = await provider.health()
            if health not in _SERVABLE_HEALTH_STATES:
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        succeeded=False,
                        error_type="UnhealthyProvider",
                        error_message=f"health check reported {health.value}",
                    )
                )
                continue

            try:
                results = await provider.fetch(capability, **params)
            except ProviderError as exc:
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        succeeded=False,
                        error_type=type(exc).__name__,
                        error_message=exc.message,
                    )
                )
                continue

            if not self._meets_confidence(results, min_confidence):
                best = max((r.confidence for r in results), default=0.0)
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        succeeded=False,
                        error_type="BelowMinConfidence",
                        error_message=f"best result confidence {best} < required {min_confidence}",
                    )
                )
                continue

            attempts.append(ProviderAttempt(provider=provider.name, succeeded=True))
            return OrchestratorResult(
                capability=capability, provider=provider.name, results=results, attempts=attempts
            )
        raise NoProviderAvailableError(capability, attempts)

    @staticmethod
    def _meets_confidence(results: list[ProviderResult], min_confidence: float | None) -> bool:
        # An empty result (provider succeeded, found nothing) is not a
        # confidence failure — there's nothing to be under-confident about,
        # and rejecting it here would wrongly trigger fallback for a
        # perfectly valid "no data" answer.
        if min_confidence is None or not results:
            return True
        return max(r.confidence for r in results) >= min_confidence
