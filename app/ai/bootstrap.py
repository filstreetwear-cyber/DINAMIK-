"""
build_default_ai_gateway — mirrors `app/tools/core/bootstrap.py`'s
`build_default_registry()` and `app/agents/bootstrap.py`'s
`build_default_agent_registry()`: the one place that turns
`config/default.yaml`'s `ai:` section into a real, wired `AIGateway`.

Returns `None` when `ai.enabled` is `false` in config -- a deliberate,
first-class "AI layer intentionally off" outcome, not an exception. Every
caller that wants to use AI (AIAnalyst construction sites, a future
CLI/API endpoint) must handle `None` by skipping AI-dependent behavior,
never by crashing unrelated functionality (section 24: a local AI failure
-- including "it's turned off" -- must never take down anything else).
This is different from `AIRouter.resolve_provider()` raising
`AICloudNotImplementedError`: that is a *misconfiguration* (asked for
something that doesn't exist), this is a *valid, supported* configuration
choice.
"""
from __future__ import annotations

from app.ai.gateway import AIGateway
from app.ai.router import AIRouter
from app.core.config import Settings, get_settings


def build_default_ai_gateway(settings: Settings | None = None) -> AIGateway | None:
    settings = settings or get_settings()
    config = settings.ai_config()

    if not config.get("enabled", True):
        return None

    if settings.ollama_base_url:
        # 2026-09-22 (post Reality Audit, Docker->Ollama networking fix,
        # §12/§18): config/default.yaml's ai.local.base_url stays the
        # local-dev default (http://localhost:11434); OLLAMA_BASE_URL
        # overrides it for a real container deploy where localhost would
        # resolve to the container itself. `config` here is `dict(...)`
        # (a shallow copy, see Settings.ai_config()'s own docstring) --
        # `config["local"]` is still a reference into the cached, shared
        # settings.yaml_config dict (get_settings() is @lru_cache'd), so
        # mutating it in place would leak into every other caller of
        # settings.ai_config() for the rest of the process. Copy "local"
        # before writing to it.
        local_config = dict(config.get("local", {}))
        local_config["base_url"] = settings.ollama_base_url
        config = {**config, "local": local_config}

    router = AIRouter(config)
    provider = router.resolve_provider()
    return AIGateway(provider)
