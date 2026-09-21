"""
ProviderRegistry unit tests — no network, no database. Fake providers only,
so this tests the registry's own bookkeeping (priority ordering, overwrite
semantics, capability filtering) in isolation from any real provider.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.models.enums import AcquisitionMethod, DataType, ToolHealthStatus
from app.tools.core.capability import Capability
from app.tools.core.provider import IntelligenceProvider, ProviderResult
from app.tools.core.registry import ProviderRegistry


class _FakeProvider(IntelligenceProvider):
    def __init__(self, name: str, capabilities: frozenset[Capability]) -> None:
        self.name = name
        self.supported_capabilities = capabilities

    async def health(self) -> ToolHealthStatus:
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        return [
            ProviderResult(
                capability=capability,
                data={"provider": self.name},
                data_type=DataType.FACT,
                confidence=100.0,
                acquisition_method=AcquisitionMethod.API,
                observed_at=datetime.now(UTC),
            )
        ]


def test_for_capability_orders_by_priority_ascending():
    registry = ProviderRegistry()
    slow = _FakeProvider("slow", frozenset({Capability.WEBSITE_DATA}))
    fast = _FakeProvider("fast", frozenset({Capability.WEBSITE_DATA}))
    registry.register(slow, priority=200)
    registry.register(fast, priority=10)

    ordered = registry.for_capability(Capability.WEBSITE_DATA)

    assert [p.name for p in ordered] == ["fast", "slow"]


def test_for_capability_excludes_unsupported_providers():
    registry = ProviderRegistry()
    registry.register(_FakeProvider("website_only", frozenset({Capability.WEBSITE_DATA})))
    registry.register(_FakeProvider("amazon_only", frozenset({Capability.PRODUCT_DATA})))

    matches = registry.for_capability(Capability.PRODUCT_DATA)

    assert [p.name for p in matches] == ["amazon_only"]


def test_for_capability_with_no_registered_provider_returns_empty_list():
    registry = ProviderRegistry()
    assert registry.for_capability(Capability.PPC_DATA) == []


def test_registering_same_name_twice_overwrites_not_duplicates():
    registry = ProviderRegistry()
    original = _FakeProvider("dup", frozenset({Capability.WEBSITE_DATA}))
    replacement = _FakeProvider("dup", frozenset({Capability.WEBSITE_DATA}))
    registry.register(original, priority=50)
    registry.register(replacement, priority=5)

    assert registry.get("dup") is replacement
    assert len(registry.all()) == 1


@pytest.mark.asyncio
async def test_fake_provider_fetch_smoke():
    """Sanity check on the test double itself, not the registry — ensures
    the fixtures used by the orchestrator tests behave like a real provider."""
    provider = _FakeProvider("smoke", frozenset({Capability.WEBSITE_DATA}))
    results = await provider.fetch(Capability.WEBSITE_DATA)
    assert results[0].data == {"provider": "smoke"}
