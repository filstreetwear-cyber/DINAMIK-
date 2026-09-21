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
