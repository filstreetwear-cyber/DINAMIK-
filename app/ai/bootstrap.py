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

    router = AIRouter(config)
    provider = router.resolve_provider()
    return AIGateway(provider)
