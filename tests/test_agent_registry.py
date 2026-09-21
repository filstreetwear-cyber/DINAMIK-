"""
AgentRegistry unit tests (registry mechanics, fake entrypoints — mirrors
test_provider_registry.py's style) + build_default_agent_registry()
integration (mirrors test_bootstrap.py's style: verifies the real wiring,
not just that the class works in isolation).
"""
from __future__ import annotations

import pytest

from app.agents.bootstrap import build_default_agent_registry
from app.agents.registry import AgentMetadata, AgentRegistry, DuplicateAgentError
from app.tools.core.capability import Capability


async def _fake_entrypoint(*, foo: int = 1) -> dict:
    return {"foo": foo}


def test_register_and_get_roundtrip():
    registry = AgentRegistry()
    metadata = AgentMetadata(name="fake_agent", description="a fake agent for tests")
    registry.register(metadata, entrypoint=_fake_entrypoint)

    entry = registry.get("fake_agent")

    assert entry is not None
    assert entry.metadata is metadata
    assert entry.entrypoint is _fake_entrypoint


def test_get_unknown_name_returns_none():
    registry = AgentRegistry()
    assert registry.get("does_not_exist") is None


def test_registering_duplicate_name_raises():
    registry = AgentRegistry()
    registry.register(AgentMetadata(name="dup", description="first"), entrypoint=_fake_entrypoint)

    with pytest.raises(DuplicateAgentError):
        registry.register(AgentMetadata(name="dup", description="second"), entrypoint=_fake_entrypoint)

    # the first registration must survive the failed second attempt
    assert registry.get("dup").metadata.description == "first"


def test_list_returns_metadata_for_every_registered_agent():
    registry = AgentRegistry()
    registry.register(AgentMetadata(name="a", description="agent a"), entrypoint=_fake_entrypoint)
    registry.register(AgentMetadata(name="b", description="agent b"), entrypoint=_fake_entrypoint)

    names = {m.name for m in registry.list()}

    assert names == {"a", "b"}


def test_find_by_capability_filters_correctly():
    registry = AgentRegistry()
    registry.register(
        AgentMetadata(
            name="search_agent",
            description="uses PRODUCT_SEARCH",
            consumes_capabilities=frozenset({Capability.PRODUCT_SEARCH}),
        ),
        entrypoint=_fake_entrypoint,
    )
    registry.register(
        AgentMetadata(
            name="website_agent",
            description="uses WEBSITE_DATA",
            consumes_capabilities=frozenset({Capability.WEBSITE_DATA}),
        ),
        entrypoint=_fake_entrypoint,
    )

    matches = registry.find_by_capability(Capability.PRODUCT_SEARCH)

    assert [m.name for m in matches] == ["search_agent"]


def test_find_by_capability_with_no_match_returns_empty_list():
    registry = AgentRegistry()
    registry.register(AgentMetadata(name="a", description="a"), entrypoint=_fake_entrypoint)

    assert registry.find_by_capability(Capability.PPC_DATA) == []


@pytest.mark.asyncio
async def test_registered_entrypoint_is_actually_callable():
    """Sanity check on the wrapping itself: get() must return the exact
    same callable, still invocable with its own real signature — the
    registry does not wrap/adapt it."""
    registry = AgentRegistry()
    registry.register(AgentMetadata(name="fake", description="fake"), entrypoint=_fake_entrypoint)

    entry = registry.get("fake")
    result = await entry.entrypoint(foo=42)

    assert result == {"foo": 42}


# --- build_default_agent_registry(): real wiring, not a fake ----------------


def test_default_agent_registry_includes_amazon_scout():
    registry = build_default_agent_registry()

    names = {m.name for m in registry.list()}
    assert names == {"amazon_scout"}


def test_default_agent_registry_amazon_scout_entrypoint_is_the_real_function():
    from app.agents.amazon_scout.discovery import run_amazon_scout_discovery

    registry = build_default_agent_registry()

    entry = registry.get("amazon_scout")
    assert entry is not None
    assert entry.entrypoint is run_amazon_scout_discovery


def test_default_agent_registry_find_by_capability_matches_amazon_scout():
    registry = build_default_agent_registry()

    matches = registry.find_by_capability(Capability.PRODUCT_SEARCH)

    assert [m.name for m in matches] == ["amazon_scout"]


def test_default_agent_registry_does_not_match_unrelated_capability():
    registry = build_default_agent_registry()

    assert registry.find_by_capability(Capability.WEBSITE_DATA) == []
