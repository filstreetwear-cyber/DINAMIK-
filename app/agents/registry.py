"""
AgentRegistry — Milestone 8.5. Genuinely new: before this, `app/agents/`
held exactly one module (`amazon_scout/discovery.py`) with no registry,
no shared metadata shape, nothing a caller could introspect ("what agents
exist, what does each one need/produce") without reading source.

Deliberately NOT a rewrite of the existing agent into a class hierarchy.
The audit (docs/architecture/milestone-8.5-audit.md) found the existing
provider layer already has a mature Capability/Provider/Registry pattern
(app/tools/core/) — this mirrors that shape (register/get/list/
find_by_capability) for consistency, but wraps the EXISTING
`run_amazon_scout_discovery` function as-is via `AgentMetadata` +
`entrypoint`, instead of forcing it into a base `Agent` class. Milestone
8.5's own constraint ("prefer incremental refactoring over replacement,
preserve existing working functionality") is the reason: amazon_scout's
function-style entrypoint already has real tests
(tests/test_amazon_scout_discovery.py) exercising it directly — rewriting
it as a class to satisfy a registry's taste would touch working, tested
code for a cosmetic gain.

If a second agent (Agent 02 — Amazon Auditor, per README's roadmap) needs
shared lifecycle behavior beyond "one async callable", that is the point
to introduce a base class — not before, per the same "don't build for an
imagined future" discipline the rest of this project follows (see
Capability enum's own history: PRODUCT_SEARCH was added exactly when
Milestone 5 needed it, not speculatively).
"""
from __future__ import annotations

import builtins
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.tools.core.capability import Capability


@dataclass(frozen=True)
class AgentMetadata:
    """What the registry (and, eventually, an orchestration layer —
    Milestone 11, not built) needs to know about an agent without
    importing or running it: identity, what it's for, and what
    capabilities it depends on (for "can this agent even run right now"
    checks against the ProviderRegistry — not wired yet, see
    AgentRegistry.__init__ docstring)."""

    name: str
    description: str
    consumes_capabilities: frozenset[Capability] = field(default_factory=frozenset)
    version: str = "1.0"


@dataclass(frozen=True)
class RegisteredAgent:
    metadata: AgentMetadata
    entrypoint: Callable[..., Awaitable[Any]]
    """The agent's existing callable, unmodified — e.g.
    `run_amazon_scout_discovery`. Signature is intentionally left as
    `Callable[..., Awaitable[Any]]` rather than unified across agents:
    forcing every agent's entrypoint onto one shared signature is exactly
    the kind of premature abstraction the module docstring above argues
    against. Callers that need a specific agent's real signature import
    the metadata's `name` to find it, then call the concrete function
    directly with full type information — the registry's job is
    discovery ("which agents exist, which capabilities does each one
    use"), not a generic invocation protocol."""


class DuplicateAgentError(Exception):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"an agent named '{name}' is already registered")


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, RegisteredAgent] = {}

    def register(self, metadata: AgentMetadata, entrypoint: Callable[..., Awaitable[Any]]) -> None:
        """Unlike ProviderRegistry.register() (Phase H — last registration
        wins, so tests/startup can override freely), this raises on a
        duplicate name. An agent registry entry is looked up by name from
        outside code (a future CLI/API, Milestone 12/13) in a way a
        silent overwrite would make confusing to debug; a provider
        registry is only ever built once per process by
        build_default_registry(), an agent registry is expected to also
        be built/extended incrementally as agents are added — the
        stricter check catches a copy-paste name collision at
        registration time instead of at debug time."""
        if metadata.name in self._agents:
            raise DuplicateAgentError(metadata.name)
        self._agents[metadata.name] = RegisteredAgent(metadata=metadata, entrypoint=entrypoint)

    def get(self, name: str) -> RegisteredAgent | None:
        return self._agents.get(name)

    def list(self) -> builtins.list[AgentMetadata]:
        # `builtins.list`, not the bare `list` this project uses everywhere
        # else, because a method literally named `list` shadows the builtin
        # in its own return annotation's scope — confuses mypy otherwise.
        return [entry.metadata for entry in self._agents.values()]

    def find_by_capability(self, capability: Capability) -> builtins.list[AgentMetadata]:
        # same builtins.list reason as list() above — this method is
        # defined after list() in the class body, so it's affected too.
        return [
            entry.metadata
            for entry in self._agents.values()
            if capability in entry.metadata.consumes_capabilities
        ]
