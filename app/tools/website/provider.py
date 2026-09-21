"""
WebsiteIntelligenceProvider — the one "core" provider (refactor doc
hierarchy) that produces real, non-mock data with zero third-party
credentials. Implements the full escalation path Phase E asked for:

    HTTP acquisition -> HTML parse -> (JS fallback / Browser fallback)

The HTTP tier (http_client.py) is tried first — cheap, fast, no browser
process. If html_parser.looks_js_rendered() flags the response as a likely
empty SPA shell, this provider escalates to the browser engine
(app/tools/browser/) for a JS-rendered fetch and re-parses that HTML
instead. The browser tier is never the default path — it is meaningfully
more expensive (a real or mocked browser launch) and only used when the
cheap tier's own output signals it wasn't enough.

Everything else in app/tools/{amazon,keepa}/ is mock-only today because no
real credentials have been confirmed (see docs/architecture.md addendum,
2026-09-21).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.enums import AcquisitionMethod, DataType, ToolHealthStatus
from app.tools.browser.engine import BrowserAutomationEngine, build_browser_engine
from app.tools.browser.session import resolve_session_handle
from app.tools.browser.task import BrowserTaskSpec
from app.tools.core.capability import Capability
from app.tools.core.provider import IntelligenceProvider, ProviderResult, ProviderUnavailableError
from app.tools.website.html_parser import looks_js_rendered, parse_website_html
from app.tools.website.http_client import WebsiteFetchError, fetch_page

# Signals that come straight from a documented HTML element (a <title> tag,
# a meta description that is either present or not) are close to FACT-grade;
# signals inferred from vendor-fingerprint heuristics (technology, chatbot)
# are never more than INFERENCE, however confident the match looks, because
# the heuristic itself can be wrong (a theme that bundles Shopify's CDN
# without the site actually running Shopify, e.g.).
_INFERRED_SIGNAL_CONFIDENCE = 70.0
_OBSERVED_SIGNAL_CONFIDENCE = 95.0
# The browser tier re-parses the SAME heuristics, but the input HTML came
# from a JS-executed DOM snapshot rather than the server's own response —
# one more inferential step removed, so confidence is lowered slightly
# rather than reused as-is.
_BROWSER_TIER_CONFIDENCE_PENALTY = 10.0


class WebsiteIntelligenceProvider(IntelligenceProvider):
    name = "website_intelligence"
    supported_capabilities = frozenset({Capability.WEBSITE_DATA})

    def __init__(self, browser_engine: BrowserAutomationEngine | None = None) -> None:
        # Lazily resolved on first use if not injected, so constructing a
        # provider never forces a MOCK_MODE/settings decision or a
        # Chromium-availability check before it's actually needed.
        self._browser_engine = browser_engine

    async def health(self) -> ToolHealthStatus:
        # No credentials, no rate limit tracked yet — this provider is
        # always structurally available; a specific fetch() can still fail
        # per-URL (DNS, timeout, 4xx/5xx), which is not a health-level
        # concern the way a missing API key would be.
        return ToolHealthStatus.AVAILABLE

    def _engine(self) -> BrowserAutomationEngine:
        if self._browser_engine is None:
            self._browser_engine = build_browser_engine()
        return self._browser_engine

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

        html = page.html
        source_url = page.url
        acquisition_method = AcquisitionMethod.HTTP
        confidence_penalty = 0.0

        if looks_js_rendered(html):
            rendered_html = await self._try_browser_fallback(url)
            if rendered_html is not None:
                html = rendered_html
                acquisition_method = AcquisitionMethod.BROWSER
                confidence_penalty = _BROWSER_TIER_CONFIDENCE_PENALTY
            # If the browser tier also comes back empty/unavailable, we
            # deliberately keep going with the original (thin) HTTP result
            # rather than raising — a JS-heavy site with no working
            # fallback is itself informative (low data completeness), not
            # a provider failure.

        signals = parse_website_html(html)
        observed_at = datetime.now(UTC)

        results = [
            ProviderResult(
                capability=Capability.WEBSITE_DATA,
                data={"technology": signals["technology"], "generator_meta_tag": signals["generator_meta_tag"]},
                data_type=DataType.INFERENCE,
                confidence=_INFERRED_SIGNAL_CONFIDENCE - confidence_penalty,
                acquisition_method=acquisition_method,
                observed_at=observed_at,
                source_url=source_url,
            ),
            ProviderResult(
                capability=Capability.WEBSITE_DATA,
                data={"has_chatbot": signals["has_chatbot"]},
                data_type=DataType.INFERENCE,
                confidence=_INFERRED_SIGNAL_CONFIDENCE - confidence_penalty,
                acquisition_method=acquisition_method,
                observed_at=observed_at,
                source_url=source_url,
            ),
            ProviderResult(
                capability=Capability.WEBSITE_DATA,
                data={"title": signals["title"], "meta_description": signals["meta_description"]},
                data_type=DataType.FACT,  # directly read from the page's own markup, not inferred
                confidence=_OBSERVED_SIGNAL_CONFIDENCE - confidence_penalty,
                acquisition_method=acquisition_method,
                observed_at=observed_at,
                source_url=source_url,
            ),
        ]
        return results

    async def _try_browser_fallback(self, url: str) -> str | None:
        """Returns rendered HTML, or None if the browser tier itself
        couldn't produce anything usable (engine failure, or a human-login
        gate) — never raises, since this is a fallback of a fallback and
        the caller has a perfectly valid non-browser result to keep using."""
        session = resolve_session_handle(provider=self.name)
        spec = BrowserTaskSpec(provider=self.name, task_type="fetch_rendered_html", params={"url": url})
        outcome = await self._engine().run(spec, session)
        if outcome.output and "html" in outcome.output:
            return outcome.output["html"]
        return None
