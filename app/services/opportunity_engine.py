"""
Opportunity engine — Milestone 8.

Deterministic rules only, no AI (Milestone 10). Given a `Brand` with its
linked `AmazonProduct`/`AmazonSeller` rows (populated by Milestone 5/6/7),
evaluates whether that brand looks like a sales-worthy opportunity and
writes `Opportunity` rows — `opportunity_type`/`level`/`rule_triggered`/
`reasons`, per the model's own section-30 docstring.

**Only two of the six `OpportunityType` values are actually implemented
here: `AMAZON_OPPORTUNITY` and `BUSINESS_VALUE`.** `config/default.yaml`'s
`scoring.weights` already carries an entry for all six (Milestone 9's job,
consuming `Score` rows this module does not write) — but a weight existing
in config for a future scoring step is not the same claim as "this
milestone can evaluate that dimension today". The other four need data
this pipeline genuinely does not have yet, and inventing a rule against
data that doesn't exist would be exactly the kind of overclaimed-confidence
shortcut this project has been careful to avoid at every prior milestone
(see `brand_resolution.py`'s `verified` discipline, `brand_deduplication.py`'s
two-tier merge caution):

* **`LISTING_OPPORTUNITY`** needs listing content quality — bullet points,
  A+ content, image/video count. That's `AmazonListing`
  (`app/models/amazon.py`), and nothing in Milestone 5's enrichment
  capabilities (`PRODUCT_DATA`/`SELLER_DATA`/`BRAND_DATA`/`SALES_RANK`)
  populates it — not one `AmazonListing` row exists anywhere in this
  pipeline yet.
* **`ADVERTISING_OPPORTUNITY`** needs ad-sponsorship signals
  (`AmazonListing.sponsored_seen` or real ad spend data) — same gap.
* **`WEBSITE_OPPORTUNITY`** needs `WEBSITE_DATA` (the one real, non-mock
  provider this project has — see `docs/providers.md`) called against a
  discovered brand's website. Nothing wires that provider into the Amazon
  Scout pipeline; discovery never learns a brand's website URL in the
  first place (`Brand.website` stays `NULL` from Milestone 5 onward).
* **`SELLER_FAST_FIT`** needs Dinamik's own fit criteria for their
  "Seller Fast" service line, which were never specified anywhere in the
  refactor document or `docs/architecture.md` — there is nothing
  deterministic to encode without guessing at Frank's actual business
  criteria, which is worse than not building it.

Building placeholder rules for these four now would produce Opportunity
rows that look evaluated but aren't grounded in anything — worse than not
having them. Wiring in `WEBSITE_OPPORTUNITY` becomes real work once/if a
brand's website gets discovered (a scope question for a future
milestone, not a blocker for this one); `LISTING_OPPORTUNITY`/
`ADVERTISING_OPPORTUNITY` need a new discovery capability entirely;
`SELLER_FAST_FIT` needs Frank's actual criteria.

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
`AmazonProvider._search()` uses for an empty category match. The one
case that gets no row at all is a brand with zero linked `AmazonProduct`
rows: there is nothing to evaluate, not even a null result.
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
    AmazonProduct,
    AmazonSeller,
    Brand,
    ErrorLog,
    Opportunity,
)
from app.models.enums import ErrorCategory, OpportunityLevel, OpportunityType

_DEFAULT_MIN_RATING_THRESHOLD = 4.2
_DEFAULT_MIN_REVIEW_COUNT_THRESHOLD = 50
_DEFAULT_PRODUCT_COUNT_MEDIUM_THRESHOLD = 3
_DEFAULT_PRODUCT_COUNT_HIGH_THRESHOLD = 8
_DEFAULT_REVIEW_COUNT_HIGH_THRESHOLD = 500


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

    outcome = BrandOpportunityOutcome(brand_id=brand.id)

    for opportunity_type, rule_result in (
        (OpportunityType.AMAZON, evaluate_amazon_opportunity(list(products), settings)),
        (OpportunityType.BUSINESS_VALUE, evaluate_business_value(list(products), list(sellers), settings)),
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
    stale data) and writes Opportunity rows for AMAZON_OPPORTUNITY and
    BUSINESS_VALUE. Meant to be called repeatedly (a future scheduler,
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
