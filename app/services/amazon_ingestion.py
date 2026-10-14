"""
Ingestion Anello 1 (2026-10-14): chiude il gap documentato di Fase 2
("nessuna riga Brand/AmazonProduct popolata dai dati scrapati").

Tre scritture, tutte idempotenti sulle entità e append-only sulle
osservazioni:

* `ingest_product_page` — una scheda prodotto parsata diventa un
  ResearchResult risolto dalle regole ESISTENTI di brand_resolution
  (nessuna nuova regola di merge), più uno snapshot AmazonListing (storico
  BSR/prezzo/recensioni) e righe Evidence che puntano all'HTML salvato.
* `apply_seller_profile` — dati della pagina /sp?seller= sul seller.
* `apply_vies_result` — azienda legale (Company) deduplicata per P.IVA.

Ogni campo scritto ha la sua Evidence: nessuna conclusione a valle
(opportunità, export) può citare un dato senza poter risalire alla pagina.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AmazonListing,
    AmazonProduct,
    AmazonSeller,
    Brand,
    Company,
    Evidence,
    ResearchJob,
    ResearchResult,
)
from app.models.enums import AcquisitionMethod, DataType, EvidenceSourceType
from app.services.brand_resolution import _get_or_create_seller as get_or_create_seller
from app.services.brand_resolution import resolve_research_result
from app.tools.amazon_scraping.page_parser import ProductPageData, SellerProfileData
from app.tools.vies.client import ViesResult

PROVIDER_NAME = "amazon_page_scraper"


@dataclass(frozen=True)
class ProductIngestOutcome:
    research_result_id: uuid.UUID
    product_id: uuid.UUID | None
    brand_id: uuid.UUID | None
    seller_row_id: uuid.UUID | None
    listing_id: uuid.UUID


def _evidence(
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    claim: str,
    raw_value: object,
    source: str,
    source_url: str | None,
    observed_at: datetime,
    source_type: EvidenceSourceType = EvidenceSourceType.AMAZON,
    acquisition_method: AcquisitionMethod = AcquisitionMethod.BROWSER,
    provider: str = PROVIDER_NAME,
    confidence: float = 90.0,
) -> Evidence:
    return Evidence(
        entity_type=entity_type,
        entity_id=str(entity_id),
        claim=claim[:1000],
        data_type=DataType.FACT.value,
        source=source[:255],
        source_url=source_url[:500] if source_url else None,
        source_type=source_type.value,
        raw_value=None if raw_value is None else str(raw_value),
        confidence=confidence,
        observed_at=observed_at,
        provider=provider,
        acquisition_method=acquisition_method.value,
    )


async def ingest_product_page(
    session: AsyncSession,
    job: ResearchJob,
    page: ProductPageData,
    *,
    source_url: str,
    html_path: Path,
    screenshot_path: Path | None,
    observed_at: datetime,
) -> ProductIngestOutcome:
    payload = {
        "asin": page.asin,
        "marketplace": page.marketplace,
        "title": page.title,
        "brand": page.brand,
        "seller_id": page.seller_id,
        "seller_name": page.seller_name,
        "seller_is_amazon": page.seller_is_amazon,
        "rating": page.rating,
        "review_count": page.review_count,
        "bsr": page.bsr,
        "category": page.bsr_category,
        "price": page.price,
        "source_url": source_url,
        "html_path": str(html_path),
    }
    result = ResearchResult(job_id=job.id, raw_payload=payload)
    session.add(result)
    await session.flush()
    outcome = await resolve_research_result(session, result)

    if outcome.product_id is None:
        raise RuntimeError(f"brand_resolution did not resolve a product for ASIN {page.asin}")

    product = await session.get(AmazonProduct, outcome.product_id)
    seller_row_id = outcome.seller_row_id
    if seller_row_id is None and page.seller_id:
        # brand_resolution collega il seller solo quando c'è anche il brand.
        # Per lo scout il venditore è il prospect anche senza brand: la riga
        # seller va creata comunque (senza link al brand, nessuna regola nuova).
        seller = await get_or_create_seller(
            session, page.seller_id, page.marketplace, page.seller_name or page.seller_id
        )
        seller_row_id = seller.id
        if product is not None and product.seller_id is None:
            product.seller_id = seller.id
    if seller_row_id is not None and page.seller_is_amazon:
        seller_row = await session.get(AmazonSeller, seller_row_id)
        if seller_row is not None:
            seller_row.is_amazon = True

    if product is not None:
        if page.price is not None:
            product.price = page.price
            product.currency = page.currency
        product.product_url = source_url
        if page.image_urls:
            product.image_url = page.image_urls[0]

    listing = AmazonListing(
        product_id=outcome.product_id,
        bullet_points=page.bullet_points,
        description=page.description,
        has_a_plus_content=page.has_a_plus_content,
        image_count=len(page.image_urls),
        video_count=1 if page.has_video else 0,
        observed_at=observed_at,
        title_length=page.title_length,
        image_urls=page.image_urls,
        price=page.price,
        rating=page.rating,
        review_count=page.review_count,
        bsr=page.bsr,
        bsr_category=page.bsr_category,
        html_path=str(html_path),
        screenshot_path=str(screenshot_path) if screenshot_path else None,
    )
    session.add(listing)
    await session.flush()

    source = f"{PROVIDER_NAME}:{html_path}"
    claims: list[tuple[str, object]] = [
        (f"Venditore della scheda {page.asin}: {page.seller_name} (id {page.seller_id})", page.seller_name),
        (f"Brand della scheda {page.asin}: {page.brand}", page.brand),
        (f"Valutazione media {page.asin}: {page.rating}", page.rating),
        (f"Numero recensioni {page.asin}: {page.review_count}", page.review_count),
        (f"BSR {page.asin}: {page.bsr} in {page.bsr_category}", page.bsr),
        (f"Immagini nella galleria {page.asin}: {len(page.image_urls)}", len(page.image_urls)),
        (f"Video presente {page.asin}: {page.has_video}", page.has_video),
        (f"Contenuto A+ presente {page.asin}: {page.has_a_plus_content}", page.has_a_plus_content),
        (f"Bullet point {page.asin}: {len(page.bullet_points)}", len(page.bullet_points)),
        (f"Lunghezza titolo {page.asin}: {page.title_length}", page.title_length),
    ]
    for claim, value in claims:
        if value is None:
            continue  # campo non trovato nella pagina: nessuna evidenza inventata
        session.add(
            _evidence(
                entity_type="product",
                entity_id=outcome.product_id,
                claim=claim,
                raw_value=value,
                source=source,
                source_url=source_url,
                observed_at=observed_at,
            )
        )
    await session.flush()

    return ProductIngestOutcome(
        research_result_id=result.id,
        product_id=outcome.product_id,
        brand_id=outcome.brand_id,
        seller_row_id=seller_row_id,
        listing_id=listing.id,
    )


async def apply_seller_profile(
    session: AsyncSession,
    seller: AmazonSeller,
    profile: SellerProfileData,
    *,
    source_url: str,
    html_path: Path,
    observed_at: datetime,
) -> None:
    seller.seller_url = source_url
    seller.business_name = profile.business_name
    seller.business_address = profile.business_address
    seller.business_country = profile.business_country
    seller.vat_number = profile.vat_number
    seller.vat_country = profile.vat_country
    seller.profile_checked_at = observed_at
    if profile.display_name and seller.seller_name == seller.seller_id:
        seller.seller_name = profile.display_name

    source = f"{PROVIDER_NAME}:{html_path}"
    for claim, value in (
        (f"Ragione sociale dichiarata su Amazon: {profile.business_name}", profile.business_name),
        (f"Indirizzo aziendale dichiarato su Amazon: {profile.business_address}", profile.business_address),
        (f"Paese della sede: {profile.business_country}", profile.business_country),
        (f"Partita IVA dichiarata su Amazon: {profile.vat_country or ''}{profile.vat_number}", profile.vat_number),
    ):
        if value is None:
            continue
        session.add(
            _evidence(
                entity_type="seller",
                entity_id=seller.id,
                claim=claim,
                raw_value=value,
                source=source,
                source_url=source_url,
                observed_at=observed_at,
            )
        )
    await session.flush()


async def apply_vies_result(session: AsyncSession, seller: AmazonSeller, vies: ViesResult) -> Company | None:
    """Get-or-create della Company per (paese, P.IVA) e collegamento a
    seller e brand. Ritorna None se VIES non ha dato una risposta
    definitiva (servizio dello Stato membro non disponibile): nessuna
    Company creata su un dato non confermato, si ritenta al prossimo giro."""
    if not vies.definitive:
        return None

    company = (
        await session.execute(
            select(Company).where(Company.vat_country == vies.country_code, Company.vat_number == vies.vat_number)
        )
    ).scalar_one_or_none()
    if company is None:
        company = Company(
            legal_name=vies.name or seller.business_name or seller.seller_name,
            vat_country=vies.country_code,
            vat_number=vies.vat_number,
            country="GR" if vies.country_code == "EL" else vies.country_code,
        )
        session.add(company)
    if vies.name:
        company.legal_name = vies.name
    if vies.address:
        company.address = vies.address
    if vies.city:
        company.city = vies.city
    company.vies_valid = vies.is_valid
    company.vies_checked_at = vies.checked_at
    await session.flush()

    seller.company_id = company.id
    if seller.brand_id is not None:
        brand = await session.get(Brand, seller.brand_id)
        if brand is not None and brand.company_id is None:
            brand.company_id = company.id

    session.add(
        _evidence(
            entity_type="company",
            entity_id=company.id,
            claim=(
                f"VIES {vies.country_code}{vies.vat_number}: valida={vies.is_valid}, "
                f"nome={vies.name}, indirizzo={vies.address}"
            ),
            raw_value=vies.is_valid,
            source="vies:ec.europa.eu",
            source_url=None,
            observed_at=vies.checked_at,
            source_type=EvidenceSourceType.VIES,
            acquisition_method=AcquisitionMethod.API,
            provider="vies",
            confidence=99.0 if vies.is_valid else 90.0,
        )
    )
    await session.flush()
    return company
