"""
Application configuration.

Two layers, merged:

1. Non-sensitive, environment-shaped configuration lives in YAML
   (config/default.yaml + config/<ENVIRONMENT>.yaml, deep-merged).
2. Sensitive / deployment-specific values (credentials, connection
   strings, feature flags) come from environment variables / .env via
   pydantic-settings. Environment variables always win.

Nothing here talks to a real external service. This module only proves
configuration loads, merges and validates correctly (Milestone 1).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class ToolAvailability:
    """Mirrors the connector health states defined in the architecture doc."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_ERROR = "AUTH_ERROR"


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime ---
    environment: str = Field(default="development", alias="ENVIRONMENT")
    mock_mode: bool = Field(default=True, alias="MOCK_MODE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- database / queue ---
    database_url: str = Field(
        default="postgresql+asyncpg://dinamik:dinamik@localhost:5432/dinamik_amazon_intelligence",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # --- AI provider ---
    ai_provider: str = Field(default="anthropic", alias="AI_PROVIDER")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    # --- Amazon SP-API: account-scoped, NOT used by Amazon Scout discovery ---
    amazon_api_key: str | None = Field(default=None, alias="AMAZON_API_KEY")
    amazon_api_secret: str | None = Field(default=None, alias="AMAZON_API_SECRET")
    amazon_marketplace: str = Field(default="IT", alias="AMAZON_MARKETPLACE")
    amazon_seller_id: str | None = Field(default=None, alias="AMAZON_SELLER_ID")

    # --- data providers used by Amazon Scout discovery ---
    keepa_api_key: str | None = Field(default=None, alias="KEEPA_API_KEY")
    junglescout_api_key: str | None = Field(default=None, alias="JUNGLESCOUT_API_KEY")
    junglescout_key_name: str | None = Field(default=None, alias="JUNGLESCOUT_KEY_NAME")
    perpetua_api_key: str | None = Field(default=None, alias="PERPETUA_API_KEY")
    helium10_api_key: str | None = Field(default=None, alias="HELIUM10_API_KEY")

    # --- feature flags ---
    enable_keepa: bool = Field(default=False, alias="ENABLE_KEEPA")
    enable_junglescout: bool = Field(default=False, alias="ENABLE_JUNGLESCOUT")
    enable_perpetua: bool = Field(default=False, alias="ENABLE_PERPETUA")
    enable_helium10: bool = Field(default=False, alias="ENABLE_HELIUM10")
    enable_ai_analysis: bool = Field(default=True, alias="ENABLE_AI_ANALYSIS")
    enable_scheduler: bool = Field(default=False, alias="ENABLE_SCHEDULER")
    enable_export: bool = Field(default=True, alias="ENABLE_EXPORT")

    # --- browser automation (Phase F, refactor 2026-09-21) ---
    # Where persistent browser profiles (cookies/local storage for
    # logged-in sessions) live on disk. On the assigned VPS this should be
    # a mounted volume (Phase J) so a human login survives a redeploy;
    # locally it defaults to a repo-relative, gitignored folder.
    browser_profiles_dir: str = Field(default="data/browser_profiles", alias="BROWSER_PROFILES_DIR")
    browser_headless: bool = Field(default=True, alias="BROWSER_HEADLESS")

    # populated from YAML after construction, not an env var
    yaml_config: dict[str, Any] = Field(default_factory=dict, exclude=True)

    def _has_key(self, tool: str) -> bool:
        mapping = {
            "keepa": bool(self.keepa_api_key) and self.enable_keepa,
            "junglescout": bool(self.junglescout_api_key) and self.enable_junglescout,
            "perpetua": bool(self.perpetua_api_key) and self.enable_perpetua,
            "helium10": bool(self.helium10_api_key) and self.enable_helium10,
            "amazon_spapi": bool(self.amazon_api_key and self.amazon_api_secret),
        }
        if tool not in mapping:
            raise ValueError(f"Unknown tool: {tool}")
        return mapping[tool]

    def tool_status(self, tool: str) -> str:
        """
        The availability state a connector should report at startup.

        In MOCK_MODE every tool reports AVAILABLE, since a mock adapter
        backs it regardless of real credentials. Outside mock mode, a
        tool without a configured (and enabled) key is UNAVAILABLE —
        the system must still boot (section 21 of the spec).
        """
        if self.mock_mode:
            return ToolAvailability.AVAILABLE
        return ToolAvailability.AVAILABLE if self._has_key(tool) else ToolAvailability.UNAVAILABLE

    def score_weights(self) -> dict[str, float]:
        return dict(self.yaml_config.get("scoring", {}).get("weights", {}))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    base = _load_yaml("default")
    override = _load_yaml(settings.environment)
    settings.yaml_config = _deep_merge(base, override)
    return settings
