"""
Exports — Milestone 15.

Scope was NOT inventable from the codebase: the only trace of this
milestone before today was a dead feature flag (`enable_export`,
`ENABLE_EXPORT` in `.env.example`, never read anywhere -- confirmed by
grep before writing this file) and the bare name "Exports" from section
58 of the original spec, with no further detail anywhere in
`docs/architecture.md`. Rather than invent a format/audience nobody
asked for (the same trap Milestone 14's scheduler almost fell into),
this was clarified directly with Fil before building:

- **Audience**: an internal Dinamik Advisor spreadsheet -- Frank's team
  uses it to prioritize outreach across scanned brands. NOT a
  prospect-facing package; that would overlap with Milestone 16's
  `AuditorPackage` (`app/models/auditor_package.py`, section 54/55 --
  already modeled as a per-brand JSONB handoff snapshot for Agent 02),
  which this deliberately does not duplicate or replace.
- **Format**: a native `.xlsx` workbook (`openpyxl`, added as a real
  dependency here -- was not previously installed), not CSV: real
  headers, frozen header row, sane column widths -- something the team
  opens and reads immediately, not raw delimited text.

**Grain: one row per Brand, not one row per AmazonProduct.** A rep
reaches out to a brand/seller, not to an individual ASIN -- that's the
actual outreach/prioritization unit. Product-level fields are
aggregated per brand (count / average rating / total reviews / best
BSR) instead of repeating every brand column on every ASIN row, which
would only make the sheet longer, not more useful for prioritization.

**Reuses the existing scoring/evidence architecture, doesn't reinvent
it**: the Brand -> ResearchResult.brand_id -> Evidence(entity_type=
"research_result") chain is the same one `scoring_engine.py`'s
`_evidence_ids_for_brand` and `ai_analysis_engine.py`'s
`_evidence_for_brand` already use (written independently here, same
join, not imported across services -- consistent with how those two
already duplicate it rather than share a private helper). `Score` is
read directly for the two implemented `OpportunityType`s
(`scoring_engine.py`'s `_LEVEL_TO_SCORE`/append-only design, same set
`app/services/weekly_scan.py`'s `_IMPLEMENTED_OPPORTUNITY_TYPES`
already narrowed to) -- no new scoring logic. `Opportunity` is read for
`rule_triggered`/`reasons`, same "most recent row per type" collapse as
`scoring_engine.py`'s `_latest_opportunities_by_type`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import (
    AmazonProduct,
    AmazonSeller,
    Brand,
    Company,
    Evidence,
    Opportunity,
    ResearchResult,
    Score,
)
from app.models.base import utcnow
from app.models.enums import OpportunityType

_IMPLEMENTED_OPPORTUNITY_TYPES: tuple[str, ...] = (
    OpportunityType.AMAZON.value,
    OpportunityType.BUSINESS_VALUE.value,
    OpportunityType.LISTING.value,  # Anello 1
)

_COLUMNS: tuple[str, ...] = (
    "Brand",
    "Company",
    "Country",
    "Website",
    "Amazon storefront",
    "Status",
    "First seen",
    "Last seen",
    "# Products",
    "Avg rating",
    "Total reviews",
    "Best BSR",
    "Amazon opportunity — level",
    "Amazon opportunity — score",
    "Amazon opportunity — confidence",
    "Amazon opportunity — why",
    "Business value — level",
    "Business value — score",
    "Business value — confidence",
    "Business value — why",
    "Evidence count",
    # Anello 1 (2026-10-14): colonne prospect in coda, così l'ordine delle
    # colonne preesistenti non cambia per chi ha già fogli/filtri salvati.
    "Listing opportunity — level",
    "Listing opportunity — score",
    "Listing opportunity — why",
    "Seller (Amazon)",
    "Seller target",
    "P.IVA",
    "VIES valid",
    "Company address",
)


@dataclass
class BrandExportRow:
    brand_id: uuid.UUID
    brand_name: str
    company_name: str | None
    country: str | None
    website: str | None
    amazon_storefront: str | None
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    product_count: int
    avg_rating: float | None
    total_review_count: int
    best_bsr: int | None
    opportunities: dict[str, dict[str, Any]] = field(default_factory=dict)  # opportunity_type -> {level, score, confidence, why}
    evidence_count: int = 0
    seller_names: str | None = None
    seller_target: bool | None = None
    vat: str | None = None
    vies_valid: bool | None = None
    company_address: str | None = None

    def as_worksheet_row(self) -> list[Any]:
        amazon = self.opportunities.get(OpportunityType.AMAZON.value, {})
        business = self.opportunities.get(OpportunityType.BUSINESS_VALUE.value, {})
        listing = self.opportunities.get(OpportunityType.LISTING.value, {})
        return [
            self.brand_name,
            self.company_name,
            self.country,
            self.website,
            self.amazon_storefront,
            self.status,
            self.first_seen_at.replace(tzinfo=None) if self.first_seen_at else None,
            self.last_seen_at.replace(tzinfo=None) if self.last_seen_at else None,
            self.product_count,
            round(self.avg_rating, 2) if self.avg_rating is not None else None,
            self.total_review_count,
            self.best_bsr,
            amazon.get("level"),
            amazon.get("score"),
            amazon.get("confidence"),
            amazon.get("why"),
            business.get("level"),
            business.get("score"),
            business.get("confidence"),
            business.get("why"),
            self.evidence_count,
            listing.get("level"),
            listing.get("score"),
            listing.get("why"),
            self.seller_names,
            self.seller_target,
            self.vat,
            self.vies_valid,
            self.company_address,
        ]


@dataclass
class ExportSummary:
    rows_exported: int
    status_filter: str
    path: str
    generated_at: datetime


async def _evidence_count_for_brand(session: AsyncSession, brand_id: uuid.UUID) -> int:
    """Same ResearchResult.brand_id -> Evidence.entity_id chain as
    scoring_engine.py's `_evidence_ids_for_brand` / ai_analysis_engine.py's
    `_evidence_for_brand` -- a COUNT instead of full rows, since the
    export sheet only needs a number, not the evidence content itself."""
    research_result_ids = (
        (await session.execute(select(ResearchResult.id).where(ResearchResult.brand_id == brand_id)))
        .scalars()
        .all()
    )
    if not research_result_ids:
        return 0
    entity_ids = [str(rid) for rid in research_result_ids]
    count = (
        await session.execute(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.entity_type == "research_result", Evidence.entity_id.in_(entity_ids))
        )
    ).scalar_one()
    return int(count)


async def _product_stats_for_brand(session: AsyncSession, brand_id: uuid.UUID) -> dict[str, Any]:
    """Aggregated, not per-ASIN -- this is what makes a one-row-per-brand
    sheet meaningful instead of throwing away product-level signal.
    `func.avg`/`func.sum`/`func.min` all correctly skip NULLs (Postgres
    semantics), so a brand with some unrated products still gets a real
    average over the rated ones instead of a NULL-poisoned result."""
    row = (
        await session.execute(
            select(
                func.count(AmazonProduct.id),
                func.avg(AmazonProduct.rating),
                func.sum(AmazonProduct.review_count),
                func.min(AmazonProduct.bsr),
            ).where(AmazonProduct.brand_id == brand_id)
        )
    ).one()
    product_count, avg_rating, total_reviews, best_bsr = row
    return {
        "product_count": int(product_count or 0),
        "avg_rating": float(avg_rating) if avg_rating is not None else None,
        "total_review_count": int(total_reviews or 0),
        "best_bsr": int(best_bsr) if best_bsr is not None else None,
    }


async def _latest_opportunities_by_type(session: AsyncSession, brand_id: uuid.UUID) -> dict[str, Opportunity]:
    """Same "most recent row per type" collapse as scoring_engine.py's
    own `_latest_opportunities_by_type` -- Opportunity is append-only
    (Milestone 8), so ordering DESC and keeping the first row seen per
    type is correct without a second query."""
    rows = (
        (await session.execute(select(Opportunity).where(Opportunity.brand_id == brand_id).order_by(Opportunity.created_at.desc())))
        .scalars()
        .all()
    )
    latest: dict[str, Opportunity] = {}
    for opp_row in rows:
        latest.setdefault(opp_row.opportunity_type, opp_row)
    return latest


async def _latest_scores_by_type(session: AsyncSession, brand_id: uuid.UUID) -> dict[str, Score]:
    rows = (
        (await session.execute(select(Score).where(Score.brand_id == brand_id).order_by(Score.created_at.desc())))
        .scalars()
        .all()
    )
    latest: dict[str, Score] = {}
    for score_row in rows:
        latest.setdefault(score_row.score_type, score_row)
    return latest


def _summarize_reasons(reasons: list[Any] | None) -> str | None:
    """Opportunity.reasons is JSONB (a list of dicts or strings,
    depending on which rule wrote it -- see opportunity_engine.py). This
    is display-only: join whatever's there into one short cell, never
    re-parsed or relied on as structured data."""
    if not reasons:
        return None
    parts: list[str] = []
    for r in reasons:
        if isinstance(r, dict):
            parts.append(str(r.get("reason", r)))
        else:
            parts.append(str(r))
    return "; ".join(parts)[:500]


async def build_brand_export_rows(
    session: AsyncSession, *, status: str = "ACTIVE", limit: int = 2000
) -> list[BrandExportRow]:
    brands = (
        (
            await session.execute(
                select(Brand).where(Brand.status == status).order_by(Brand.last_seen_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )

    rows: list[BrandExportRow] = []
    for brand in brands:
        company_name: str | None = None
        company: Company | None = None
        if brand.company_id is not None:
            company = await session.get(Company, brand.company_id)
            company_name = company.legal_name if company is not None else None
        sellers = (
            (await session.execute(select(AmazonSeller).where(AmazonSeller.brand_id == brand.id))).scalars().all()
        )
        seller_flags = [s.is_target for s in sellers if s.is_target is not None]

        product_stats = await _product_stats_for_brand(session, brand.id)
        opportunities_by_type = await _latest_opportunities_by_type(session, brand.id)
        scores_by_type = await _latest_scores_by_type(session, brand.id)
        evidence_count = await _evidence_count_for_brand(session, brand.id)

        opportunities: dict[str, dict[str, Any]] = {}
        for opp_type in _IMPLEMENTED_OPPORTUNITY_TYPES:
            opp = opportunities_by_type.get(opp_type)
            score = scores_by_type.get(opp_type)
            if opp is None and score is None:
                continue
            opportunities[opp_type] = {
                "level": opp.level if opp is not None else None,
                "score": float(score.score) if score is not None else None,
                "confidence": float(score.confidence) if score is not None else None,
                "why": _summarize_reasons(opp.reasons) if opp is not None else None,
            }

        rows.append(
            BrandExportRow(
                brand_id=brand.id,
                brand_name=brand.name,
                company_name=company_name,
                country=brand.country,
                website=brand.website,
                amazon_storefront=brand.amazon_storefront,
                status=brand.status,
                first_seen_at=brand.first_seen_at,
                last_seen_at=brand.last_seen_at,
                product_count=product_stats["product_count"],
                avg_rating=product_stats["avg_rating"],
                total_review_count=product_stats["total_review_count"],
                best_bsr=product_stats["best_bsr"],
                opportunities=opportunities,
                evidence_count=evidence_count,
                seller_names=", ".join(s.seller_name for s in sellers) or None,
                seller_target=any(seller_flags) if seller_flags else None,
                vat=f"{company.vat_country}{company.vat_number}" if company and company.vat_number else None,
                vies_valid=company.vies_valid if company else None,
                company_address=company.address if company else None,
            )
        )
    return rows


def write_brand_export_workbook(rows: list[BrandExportRow], path: Path) -> Path:
    """Real .xlsx, not a renamed CSV: bold frozen header row and
    column widths sized to their content -- the two things Fil asked
    for when choosing "native Excel" over plain CSV."""
    workbook = Workbook()
    active = workbook.active
    assert isinstance(active, Worksheet)  # a freshly-constructed Workbook always has one real worksheet
    sheet = active
    sheet.title = "Brands"

    sheet.append(list(_COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"

    for row in rows:
        sheet.append(row.as_worksheet_row())

    _autosize_columns(sheet, header=_COLUMNS)

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _autosize_columns(sheet: Worksheet, *, header: tuple[str, ...], max_width: int = 60) -> None:
    for idx, title in enumerate(header, start=1):
        longest = len(title)
        for cell in sheet[get_column_letter(idx)][1:]:
            if cell.value is not None:
                longest = max(longest, len(str(cell.value)))
        sheet.column_dimensions[get_column_letter(idx)].width = min(longest + 2, max_width)


async def run_brand_export(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    status: str = "ACTIVE",
    limit: int = 2000,
    reference_time: datetime | None = None,
) -> ExportSummary:
    """Read-only end to end -- an export never writes to the database
    (no new rows, no status changes), unlike the weekly scan or the
    pipeline. `enable_export` (`.env.example`'s ENABLE_EXPORT, declared
    since the original refactor pass but never read by any code until
    this function) is this feature's own gate, the same role
    `ai.enabled`/`scheduler.enabled` play for theirs -- checked here so
    every caller (CLI, future API) gets the same behavior for free
    instead of each one re-checking the flag itself."""
    settings = settings or get_settings()
    if not settings.enable_export:
        msg = "export disabled (ENABLE_EXPORT=false)"
        raise ExportDisabledError(msg)

    rows = await build_brand_export_rows(session, status=status, limit=limit)
    generated_at = reference_time or utcnow()
    filename = f"brands-{status.lower()}-{generated_at:%Y-%m-%dT%H%M%S}.xlsx"
    path = Path(settings.exports_dir) / filename
    write_brand_export_workbook(rows, path)

    return ExportSummary(rows_exported=len(rows), status_filter=status, path=str(path), generated_at=generated_at)


class ExportDisabledError(RuntimeError):
    """Raised when `run_brand_export` is called with `enable_export:
    false` -- a controlled, named error the caller (CLI/API) can catch
    and report cleanly, not a generic exception."""
