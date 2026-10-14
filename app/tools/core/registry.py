"""
ProviderRegistry — where providers get registered and looked up by
capability, ordered by (priority, cost).

Priority and cost_model are passed explicitly at registration time by the
caller (app/tools/core/bootstrap.py, Phase H) rather than looked up here —
the registry itself has no dependency on app.core.config, so it stays
testable with plain fake providers and arbitrary numbers, the way
tests/test_provider_registry.py already does.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.tools.core.capability import Capability
from app.tools.core.provider import IntelligenceProvider

# Coarse cost tiers (Phase H) — not a currency amount, a relative ranking
# used only to break ties between providers at the same priority. Real
# providers today are free (Website Intelligence, no credentials) or mock
# (Amazon/Keepa fixtures, effectively free but kept in their own tier so a
# future real-money Amazon/Keepa integration doesn't have to touch this
# table). "unknown" ranks worst-case (highest) deliberately: a provider
# nobody has costed yet should not be preferred over one that is known to
# be cheap.
_COST_MODEL_RANK: dict[str, int] = {"free": 0, "mock": 1, "cheap": 2, "paid": 3, "unknown": 4}
_UNKNOWN_COST_RANK = _COST_MODEL_RANK["unknown"]


def cost_rank(cost_model: str) -> int:
    return _COST_MODEL_RANK.get(cost_model, _UNKNOWN_COST_RANK)


@dataclass(order=True)
class _RegisteredProvider:
    sort_key: tuple[int, int]
    provider: IntelligenceProvider = field(compare=False)
    priority: int = field(compare=False)
    cost_model: str = field(compare=False)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, _RegisteredProvider] = {}

    def register(self, provider: IntelligenceProvider, priority: int = 100, cost_model: str = "unknown") -> None:
        """Lower priority number = tried first; among equal priorities,
        lower cost_model rank = tried first. Re-registering a name
        overwrites the previous entry — last registration wins, on purpose,
        so tests and startup code can override without a separate API."""
        self._providers[provider.name] = _RegisteredProvider(
            sort_key=(priority, cost_rank(cost_model)),
            provider=provider,
            priority=priority,
            cost_model=cost_model,
        )

    def get(self, name: str) -> IntelligenceProvider | None:
        entry = self._providers.get(name)
        return entry.provider if entry else None

    def for_capability(self, capability: Capability) -> list[IntelligenceProvider]:
        """Providers supporting `capability`, ordered lowest-(priority,cost)-first."""
        matches = [
            entry for entry in self._providers.values() if entry.provider.supports(capability)
        ]
        matches.sort(key=lambda entry: entry.sort_key)
        return [entry.provider for entry in matches]

    def all(self) -> list[IntelligenceProvider]:
        return [entry.provider for entry in self._providers.values()]
