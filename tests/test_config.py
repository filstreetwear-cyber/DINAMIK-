"""Milestone 1 smoke tests: configuration loads, merges and behaves as specified."""
from __future__ import annotations

import pytest

from app.core.config import ToolAvailability, get_settings
from app.models.enums import ProviderMode


def _fresh_settings():
    get_settings.cache_clear()
    return get_settings()


def test_settings_default_to_mock_mode(monkeypatch):
    monkeypatch.delenv("MOCK_MODE", raising=False)
    settings = _fresh_settings()
    assert settings.mock_mode is True


def test_env_overrides_yaml_defaults(monkeypatch):
    monkeypatch.setenv("AMAZON_MARKETPLACE", "DE")
    settings = _fresh_settings()
    assert settings.amazon_marketplace == "DE"


def test_yaml_config_is_merged_and_environment_override_applies(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    settings = _fresh_settings()
    # default.yaml sets cache TTL to 86400, development.yaml overrides to 300
    assert settings.yaml_config["cache"]["default_ttl_seconds"] == 300
    # keys only present in default.yaml must survive the merge
    assert "IT" in settings.yaml_config["marketplaces"]


def test_score_weights_sum_to_one(monkeypatch):
    settings = _fresh_settings()
    weights = settings.score_weights()
    assert weights, "score weights must be loaded from config/default.yaml"
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_tool_status_unavailable_without_key_outside_mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("ENABLE_KEEPA", "true")
    monkeypatch.delenv("KEEPA_API_KEY", raising=False)
    settings = _fresh_settings()
    assert settings.tool_status("keepa") == ToolAvailability.UNAVAILABLE


def test_tool_status_available_with_key_outside_mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("ENABLE_KEEPA", "true")
    monkeypatch.setenv("KEEPA_API_KEY", "test-key")
    settings = _fresh_settings()
    assert settings.tool_status("keepa") == ToolAvailability.AVAILABLE


def test_tool_status_always_available_in_mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.delenv("KEEPA_API_KEY", raising=False)
    settings = _fresh_settings()
    assert settings.tool_status("keepa") == ToolAvailability.AVAILABLE


def test_amazon_spapi_status_is_independent_of_discovery_tools(monkeypatch):
    """
    Amazon SP-API is account-scoped and irrelevant to Scout discovery
    (see docs/architecture.md). It must have its own status, not be
    conflated with the data providers used for discovery.
    """
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.delenv("AMAZON_API_KEY", raising=False)
    monkeypatch.delenv("AMAZON_API_SECRET", raising=False)
    settings = _fresh_settings()
    assert settings.tool_status("amazon_spapi") == ToolAvailability.UNAVAILABLE


def test_provider_configs_parses_the_full_providers_section(monkeypatch):
    settings = _fresh_settings()
    configs = settings.provider_configs()

    assert configs["website_intelligence"].priority == 10
    assert configs["website_intelligence"].cost_model == "free"
    assert configs["amazon"].priority == 20
    assert configs["amazon"].cost_model == "mock"
    assert configs["helium10"].enabled is False
    assert configs["helium10"].mode == ProviderMode.DISABLED


def test_provider_config_raises_for_unknown_provider(monkeypatch):
    settings = _fresh_settings()
    with pytest.raises(KeyError):
        settings.provider_config("not_a_real_provider")


def test_provider_config_single_lookup_matches_provider_configs(monkeypatch):
    settings = _fresh_settings()
    assert settings.provider_config("keepa") == settings.provider_configs()["keepa"]
