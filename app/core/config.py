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
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.enums import ProviderMode

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


class ProviderConfig(BaseModel):
    """One entry of config/default.yaml's `providers:` section (Phase H,
    refactor 21/09) — REQUIRED CHANGES #1 from the original audit, closed
    here. `mode` is validated but not yet read by any provider (see
    ProviderMode's docstring). `cost_model` is a coarse tier string
    (free/mock/cheap/paid/unknown), not a currency amount — see
    app/tools/core/registry.py's cost_rank() for how it's used."""

    name: str
    enabled: bool = True
    mode: ProviderMode = ProviderMode.API
    priority: int = 100
    cost_model: str = "unknown"


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
    # 2026-09-21 (Phase J): the engine's hardcoded /opt/pw-browsers/chromium
    # default is THIS SANDBOX's own convention (a stable symlink some setup
    # step created), not something `playwright install` produces on a bare
    # VPS/Docker image. Left unset, nothing changes — the engine still
    # falls back to that same hardcoded default. Set it when the
    # browser-worker image installs Chromium somewhere else and creates its
    # own equivalent symlink (see Dockerfile.browser-worker).
    browser_executable_path: str | None = Field(default=None, alias="BROWSER_EXECUTABLE_PATH")

    # Weekly Intelligence Scan feature (2026-09-21): where persisted
    # markdown reports (app/services/weekly_scan.py) land. Same
    # env-overridable, repo-relative-by-default shape as
    # browser_profiles_dir above, for the same reason -- tests isolate it
    # to a tmp dir (see tests/conftest.py) instead of writing real report
    # files into the working tree on every run.
    weekly_reports_dir: str = Field(default="reports/weekly", alias="WEEKLY_REPORTS_DIR")

    # Milestone 15 (Exports, 2026-09-21): where the .xlsx brand export
    # workbooks (app/services/export_service.py) land -- same
    # env-overridable, repo-relative-by-default, tmp-dir-isolated-in-tests
    # shape as weekly_reports_dir/browser_profiles_dir above, not a new
    # pattern invented for this one feature.
    exports_dir: str = Field(default="exports", alias="EXPORTS_DIR")

    # Anello 1 (2026-10-14): dove lo scout salva le evidenze grezze (HTML
    # di ogni pagina, screenshot delle schede). Stessa forma degli altri
    # dir generati: relativo al repo, sovrascrivibile, isolato nei test.
    scout_evidence_dir: str = Field(default="data/evidence", alias="SCOUT_EVIDENCE_DIR")
    scout_reports_dir: str = Field(default="reports/anello1", alias="SCOUT_REPORTS_DIR")

    # 2026-09-22 (post Reality Audit, Docker->Ollama networking fix): the
    # Reality Audit (§12/§18) found a real, unaddressed deploy gap --
    # config/default.yaml's ai.local.base_url defaults to
    # http://localhost:11434, which inside a Docker container resolves to
    # the CONTAINER itself, not the Docker host where Ollama actually runs.
    # Left unset (the default everywhere except a real container deploy),
    # nothing changes -- app/ai/bootstrap.py only overrides
    # ai.local.base_url when this is set. See docker-compose.yml's
    # api/worker/beat services for the paired `extra_hosts:
    # host.docker.internal:host-gateway` + `OLLAMA_BASE_URL` env default
    # that makes this resolve on a real Linux Docker host.
    ollama_base_url: str | None = Field(default=None, alias="OLLAMA_BASE_URL")

    # 2026-09-22 (ops dashboard requirement): a shared secret protecting
    # every /jobs, /brands, /opportunities, /ai-claims, /weekly-scans
    # endpoint (see app/main.py's require_dashboard_key dependency).
    # Left unset (the default everywhere except a real deploy where this
    # is explicitly configured), the API stays exactly as open as it was
    # before this field existed -- same "no-op until set" shape as
    # ollama_base_url/browser_executable_path above. This is a single
    # shared value, not per-user auth: adequate for "keep randoms on the
    # public internet from hitting POST /jobs", not a real authentication
    # system -- see docs/architecture.md's addendum for what that would
    # take and why it's explicitly out of scope here.
    dashboard_api_key: str | None = Field(default=None, alias="DASHBOARD_API_KEY")

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

    def opportunity_rule_config(self, rule_name: str) -> dict[str, Any]:
        """One entry of config/default.yaml's `opportunity_rules:` section
        (Milestone 8) — e.g. `opportunity_rule_config("amazon_opportunity")`.
        Returns {} for an unknown rule name rather than raising: unlike
        provider_config() (where a missing entry means the registry has
        nothing to route on — a wiring bug), a missing rule config here
        just means that rule's evaluator falls back to its own in-code
        defaults, which is a safe, working state, not a bug to surface."""
        return dict(self.yaml_config.get("opportunity_rules", {}).get(rule_name, {}))

    def provider_configs(self) -> dict[str, ProviderConfig]:
        """Every entry under config/default.yaml's `providers:` section
        (deep-merged with the environment override, same as everything
        else in yaml_config)."""
        raw = self.yaml_config.get("providers", {})
        return {name: ProviderConfig(name=name, **cfg) for name, cfg in raw.items()}

    def provider_config(self, name: str) -> ProviderConfig:
        """Raises KeyError for a provider with no entry in `providers:` —
        deliberately not silently defaulting, since a missing entry means
        the registry has nothing to base priority/cost ordering on, which
        should be caught at wiring time, not produce a mysteriously
        low-priority provider."""
        configs = self.provider_configs()
        if name not in configs:
            raise KeyError(f"no provider config for '{name}' in config/default.yaml providers: section")
        return configs[name]

    def ai_config(self) -> dict[str, Any]:
        """config/default.yaml's `ai:` section (Milestone 9.5) —
        restructured 2026-09-21 from the unused `default_model/max_tokens/
        temperature` shape (never read by any code, confirmed by grep) into
        `enabled/provider/local/cloud`. Returns {} if absent rather than
        raising: app/ai/router.py's own defaults (provider=local,
        backend=ollama, base_url=http://localhost:11434, model=qwen3:1.7b)
        are what a bare `ai: {}` — or a missing section entirely — should
        resolve to, mirroring opportunity_rule_config()'s "missing config
        is a safe default, not a wiring bug" precedent rather than
        provider_config()'s "missing entry is an error" one, since there is
        exactly one valid local provider shape today, not an open set the
        caller must be told about explicitly."""
        return dict(self.yaml_config.get("ai", {}))

    def scheduler_config(self) -> dict[str, Any]:
        """config/default.yaml's `scheduler:` section (`enabled: false`,
        `research_job_cron: "0 8 * * 1"`) — declared since this project's
        original refactor pass but never read by any Python code until
        Milestone 14 (confirmed by grep before this method existed). The
        `enable_scheduler` env flag above turns out to be the exact same
        situation `enable_ai_analysis` already was before Milestone 9.5:
        a feature flag declared in `.env`/Settings that nothing consumes,
        because the section it would gate is controlled by its own
        `enabled` key in YAML instead. This method follows that same,
        already-established precedent (YAML `ai.enabled` is authoritative
        for the AI layer, not `ENABLE_AI_ANALYSIS`) rather than picking a
        new convention: `scheduler.enabled` in YAML is what
        `app/workers/celery_app.py` actually checks. `enable_scheduler`
        stays defined (removing a Settings field that some future .env
        might still reference is a separate, unrelated cleanup) but is not
        wired to anything by this milestone either, honestly, same as
        before it."""
        return dict(self.yaml_config.get("scheduler", {}))

    def scout_config(self) -> dict[str, Any]:
        """config/default.yaml's `scout:` section (Anello 1): ritmo delle
        richieste, tetto giornaliero, paesi target, endpoint VIES. Sezione
        mancante = default in codice (app/services/scout_runner.py)."""
        return dict(self.yaml_config.get("scout", {}))

    def weekly_scan_config(self) -> dict[str, Any]:
        """config/default.yaml's `weekly_scan:` section -- the Weekly
        Intelligence Scan feature's own schedule/significance config, a
        different, additive thing from `scheduler_config()` above (see
        that section's own comment in the YAML for the distinction).
        Same "missing section is a safe default, not a wiring bug"
        precedent as `ai_config()`/`scheduler_config()`."""
        return dict(self.yaml_config.get("weekly_scan", {}))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    base = _load_yaml("default")
    override = _load_yaml(settings.environment)
    settings.yaml_config = _deep_merge(base, override)
    return settings
