"""
Pure HTML → signals parsing for Website Intelligence. Deliberately has no
network code and no knowledge of ProviderResult/Evidence — it takes a raw
HTML string and returns a plain dict, so it can be unit-tested against
fixture HTML with zero network calls and zero database.

Every heuristic here is exactly that — a heuristic, not a documented API
contract. The provider (provider.py) is responsible for turning
`confidence` values into the right DataType (INFERENCE, not FACT) before
this becomes an Evidence row; this module only produces raw signals plus a
per-signal confidence, it does not decide the data_type itself.
"""
from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from app.models.enums import WebsiteTechnology

# Each entry: (technology, [needle substrings checked against lowercased HTML])
# Order matters — checked top to bottom, first match wins. Shopify/WooCommerce
# before generic WordPress since WooCommerce sites also contain "wp-content".
_TECHNOLOGY_SIGNATURES: list[tuple[WebsiteTechnology, list[str]]] = [
    (WebsiteTechnology.SHOPIFY, ["cdn.shopify.com", "shopify.theme", "cdn/shop"]),
    (WebsiteTechnology.WOOCOMMERCE, ["woocommerce", "wc-ajax"]),
    (WebsiteTechnology.MAGENTO, ["mage.cookies", "magento", "/skin/frontend/"]),
    (WebsiteTechnology.WORDPRESS_OTHER, ["wp-content", "wp-includes"]),
]

# Known chat-widget vendors. This is a signal for "has_chatbot", not a
# vendor identification feature — good enough for opportunity scoring
# (section 5/6 of the original spec), not exhaustive.
_CHATBOT_SIGNATURES = [
    "intercom",
    "drift.com",
    "tawk.to",
    "crisp.chat",
    "zdassets.com",  # Zendesk widget CDN
    "livechatinc.com",
    "hubspot",
    "sellerfast",  # Dinamik's own product — worth flagging explicitly when detected
]

_SEARCH_SIGNATURES = ['type="search"', "role=\"search\"", "search-form", 'name="s"']


_JS_SHELL_MIN_HTML_LENGTH = 500
_JS_SHELL_MAX_VISIBLE_TEXT_LENGTH = 200


def looks_js_rendered(html: str) -> bool:
    """Heuristic for 'this HTTP response is a near-empty SPA shell, the
    real content only exists after JS runs' — i.e. the HTTP acquisition
    tier is not enough, escalate to the browser tier (provider.py). Not a
    proof, just a signal: a long HTML document with little visible text is
    the common shape of a client-rendered app; a short, content-ful page
    is not, however few characters it has."""
    if len(html) < _JS_SHELL_MIN_HTML_LENGTH:
        return False
    soup = BeautifulSoup(html, "lxml")
    visible_text = soup.get_text(strip=True)
    return len(visible_text) < _JS_SHELL_MAX_VISIBLE_TEXT_LENGTH


def parse_website_html(html: str) -> dict[str, Any]:
    """Returns a dict of raw signals. Never raises on malformed HTML —
    BeautifulSoup with lxml is tolerant by design; a genuinely empty/broken
    document just yields UNKNOWN/False/None fields, which is the correct
    outcome (absence of evidence, not an error)."""
    soup = BeautifulSoup(html, "lxml")
    lowered = html.lower()

    technology = WebsiteTechnology.UNKNOWN
    for tech, needles in _TECHNOLOGY_SIGNATURES:
        if any(needle in lowered for needle in needles):
            technology = tech
            break

    generator_tag = soup.find("meta", attrs={"name": "generator"})
    generator_content = generator_tag.get("content") if generator_tag else None

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    description_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = description_tag.get("content") if description_tag else None

    has_chatbot = any(needle in lowered for needle in _CHATBOT_SIGNATURES)
    has_search = any(needle in lowered for needle in _SEARCH_SIGNATURES)

    return {
        "technology": technology.value,
        "generator_meta_tag": generator_content,
        "title": title,
        "meta_description": meta_description,
        "has_chatbot": has_chatbot,
        "has_search": has_search,
    }
