"""
ProviderRegistry — where providers get registered and looked up by
capability, ordered by priority.

Priority is passed explicitly at registration time (`register(provider,
priority=...)`) rather than read from config, because app/core/config.py's
per-provider restructuring (audit REQUIRED CHANGES #1 — mode/priority/
cost_model per provider in config/default.yaml) has not been done yet; that
is still open work, separate from this phase. Whoever wires up the
registry at startup (a future app/tools/bootstrap.py or equivalent) decides
priority today; once config carries it, that wiring point reads from
settings instead — the registry's own interface does not need to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.tools.core.capability import Capability
from app.tools.core.provider import IntelligenceProvider


@dataclass(order=True)
class _RegisteredProvider:
    priority: int
    provider: IntelligenceProvider = field(compare=False)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, _RegisteredProvider] = {}

    def register(self, provider: IntelligenceProvider, priority: int = 100) -> None:
        """Lower priority number = tried first. Re-registering a name
        overwrites the previous entry — last registration wins, on purpose,
        so tests and startup code can override without a separate API."""
        self._providers[provider.name] = _RegisteredProvider(priority=priority, provider=provider)

    def get(self, name: str) -> IntelligenceProvider | None:
        entry = self._providers.get(name)
        return entry.provider if entry else None

    def for_capability(self, capability: Capability) -> list[IntelligenceProvider]:
        """Providers supporting `capability`, ordered lowest-priority-first."""
        matches = [
            entry for entry in self._providers.values() if entry.provider.supports(capability)
        ]
        matches.sort(key=lambda entry: entry.priority)
        return [entry.provider for entry in matches]

    def all(self) -> list[IntelligenceProvider]:
        return [entry.provider for entry in self._providers.values()]
