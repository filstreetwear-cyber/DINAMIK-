"""
IntelligenceProvider — the abstraction the refactor doc asks for (sections
2-13): the core agent talks to a Capability, never to "Keepa" or "Helium10"
by name. A concrete provider (WebsiteIntelligenceProvider, AmazonProvider,
...) implements this and declares which capabilities it can serve; the
ProviderRegistry and Orchestrator (registry.py, orchestrator.py) are the
only things that know provider names exist.

Design notes that matter for anyone adding a provider later:

* `fetch()` returns a list of ProviderResult, not a single blob — one call
  can legitimately produce several independent claims (e.g. a website crawl
  produces WEBSITE_DATA about platform, chat widget presence, and SEO
  signals in one HTTP round trip). Each ProviderResult is later written as
  its own Evidence row, so each needs its own confidence and data_type.
* A provider MUST NOT invent a FACT it did not directly observe. If a
  provider is only estimating (parsing a heuristic, not reading a
  documented field), it sets data_type=ESTIMATE or INFERENCE itself, not
  the caller — the provider is the only thing that knows how it got the
  number. This is the same discipline as section 15 of the original spec,
  just enforced one layer down.
* Providers never raise for "no data found" (that's a valid, non-exceptional
  result: `fetch()` returns `[]`). The exceptions below are for the
  provider itself being unusable right now — the distinction the
  Orchestrator needs to decide whether to fall back to another provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.enums import AcquisitionMethod, DataType, ToolHealthStatus
from app.tools.core.capability import Capability


class ProviderError(Exception):
    """Base for all "this provider could not serve this call right now"
    errors. The Orchestrator catches this family to decide on fallback;
    anything else (a bug, a programming error) is left to propagate."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        self.message = message
        super().__init__(f"{provider}: {message}")


class ProviderUnavailableError(ProviderError):
    """Provider is down/unreachable/not implemented for this capability."""


class ProviderAuthError(ProviderError):
    """Provider rejected the call for credential reasons (bad/expired key)."""


class ProviderNotConfiguredError(ProviderError):
    """Provider has no credentials/config at all — distinct from AuthError,
    which implies a call was actually attempted and rejected."""


class ProviderRateLimitedError(ProviderError):
    """Provider is reachable but is throttling; retry_after is informational,
    not a promise the orchestrator has to honor."""

    def __init__(self, provider: str, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(provider, message)
        self.retry_after_seconds = retry_after_seconds


class ProviderRequiresHumanError(ProviderError):
    """The provider needs a human step (login/CAPTCHA/MFA) before this call
    can proceed. This is a real, first-class outcome — never caught and
    silently retried as if it were a transient failure."""


@dataclass(frozen=True)
class ProviderResult:
    """One claim produced by one provider call. Maps close to 1:1 onto an
    Evidence row (app/models/evidence.py) — the service layer that writes
    Evidence is expected to pass these fields through almost unchanged."""

    capability: Capability
    data: dict[str, Any]
    data_type: DataType
    confidence: float  # 0-100, the PROVIDER's own estimate before any orchestrator/AI adjustment
    acquisition_method: AcquisitionMethod
    observed_at: datetime
    source_url: str | None = None
    raw_value: str | None = None
    cost_estimate: float | None = None
    is_mock: bool = False
    """True when this result came from fixture/mock data (MOCK_MODE), not a
    real external call. Kept separate from `data_type` on purpose:
    data_type answers "how sure are we this claim is true", is_mock answers
    "is this even about a real product". A FACT can be mock (a fixture
    stating a fake ASIN's fake price, stated with total confidence about
    the fixture) — collapsing the two would let mock output masquerade as
    a genuine low-confidence-but-real claim, or vice versa. The service
    layer that writes Evidence rows MUST check this before Milestone 5+
    ever treats a research job's output as real."""


class IntelligenceProvider(ABC):
    """One concrete data source. `name` must be stable — it is what gets
    written to Evidence.provider and ToolCall.provider, so renaming it is a
    silent data-continuity break, not just a refactor."""

    name: str
    supported_capabilities: frozenset[Capability] = frozenset()

    @abstractmethod
    async def health(self) -> ToolHealthStatus:
        """Cheap, side-effect-free check of whether this provider can
        currently serve calls at all. Must not raise — a failing health
        check reports a status, it doesn't throw."""

    @abstractmethod
    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        """Raises one of the ProviderError subclasses above on failure.
        Returns an empty list (not an exception) when the call succeeded
        but found nothing."""

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported_capabilities
