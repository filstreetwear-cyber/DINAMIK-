"""
IntelligenceSourceOrchestrator tests — this is the actual point of the
refactor (docs/refactor-audit-2026-09-21.md, section 106): prove that a
capability request falls back across providers on failure, that a bug
inside a provider is NOT swallowed as a fallback trigger, that the
attempt trail is complete enough to reconstruct what happened without
re-running anything, and (Phase H) that availability and confidence are
real orchestration signals, not just fields nobody reads.

Two distinct "provider can't serve this" paths are tested separately on
purpose: a provider whose health() already reports it as unusable is
skipped WITHOUT fetch() ever being called (the availability check); a
provider that reports itself healthy but still fails on a specific call
is caught via the fetch()-raised ProviderError (the pre-existing Phase C
path — a cheap health check can't predict every failure, e.g. a rate
limit that only bites on this particular call).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.models.enums import AcquisitionMethod, DataType, ToolHealthStatus
from app.tools.core.capability import Capability
from app.tools.core.orchestrator import IntelligenceSourceOrchestrator, NoProviderAvailableError
from app.tools.core.provider import (
    IntelligenceProvider,
    ProviderRateLimitedError,
    ProviderResult,
    ProviderUnavailableError,
)
from app.tools.core.registry import ProviderRegistry


class _FailsOnCallProvider(IntelligenceProvider):
    """Reports itself healthy (so the availability pre-check lets it
    through) but raises a ProviderError from fetch() itself — the case a
    health check can't predict."""

    def __init__(self, name: str, capabilities: frozenset[Capability]) -> None:
        self.name = name
        self.supported_capabilities = capabilities

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        raise ProviderUnavailableError(self.name, "simulated outage on this specific call")


class _FailsOnCallWithRateLimitProvider(IntelligenceProvider):
    def __init__(self, name: str, capabilities: frozenset[Capability]) -> None:
        self.name = name
        self.supported_capabilities = capabilities

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        raise ProviderRateLimitedError(self.name, "simulated rate limit", retry_after_seconds=30)


class _UnhealthyProvider(IntelligenceProvider):
    """health() itself reports the provider unusable. fetch() raises
    AssertionError if ever called — proving the orchestrator skips it via
    the availability check rather than reaching fetch() and catching a
    ProviderError there."""

    def __init__(self, name: str, capabilities: frozenset[Capability], health_status: ToolHealthStatus) -> None:
        self.name = name
        self.supported_capabilities = capabilities
        self._health_status = health_status

    async def health(self) -> ToolHealthStatus:
        return self._health_status

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        raise AssertionError(f"{self.name}.fetch() must never be called — health() already said it's unusable")


class _SucceedsProvider(IntelligenceProvider):
    def __init__(
        self, name: str, capabilities: frozenset[Capability], confidence: float = 100.0
    ) -> None:
        self.name = name
        self.supported_capabilities = capabilities
        self._confidence = confidence

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        return [
            ProviderResult(
                capability=capability,
                data={"answered_by": self.name},
                data_type=DataType.FACT,
                confidence=self._confidence,
                acquisition_method=AcquisitionMethod.API,
                observed_at=datetime.now(UTC),
            )
        ]


class _SucceedsWithNoDataProvider(IntelligenceProvider):
    """A valid, non-exceptional 'found nothing' result — see provider.py's
    own docstring on why this is not an error."""

    def __init__(self, name: str, capabilities: frozenset[Capability]) -> None:
        self.name = name
        self.supported_capabilities = capabilities

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        return []


class _BuggyProvider(IntelligenceProvider):
    """Raises something that is NOT a ProviderError — simulates a real bug
    inside a provider, which must propagate, not be treated as a fallback
    trigger."""

    def __init__(self, name: str, capabilities: frozenset[Capability]) -> None:
        self.name = name
        self.supported_capabilities = capabilities

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        raise ValueError("this is a programming bug, not a provider outage")


@pytest.mark.asyncio
async def test_falls_back_to_second_provider_after_fetch_raises():
    registry = ProviderRegistry()
    registry.register(_FailsOnCallProvider("flaky", frozenset({Capability.SALES_RANK})), priority=10)
    registry.register(_SucceedsProvider("reliable", frozenset({Capability.SALES_RANK})), priority=20)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.SALES_RANK)

    assert result.provider == "reliable"
    assert result.results[0].data == {"answered_by": "reliable"}
    assert [a.provider for a in result.attempts] == ["flaky", "reliable"]
    assert result.attempts[0].succeeded is False
    assert result.attempts[0].error_type == "ProviderUnavailableError"
    assert result.attempts[1].succeeded is True


@pytest.mark.asyncio
async def test_rate_limited_on_call_also_triggers_fallback():
    registry = ProviderRegistry()
    registry.register(
        _FailsOnCallWithRateLimitProvider("throttled", frozenset({Capability.PRICE_HISTORY})), priority=10
    )
    registry.register(_SucceedsProvider("backup", frozenset({Capability.PRICE_HISTORY})), priority=20)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.PRICE_HISTORY)

    assert result.provider == "backup"
    assert result.attempts[0].error_type == "ProviderRateLimitedError"


@pytest.mark.asyncio
async def test_raises_no_provider_available_when_every_provider_fails():
    registry = ProviderRegistry()
    registry.register(_FailsOnCallProvider("a", frozenset({Capability.KEYWORD_DATA})), priority=1)
    registry.register(_FailsOnCallProvider("b", frozenset({Capability.KEYWORD_DATA})), priority=2)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    with pytest.raises(NoProviderAvailableError) as exc_info:
        await orchestrator.fetch(Capability.KEYWORD_DATA)

    assert exc_info.value.capability == Capability.KEYWORD_DATA
    assert [a.provider for a in exc_info.value.attempts] == ["a", "b"]
    assert all(not a.succeeded for a in exc_info.value.attempts)


@pytest.mark.asyncio
async def test_no_registered_provider_raises_with_empty_attempts():
    registry = ProviderRegistry()
    orchestrator = IntelligenceSourceOrchestrator(registry)

    with pytest.raises(NoProviderAvailableError) as exc_info:
        await orchestrator.fetch(Capability.PPC_DATA)

    assert exc_info.value.attempts == []


@pytest.mark.asyncio
async def test_bug_inside_a_provider_is_not_treated_as_fallback_trigger():
    """A ValueError is a real bug, not 'this provider is unavailable' — the
    orchestrator must let it propagate, not swallow it and silently try the
    next provider as if nothing happened."""
    registry = ProviderRegistry()
    registry.register(_BuggyProvider("buggy", frozenset({Capability.MARKET_DATA})), priority=1)
    registry.register(_SucceedsProvider("never_reached", frozenset({Capability.MARKET_DATA})), priority=2)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    with pytest.raises(ValueError, match="programming bug"):
        await orchestrator.fetch(Capability.MARKET_DATA)


@pytest.mark.asyncio
async def test_first_provider_success_does_not_call_second_provider():
    """priority ordering matters, not just correctness: reliable is tried
    first here and must win without the lower-priority provider ever
    running — proven by attempts having exactly one entry."""
    registry = ProviderRegistry()
    registry.register(_SucceedsProvider("first", frozenset({Capability.SELLER_DATA})), priority=1)
    registry.register(_FailsOnCallProvider("second", frozenset({Capability.SELLER_DATA})), priority=2)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.SELLER_DATA)

    assert len(result.attempts) == 1
    assert result.attempts[0].provider == "first"


# --- Phase H: availability -------------------------------------------------


@pytest.mark.asyncio
async def test_unhealthy_provider_is_skipped_without_calling_fetch():
    registry = ProviderRegistry()
    registry.register(
        _UnhealthyProvider("down", frozenset({Capability.BRAND_DATA}), ToolHealthStatus.UNAVAILABLE),
        priority=10,
    )
    registry.register(_SucceedsProvider("healthy", frozenset({Capability.BRAND_DATA})), priority=20)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.BRAND_DATA)

    assert result.provider == "healthy"
    assert result.attempts[0].provider == "down"
    assert result.attempts[0].error_type == "UnhealthyProvider"
    assert "UNAVAILABLE" in (result.attempts[0].error_message or "")


@pytest.mark.asyncio
async def test_configuration_error_health_is_also_skipped():
    """Not just UNAVAILABLE — every non-servable ToolHealthStatus (Phase B's
    AUTH_REQUIRED/REQUIRES_HUMAN/CONFIGURATION_ERROR additions included)
    is treated the same way by the availability check."""
    registry = ProviderRegistry()
    registry.register(
        _UnhealthyProvider(
            "misconfigured", frozenset({Capability.COMPETITOR_DATA}), ToolHealthStatus.CONFIGURATION_ERROR
        ),
        priority=1,
    )
    registry.register(_SucceedsProvider("backup", frozenset({Capability.COMPETITOR_DATA})), priority=2)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.COMPETITOR_DATA)

    assert result.provider == "backup"


@pytest.mark.asyncio
async def test_degraded_provider_is_still_called():
    """DEGRADED is servable, not skipped — it means 'works, but not
    perfectly', which is still worth trying before falling back."""
    registry = ProviderRegistry()
    registry.register(
        _UnhealthyProvider("degraded_but_ok", frozenset({Capability.MARKET_DATA}), ToolHealthStatus.DEGRADED),
        priority=1,
    )
    orchestrator = IntelligenceSourceOrchestrator(registry)

    with pytest.raises(AssertionError, match="fetch\\(\\) must never be called"):
        # _UnhealthyProvider's fetch() always raises AssertionError when
        # called — DEGRADED reaching it (and hitting that assertion) is
        # exactly what proves it was NOT skipped by the availability check.
        await orchestrator.fetch(Capability.MARKET_DATA)


# --- Phase H: confidence -----------------------------------------------------


@pytest.mark.asyncio
async def test_below_min_confidence_result_triggers_fallback():
    registry = ProviderRegistry()
    registry.register(_SucceedsProvider("low_confidence", frozenset({Capability.PPC_DATA}), confidence=40.0), priority=1)
    registry.register(_SucceedsProvider("high_confidence", frozenset({Capability.PPC_DATA}), confidence=90.0), priority=2)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.PPC_DATA, min_confidence=80.0)

    assert result.provider == "high_confidence"
    assert result.attempts[0].error_type == "BelowMinConfidence"


@pytest.mark.asyncio
async def test_min_confidence_none_accepts_any_result():
    registry = ProviderRegistry()
    registry.register(_SucceedsProvider("low_confidence", frozenset({Capability.PPC_DATA}), confidence=1.0), priority=1)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.PPC_DATA)  # no min_confidence

    assert result.provider == "low_confidence"


@pytest.mark.asyncio
async def test_empty_result_is_not_treated_as_below_confidence():
    """A provider that succeeds but finds nothing is a valid answer, not a
    confidence failure — must not trigger fallback just because
    min_confidence was set."""
    registry = ProviderRegistry()
    registry.register(_SucceedsWithNoDataProvider("empty", frozenset({Capability.SEARCH_VOLUME})), priority=1)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.SEARCH_VOLUME, min_confidence=95.0)

    assert result.provider == "empty"
    assert result.results == []


@pytest.mark.asyncio
async def test_all_providers_below_min_confidence_raises_no_provider_available():
    registry = ProviderRegistry()
    registry.register(_SucceedsProvider("weak", frozenset({Capability.SALES_RANK}), confidence=10.0), priority=1)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    with pytest.raises(NoProviderAvailableError) as exc_info:
        await orchestrator.fetch(Capability.SALES_RANK, min_confidence=99.0)

    assert exc_info.value.attempts[0].error_type == "BelowMinConfidence"
