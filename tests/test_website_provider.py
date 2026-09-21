"""
Website Intelligence — the one provider in this phase that does real work
with zero credentials. html_parser tests use raw fixture HTML strings (no
network). provider tests monkeypatch fetch_page so WebsiteIntelligenceProvider
itself is exercised without a real HTTP call.
"""
from __future__ import annotations

import pytest

from app.models.enums import DataType, WebsiteTechnology
from app.tools.core.capability import Capability
from app.tools.core.provider import ProviderUnavailableError
from app.tools.website import html_parser
from app.tools.website import provider as provider_module
from app.tools.website.http_client import FetchedPage, WebsiteFetchError
from app.tools.website.provider import WebsiteIntelligenceProvider

_SHOPIFY_HTML = """
<html><head><title>Mock Shopify Store</title>
<meta name="description" content="Buy mock things.">
<script src="https://cdn.shopify.com/s/files/theme.js"></script>
</head><body><div id="intercom-container"></div></body></html>
"""

_WOOCOMMERCE_HTML = """
<html><head><title>Mock WP Shop</title><meta name="generator" content="WordPress 6.4">
</head><body><div class="woocommerce"><form class="search-form" role="search"></form></div></body></html>
"""

_PLAIN_HTML = "<html><head><title>Nothing special</title></head><body>hello</body></html>"


def test_parser_detects_shopify_and_chatbot():
    signals = html_parser.parse_website_html(_SHOPIFY_HTML)
    assert signals["technology"] == WebsiteTechnology.SHOPIFY.value
    assert signals["has_chatbot"] is True
    assert signals["title"] == "Mock Shopify Store"
    assert signals["meta_description"] == "Buy mock things."


def test_parser_detects_woocommerce_and_search():
    signals = html_parser.parse_website_html(_WOOCOMMERCE_HTML)
    assert signals["technology"] == WebsiteTechnology.WOOCOMMERCE.value
    assert signals["has_search"] is True
    assert signals["generator_meta_tag"] == "WordPress 6.4"


def test_parser_defaults_to_unknown_and_false_on_plain_html():
    signals = html_parser.parse_website_html(_PLAIN_HTML)
    assert signals["technology"] == WebsiteTechnology.UNKNOWN.value
    assert signals["has_chatbot"] is False
    assert signals["has_search"] is False


def test_parser_never_raises_on_malformed_html():
    signals = html_parser.parse_website_html("<html><body><div>unclosed")
    assert signals["technology"] == WebsiteTechnology.UNKNOWN.value


@pytest.mark.asyncio
async def test_provider_fetch_maps_signals_to_provider_results(monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch_page(url: str, timeout_seconds: float = 10.0) -> FetchedPage:
        return FetchedPage(url=url, status_code=200, html=_SHOPIFY_HTML)

    monkeypatch.setattr(provider_module, "fetch_page", fake_fetch_page)

    results = await WebsiteIntelligenceProvider().fetch(Capability.WEBSITE_DATA, url="https://mockstore.test")

    assert len(results) == 3
    technology_result = next(r for r in results if "technology" in r.data)
    assert technology_result.data["technology"] == WebsiteTechnology.SHOPIFY.value
    assert technology_result.data_type == DataType.INFERENCE  # heuristic, never a FACT

    title_result = next(r for r in results if "title" in r.data)
    assert title_result.data_type == DataType.FACT  # read straight off the page's own markup
    assert title_result.data["title"] == "Mock Shopify Store"

    assert all(not r.is_mock for r in results)  # real HTTP path, not fixture data


@pytest.mark.asyncio
async def test_provider_fetch_wraps_fetch_failure_as_provider_unavailable(monkeypatch: pytest.MonkeyPatch):
    async def failing_fetch_page(url: str, timeout_seconds: float = 10.0) -> FetchedPage:
        raise WebsiteFetchError("simulated DNS failure")

    monkeypatch.setattr(provider_module, "fetch_page", failing_fetch_page)

    with pytest.raises(ProviderUnavailableError):
        await WebsiteIntelligenceProvider().fetch(Capability.WEBSITE_DATA, url="https://unreachable.test")


@pytest.mark.asyncio
async def test_provider_fetch_requires_url_param():
    with pytest.raises(ProviderUnavailableError):
        await WebsiteIntelligenceProvider().fetch(Capability.WEBSITE_DATA)


@pytest.mark.asyncio
async def test_provider_rejects_unsupported_capability():
    with pytest.raises(ProviderUnavailableError):
        await WebsiteIntelligenceProvider().fetch(Capability.SALES_RANK, url="https://mockstore.test")


@pytest.mark.asyncio
async def test_provider_health_is_always_available():
    from app.models.enums import ToolHealthStatus

    assert await WebsiteIntelligenceProvider().health() == ToolHealthStatus.AVAILABLE
