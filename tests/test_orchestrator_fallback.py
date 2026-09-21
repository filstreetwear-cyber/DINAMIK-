"""
IntelligenceSourceOrchestrator tests — this is the actual point of the
refactor (docs/refactor-audit-2026-09-21.md, section 106): prove that a
capability request falls back across providers on failure, that a bug
inside a provider is NOT swallowed as a fallback trigger, and that the
attempt trail is complete enough to reconstruct what happened without
re-running anything.
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


class _AlwaysFailsProvider(IntelligenceProvider):
    def __init__(self, name: str, capabilities: frozenset[Capability]) -> None:
        self.name = name
        self.supported_capabilities = capabilities

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.UNAVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        raise ProviderUnavailableError(self.name, "simulated outage")


class _RateLimitedProvider(IntelligenceProvider):
    def __init__(self, name: str, capabilities: frozenset[Capability]) -> None:
        self.name = name
        self.supported_capabilities = capabilities

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.RATE_LIMITED

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        raise ProviderRateLimitedError(self.name, "simulated rate limit", retry_after_seconds=30)


class _SucceedsProvider(IntelligenceProvider):
    def __init__(self, name: str, capabilities: frozenset[Capability]) -> None:
        self.name = name
        self.supported_capabilities = capabilities

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        return [
            ProviderResult(
                capability=capability,
                data={"answered_by": self.name},
                data_type=DataType.FACT,
                confidence=100.0,
                acquisition_method=AcquisitionMethod.API,
                observed_at=datetime.now(UTC),
            )
        ]


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
async def test_falls_back_to_second_provider_after_first_fails():
    registry = ProviderRegistry()
    registry.register(_AlwaysFailsProvider("flaky", frozenset({Capability.SALES_RANK})), priority=10)
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
async def test_rate_limited_provider_also_triggers_fallback():
    registry = ProviderRegistry()
    registry.register(_RateLimitedProvider("throttled", frozenset({Capability.PRICE_HISTORY})), priority=10)
    registry.register(_SucceedsProvider("backup", frozenset({Capability.PRICE_HISTORY})), priority=20)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.PRICE_HISTORY)

    assert result.provider == "backup"
    assert result.attempts[0].error_type == "ProviderRateLimitedError"


@pytest.mark.asyncio
async def test_raises_no_provider_available_when_every_provider_fails():
    registry = ProviderRegistry()
    registry.register(_AlwaysFailsProvider("a", frozenset({Capability.KEYWORD_DATA})), priority=1)
    registry.register(_AlwaysFailsProvider("b", frozenset({Capability.KEYWORD_DATA})), priority=2)
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
    registry.register(_AlwaysFailsProvider("second", frozenset({Capability.SELLER_DATA})), priority=2)
    orchestrator = IntelligenceSourceOrchestrator(registry)

    result = await orchestrator.fetch(Capability.SELLER_DATA)

    assert len(result.attempts) == 1
    assert result.attempts[0].provider == "first"
