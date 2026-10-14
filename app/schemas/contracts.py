"""
Data Contracts — Milestone 8.5. A normalized, source-independent shape for
each kind of intelligence this project deals with, distinct from BOTH the
persisted ORM row (app/models/*.py — has FKs, timestamps, DB-only concerns)
and a single provider's raw ProviderResult (app/tools/core/provider.py —
one call's worth of unstructured `dict[str, Any]`, tagged with
confidence/data_type but not yet shaped into a stable schema). A contract
is what a consumer downstream of acquisition (opportunity engine, a future
AI layer, an export) should depend on instead of reaching into either of
those two layers directly.

This does not replace the ORM models or ProviderResult — both stay exactly
as they are (Milestone 8.5's own "prefer incremental refactoring, preserve
existing working functionality" rule). It adds a translation layer with
real, tested `from_orm()` conversions for the three contracts this project
actually has data for. `opportunity_engine.py` is NOT migrated onto these
in this pass — it already works against `AmazonProduct`/`AmazonSeller`
rows directly, with its own tests; forcing that migration now would touch
working code for a cosmetic gain, which is exactly what the audit
(docs/architecture/milestone-8.5-audit.md) recommended against. These
contracts exist as the abstraction a FUTURE consumer (a second agent, an
export, an AI layer) should use, proven correct by real conversions and
tests today.

Pydantic, not dataclasses — consistent with this project's existing schema
layer intent (`app/schemas/` was reserved, empty, for exactly this; Settings
itself is pydantic, app/core/config.py) and because a contract crossing a
layer boundary benefits from real validation, not just type hints.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.amazon import AmazonListing, AmazonProduct
from app.models.website import WebsiteProfile


class AmazonProductContract(BaseModel):
    """Normalized view of a product, independent of whether it came from
    the `amazon` provider, the `keepa` provider, or a persisted
    AmazonProduct row — same shape either way."""

    model_config = ConfigDict(frozen=True)

    asin: str
    marketplace: str
    title: str
    category: str | None = None
    subcategory: str | None = None
    price: float | None = None
    currency: str | None = None
    rating: float | None = None
    review_count: int | None = None
    bsr: int | None = None

    @classmethod
    def from_orm_product(cls, product: AmazonProduct) -> AmazonProductContract:
        return cls(
            asin=product.asin,
            marketplace=product.marketplace,
            title=product.title,
            category=product.category,
            subcategory=product.subcategory,
            price=float(product.price) if product.price is not None else None,
            currency=product.currency,
            rating=float(product.rating) if product.rating is not None else None,
            review_count=product.review_count,
            bsr=product.bsr,
        )


class AmazonListingContract(BaseModel):
    """Point-in-time listing-content snapshot — mirrors AmazonListing.
    Exists as a contract even though nothing populates AmazonListing rows
    yet (see the audit, section 2): the SHAPE is real and tested via
    from_orm_listing(); what's missing is an acquisition path that writes
    AmazonListing rows in the first place, a separate, larger gap this
    contract does not paper over."""

    model_config = ConfigDict(frozen=True)

    product_asin: str
    bullet_points: list[str] | None = None
    description: str | None = None
    has_a_plus_content: bool = False
    image_count: int | None = None
    video_count: int | None = None
    sponsored_seen: bool = False
    observed_at: datetime

    @classmethod
    def from_orm_listing(cls, listing: AmazonListing, *, product_asin: str) -> AmazonListingContract:
        return cls(
            product_asin=product_asin,
            bullet_points=listing.bullet_points,
            description=listing.description,
            has_a_plus_content=listing.has_a_plus_content,
            image_count=listing.image_count,
            video_count=listing.video_count,
            sponsored_seen=listing.sponsored_seen,
            observed_at=listing.observed_at,
        )


class WebsiteDataContract(BaseModel):
    """Mirrors WebsiteProfile — the one contract in this file backed by a
    provider that is actually real and non-mock today
    (WebsiteIntelligenceProvider, docs/providers.md)."""

    model_config = ConfigDict(frozen=True)

    domain: str
    technology: str = "UNKNOWN"
    has_chatbot: bool | None = None
    has_product_recommendation: bool | None = None
    mobile_friendly: bool | None = None
    page_speed_score: int | None = None
    seo_score: int | None = None
    last_crawled_at: datetime | None = None

    @classmethod
    def from_orm_profile(cls, profile: WebsiteProfile) -> WebsiteDataContract:
        return cls(
            domain=profile.domain,
            technology=profile.technology,
            has_chatbot=profile.has_chatbot,
            has_product_recommendation=profile.has_product_recommendation,
            mobile_friendly=profile.mobile_friendly,
            page_speed_score=profile.page_speed_score,
            seo_score=profile.seo_score,
            last_crawled_at=profile.last_crawled_at,
        )


class AdvertisingDataContract(BaseModel):
    """NOT POPULATED ANYWHERE IN THIS CODEBASE — the shape exists so a
    future ADVERTISING_OPPORTUNITY implementation has a contract to target,
    per Milestone 8.5's request for the "architectural path", not because
    any provider produces this today. No `from_orm_*`/`from_provider_*`
    factory exists on purpose: writing one would need a real data source to
    validate against, and there is none (no PPC_DATA capability is ever
    called by any agent — confirmed in the Milestone 8.5 audit, section 2).
    Constructing one of these directly, with invented numbers, is exactly
    the "do not fabricate completeness" failure mode Milestone 8.5
    explicitly forbids — nothing in this codebase does that; this class is
    a shape, not a population mechanism."""

    model_config = ConfigDict(frozen=True)

    STATUS: str = "NOT_IMPLEMENTED"

    asin: str
    sponsored_placements_observed: int | None = None
    ppc_keywords: list[str] | None = None
    estimated_ad_spend: float | None = None
    observed_at: datetime


class SellerFastFitContract(BaseModel):
    """NOT FORMALLY SPECIFIED — Seller Fast's fit criteria were never given
    by Frank, confirmed independently in three places: the original
    refactor documentation, docs/architecture.md, and Fil's own notes
    (Seller Fast / sellerfast.tech is Frank's separate AI shopping-assistant
    product, not a documented lead-qualification rubric). This class exists
    only to give SELLER_FAST_FIT a named shape for when criteria ARE
    specified — every field below is a structural guess at what a "fit"
    assessment would need to carry (a score, reasons, evidence), not a
    business rule. No factory function exists; nothing in this codebase
    constructs one. Milestone 8.5 explicitly asked that no criteria be
    invented here, and none are — see docs/architecture/opportunity-data-matrix.md."""

    model_config = ConfigDict(frozen=True)

    STATUS: str = "NOT_FORMALLY_SPECIFIED"

    brand_id: str
    fit_score: float | None = None
    reasons: list[str] | None = None
    observed_at: datetime
