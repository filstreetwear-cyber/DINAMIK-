"""
Website Intelligence — the one provider in this phase that does real work
with zero credentials. html_parser tests use raw fixture HTML strings (no
network). provider tests monkeypatch fetch_page so WebsiteIntelligenceProvider
itself is exercised without a real HTTP call.
"""
from __future__ import annotations

import pytest

from app.models.enums import AcquisitionMethod, BrowserTaskStatus, DataType, WebsiteTechnology
from app.tools.browser.mock_engine import MockBrowserAutomationEngine
from app.tools.browser.task import BrowserTaskOutcome
from app.tools.core.capability import Capability
from app.tools.core.provider import ProviderUnavailableError
from app.tools.website import html_parser
from app.tools.website import provider as provider_module
from app.tools.website.http_client import FetchedPage, WebsiteFetchError
from app.tools.website.provider import WebsiteIntelligenceProvider

# A near-empty SPA shell: long enough HTML, almost no visible text — this
# is exactly the shape looks_js_rendered() is meant to catch.
_JS_SHELL_HTML = "<html><head><title>App</title></head><body><div id=\"root\"></div>" + (
    "<!-- padding so this clears the length threshold -->" * 10
) + "</body></html>"

_RENDERED_SHOPIFY_HTML = """
<html><head><title>Rendered Shopify Store</title>
<meta name="description" content="Now with real content.">
<script src="https://cdn.shopify.com/s/files/theme.js"></script>
</head><body><p>Real product content, only visible after JS runs.</p></body></html>
"""

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


def test_looks_js_rendered_flags_thin_long_html():
    assert html_parser.looks_js_rendered(_JS_SHELL_HTML) is True


def test_looks_js_rendered_does_not_flag_short_content_ful_html():
    assert html_parser.looks_js_rendered(_PLAIN_HTML) is False  # short HTML, not a shell signal


def test_looks_js_rendered_does_not_flag_long_html_with_real_text():
    long_content_ful_html = "<html><body><p>" + ("real visible sentence. " * 40) + "</p></body></html>"
    assert html_parser.looks_js_rendered(long_content_ful_html) is False


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


@pytest.mark.asyncio
async def test_provider_escalates_to_browser_tier_when_http_result_looks_js_rendered(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_fetch_page(url: str, timeout_seconds: float = 10.0) -> FetchedPage:
        return FetchedPage(url=url, status_code=200, html=_JS_SHELL_HTML)

    monkeypatch.setattr(provider_module, "fetch_page", fake_fetch_page)

    browser_outcome = BrowserTaskOutcome(
        status=BrowserTaskStatus.COMPLETED,
        output={"html": _RENDERED_SHOPIFY_HTML, "final_url": "https://spa.test"},
        error=None,
        requires_human=False,
        duration_ms=500,
    )
    engine = MockBrowserAutomationEngine(scripted_outcomes={"fetch_rendered_html": browser_outcome})

    results = await WebsiteIntelligenceProvider(browser_engine=engine).fetch(
        Capability.WEBSITE_DATA, url="https://spa.test"
    )

    technology_result = next(r for r in results if "technology" in r.data)
    assert technology_result.data["technology"] == WebsiteTechnology.SHOPIFY.value  # only visible post-render
    assert technology_result.acquisition_method == AcquisitionMethod.BROWSER
    assert technology_result.confidence < 70.0  # browser-tier penalty applied


@pytest.mark.asyncio
async def test_provider_keeps_thin_http_result_when_browser_tier_also_fails(monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch_page(url: str, timeout_seconds: float = 10.0) -> FetchedPage:
        return FetchedPage(url=url, status_code=200, html=_JS_SHELL_HTML)

    monkeypatch.setattr(provider_module, "fetch_page", fake_fetch_page)

    # No scripted outcome for fetch_rendered_html -> MockBrowserAutomationEngine's
    # default COMPLETED-with-empty-output has no "html" key, exactly like a
    # real engine failure would look to this provider.
    engine = MockBrowserAutomationEngine()

    results = await WebsiteIntelligenceProvider(browser_engine=engine).fetch(
        Capability.WEBSITE_DATA, url="https://spa-with-no-fallback.test"
    )

    technology_result = next(r for r in results if "technology" in r.data)
    assert technology_result.acquisition_method == AcquisitionMethod.HTTP  # never claimed BROWSER
    assert technology_result.confidence == 70.0  # no penalty applied — original HTTP-tier result kept


@pytest.mark.asyncio
async def test_provider_does_not_escalate_for_normal_content_ful_pages(monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch_page(url: str, timeout_seconds: float = 10.0) -> FetchedPage:
        return FetchedPage(url=url, status_code=200, html=_SHOPIFY_HTML)

    monkeypatch.setattr(provider_module, "fetch_page", fake_fetch_page)

    engine_that_must_not_be_called = MockBrowserAutomationEngine()
    calls: list[str] = []
    original_run = engine_that_must_not_be_called.run

    async def spying_run(spec, session):  # type: ignore[no-untyped-def]
        calls.append(spec.task_type)
        return await original_run(spec, session)

    engine_that_must_not_be_called.run = spying_run  # type: ignore[method-assign]

    await WebsiteIntelligenceProvider(browser_engine=engine_that_must_not_be_called).fetch(
        Capability.WEBSITE_DATA, url="https://normal-site.test"
    )

    assert calls == []  # browser tier never invoked for a normal, content-ful page
