"""
WebsiteIntelligenceProvider — the one "core" provider (refactor doc
hierarchy) that produces real, non-mock data with zero third-party
credentials: it fetches a public URL and parses it. Everything else in
app/tools/{amazon,keepa}/ is mock-only today because no real credentials
have been confirmed (see docs/architecture.md addendum, 2026-09-21).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.enums import AcquisitionMethod, DataType, ToolHealthStatus
from app.tools.core.capability import Capability
from app.tools.core.provider import IntelligenceProvider, ProviderResult, ProviderUnavailableError
from app.tools.website.html_parser import parse_website_html
from app.tools.website.http_client import WebsiteFetchError, fetch_page

# Signals that come straight from a documented HTML element (a <title> tag,
# a meta description that is either present or not) are close to FACT-grade;
# signals inferred from vendor-fingerprint heuristics (technology, chatbot)
# are never more than INFERENCE, however confident the match looks, because
# the heuristic itself can be wrong (a theme that bundles Shopify's CDN
# without the site actually running Shopify, e.g.).
_INFERRED_SIGNAL_CONFIDENCE = 70.0
_OBSERVED_SIGNAL_CONFIDENCE = 95.0


class WebsiteIntelligenceProvider(IntelligenceProvider):
    name = "website_intelligence"
    supported_capabilities = frozenset({Capability.WEBSITE_DATA})

    async def health(self) -> ToolHealthStatus:
        # No credentials, no rate limit tracked yet — this provider is
        # always structurally available; a specific fetch() can still fail
        # per-URL (DNS, timeout, 4xx/5xx), which is not a health-level
        # concern the way a missing API key would be.
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        if capability != Capability.WEBSITE_DATA:
            raise ProviderUnavailableError(self.name, f"does not support {capability.value}")

        url = params.get("url")
        if not url:
            raise ProviderUnavailableError(self.name, "fetch() requires a 'url' parameter")

        try:
            page = await fetch_page(url)
        except WebsiteFetchError as exc:
            raise ProviderUnavailableError(self.name, str(exc)) from exc

        signals = parse_website_html(page.html)
        observed_at = datetime.now(UTC)

        results = [
            ProviderResult(
                capability=Capability.WEBSITE_DATA,
                data={"technology": signals["technology"], "generator_meta_tag": signals["generator_meta_tag"]},
                data_type=DataType.INFERENCE,
                confidence=_INFERRED_SIGNAL_CONFIDENCE,
                acquisition_method=AcquisitionMethod.HTTP,
                observed_at=observed_at,
                source_url=page.url,
            ),
            ProviderResult(
                capability=Capability.WEBSITE_DATA,
                data={"has_chatbot": signals["has_chatbot"]},
                data_type=DataType.INFERENCE,
                confidence=_INFERRED_SIGNAL_CONFIDENCE,
                acquisition_method=AcquisitionMethod.HTTP,
                observed_at=observed_at,
                source_url=page.url,
            ),
            ProviderResult(
                capability=Capability.WEBSITE_DATA,
                data={"title": signals["title"], "meta_description": signals["meta_description"]},
                data_type=DataType.FACT,  # directly read from the page's own markup, not inferred
                confidence=_OBSERVED_SIGNAL_CONFIDENCE,
                acquisition_method=AcquisitionMethod.HTTP,
                observed_at=observed_at,
                source_url=page.url,
            ),
        ]
        return results
