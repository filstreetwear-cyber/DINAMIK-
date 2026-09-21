"""
KeepaProvider — mock-only today, same reasoning as app/tools/amazon/
provider.py: no Keepa credentials confirmed for Dinamik (docs/architecture.md
section 0 / README connector table), so this exists to give the
Capability/Orchestrator layer a second real provider to route PRICE_HISTORY
and SALES_RANK to — which is also what makes the orchestrator's fallback
behavior (Amazon fails for SALES_RANK -> try Keepa) actually testable
against two independent implementations, not just a single mocked path.
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

_FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "keepa_mock_history.json"

_SUPPORTED = frozenset({Capability.PRICE_HISTORY, Capability.SALES_RANK, Capability.PRODUCT_PRICE})


def _load_fixtures() -> dict[str, dict[str, Any]]:
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


class KeepaProvider(IntelligenceProvider):
    name = "keepa"
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
            raise ProviderNotConfiguredError(self.name, "no Keepa API key is configured yet")

        asin = params.get("asin")
        if not asin:
            raise ProviderUnavailableError(self.name, "fetch() requires an 'asin' parameter")

        fixtures = _load_fixtures()
        history = fixtures.get(asin)
        if history is None:
            return []

        observed_at = datetime.now(UTC)
        data_by_capability: dict[Capability, dict[str, Any]] = {
            Capability.PRICE_HISTORY: {"price_history_90d": history["price_history_90d"]},
            Capability.SALES_RANK: {
                "bsr_history_90d": history["bsr_history_90d"],
                "sales_rank_drops_30d": history["sales_rank_drops_30d"],
            },
            Capability.PRODUCT_PRICE: {"price": history["price_history_90d"][-1]},
        }
        payload = data_by_capability.get(capability)
        if payload is None:
            return []

        return [
            ProviderResult(
                capability=capability,
                data=payload,
                data_type=DataType.FACT,
                confidence=100.0,
                acquisition_method=AcquisitionMethod.IMPORT,
                observed_at=observed_at,
                is_mock=True,
            )
        ]
