"""
IntelligenceSourceOrchestrator — the thing that turns "I need
Capability.SALES_RANK" into "call the right provider, and if it fails, try
the next one" without the caller ever naming a provider (refactor doc,
sections 2-13). This is the piece section 106 of the audit called out as
the actual point of the whole refactor.

Fallback rule: only ProviderError subclasses (provider.py) trigger a move
to the next provider — a bug inside a provider's fetch() is not caught
here and propagates normally, because silently swallowing a real exception
and calling it "provider unavailable" would hide the bug. `attempts` on
the result records the full trail (who was tried, what happened) precisely
so a ToolCall row can be written per attempt later without the caller
having to re-derive it.

Budget/cost-aware routing (refactor doc: per-job intelligence budget,
tiered enrichment) is intentionally NOT implemented yet — it needs the
config restructuring (priority/cost_model per provider) that is still
open, plus a place to track spend across a whole research job, which does
not exist until Milestone 5+ wires ResearchJob into this. Wiring it in now
would mean guessing at an interface nothing calls yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.tools.core.capability import Capability
from app.tools.core.provider import ProviderError, ProviderResult
from app.tools.core.registry import ProviderRegistry


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
    """Every registered provider for this capability failed. `attempts`
    carries the full trail so the caller can log/report exactly why,
    rather than a bare 'no data'."""

    def __init__(self, capability: Capability, attempts: list[ProviderAttempt]) -> None:
        self.capability = capability
        self.attempts = attempts
        tried = ", ".join(a.provider for a in attempts) or "none registered"
        super().__init__(f"No provider could serve {capability.value} (tried: {tried})")


class IntelligenceSourceOrchestrator:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def fetch(self, capability: Capability, **params: Any) -> OrchestratorResult:
        attempts: list[ProviderAttempt] = []
        providers = self._registry.for_capability(capability)
        for provider in providers:
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
            attempts.append(ProviderAttempt(provider=provider.name, succeeded=True))
            return OrchestratorResult(
                capability=capability, provider=provider.name, results=results, attempts=attempts
            )
        raise NoProviderAvailableError(capability, attempts)
