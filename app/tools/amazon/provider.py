"""
AmazonProvider — mock-only today. No real Amazon discovery integration
exists: SP-API is account-scoped (useless for prospect discovery, see
docs/architecture.md section 0) and PA-API is Associates-gated. This
provider exists so the Capability/Orchestrator layer has something real to
route PRODUCT_SEARCH/PRODUCT_DATA/PRODUCT_PRICE/SALES_RANK/SELLER_DATA/BRAND_DATA
to in MOCK_MODE, and so a real scraping/API integration can be dropped in
later by replacing this file's `fetch()` body without touching the
orchestrator, the registry, or anything upstream.

PRODUCT_SEARCH added 2026-09-21 for Milestone 5 (Amazon Scout discovery) —
see `_search()` below and `Capability.PRODUCT_SEARCH`'s docstring
(app/models/enums.py) for why this needed a new capability rather than
reusing an existing one.

health() deliberately returns CONFIGURATION_ERROR outside MOCK_MODE, not
UNAVAILABLE — UNAVAILABLE implies "this is wired up and currently down";
the honest state here is "this was never wired up to anything real".
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.models.enums import AcquisitionMethod, DataType, ToolHealthStatus
from app.tools.core.capability import Capability
from app.tools.core.provider import (
    IntelligenceProvider,
    ProviderNotConfiguredError,
    ProviderResult,
    ProviderUnavailableError,
)

_FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "amazon_mock_products.json"

_SUPPORTED = frozenset(
    {
        Capability.PRODUCT_SEARCH,
        Capability.PRODUCT_DATA,
        Capability.PRODUCT_PRICE,
        Capability.SALES_RANK,
        Capability.SELLER_DATA,
        Capability.BRAND_DATA,
    }
)


def _load_fixtures() -> dict[str, dict[str, Any]]:
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


class AmazonProvider(IntelligenceProvider):
    name = "amazon"
    supported_capabilities = _SUPPORTED

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def health(self) -> ToolHealthStatus:
        if self._settings.mock_mode:
            return ToolHealthStatus.AVAILABLE
        return ToolHealthStatus.CONFIGURATION_ERROR

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        if capability not in _SUPPORTED:
            raise ProviderUnavailableError(self.name, f"does not support {capability.value}")
        if not self._settings.mock_mode:
            raise ProviderNotConfiguredError(
                self.name, "no real Amazon discovery integration is configured yet"
            )

        if capability is Capability.PRODUCT_SEARCH:
            return self._search(params)

        asin = params.get("asin")
        if not asin:
            raise ProviderUnavailableError(self.name, "fetch() requires an 'asin' parameter")

        fixtures = _load_fixtures()
        product = fixtures.get(asin)
        if product is None:
            return []

        observed_at = datetime.now(UTC)
        data_by_capability: dict[Capability, dict[str, Any]] = {
            Capability.PRODUCT_DATA: {
                "title": product["title"],
                "category": product["category"],
                "rating": product["rating"],
                "review_count": product["review_count"],
            },
            Capability.PRODUCT_PRICE: {"price": product["price"], "currency": product["currency"]},
            Capability.SALES_RANK: {"bsr": product["bsr"]},
            Capability.SELLER_DATA: {"seller_id": product["seller_id"], "seller_name": product["seller_name"]},
            Capability.BRAND_DATA: {"brand": product["brand"]},
        }
        payload = data_by_capability.get(capability)
        if payload is None:
            return []

        return [
            ProviderResult(
                capability=capability,
                data=payload,
                data_type=DataType.FACT,  # fixture states it as ground truth of the fake catalog, not a guess
                confidence=100.0,
                acquisition_method=AcquisitionMethod.IMPORT,
                observed_at=observed_at,
                is_mock=True,
            )
        ]

    def _search(self, params: dict[str, Any]) -> list[ProviderResult]:
        """Milestone 5 discovery entry point: category/marketplace IN,
        candidate ASINs OUT — the one capability here that goes the
        opposite direction of every other lookup in this file.

        `keywords` is accepted (so a caller matching the refactor doc's
        param shape doesn't get a TypeError) but not matched against
        anything: the fixture has no free-text fields worth searching, and
        pretending it does would fabricate a capability this mock doesn't
        actually have. category/marketplace are real filters against the
        fixture's own fields.

        Unlike the per-ASIN lookups above, an empty match list is still a
        real, substantive answer ("search ran, found nothing for this
        category") — not "we don't know this entity" — so this always
        returns one ProviderResult, never `[]`.
        """
        fixtures = _load_fixtures()
        category = params.get("category")
        marketplace = params.get("marketplace")

        matches = [
            product
            for product in fixtures.values()
            if (category is None or product["category"] == category)
            and (marketplace is None or product["marketplace"] == marketplace)
        ]
        asins = [product["asin"] for product in matches]

        return [
            ProviderResult(
                capability=Capability.PRODUCT_SEARCH,
                data={"asins": asins},
                data_type=DataType.FACT,
                confidence=100.0,
                acquisition_method=AcquisitionMethod.IMPORT,
                observed_at=datetime.now(UTC),
                is_mock=True,
            )
        ]
