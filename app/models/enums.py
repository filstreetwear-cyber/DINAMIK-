"""
Application-level vocabularies (section 11, 15, 22, 37, 44 of the spec).

These back plain String columns (see base.py for why) — they exist for
validation in the service layer and structured-output schemas, not as
Postgres native types.
"""
from __future__ import annotations

import enum


class ResearchJobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ToolHealthStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_ERROR = "AUTH_ERROR"
    # Added 2026-09-21 (refactor: Helium10 Optional + Provider/Capability) —
    # a provider-level "UNAVAILABLE" was never precise enough once a single
    # provider can support several capabilities with different states.
    AUTH_REQUIRED = "AUTH_REQUIRED"  # credentials known to be missing/not configured, distinct from AUTH_ERROR (a call was attempted and rejected)
    REQUIRES_HUMAN = "REQUIRES_HUMAN"  # e.g. a browser session needs a human login/CAPTCHA/MFA step — never bypassed
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"  # provider is enabled but misconfigured (bad mode, missing required setting)


class EvidenceSourceType(str, enum.Enum):
    AMAZON = "AMAZON"
    KEEPA = "KEEPA"
    JUNGLESCOUT = "JUNGLESCOUT"
    PERPETUA = "PERPETUA"
    HELIUM10 = "HELIUM10"
    WEBSITE = "WEBSITE"
    AI_INFERENCE = "AI_INFERENCE"
    USER = "USER"


class DataType(str, enum.Enum):
    """Section 15: the AI must never silently promote an ESTIMATE to a FACT."""

    FACT = "FACT"
    ESTIMATE = "ESTIMATE"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"


class ProspectQualification(str, enum.Enum):
    AUTO_QUALIFIED = "AUTO_QUALIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OpportunityType(str, enum.Enum):
    AMAZON = "AMAZON_OPPORTUNITY"
    LISTING = "LISTING_OPPORTUNITY"
    ADVERTISING = "ADVERTISING_OPPORTUNITY"
    WEBSITE = "WEBSITE_OPPORTUNITY"
    SELLER_FAST_FIT = "SELLER_FAST_FIT"
    BUSINESS_VALUE = "BUSINESS_VALUE"


class OpportunityLevel(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    POSSIBLE = "POSSIBLE"
    NONE = "NONE"


class ErrorCategory(str, enum.Enum):
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    PARSING_ERROR = "PARSING_ERROR"
    AI_ERROR = "AI_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    UNKNOWN = "UNKNOWN"


class WebsiteTechnology(str, enum.Enum):
    WOOCOMMERCE = "WOOCOMMERCE"
    SHOPIFY = "SHOPIFY"
    WORDPRESS_OTHER = "WORDPRESS_OTHER"
    MAGENTO = "MAGENTO"
    CUSTOM = "CUSTOM"
    UNKNOWN = "UNKNOWN"


class Capability(str, enum.Enum):
    """What a piece of data IS, independent of who supplies it or how
    (refactor doc, sections 2-13). The orchestrator routes on this, never
    on a hardcoded provider name."""

    # Added 2026-09-21 for Milestone 5 (Amazon Scout discovery): every
    # other capability here is a per-ASIN lookup, but discovery needs to
    # go the other direction — category/keyword IN, candidate ASINs OUT.
    # No real Amazon/Keepa/Jungle Scout API gives Dinamik open product
    # search today (see docs/architecture.md section 0) — this stays
    # mock-only until a real search-capable provider is confirmed.
    PRODUCT_SEARCH = "PRODUCT_SEARCH"
    PRODUCT_DATA = "PRODUCT_DATA"
    PRODUCT_PRICE = "PRODUCT_PRICE"
    PRICE_HISTORY = "PRICE_HISTORY"
    SALES_RANK = "SALES_RANK"
    SELLER_DATA = "SELLER_DATA"
    BRAND_DATA = "BRAND_DATA"
    KEYWORD_DATA = "KEYWORD_DATA"
    SEARCH_VOLUME = "SEARCH_VOLUME"
    COMPETITOR_DATA = "COMPETITOR_DATA"
    MARKET_DATA = "MARKET_DATA"
    WEBSITE_DATA = "WEBSITE_DATA"
    PPC_DATA = "PPC_DATA"


class AcquisitionMethod(str, enum.Enum):
    """HOW a provider was actually reached for one call/task — kept
    separate from Capability (what) and provider (who) so Evidence/ToolCall
    rows carry full provenance without three separate lookups."""

    API = "API"
    BROWSER = "BROWSER"
    HTTP = "HTTP"
    HTML = "HTML"
    MANUAL = "MANUAL"
    AI = "AI"
    IMPORT = "IMPORT"


class BrowserTaskStatus(str, enum.Enum):
    """Mirrors ResearchJobStatus but adds WAITING_FOR_HUMAN (refactor doc):
    a real, first-class state for logins/CAPTCHA/MFA — never silently
    bypassed, never conflated with FAILED."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BrowserSessionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REQUIRES_LOGIN = "REQUIRES_LOGIN"
    REVOKED = "REVOKED"


class ProviderMode(str, enum.Enum):
    """Per-provider acquisition mode, config/default.yaml `providers:`
    section (Phase H). Validated today; not yet consumed by any provider's
    logic — reserved for Phase G, when a single provider name (Helium10)
    needs to pick between API/BROWSER/MANUAL implementations from config
    rather than the caller hardcoding which class to instantiate."""

    API = "api"
    BROWSER = "browser"
    MANUAL = "manual"
    DISABLED = "disabled"
