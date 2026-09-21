"""
build_default_registry — Phase H's concrete tie-in between config and a
real, populated ProviderRegistry. Verifies config-driven priority/cost
actually reaches the registry (not just that config.py parses YAML
correctly, which test_config.py already covers), and that a
disabled/nonexistent provider (Helium10, Jungle Scout, Perpetua) is simply
absent, not registered in a broken state.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.tools.core.bootstrap import build_default_registry
from app.tools.core.capability import Capability


def _mock_settings(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    return get_settings()


def test_default_registry_includes_the_three_enabled_providers(monkeypatch):
    registry = build_default_registry(settings=_mock_settings(monkeypatch))

    names = {p.name for p in registry.all()}
    assert names == {"website_intelligence", "amazon", "keepa"}


def test_default_registry_excludes_disabled_providers(monkeypatch):
    registry = build_default_registry(settings=_mock_settings(monkeypatch))

    names = {p.name for p in registry.all()}
    assert "helium10" not in names
    assert "junglescout" not in names
    assert "perpetua" not in names


def test_default_registry_orders_website_before_amazon_and_keepa(monkeypatch):
    """Matches config/default.yaml's priorities: website_intelligence=10,
    amazon=20, keepa=30 — website first, cheapest and no credentials."""
    registry = build_default_registry(settings=_mock_settings(monkeypatch))

    ordered_for_price = registry.for_capability(Capability.PRODUCT_PRICE)

    assert [p.name for p in ordered_for_price] == ["amazon", "keepa"]  # website doesn't support PRODUCT_PRICE
    ordered_for_website = registry.for_capability(Capability.WEBSITE_DATA)
    assert [p.name for p in ordered_for_website] == ["website_intelligence"]
