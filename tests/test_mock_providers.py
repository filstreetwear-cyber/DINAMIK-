"""
AmazonProvider / KeepaProvider — both mock-only today (no real credentials
confirmed for Dinamik). These tests exist to prove three things: fixture
data actually loads and maps to the right capability, every result is
tagged is_mock=True so it can never be mistaken for a real observation
downstream, and both providers refuse to serve outside MOCK_MODE rather
than silently returning fixture data as if it were real.
"""
from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings
from app.tools.amazon.provider import AmazonProvider
from app.tools.core.capability import Capability
from app.tools.core.provider import ProviderNotConfiguredError, ProviderUnavailableError
from app.tools.keepa.provider import KeepaProvider


def _mock_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    return get_settings()


def _live_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("MOCK_MODE", "false")
    get_settings.cache_clear()
    return get_settings()


@pytest.mark.asyncio
async def test_amazon_provider_returns_mock_tagged_results_for_known_asin(monkeypatch: pytest.MonkeyPatch):
    provider = AmazonProvider(settings=_mock_settings(monkeypatch))

    results = await provider.fetch(Capability.PRODUCT_PRICE, asin="B0MOCK0001")

    assert len(results) == 1
    assert results[0].is_mock is True
    assert results[0].data == {"price": 39.99, "currency": "EUR"}


@pytest.mark.asyncio
async def test_amazon_provider_returns_empty_list_for_unknown_asin(monkeypatch: pytest.MonkeyPatch):
    provider = AmazonProvider(settings=_mock_settings(monkeypatch))
    results = await provider.fetch(Capability.PRODUCT_DATA, asin="B0NOTREAL")
    assert results == []


@pytest.mark.asyncio
async def test_amazon_provider_refuses_outside_mock_mode(monkeypatch: pytest.MonkeyPatch):
    provider = AmazonProvider(settings=_live_settings(monkeypatch))
    with pytest.raises(ProviderNotConfiguredError):
        await provider.fetch(Capability.PRODUCT_DATA, asin="B0MOCK0001")


@pytest.mark.asyncio
async def test_amazon_provider_rejects_unsupported_capability(monkeypatch: pytest.MonkeyPatch):
    provider = AmazonProvider(settings=_mock_settings(monkeypatch))
    with pytest.raises(ProviderUnavailableError):
        await provider.fetch(Capability.PRICE_HISTORY, asin="B0MOCK0001")


@pytest.mark.asyncio
async def test_keepa_provider_returns_mock_tagged_price_history(monkeypatch: pytest.MonkeyPatch):
    provider = KeepaProvider(settings=_mock_settings(monkeypatch))

    results = await provider.fetch(Capability.PRICE_HISTORY, asin="B0MOCK0002")

    assert len(results) == 1
    assert results[0].is_mock is True
    assert results[0].data["price_history_90d"] == [26.0, 26.0, 24.5, 24.5, 22.99, 24.5]


@pytest.mark.asyncio
async def test_keepa_provider_refuses_outside_mock_mode(monkeypatch: pytest.MonkeyPatch):
    provider = KeepaProvider(settings=_live_settings(monkeypatch))
    with pytest.raises(ProviderNotConfiguredError):
        await provider.fetch(Capability.SALES_RANK, asin="B0MOCK0001")


@pytest.mark.asyncio
async def test_amazon_and_keepa_both_serve_the_same_asin_for_overlapping_capability(
    monkeypatch: pytest.MonkeyPatch,
):
    """Both providers support PRODUCT_PRICE for the same ASIN — this is the
    real-world case the orchestrator's fallback exists for (docs/refactor-
    audit-2026-09-21.md): if Amazon is down, Keepa can still answer a price
    question for a product it tracks."""
    settings = _mock_settings(monkeypatch)
    amazon = AmazonProvider(settings=settings)
    keepa = KeepaProvider(settings=settings)

    amazon_result = await amazon.fetch(Capability.PRODUCT_PRICE, asin="B0MOCK0001")
    keepa_result = await keepa.fetch(Capability.PRODUCT_PRICE, asin="B0MOCK0001")

    assert amazon_result[0].data["price"] == 39.99
    assert keepa_result[0].data["price"] == 39.99  # last point of the fixture's price history
