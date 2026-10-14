"""
Opportunity engine — Milestone 8.

Deterministic rules only, no AI (Milestone 10). Given a `Brand` with its
linked `AmazonProduct`/`AmazonSeller` rows (populated by Milestone 5/6/7),
evaluates whether that brand looks like a sales-worthy opportunity and
writes `Opportunity` rows — `opportunity_type`/`level`/`rule_triggered`/
`reasons`, per the model's own section-30 docstring.

**Three of the six `OpportunityType` values are implemented here:
`AMAZON_OPPORTUNITY`, `BUSINESS_VALUE`, and (Fase 3/4, 28/09)
`WEBSITE_OPPORTUNITY`.** `config/default.yaml`'s `scoring.weights` already
carries an entry for all six (Milestone 9's job, consuming `Score` rows
this module does not write) — but a weight existing in config for a
future scoring step is not the same claim as "this milestone can evaluate
that dimension today". The other three need data this pipeline genuinely
does not have yet, and inventing a rule against data that doesn't exist
would be exactly the kind of overclaimed-confidence shortcut this project
has been careful to avoid at every prior milestone (see
`brand_resolution.py`'s `verified` discipline, `brand_deduplication.py`'s
two-tier merge caution):

* **`LISTING_OPPORTUNITY`** needs listing content quality — bullet points,
  A+ content, image/video count. That's `AmazonListing`
  (`app/models/amazon.py`), and nothing in Milestone 5's enrichment
  capabilities (`PRODUCT_DATA`/`SELLER_DATA`/`BRAND_DATA`/`SALES_RANK`)
  populates it — not one `AmazonListing` row exists anywhere in this
  pipeline yet. **Aggiornamento Anello 1 (2026-10-14)**: lo scout live
  (`app/services/scout_runner.py`) ora scrive snapshot `AmazonListing`
  reali; `evaluate_listing_opportunity` qui sotto è implementata.
* **`ADVERTISING_OPPORTUNITY`** needs ad-sponsorship signals
  (`AmazonListing.sponsored_seen` or real ad spend data) — same gap.
* **`SELLER_FAST_FIT`** needs Dinamik's own fit criteria for their
  "Seller Fast" service line, which were never specified anywhere in the
  refactor document or `docs/architecture.md` — there is nothing
  deterministic to encode without guessing at Frank's actual business
  criteria, which is worse than not building it.

Building placeholder rules for these three now would produce Opportunity
rows that look evaluated but aren't grounded in anything — worse than not
having them. `LISTING_OPPORTUNITY`/`ADVERTISING_OPPORTUNITY` need a new
discovery capability entirely; `SELLER_FAST_FIT` needs Frank's actual
criteria.

**`WEBSITE_OPPORTUNITY` is real and tested (`evaluate_website_opportunity`
below, fed by `WebsiteIntelligenceProvider`'s `mobile_friendly`/
`page_speed_score`/`seo_score` signals, Fase 3/4) but has NO live input
yet, and will not fire in practice until a separate gap closes**: nothing
in this pipeline populates `Brand.website` (discovery never learns a
brand's website URL) or writes a `WebsiteProfile` row from the provider's
output — `_evaluate_brand` below queries `WebsiteProfile` by `brand_id`
and gets `None` for every brand today, so this rule always returns `None`
(same as `evaluate_amazon_opportunity([])` for a brand with zero
products) until that ingestion wiring is built. Flagged explicitly rather
than left to be discovered later — the same discipline `AmazonScrapingProvider`
followed for its own missing DB-ingestion half (see its module docstring).

**`Opportunity` is treated as an append-only time series, like
`AgentEvent`** — there's no unique constraint on `(brand_id,
opportunity_type)` in the schema, and re-running this engine over the same
brand writes a NEW row rather than updating one in place, so a later
consumer (CLI/API, not built yet) can see how an assessment changed over
time instead of only its current state. The same reasoning `Score` (this
milestone doesn't touch it) is built for.

An assessed brand always gets a row per implemented type, even when the
level is `NONE` ("checked, no signal found") — a real "we evaluated this
and found nothing" answer, not silence, following the same principle
`AmazonProvider._search()` uses for an empty category match. A brand with
zero linked `AmazonProduct` rows gets no `AMAZON_OPPORTUNITY`/
`BUSINESS_VALUE` row at all -- there is nothing to evaluate, not even a
null result -- but (Fase 3/4) it can still get a `WEBSITE_OPPORTUNITY`
row if a `WebsiteProfile` exists for it: website quality is independent
of whether this pipeline has any Amazon product data for that brand, so
the two are no longer coupled the way the original two-rule version of
this module was.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import (
    AgentEvent,
    AgentRun,
    AmazonListing,
    AmazonProduct,
    AmazonSeller,
    Brand,
    ErrorLog,
    Opportunity,
    WebsiteProfile,
)
from app.models.enums import ErrorCategory, OpportunityLevel, OpportunityType

_DEFAULT_MIN_RATING_THRESHOLD = 4.2
_DEFAULT_MIN_REVIEW_COUNT_THRESHOLD = 50
_DEFAULT_PRODUCT_COUNT_MEDIUM_THRESHOLD = 3
_DEFAULT_PRODUCT_COUNT_HIGH_THRESHOLD = 8
_DEFAULT_REVIEW_COUNT_HIGH_THRESHOLD = 500
_DEFAULT_MIN_PAGE_SPEED_SCORE_THRESHOLD = 50
_DEFAULT_MIN_SEO_SCORE_THRESHOLD = 50
_DEFAULT_LISTING_RULES: dict[str, float | int | bool] = {
    "min_image_count": 6,
    "min_bullet_count": 5,
    "min_title_length": 60,
    "max_title_length": 200,
    "require_video": True,
    "require_a_plus": True,
    "min_issues_per_product": 2,
    "high_weak_share": 0.5,
}


@dataclass
class RuleResult:
    level: OpportunityLevel
    rule_triggered: str
    reasons: list[str]


def evaluate_amazon_opportunity(products: list[AmazonProduct], settings: Settings) -> RuleResult | None:
    """Room-for-improvement signal from what real product data exists:
    weak average rating and/or a thin review base despite the product
    actually being live and enriched. Returns None only when `products`
    is empty (nothing to evaluate at all) -- a brand with products but no
    rating/review data still gets a RuleResult (level NONE, rule
    "insufficient_product_data"), because that's a real, distinct outcome
    from "never evaluated"."""
    if not products:
        return None

    cfg = settings.opportunity_rule_config("amazon_opportunity")
    rating_threshold = float(cfg.get("min_rating_threshold", _DEFAULT_MIN_RATING_THRESHOLD))
    review_threshold = int(cfg.get("min_review_count_threshold", _DEFAULT_MIN_REVIEW_COUNT_THRESHOLD))

    rated_values = [float(p.rating) for p in products if p.rating is not None]
    if not rated_values:
        return RuleResult(OpportunityLevel.NONE, "insufficient_product_data", [])

    avg_rating = sum(rated_values) / len(rated_values)
    total_reviews = sum(p.review_count or 0 for p in products)

    reasons: list[str] = []
    if avg_rating < rating_threshold:
        reasons.append(f"average_rating={avg_rating:.2f} is below threshold {rating_threshold}")
    if total_reviews < review_threshold:
        reasons.append(f"total_reviews={total_reviews} is below threshold {review_threshold}")

    if len(reasons) >= 2:
        level = OpportunityLevel.HIGH
    elif len(reasons) == 1:
        level = OpportunityLevel.MEDIUM
    else:
        level = OpportunityLevel.NONE

    return RuleResult(level, "rating_and_review_volume_v1", reasons)


def evaluate_business_value(
    products: list[AmazonProduct], sellers: list[AmazonSeller], settings: Settings
) -> RuleResult | None:
    """Rough proxy for account size/potential engagement value from
    product count and total review volume -- not revenue, since Milestone
    5's enrichment never fetches PRODUCT_PRICE (see brand_resolution.py's
    own docstring) and price/currency stay NULL on every AmazonProduct
    this pipeline creates. Never returns NONE for a brand with products:
    at minimum a single-product brand is a real, if small, business."""
    if not products:
        return None

    cfg = settings.opportunity_rule_config("business_value")
    product_count_medium = int(cfg.get("product_count_medium_threshold", _DEFAULT_PRODUCT_COUNT_MEDIUM_THRESHOLD))
    product_count_high = int(cfg.get("product_count_high_threshold", _DEFAULT_PRODUCT_COUNT_HIGH_THRESHOLD))
    review_count_high = int(cfg.get("review_count_high_threshold", _DEFAULT_REVIEW_COUNT_HIGH_THRESHOLD))

    product_count = len(products)
    total_reviews = sum(p.review_count or 0 for p in products)
    seller_count = len(sellers)

    reasons: list[str] = []
    if product_count >= product_count_high:
        reasons.append(f"product_count={product_count} meets high threshold {product_count_high}")
    if total_reviews >= review_count_high:
        reasons.append(f"total_reviews={total_reviews} meets high threshold {review_count_high}")

    if reasons:
        level = OpportunityLevel.HIGH
    elif product_count >= product_count_medium:
        level = OpportunityLevel.MEDIUM
        reasons.append(f"product_count={product_count} meets medium threshold {product_count_medium}")
    else:
        level = OpportunityLevel.LOW
        reasons.append(f"product_count={product_count} is below medium threshold {product_count_medium}")

    if seller_count > 1:
        reasons.append(f"seller_count={seller_count} -- multi-seller presence")

    return RuleResult(level, "product_and_review_footprint_v1", reasons)


def evaluate_website_opportunity(website_profile: WebsiteProfile | None, settings: Settings) -> RuleResult | None:
    """Fase 3/4 (28/09) — room-for-improvement signal from the one real,
    non-mock provider this project has (WebsiteIntelligenceProvider):
    a site that isn't mobile-friendly and/or scores poorly on Lighthouse's
    performance/SEO categories is a legitimate reason to reach out with a
    "your website is costing you sales" pitch, the same shape as
    evaluate_amazon_opportunity's rating/review-volume signal.

    Returns None only when `website_profile` is None -- no WebsiteProfile
    row exists for this brand at all (today: every brand, see this
    module's docstring on the missing ingestion wiring), which is a
    distinct "never looked" outcome from "looked, found nothing" (level
    NONE, rule "insufficient_website_data" -- mirrors
    evaluate_amazon_opportunity's `rated_values` empty-list branch, for a
    WebsiteProfile row that exists but has all three signal fields
    unmeasured, e.g. because include_performance_audit was never
    requested for that fetch)."""
    if website_profile is None:
        return None

    cfg = settings.opportunity_rule_config("website_opportunity")
    page_speed_threshold = int(cfg.get("min_page_speed_score_threshold", _DEFAULT_MIN_PAGE_SPEED_SCORE_THRESHOLD))
    seo_threshold = int(cfg.get("min_seo_score_threshold", _DEFAULT_MIN_SEO_SCORE_THRESHOLD))

    has_any_signal = (
        website_profile.mobile_friendly is not None
        or website_profile.page_speed_score is not None
        or website_profile.seo_score is not None
    )
    if not has_any_signal:
        return RuleResult(OpportunityLevel.NONE, "insufficient_website_data", [])

    reasons: list[str] = []
    if website_profile.mobile_friendly is False:
        reasons.append("mobile_friendly=False -- no responsive viewport detected")
    if website_profile.page_speed_score is not None and website_profile.page_speed_score < page_speed_threshold:
        reasons.append(
            f"page_speed_score={website_profile.page_speed_score} is below threshold {page_speed_threshold}"
        )
    if website_profile.seo_score is not None and website_profile.seo_score < seo_threshold:
        reasons.append(f"seo_score={website_profile.seo_score} is below threshold {seo_threshold}")

    if len(reasons) >= 2:
        level = OpportunityLevel.HIGH
    elif len(reasons) == 1:
        level = OpportunityLevel.MEDIUM
    else:
        level = OpportunityLevel.NONE

    return RuleResult(level, "mobile_and_performance_signals_v1", reasons)


def listing_issues(listing: AmazonListing, cfg: dict[str, object]) -> list[str]:
    """Problemi concreti di UNA scheda, ciascuno verificabile aprendo
    l'evidenza (HTML/screenshot) della stessa osservazione."""
    rules = {**_DEFAULT_LISTING_RULES, **cfg}
    issues: list[str] = []
    min_images = int(rules["min_image_count"])  # type: ignore[call-overload]
    if listing.image_count is not None and listing.image_count < min_images:
        issues.append(f"immagini={listing.image_count}<{min_images}")
    bullets = len(listing.bullet_points or [])
    min_bullets = int(rules["min_bullet_count"])  # type: ignore[call-overload]
    if bullets < min_bullets:
        issues.append(f"bullet={bullets}<{min_bullets}")
    if listing.title_length is not None:
        if listing.title_length < int(rules["min_title_length"]):  # type: ignore[call-overload]
            issues.append(f"titolo_corto={listing.title_length}")
        elif listing.title_length > int(rules["max_title_length"]):  # type: ignore[call-overload]
            issues.append(f"titolo_lungo={listing.title_length}")
    if rules["require_video"] and not listing.video_count:
        issues.append("nessun_video")
    if rules["require_a_plus"] and not listing.has_a_plus_content:
        issues.append("nessun_contenuto_A+")
    return issues


def evaluate_listing_opportunity(
    latest_listings: list[tuple[str, AmazonListing]], settings: Settings
) -> RuleResult | None:
    """LISTING_OPPORTUNITY (Anello 1, 2026-10-14) — fino a oggi "non
    implementabile" perché nessuno scriveva AmazonListing; lo scout live
    ora lo fa. Input: (ASIN, ultimo snapshot) per ogni prodotto del brand.
    None se il brand non ha alcuno snapshot (mai osservato dallo scout)."""
    if not latest_listings:
        return None
    cfg = settings.opportunity_rule_config("listing_opportunity")
    rules = {**_DEFAULT_LISTING_RULES, **cfg}
    min_issues = int(rules["min_issues_per_product"])  # type: ignore[call-overload]
    high_share = float(rules["high_weak_share"])  # type: ignore[arg-type]

    reasons: list[str] = []
    weak = 0
    for asin, listing in latest_listings:
        issues = listing_issues(listing, cfg)
        if len(issues) >= min_issues:
            weak += 1
            reasons.append(f"{asin}: " + ", ".join(issues))

    if weak == 0:
        return RuleResult(OpportunityLevel.NONE, "listing_quality_v1", [])
    share = weak / len(latest_listings)
    level = OpportunityLevel.HIGH if share >= high_share else OpportunityLevel.MEDIUM
    reasons.insert(0, f"schede_deboli={weak}/{len(latest_listings)}")
    return RuleResult(level, "listing_quality_v1", reasons)


async def _latest_listings_for_brand(session: AsyncSession, brand_id: uuid.UUID) -> list[tuple[str, AmazonListing]]:
    rows = (
        await session.execute(
            select(AmazonProduct.asin, AmazonListing)
            .join(AmazonListing, AmazonListing.product_id == AmazonProduct.id)
            .where(AmazonProduct.brand_id == brand_id)
            .order_by(AmazonProduct.asin, AmazonListing.observed_at.desc())
        )
    ).all()
    latest: dict[str, AmazonListing] = {}
    for asin, listing in rows:
        latest.setdefault(asin, listing)
    return list(latest.items())


@dataclass
class BrandOpportunityOutcome:
    brand_id: uuid.UUID
    opportunities_created: list[uuid.UUID] = field(default_factory=list)


@dataclass
class OpportunityEngineSummary:
    agent_run_id: uuid.UUID
    brands_considered: int
    opportunities_created: int
    truncated: bool = False
    """True when more than max_brands ACTIVE brands existed and the run
    only considered the first max_brands (ordered by first_seen_at) --
    same pattern as resolve_research_job's max_results and
    run_brand_deduplication's max_brands."""


async def _evaluate_brand(
    session: AsyncSession, agent_run_id: uuid.UUID, brand: Brand, settings: Settings
) -> BrandOpportunityOutcome:
    products = (
        (await session.execute(select(AmazonProduct).where(AmazonProduct.brand_id == brand.id)))
        .scalars()
        .all()
    )
    sellers = (
        (await session.execute(select(AmazonSeller).where(AmazonSeller.brand_id == brand.id)))
        .scalars()
        .all()
    )
    website_profile = (
        await session.execute(select(WebsiteProfile).where(WebsiteProfile.brand_id == brand.id))
    ).scalar_one_or_none()

    latest_listings = await _latest_listings_for_brand(session, brand.id)

    outcome = BrandOpportunityOutcome(brand_id=brand.id)

    for opportunity_type, rule_result in (
        (OpportunityType.AMAZON, evaluate_amazon_opportunity(list(products), settings)),
        (OpportunityType.BUSINESS_VALUE, evaluate_business_value(list(products), list(sellers), settings)),
        (OpportunityType.WEBSITE, evaluate_website_opportunity(website_profile, settings)),
        (OpportunityType.LISTING, evaluate_listing_opportunity(latest_listings, settings)),
    ):
        if rule_result is None:
            continue  # no products at all -- nothing to evaluate for this brand

        opportunity = Opportunity(
            brand_id=brand.id,
            opportunity_type=opportunity_type.value,
            level=rule_result.level.value,
            rule_triggered=rule_result.rule_triggered,
            reasons=rule_result.reasons,
        )
        session.add(opportunity)
        await session.flush()
        outcome.opportunities_created.append(opportunity.id)

        session.add(
            AgentEvent(
                agent_run_id=agent_run_id,
                event_type="opportunity_created",
                payload={
                    "brand_id": str(brand.id),
                    "opportunity_id": str(opportunity.id),
                    "opportunity_type": opportunity_type.value,
                    "level": rule_result.level.value,
                    "rule_triggered": rule_result.rule_triggered,
                },
            )
        )

    return outcome


async def run_opportunity_engine(
    session: AsyncSession, max_brands: int = 200, settings: Settings | None = None
) -> OpportunityEngineSummary:
    """Evaluates up to `max_brands` ACTIVE Brand rows (MERGED brands --
    Milestone 7 -- are excluded: their children were already repointed to
    a canonical, so evaluating the dead row again would be evaluating
    stale data) and writes Opportunity rows for AMAZON_OPPORTUNITY,
    BUSINESS_VALUE, and WEBSITE_OPPORTUNITY. Meant to be called repeatedly (a future scheduler,
    Milestone 14) the same way resolve_research_job/run_brand_deduplication
    are -- `summary.truncated` says whether this call saw every ACTIVE
    brand.
    """
    settings = settings or get_settings()

    agent_run = AgentRun(
        agent_name="opportunity_engine",
        input={"max_brands": max_brands},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    opportunities_created = 0
    brands_considered = 0
    truncated = False

    try:
        all_active = (
            (
                await session.execute(
                    select(Brand).where(Brand.status == "ACTIVE").order_by(Brand.first_seen_at, Brand.id)
                )
            )
            .scalars()
            .all()
        )
        truncated = len(all_active) > max_brands
        brands = list(all_active[:max_brands])
        brands_considered = len(brands)

        for brand in brands:
            outcome = await _evaluate_brand(session, agent_run.id, brand, settings)
            opportunities_created += len(outcome.opportunities_created)

        agent_run.status = "SUCCESS"
        agent_run.output = {
            "brands_considered": brands_considered,
            "opportunities_created": opportunities_created,
            "truncated": truncated,
        }
        await session.flush()

    except Exception as exc:  # noqa: BLE001 -- same agent-run boundary discipline as brand_resolution.py
        agent_run.status = "FAILED"
        agent_run.error = str(exc)
        session.add(
            ErrorLog(
                category=ErrorCategory.UNKNOWN.value,
                message=str(exc),
                agent_run_id=agent_run.id,
            )
        )
        await session.flush()

    return OpportunityEngineSummary(
        agent_run_id=agent_run.id,
        brands_considered=brands_considered,
        opportunities_created=opportunities_created,
        truncated=truncated,
    )
