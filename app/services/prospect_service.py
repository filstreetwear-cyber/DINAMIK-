"""
Prospect service (2026-10-14): la vista "commerciale" sopra i dati.

Un prospect è un Brand ACTIVE. Questo modulo aggrega in un solo posto
quello che l'interfaccia e l'API mostrano (azienda, venditori, prodotti con
l'ultimo snapshot della scheda e i suoi problemi, opportunità, punteggi,
evidenze, sito, bozze, storico stati) e le uniche azioni umane previste:
cambio stato, modifica bozza, conferma sito, generazione AI on demand.

Nessuna azione invia messaggi a nessuno.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import AIGateway
from app.core.config import Settings, get_settings
from app.models import (
    AgentRun,
    AIRecommendation,
    AmazonListing,
    AmazonProduct,
    AmazonSeller,
    Brand,
    Company,
    ContactDraft,
    Evidence,
    EvidenceFile,
    Opportunity,
    ProspectStatus,
    Score,
    WebsiteProfile,
)
from app.models.enums import ProspectQualification
from app.services.brand_evidence import brand_evidence_condition
from app.services.opportunity_engine import listing_issues

STATUS_LABELS = {
    "NEW": "Da valutare",
    ProspectQualification.NEEDS_REVIEW.value: "Da rivedere",
    ProspectQualification.AUTO_QUALIFIED.value: "Qualificato (auto)",
    ProspectQualification.APPROVED.value: "Approvato",
    ProspectQualification.REJECTED.value: "Scartato",
    ProspectQualification.CONTACTED.value: "Contattato",
}
HUMAN_STATUSES = (
    ProspectQualification.APPROVED.value,
    ProspectQualification.REJECTED.value,
    ProspectQualification.CONTACTED.value,
    ProspectQualification.NEEDS_REVIEW.value,
)
_LEVEL_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "POSSIBLE": 1, "NONE": 0}


class ProspectNotFoundError(LookupError):
    pass


@dataclass
class ProspectRow:
    brand_id: uuid.UUID
    brand_name: str
    company_name: str | None
    vat: str | None
    city: str | None
    country: str | None
    website: str | None
    seller_names: list[str]
    is_target: bool | None
    status: str
    score: float | None
    listing_level: str | None
    amazon_level: str | None
    product_count: int
    last_seen_at: datetime

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)


@dataclass
class ProductView:
    product: AmazonProduct
    listing: AmazonListing | None
    issues: list[str]
    screenshot_id: uuid.UUID | None
    history: list[AmazonListing] = field(default_factory=list)


@dataclass
class ProspectDetail:
    brand: Brand
    company: Company | None
    sellers: list[AmazonSeller]
    products: list[ProductView]
    opportunities: dict[str, Opportunity]
    scores: dict[str, Score]
    website_profile: WebsiteProfile | None
    possible_websites: list[str]
    evidence: list[Evidence]
    drafts: list[ContactDraft]
    recommendations: list[AIRecommendation]
    status_history: list[ProspectStatus]

    @property
    def status(self) -> str:
        return self.status_history[0].status if self.status_history else "NEW"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def overall_score(self) -> float | None:
        return _overall(list(self.scores.values()))


def _overall(scores: list[Score]) -> float | None:
    """Media pesata dei punteggi per tipo (pesi già salvati sullo Score)."""
    if not scores:
        return None
    weights = [float(s.weight_used or 0) for s in scores]
    if sum(weights) <= 0:
        return round(sum(float(s.score) for s in scores) / len(scores), 1)
    return round(sum(float(s.score) * w for s, w in zip(scores, weights, strict=True)) / sum(weights), 1)


async def _latest_status(session: AsyncSession, brand_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not brand_ids:
        return {}
    rows = (
        await session.execute(
            select(ProspectStatus.brand_id, ProspectStatus.status)
            .where(ProspectStatus.brand_id.in_(brand_ids))
            .order_by(ProspectStatus.brand_id, ProspectStatus.created_at.desc())
            .ext(distinct_on(ProspectStatus.brand_id))
        )
    ).all()
    return {b: s for b, s in rows}


async def _latest_by_type(session: AsyncSession, model: Any, type_col: Any, brand_ids: list[uuid.UUID]) -> dict:
    if not brand_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(model)
                .where(model.brand_id.in_(brand_ids))
                .order_by(model.brand_id, type_col, model.created_at.desc())
                .ext(distinct_on(model.brand_id, type_col))
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        out.setdefault(row.brand_id, {})[getattr(row, type_col.key)] = row
    return out


async def list_prospects(
    session: AsyncSession,
    *,
    status: str | None = None,
    target_only: bool = True,
    search: str | None = None,
    limit: int = 200,
) -> list[ProspectRow]:
    query = select(Brand).where(Brand.status == "ACTIVE")
    if target_only:
        query = query.where(
            select(AmazonSeller.id).where(AmazonSeller.brand_id == Brand.id, AmazonSeller.is_target.is_(True)).exists()
        )
    if search:
        like = f"%{search.strip()}%"
        company_match = select(Company.id).where(Company.id == Brand.company_id, Company.legal_name.ilike(like))
        query = query.where(or_(Brand.name.ilike(like), company_match.exists()))
    brands = list((await session.execute(query.order_by(Brand.last_seen_at.desc()).limit(limit * 3))).scalars())
    ids = [b.id for b in brands]

    statuses = await _latest_status(session, ids)
    scores = await _latest_by_type(session, Score, Score.score_type, ids)
    opps = await _latest_by_type(session, Opportunity, Opportunity.opportunity_type, ids)
    companies = {
        c.id: c
        for c in (
            await session.execute(select(Company).where(Company.id.in_([b.company_id for b in brands if b.company_id])))
        ).scalars()
    }
    sellers_by_brand: dict[uuid.UUID, list[AmazonSeller]] = {}
    for seller in (await session.execute(select(AmazonSeller).where(AmazonSeller.brand_id.in_(ids)))).scalars():
        sellers_by_brand.setdefault(seller.brand_id, []).append(seller)  # type: ignore[arg-type]
    counts = dict(
        (
            await session.execute(
                select(AmazonProduct.brand_id, func.count()).where(AmazonProduct.brand_id.in_(ids)).group_by(
                    AmazonProduct.brand_id
                )
            )
        ).all()
    )

    rows: list[ProspectRow] = []
    for brand in brands:
        current = statuses.get(brand.id, "NEW")
        if status and current != status:
            continue
        company = companies.get(brand.company_id) if brand.company_id else None
        sellers = sellers_by_brand.get(brand.id, [])
        flags = [s.is_target for s in sellers if s.is_target is not None]
        brand_opps = opps.get(brand.id, {})
        rows.append(
            ProspectRow(
                brand_id=brand.id,
                brand_name=brand.name,
                company_name=company.legal_name if company else None,
                vat=f"{company.vat_country}{company.vat_number}" if company and company.vat_number else None,
                city=company.city if company else None,
                country=company.country if company else None,
                website=brand.website,
                seller_names=[s.seller_name for s in sellers],
                is_target=any(flags) if flags else None,
                status=current,
                score=_overall(list(scores.get(brand.id, {}).values())),
                listing_level=getattr(brand_opps.get("LISTING_OPPORTUNITY"), "level", None),
                amazon_level=getattr(brand_opps.get("AMAZON_OPPORTUNITY"), "level", None),
                product_count=int(counts.get(brand.id, 0)),
                last_seen_at=brand.last_seen_at,
            )
        )
    rows.sort(
        key=lambda r: (
            -_LEVEL_RANK.get(r.listing_level or "NONE", 0),
            -(r.score or 0),
            r.brand_name.lower(),
        )
    )
    return rows[:limit]


async def get_prospect(session: AsyncSession, brand_id: uuid.UUID, settings: Settings | None = None) -> ProspectDetail:
    settings = settings or get_settings()
    brand = await session.get(Brand, brand_id)
    if brand is None:
        raise ProspectNotFoundError(str(brand_id))
    company = await session.get(Company, brand.company_id) if brand.company_id else None
    sellers = list((await session.execute(select(AmazonSeller).where(AmazonSeller.brand_id == brand.id))).scalars())

    cfg = settings.opportunity_rule_config("listing_opportunity")
    products: list[ProductView] = []
    for product in (
        await session.execute(select(AmazonProduct).where(AmazonProduct.brand_id == brand.id).order_by(AmazonProduct.asin))
    ).scalars():
        history = list(
            (
                await session.execute(
                    select(AmazonListing)
                    .where(AmazonListing.product_id == product.id)
                    .order_by(AmazonListing.observed_at.desc())
                    .limit(20)
                )
            ).scalars()
        )
        latest = history[0] if history else None
        screenshot_id = None
        if history:
            screenshot_id = (
                await session.execute(
                    select(EvidenceFile.id)
                    .where(EvidenceFile.listing_id.in_([h.id for h in history]), EvidenceFile.kind == "screenshot")
                    .order_by(EvidenceFile.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        products.append(
            ProductView(
                product=product,
                listing=latest,
                issues=listing_issues(latest, cfg) if latest else [],
                screenshot_id=screenshot_id,
                history=history,
            )
        )

    opps = (await _latest_by_type(session, Opportunity, Opportunity.opportunity_type, [brand.id])).get(brand.id, {})
    scores = (await _latest_by_type(session, Score, Score.score_type, [brand.id])).get(brand.id, {})
    website_profile = (
        await session.execute(select(WebsiteProfile).where(WebsiteProfile.brand_id == brand.id))
    ).scalar_one_or_none()
    condition = await brand_evidence_condition(session, brand.id)
    evidence = list(
        (await session.execute(select(Evidence).where(condition).order_by(Evidence.observed_at.desc()).limit(200))).scalars()
    )
    possible_websites = sorted(
        {
            e.raw_value
            for e in evidence
            if e.provider == "website_discovery" and e.source == "website_discovery:brand_in_title" and e.raw_value
        }
    )
    drafts = list(
        (
            await session.execute(
                select(ContactDraft).where(ContactDraft.brand_id == brand.id).order_by(ContactDraft.created_at.desc())
            )
        ).scalars()
    )
    recommendations = list(
        (
            await session.execute(
                select(AIRecommendation)
                .where(AIRecommendation.entity_type == "brand", AIRecommendation.entity_id == str(brand.id))
                .order_by(AIRecommendation.created_at.desc())
            )
        ).scalars()
    )
    status_history = list(
        (
            await session.execute(
                select(ProspectStatus).where(ProspectStatus.brand_id == brand.id).order_by(ProspectStatus.created_at.desc())
            )
        ).scalars()
    )
    return ProspectDetail(
        brand=brand,
        company=company,
        sellers=sellers,
        products=products,
        opportunities=opps,
        scores=scores,
        website_profile=website_profile,
        possible_websites=possible_websites,
        evidence=evidence,
        drafts=drafts,
        recommendations=recommendations,
        status_history=status_history,
    )


async def set_status(
    session: AsyncSession, brand_id: uuid.UUID, status: str, *, reason: str | None, changed_by: str
) -> ProspectStatus:
    if status not in HUMAN_STATUSES:
        raise ValueError(f"stato non consentito: {status}")
    if await session.get(Brand, brand_id) is None:
        raise ProspectNotFoundError(str(brand_id))
    row = ProspectStatus(brand_id=brand_id, status=status, reason=(reason or None), changed_by=changed_by)
    session.add(row)
    await session.flush()
    return row


async def save_draft_edit(
    session: AsyncSession, brand_id: uuid.UUID, *, subject: str, body: str, changed_by: str
) -> ContactDraft:
    """Le bozze sono append-only: una modifica umana è una riga nuova con
    provider="human", così resta visibile cosa aveva scritto l'AI."""
    if await session.get(Brand, brand_id) is None:
        raise ProspectNotFoundError(str(brand_id))
    previous = (
        await session.execute(
            select(ContactDraft).where(ContactDraft.brand_id == brand_id).order_by(ContactDraft.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    draft = ContactDraft(
        brand_id=brand_id,
        subject=subject.strip()[:255],
        body=body.strip()[:4000],
        recommendation_ids=list(previous.recommendation_ids) if previous else [],
        model=f"edit:{changed_by}"[:100],
        provider="human",
        generated_at=datetime.now(UTC),
    )
    session.add(draft)
    await session.flush()
    return draft


async def confirm_website(
    session: AsyncSession, brand_id: uuid.UUID, url: str, *, provider: Any | None = None
) -> None:
    from app.services.website_discovery import apply_website

    brand = await session.get(Brand, brand_id)
    if brand is None:
        raise ProspectNotFoundError(str(brand_id))
    clean = url.strip()
    if not clean.startswith(("http://", "https://")):
        clean = f"https://{clean}"
    await apply_website(session, brand, clean, provider=provider)


@dataclass
class AIGenerationOutcome:
    ai_disabled: bool
    recommendations_created: int
    draft_id: uuid.UUID | None


async def generate_ai_for_brand(
    session: AsyncSession, brand_id: uuid.UUID, *, gateway: AIGateway | None = None, settings: Settings | None = None
) -> AIGenerationOutcome:
    """Raccomandazioni (AIAdvisor) + bozza (AIOutreachWriter) per UN brand,
    on demand dall'interfaccia. Pensata per i prospect approvati: niente
    testi generati per brand che non verranno mai contattati."""
    from app.ai.advisor import AIAdvisor
    from app.ai.bootstrap import build_default_ai_gateway
    from app.ai.outreach_writer import AIOutreachWriter
    from app.services.ai_recommendation_engine import _recommend_for_brand
    from app.services.contact_draft_engine import _draft_for_brand

    settings = settings or get_settings()
    brand = await session.get(Brand, brand_id)
    if brand is None:
        raise ProspectNotFoundError(str(brand_id))
    resolved = gateway if gateway is not None else build_default_ai_gateway(settings)
    if resolved is None:
        return AIGenerationOutcome(ai_disabled=True, recommendations_created=0, draft_id=None)
    run = AgentRun(agent_name="prospect_ai_on_demand", input={"brand_id": str(brand_id)}, status="RUNNING")
    session.add(run)
    await session.flush()
    rec = await _recommend_for_brand(session, run.id, brand, AIAdvisor(resolved))
    draft = await _draft_for_brand(session, run.id, brand, AIOutreachWriter(resolved))
    run.status = "SUCCESS"
    run.output = {"recommendations_created": len(rec.recommendations_created), "draft_id": str(draft.draft_id)}
    await session.flush()
    return AIGenerationOutcome(
        ai_disabled=False, recommendations_created=len(rec.recommendations_created), draft_id=draft.draft_id
    )


async def dashboard_counts(session: AsyncSession) -> dict[str, int]:
    brands = (await session.execute(select(func.count()).select_from(Brand).where(Brand.status == "ACTIVE"))).scalar_one()
    targets = (
        await session.execute(select(func.count(func.distinct(AmazonSeller.brand_id))).where(AmazonSeller.is_target.is_(True)))
    ).scalar_one()
    sellers = (await session.execute(select(func.count()).select_from(AmazonSeller))).scalar_one()
    products = (await session.execute(select(func.count()).select_from(AmazonProduct))).scalar_one()
    companies = (
        await session.execute(select(func.count()).select_from(Company).where(Company.vies_valid.is_(True)))
    ).scalar_one()
    with_site = (
        await session.execute(select(func.count()).select_from(Brand).where(Brand.website.is_not(None)))
    ).scalar_one()
    ids = list((await session.execute(select(Brand.id).where(Brand.status == "ACTIVE"))).scalars())
    statuses = await _latest_status(session, ids)
    return {
        "brands": int(brands),
        "targets": int(targets),
        "sellers": int(sellers),
        "products": int(products),
        "companies_verified": int(companies),
        "with_website": int(with_site),
        "approved": sum(1 for s in statuses.values() if s == ProspectQualification.APPROVED.value),
        "contacted": sum(1 for s in statuses.values() if s == ProspectQualification.CONTACTED.value),
        "rejected": sum(1 for s in statuses.values() if s == ProspectQualification.REJECTED.value),
    }
