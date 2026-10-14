"""
Thin async HTTP fetch wrapper for Website Intelligence. Kept separate from
provider.py so it can be swapped for a test double without touching
parsing logic, and separate from html_parser.py so the parser stays
network-free and trivially unit-testable.

This is the HTTP acquisition tier only — the refactor doc's escalation
path (HTTP → HTML rendering → JS execution → browser) stops here for now.
A site that needs JS rendering to reveal its real content returns thin/
empty HTML today and the provider reports low confidence accordingly; the
next tiers plug into the same WebsiteIntelligenceProvider interface later
without changing its callers.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

_DEFAULT_TIMEOUT_SECONDS = 10.0
_USER_AGENT = "DinamikAdvisorAmazonIntelligence/1.0 (+website-intelligence)"


class WebsiteFetchError(Exception):
    """Raised for any failure to retrieve a page: DNS/connect/timeout, or a
    non-2xx status. provider.py maps this to a ProviderError subclass —
    this module itself has no knowledge of the provider abstraction."""


@dataclass(frozen=True)
class FetchedPage:
    url: str
    status_code: int
    html: str


async def fetch_page(url: str, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> FetchedPage:
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise WebsiteFetchError(f"could not fetch {url}: {exc}") from exc

    if response.status_code >= 400:
        raise WebsiteFetchError(f"{url} returned HTTP {response.status_code}")

    return FetchedPage(url=str(response.url), status_code=response.status_code, html=response.text)
