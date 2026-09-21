"""
Brand resolution — Milestone 6.

Deterministic rules only, no AI: given a `ResearchResult` produced by
Milestone 5 discovery, resolve or create the `AmazonSeller` / `Brand` /
`AmazonProduct` rows its raw payload implies, and fill in
`ResearchResult.product_id`/`brand_id` — deliberately left `NULL` by
discovery (see `app/agents/amazon_scout/discovery.py`'s own docstring:
"before brand resolution/dedup").

Rules-before-AI is a standing discipline on this project, not just a name:
`app/models/amazon.py` already carries the design note this module
implements — "a seller<->brand link starts unverified until the
BrandResolver (rules first, then AI) confirms it with enough confidence."
Concretely: this module resolves and LINKS rows with full confidence (an
ASIN either matches an existing product row or it doesn't — that part is
exact), but it never sets `AmazonSeller.verified = True`. A seller and a
brand name co-occurring on one Amazon listing is not independent
confirmation that the seller represents that brand — a marketplace
reseller can list a product under a brand it doesn't own. Verification is
reserved for Milestone 10 (AI) or a future human review step; inventing a
"deterministic verified" tier here would be overclaiming confidence this
module doesn't actually have.

Explicitly out of scope, and left that way on purpose:

* **Company resolution.** A `Company` (the legal entity that may own
  several brands) needs data this pipeline doesn't have — no domain, no
  legal name. Milestone 5's discovery only ever calls
  PRODUCT_DATA/SELLER_DATA/BRAND_DATA/SALES_RANK; nothing fetches
  WEBSITE_DATA for a discovered brand yet. `Brand.company_id` stays `NULL`
  here — wiring it up needs a capability call this pipeline doesn't make,
  not more code in this file.
* **Deduplication across near-duplicate names** ("Acme Kitchenware" vs
  "Acme Kitchen Ware Srl") — Milestone 7, a distinct concern from
  resolving what one `ResearchResult` itself claims. Brand matching here
  is exact, case-insensitive string equality only.
* **Price/currency on `AmazonProduct`** — Milestone 5's enrichment never
  calls `PRODUCT_PRICE` (see its own `_ENRICHMENT_CAPABILITIES`), so those
  fields stay whatever they already were (`NULL` on first creation). Not
  a bug here; a real gap in what Milestone 5 fetches.
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentEvent,
    AgentRun,
    AmazonProduct,
    AmazonSeller,
    Brand,
    ErrorLog,
    ResearchJob,
    ResearchResult,
)
from app.models.base import utcnow
from app.models.enums import ErrorCategory


class BrandResolutionRule(str, enum.Enum):
    """Which deterministic rule resolved (or failed to resolve) a brand
    for one ResearchResult. Not a DB vocabulary column — lives here, next
    to its only user, the way BrowserTaskSpec/Outcome live in
    app/tools/browser/ rather than app/models/."""

    EXISTING_SELLER_LINK = "EXISTING_SELLER_LINK"
    EXACT_BRAND_NAME_MATCH = "EXACT_BRAND_NAME_MATCH"
    NEW_BRAND_CREATED = "NEW_BRAND_CREATED"
    UNRESOLVED_MISSING_DATA = "UNRESOLVED_MISSING_DATA"


@dataclass
class BrandResolutionOutcome:
    research_result_id: uuid.UUID
    rule_applied: BrandResolutionRule
    product_id: uuid.UUID | None
    brand_id: uuid.UUID | None
    seller_row_id: uuid.UUID | None
    verified: bool
    conflict: bool = False
    """True when the seller was already linked to a brand whose name does
    not match this observation's brand claim. The EXISTING link is kept
    (see resolve_research_result) — this flag exists so the caller can
    surface the mismatch (job driver writes an AgentEvent) instead of it
    disappearing silently."""


@dataclass
class BrandResolutionJobSummary:
    agent_run_id: uuid.UUID
    processed: int
    resolved: int
    unresolved: int
    conflicts: int


async def _get_or_create_seller(
    session: AsyncSession, seller_id_raw: str, marketplace: str, seller_name: str
) -> AmazonSeller:
    existing = (
        await session.execute(
            select(AmazonSeller).where(
                AmazonSeller.seller_id == seller_id_raw, AmazonSeller.marketplace == marketplace
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_seen_at = utcnow()
        return existing

    now = utcnow()
    seller = AmazonSeller(
        seller_id=seller_id_raw,
        seller_name=seller_name,
        marketplace=marketplace,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(seller)
    await session.flush()
    return seller


async def _get_or_create_brand_by_name(session: AsyncSession, name: str) -> tuple[Brand, bool]:
    """Case-insensitive EXACT match only. Returns (brand, created)."""
    normalized = name.strip()
    existing = (
        await session.execute(select(Brand).where(func.lower(Brand.name) == normalized.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_seen_at = utcnow()
        return existing, False

    brand = Brand(name=normalized)  # first_seen_at/last_seen_at/status have real defaults on this model
    session.add(brand)
    await session.flush()
    return brand, True


async def _get_or_create_product(session: AsyncSession, asin: str, marketplace: str, payload: dict[str, Any]) -> AmazonProduct:
    product = (
        await session.execute(
            select(AmazonProduct).where(AmazonProduct.asin == asin, AmazonProduct.marketplace == marketplace)
        )
    ).scalar_one_or_none()
    now = utcnow()
    if product is None:
        # title is NOT NULL on this model; discovery may have failed the
        # PRODUCT_DATA call for this ASIN (the enriched_any gap noted in
        # docs/architecture.md addendum (5)) and left no title at all —
        # fall back to the ASIN itself rather than fabricating a name.
        product = AmazonProduct(
            asin=asin,
            marketplace=marketplace,
            title=payload.get("title") or asin,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(product)
    else:
        product.last_seen_at = now
        if payload.get("title"):
            product.title = payload["title"]

    if payload.get("category") is not None:
        product.category = payload["category"]
    if payload.get("rating") is not None:
        product.rating = payload["rating"]
    if payload.get("review_count") is not None:
        product.review_count = payload["review_count"]
    if payload.get("bsr") is not None:
        product.bsr = payload["bsr"]

    await session.flush()
    return product


async def resolve_research_result(session: AsyncSession, result: ResearchResult) -> BrandResolutionOutcome:
    payload = result.raw_payload or {}
    asin = payload.get("asin")
    marketplace = payload.get("marketplace")
    seller_id_raw = payload.get("seller_id")
    seller_name = payload.get("seller_name")
    brand_name = payload.get("brand")

    if not asin or not marketplace:
        # Discovery always sets these on every ResearchResult it creates —
        # a missing one means raw_payload was never populated at all.
        # Nothing to resolve; return without touching the DB.
        return BrandResolutionOutcome(
            research_result_id=result.id,
            rule_applied=BrandResolutionRule.UNRESOLVED_MISSING_DATA,
            product_id=None,
            brand_id=None,
            seller_row_id=None,
            verified=False,
        )

    product = await _get_or_create_product(session, asin, marketplace, payload)
    result.product_id = product.id

    if not seller_id_raw or not brand_name:
        # Partial enrichment (SELLER_DATA and/or BRAND_DATA failed for
        # this ASIN) -- the product itself still resolves, the brand link
        # cannot. Not an error: recorded as UNRESOLVED, not raised.
        return BrandResolutionOutcome(
            research_result_id=result.id,
            rule_applied=BrandResolutionRule.UNRESOLVED_MISSING_DATA,
            product_id=product.id,
            brand_id=None,
            seller_row_id=None,
            verified=False,
        )

    seller = await _get_or_create_seller(session, seller_id_raw, marketplace, seller_name or seller_id_raw)

    conflict = False
    if seller.brand_id is not None:
        # Already linked -- trust the earlier resolution rather than
        # re-linking on every re-observation, which would make the link
        # flip-flop if a listing's brand field is ever noisy or wrong.
        brand = await session.get(Brand, seller.brand_id)
        if brand is None:
            raise RuntimeError(
                f"AmazonSeller {seller.id} has brand_id={seller.brand_id} pointing at a Brand "
                "that no longer exists -- FK integrity should make this impossible; a real bug."
            )
        if brand.name.strip().lower() != brand_name.strip().lower():
            conflict = True
        rule = BrandResolutionRule.EXISTING_SELLER_LINK
    else:
        brand, created = await _get_or_create_brand_by_name(session, brand_name)
        seller.brand_id = brand.id
        rule = BrandResolutionRule.NEW_BRAND_CREATED if created else BrandResolutionRule.EXACT_BRAND_NAME_MATCH

    product.brand_id = brand.id
    product.seller_id = seller.id
    await session.flush()

    result.brand_id = brand.id

    return BrandResolutionOutcome(
        research_result_id=result.id,
        rule_applied=rule,
        product_id=product.id,
        brand_id=brand.id,
        seller_row_id=seller.id,
        verified=seller.verified,  # never set True by this module -- see module docstring
        conflict=conflict,
    )


async def resolve_research_job(session: AsyncSession, job: ResearchJob) -> BrandResolutionJobSummary:
    """Resolves every ResearchResult belonging to `job` that still has
    product_id IS NULL. An already-resolved result (e.g. running this
    twice over the same job) is left untouched, not re-processed.

    Deliberately does NOT touch job.status — that field belongs to the
    discovery step (Milestone 5) and already carries its own COMPLETED/
    PARTIAL/FAILED meaning; overwriting it here would conflate two
    different pipeline stages into one field. This step's own
    success/failure lives on its own AgentRun.
    """
    agent_run = AgentRun(
        agent_name="brand_resolver",
        job_id=job.id,
        input={"job_id": str(job.id)},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    processed = resolved = unresolved = conflicts = 0

    try:
        pending = (
            (
                await session.execute(
                    select(ResearchResult).where(
                        ResearchResult.job_id == job.id, ResearchResult.product_id.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )

        for result in pending:
            outcome = await resolve_research_result(session, result)
            processed += 1
            if outcome.brand_id is not None:
                resolved += 1
            else:
                unresolved += 1

            if outcome.conflict:
                conflicts += 1
                session.add(
                    AgentEvent(
                        agent_run_id=agent_run.id,
                        event_type="seller_brand_conflict",
                        payload={
                            "research_result_id": str(result.id),
                            "kept_brand_id": str(outcome.brand_id),
                        },
                    )
                )

            session.add(
                AgentEvent(
                    agent_run_id=agent_run.id,
                    event_type="result_resolved",
                    payload={
                        "research_result_id": str(result.id),
                        "rule": outcome.rule_applied.value,
                        "brand_id": str(outcome.brand_id) if outcome.brand_id else None,
                    },
                )
            )

        agent_run.status = "SUCCESS"
        agent_run.output = {
            "processed": processed,
            "resolved": resolved,
            "unresolved": unresolved,
            "conflicts": conflicts,
        }
        await session.flush()

    except Exception as exc:  # noqa: BLE001 -- same agent-run boundary discipline as discovery.py
        agent_run.status = "FAILED"
        agent_run.error = str(exc)
        session.add(
            ErrorLog(
                category=ErrorCategory.UNKNOWN.value,
                message=str(exc),
                job_id=job.id,
                agent_run_id=agent_run.id,
            )
        )
        await session.flush()

    return BrandResolutionJobSummary(
        agent_run_id=agent_run.id,
        processed=processed,
        resolved=resolved,
        unresolved=unresolved,
        conflicts=conflicts,
    )
