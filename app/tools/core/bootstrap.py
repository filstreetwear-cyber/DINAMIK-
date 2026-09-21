"""
build_default_registry — the one place that turns config/default.yaml's
`providers:` section (Phase H) into a real, populated ProviderRegistry.
Without this, "config-driven priority/cost" would only be theoretical —
every caller (Milestone 5's Amazon Scout, later phases) is expected to get
its registry from here rather than hand-registering providers with
made-up priority numbers, which is what the Phase C/D/E tests still do on
purpose (they're testing the registry/orchestrator mechanics in isolation,
not this wiring).

Only providers with `enabled: true` in config are registered — a provider
whose config sets enabled: false (Jungle Scout, Perpetua, Helium10 today)
is simply absent from the registry, not registered-but-broken. This is how
Helium10 being "postponed" (docs/architecture.md, 21/09 decision)
concretely shows up in the running system: an orchestrator built from this
function has no Helium10 entry at all, not a stub that fails every call.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.tools.amazon.provider import AmazonProvider
from app.tools.core.registry import ProviderRegistry
from app.tools.keepa.provider import KeepaProvider
from app.tools.website.provider import WebsiteIntelligenceProvider


def build_default_registry(settings: Settings | None = None) -> ProviderRegistry:
    settings = settings or get_settings()
    registry = ProviderRegistry()

    candidates = {
        "website_intelligence": lambda: WebsiteIntelligenceProvider(),
        "amazon": lambda: AmazonProvider(settings=settings),
        "keepa": lambda: KeepaProvider(settings=settings),
        # junglescout / perpetua / helium10: no provider class exists yet
        # (junglescout, perpetua) or is intentionally not built
        # (helium10, Phase G postponed) — their config entries exist for
        # documentation/future wiring but are skipped here regardless of
        # `enabled`, since there is nothing to instantiate.
    }

    for name, factory in candidates.items():
        config = settings.provider_config(name)
        if not config.enabled:
            continue
        registry.register(factory(), priority=config.priority, cost_model=config.cost_model)

    return registry
