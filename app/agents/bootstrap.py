"""
build_default_agent_registry — mirrors app/tools/core/bootstrap.py's
build_default_registry() on purpose: the one place that turns "agents that
exist in this codebase" into a populated AgentRegistry, so callers don't
hand-register with made-up metadata (same rationale as the provider
version's own docstring).

Only one entry today — amazon_scout — because only one agent exists
(confirmed by the Milestone 8.5 audit, not assumed). Adding Agent 02
(Amazon Auditor) or any future agent means adding one entry here, not
touching the registry class itself.
"""
from __future__ import annotations

from app.agents.amazon_scout.discovery import run_amazon_scout_discovery
from app.agents.registry import AgentMetadata, AgentRegistry
from app.tools.core.capability import Capability


def build_default_agent_registry() -> AgentRegistry:
    registry = AgentRegistry()

    registry.register(
        AgentMetadata(
            name="amazon_scout",
            description=(
                "Discovery: category/keyword search for candidate ASINs, then "
                "per-ASIN enrichment via the IntelligenceSourceOrchestrator. "
                "Writes only raw ResearchResult + Evidence rows — no brand "
                "resolution, dedup, opportunity, or AI inference (Milestone 5 "
                "scope, unchanged since introduction)."
            ),
            consumes_capabilities=frozenset(
                {
                    Capability.PRODUCT_SEARCH,
                    Capability.PRODUCT_DATA,
                    Capability.SELLER_DATA,
                    Capability.BRAND_DATA,
                    Capability.SALES_RANK,
                }
            ),
        ),
        entrypoint=run_amazon_scout_discovery,
    )

    return registry
